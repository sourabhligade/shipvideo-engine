"""Journey progress bar + click ripple overlays for demo frames."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Tuple, Union

from PIL import Image, ImageDraw

from app.product.brand_theme import BrandTheme, DEFAULT_BRAND

PathLike = Union[str, Path]
BBox = Dict[str, float]


def draw_progress_bar(
    src: PathLike,
    dest: PathLike,
    *,
    step_index: int,
    total_steps: int,
    color: Optional[Tuple[int, int, int, int]] = None,
    theme: Optional[BrandTheme] = None,
) -> Path:
    theme = theme or DEFAULT_BRAND
    if color is None:
        color = theme.primary_rgba(230)
    src = Path(src)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img = Image.open(src).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    w, h = img.size
    total = max(1, int(total_steps))
    idx = max(0, min(int(step_index), total))
    frac = idx / total

    bar_h = max(4, h // 90)
    y0 = h - bar_h - 2
    draw.rectangle((0, y0, w, h), fill=(0, 0, 0, 120))
    draw.rectangle((0, y0, int(w * frac), h), fill=color)

    # Step ticks
    if total <= 24:
        for i in range(1, total):
            x = int(w * (i / total))
            draw.line((x, y0, x, h), fill=(255, 255, 255, 40), width=1)

    composed = Image.alpha_composite(img, overlay).convert("RGB")
    composed.save(dest, format="PNG")
    return dest


def draw_click_ripple(
    src: PathLike,
    dest: PathLike,
    bbox: Optional[BBox],
    *,
    rings: int = 3,
) -> Path:
    """Expanding rings centered on click target (screencast-style click feedback)."""
    src = Path(src)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img = Image.open(src).convert("RGBA")
    if not bbox:
        img.convert("RGB").save(dest, format="PNG")
        return dest

    try:
        x = float(bbox.get("x", 0))
        y = float(bbox.get("y", 0))
        bw = float(bbox.get("w", bbox.get("width", 0)))
        bh = float(bbox.get("h", bbox.get("height", 0)))
    except (TypeError, ValueError):
        img.convert("RGB").save(dest, format="PNG")
        return dest

    cx = int(x + bw / 2)
    cy = int(y + bh / 2)
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    base_r = int(max(bw, bh) * 0.55) + 8
    for i in range(rings):
        r = base_r + i * 14
        alpha = max(40, 200 - i * 55)
        draw.ellipse(
            (cx - r, cy - r, cx + r, cy + r),
            outline=(64, 156, 255, alpha),
            width=3,
        )
    # solid cursor dot
    draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=(255, 255, 255, 230))
    draw.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=(64, 156, 255, 255))

    composed = Image.alpha_composite(img, overlay).convert("RGB")
    composed.save(dest, format="PNG")
    return dest
