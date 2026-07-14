"""Export a short looping GIF from demo frames for PR/social previews."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence, Union

from PIL import Image

PathLike = Union[str, Path]


def export_gif(
    frames: Sequence[PathLike],
    out_path: PathLike,
    *,
    max_frames: int = 12,
    size: tuple[int, int] = (640, 360),
    duration_ms: int = 900,
) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    paths = [Path(p) for p in frames if Path(p).exists()][:max_frames]
    if not paths:
        raise FileNotFoundError("No frames available for GIF export")

    images: List[Image.Image] = []
    for p in paths:
        im = Image.open(p).convert("RGB")
        im.thumbnail(size, Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", size, (10, 12, 18))
        x = (size[0] - im.size[0]) // 2
        y = (size[1] - im.size[1]) // 2
        canvas.paste(im, (x, y))
        images.append(canvas)

    images[0].save(
        out_path,
        format="GIF",
        save_all=True,
        append_images=images[1:],
        duration=duration_ms,
        loop=0,
        optimize=True,
    )
    return out_path
