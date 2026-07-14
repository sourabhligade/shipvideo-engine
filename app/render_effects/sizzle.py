"""Vertical (9:16) sizzle clip from demo frames for social/short form."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence, Union

from PIL import Image

PathLike = Union[str, Path]
logger = logging.getLogger(__name__)


def make_vertical_frame(
    src: PathLike,
    dest: PathLike,
    *,
    size: tuple[int, int] = (1080, 1920),
    focus_bbox: Optional[dict] = None,
) -> Path:
    """Center-crop / focus-crop a landscape frame into 9:16."""
    src = Path(src)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img = Image.open(src).convert("RGB")
    w, h = img.size
    tw, th = size
    target_aspect = tw / th
    src_aspect = w / h

    # Prefer crop around click focus when present
    if focus_bbox and isinstance(focus_bbox, dict):
        try:
            cx = float(focus_bbox.get("x", 0)) + float(focus_bbox.get("w", 0)) / 2.0
            cy = float(focus_bbox.get("y", 0)) + float(focus_bbox.get("h", 0)) / 2.0
        except (TypeError, ValueError):
            cx, cy = w / 2.0, h / 2.0
    else:
        cx, cy = w / 2.0, h * 0.42

    if src_aspect > target_aspect:
        # too wide — crop width
        new_w = int(h * target_aspect)
        left = int(max(0, min(w - new_w, cx - new_w / 2)))
        box = (left, 0, left + new_w, h)
    else:
        new_h = int(w / target_aspect)
        top = int(max(0, min(h - new_h, cy - new_h / 2)))
        box = (0, top, w, top + new_h)

    cropped = img.crop(box).resize(size, Image.Resampling.LANCZOS)
    cropped.save(dest, format="PNG")
    return dest


def export_sizzle_mp4(
    frames: Sequence[PathLike],
    out_path: PathLike,
    *,
    durations: Optional[Sequence[float]] = None,
    max_frames: int = 8,
    max_seconds: float = 15.0,
    size: tuple[int, int] = (1080, 1920),
    bboxes: Optional[Sequence[Optional[dict]]] = None,
    work_dir: Optional[PathLike] = None,
) -> Path:
    """Build a short vertical MP4 from key frames (focus-first selection)."""
    out_path = Path(out_path)
    work = Path(work_dir or out_path.parent / "sizzle_work")
    work.mkdir(parents=True, exist_ok=True)

    paths = [Path(p) for p in frames if Path(p).exists()]
    if not paths:
        raise FileNotFoundError("No frames for sizzle export")

    # Prefer middle/focus frames for sizzle pacing
    if len(paths) > max_frames:
        # take first, evenly spaced middle, last
        idxs = [0]
        mid = len(paths) - 2
        for k in range(1, max_frames - 1):
            idxs.append(int(k * mid / (max_frames - 1)))
        idxs.append(len(paths) - 1)
        # unique preserve order
        seen = set()
        ordered = []
        for i in idxs:
            if i not in seen:
                seen.add(i)
                ordered.append(i)
        sel = ordered[:max_frames]
    else:
        sel = list(range(len(paths)))

    vframes: List[Path] = []
    durs: List[float] = []
    default_dur = max(0.6, min(2.0, max_seconds / max(1, len(sel))))
    for j, i in enumerate(sel):
        bbox = None
        if bboxes and i < len(bboxes):
            bbox = bboxes[i]
        dest = work / f"v_{j:03d}.png"
        make_vertical_frame(paths[i], dest, size=size, focus_bbox=bbox)
        vframes.append(dest)
        if durations and i < len(durations):
            durs.append(max(0.4, min(2.5, float(durations[i]))))
        else:
            durs.append(default_dur)

    # Cap total duration
    total = sum(durs)
    if total > max_seconds and total > 0:
        scale = max_seconds / total
        durs = [max(0.35, d * scale) for d in durs]

    # FFmpeg concat slideshow
    if len(vframes) == 1:
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-loop", "1", "-t", str(durs[0]), "-i", str(vframes[0]),
            "-vf", f"scale={size[0]}:{size[1]},format=yuv420p",
            "-r", "30",
            "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.0",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-an",
            str(out_path),
        ]
    else:
        input_args: List[str] = []
        for fr, dur in zip(vframes, durs):
            input_args.extend(["-loop", "1", "-t", str(dur), "-i", str(fr)])
        chains = "".join(
            f"[{i}:v]scale={size[0]}:{size[1]},format=yuv420p[v{i}];"
            for i in range(len(vframes))
        )
        concat_in = "".join(f"[v{i}]" for i in range(len(vframes)))
        filt = f"{chains}{concat_in}concat=n={len(vframes)}:v=1:a=0"
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            *input_args,
            "-filter_complex", filt,
            "-r", "30",
            "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.0",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-an",
            str(out_path),
        ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"sizzle ffmpeg failed: {(proc.stderr or '')[-1500:]}")
    return out_path
