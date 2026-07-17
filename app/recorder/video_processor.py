from __future__ import annotations

import subprocess
from pathlib import Path

FFMPEG_TIMEOUT_SECONDS: float = 120.0


def convert_webm_to_mp4(webm_path: Path, output_dir: Path) -> Path:
    if not webm_path.exists():
        raise FileNotFoundError(f"webm not found: {webm_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    mp4_path = output_dir / "out.mp4"

    cmd = [
        "ffmpeg", "-y",
        "-loglevel", "error",
        "-i", str(webm_path),
        "-c:v", "libx264",
        "-profile:v", "baseline",
        "-level", "3.0",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-an",
        str(mp4_path),
    ]

    print(f"[video_processor] converting webm→mp4 src={webm_path.name}", flush=True)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        print(
            f"[video_processor] ffmpeg timeout after {FFMPEG_TIMEOUT_SECONDS}s "
            f"src={webm_path.name}",
            flush=True,
        )
        raise RuntimeError(
            f"ffmpeg webm→mp4 timed out after {FFMPEG_TIMEOUT_SECONDS}s"
        ) from exc

    if result.returncode != 0:
        print(f"[video_processor] ffmpeg stderr: {result.stderr.strip()}", flush=True)
    result.check_returncode()

    print(f"[video_processor] mp4 ready path={mp4_path}", flush=True)
    return mp4_path
