import logging
import subprocess
import time
from pathlib import Path
from typing import Iterable, List, Optional
from observability import pipeline_step
from app.config_types import load_capture_settings
from app.frame_dedup import dedupe_frames

logger = logging.getLogger(__name__)

BASE_APP_DIR = Path(__file__).resolve().parent
SCREENSHOT_DIR = BASE_APP_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


FFMPEG_LOGLEVEL = "-loglevel", "error"


@pipeline_step("render")
def render_video(
    approved_frames: Optional[Iterable[str | Path]] = None,
    *,
    render_approval: Optional[dict] = None,
):
    output_path = SCREENSHOT_DIR / "out.mp4"

    cs = load_capture_settings()
    W = cs.viewport_width
    H = cs.viewport_height

    shot_files: List[str] = []
    missing_frames: List[str] = []
    for frame in approved_frames or []:
        path = Path(frame)
        if path.exists():
            shot_files.append(str(path))
        else:
            missing_frames.append(str(path))
    if missing_frames:
        logger.debug(
            "render_video: skipping missing approved frames",
            extra={
                "operation": "render_video",
                "missing_count": len(missing_frames),
                "missing_frames": missing_frames[:20],
                "output_path": str(output_path),
            },
        )

    # Drop true duplicates only; small localized UI changes are retained.
    before_dedup = len(shot_files)
    shot_files = dedupe_frames(shot_files)
    removed = before_dedup - len(shot_files)
    logger.debug(
        "render_video: frame_dedup completed",
        extra={
            "operation": "frame_dedup",
            "frames_before": before_dedup,
            "frames_after": len(shot_files),
            "frames_removed": removed,
            "frames_kept": len(shot_files),
            "missing_count": len(missing_frames),
            "output_path": str(output_path),
        },
    )
    if removed:
        print(
            f"[render] frame_dedup removed {removed} "
            f"true-duplicate frame(s) ({before_dedup} -> {len(shot_files)})",
            flush=True,
        )

    approval = render_approval or {}
    if approval and not bool(approval.get("is_sendable")):
        reasons = approval.get("reasons") or ["unknown"]
        logger.error(
            "render_video: aborted — video approval not sendable",
            extra={
                "operation": "render_video",
                "approval_reasons": reasons,
                "output_path": str(output_path),
            },
        )
        raise RuntimeError(
            "Render aborted because video approval is not sendable: "
            f"{reasons}"
        )

    if not shot_files:
        logger.error(
            "render_video: no approved screenshot frames provided",
            extra={
                "operation": "render_video",
                "missing_frames": missing_frames[:20],
                "output_path": str(output_path),
            },
        )
        raise FileNotFoundError("No approved screenshot frames provided for render")

    print(f"[render] screenshots={len(shot_files)} viewport={W}x{H}", flush=True)
    logger.debug(
        "render_video: encoding slideshow",
        extra={
            "operation": "render_video",
            "frame_count": len(shot_files),
            "viewport": f"{W}x{H}",
            "output_path": str(output_path),
        },
    )

    scale_pad = (
        f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2"
    )


    if len(shot_files) == 1:

        shot_path = shot_files[0]
        cmd = [
            "ffmpeg", "-y",
            *FFMPEG_LOGLEVEL,
            "-loop", "1", "-t", "3", "-i", str(shot_path),
            "-vf", scale_pad,
            "-r", "30",
            "-c:v", "libx264",
            "-profile:v", "baseline",
            "-level", "3.0",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(output_path),
        ]
    else:

        input_args = []
        for shot in shot_files:
            input_args.extend(["-loop", "1", "-t", "3", "-i", str(shot)])

        filter_chains = "".join(
            f"[{i}:v]{scale_pad}[v{i}];" for i in range(len(shot_files))
        )
        concat_inputs = "".join(f"[v{i}]" for i in range(len(shot_files)))
        concat_filter = f"{filter_chains}{concat_inputs}concat=n={len(shot_files)}:v=1:a=0,format=yuv420p"
        cmd = [
            "ffmpeg", "-y",
            *FFMPEG_LOGLEVEL,
            *input_args,
            "-filter_complex", concat_filter,
            "-r", "30",
            "-c:v", "libx264",
            "-profile:v", "baseline",
            "-level", "3.0",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(output_path),
        ]

    encode_t0 = time.monotonic()
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )
    encode_duration_sec = time.monotonic() - encode_t0
    if result.returncode != 0:
        stderr_tail = (result.stderr or "").strip()
        if stderr_tail:
            print(f"[render] ffmpeg stderr: {stderr_tail}", flush=True)
        logger.error(
            "render_video: ffmpeg encode failed",
            extra={
                "operation": "ffmpeg_encode",
                "returncode": result.returncode,
                "frame_count": len(shot_files),
                "output_path": str(output_path),
                "duration_sec": round(encode_duration_sec, 3),
                "stderr_tail": stderr_tail[-2000:],
                "stdout_tail": (result.stdout or "")[-500:],
            },
        )
    else:
        out_bytes = output_path.stat().st_size if output_path.exists() else 0
        log_fn = logger.warning if encode_duration_sec > 60.0 else logger.debug
        log_fn(
            "render_video: ffmpeg encode completed",
            extra={
                "operation": "ffmpeg_encode",
                "frame_count": len(shot_files),
                "frames_kept": len(shot_files),
                "output_path": str(output_path),
                "duration_sec": round(encode_duration_sec, 3),
                "bytes": out_bytes,
                "slow": encode_duration_sec > 60.0,
            },
        )
    result.check_returncode()

if __name__ == "__main__":
    render_video()
