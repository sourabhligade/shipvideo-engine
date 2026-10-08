import re
import subprocess
from pathlib import Path
from typing import Iterable, List, Optional
from observability import pipeline_step
from app.config_types import load_capture_settings

BASE_APP_DIR = Path(__file__).resolve().parent
SCREENSHOT_DIR = BASE_APP_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


FFMPEG_LOGLEVEL = "-loglevel", "error"

_SAFE_RUN_ID = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_run_token(run_id: str) -> str:
    token = _SAFE_RUN_ID.sub("", str(run_id or "").strip()).strip("._-")
    if not token:
        raise ValueError("run_id is required for per-run capture output")
    return token


def capture_dir_for_run(
    run_id: str,
    *,
    screenshot_dir: Optional[Path] = None,
) -> Path:
    """Per-run screenshot directory so concurrent captures cannot clobber shot*.png."""
    dest = (screenshot_dir or SCREENSHOT_DIR) / _safe_run_token(run_id)
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def video_output_path_for_run(
    run_id: str,
    *,
    screenshot_dir: Optional[Path] = None,
) -> Path:
    """Per-run mp4 path so concurrent pipelines cannot clobber one out.mp4."""
    return (screenshot_dir or SCREENSHOT_DIR) / f"{_safe_run_token(run_id)}.mp4"


@pipeline_step("render")
def render_video(
    approved_frames: Optional[Iterable[str | Path]] = None,
    *,
    render_approval: Optional[dict] = None,
    output_path: Optional[str | Path] = None,
) -> Path:
    dest = Path(output_path) if output_path else (SCREENSHOT_DIR / "out.mp4")
    dest.parent.mkdir(parents=True, exist_ok=True)

    cs = load_capture_settings()
    W = cs.viewport_width
    H = cs.viewport_height

    shot_files: List[str] = []
    for frame in approved_frames or []:
        path = Path(frame)
        if path.exists():
            shot_files.append(str(path))

    approval = render_approval or {}
    if approval and not bool(approval.get("is_sendable")):
        raise RuntimeError(
            "Render aborted because video approval is not sendable: "
            f"{approval.get('reasons') or ['unknown']}"
        )

    if not shot_files:
        raise FileNotFoundError("No approved screenshot frames provided for render")

    print(f"[render] screenshots={len(shot_files)} viewport={W}x{H}", flush=True)



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
            str(dest),
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
            str(dest),
        ]

    print(f"[render] output={dest}", flush=True)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0 and result.stderr:
        print(f"[render] ffmpeg stderr: {result.stderr.strip()}", flush=True)
    result.check_returncode()
    return dest

if __name__ == "__main__":
    render_video()
