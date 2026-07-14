"""Brand stamp (corner badge) on frames."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple, Union

from PIL import Image, ImageDraw, ImageFont

from app.product.brand_theme import BrandTheme, DEFAULT_BRAND

PathLike = Union[str, Path]


def _font(size: int = 16) -> ImageFont.ImageFont:
    for p in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def stamp_brand(
    src: PathLike,
    dest: PathLike,
    *,
    text: str = "",
    corner: str = "br",
    theme: Optional[BrandTheme] = None,
) -> Path:
    theme = theme or DEFAULT_BRAND
    src = Path(src)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    img = Image.open(src).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _font(15)
    label = (text or theme.name or "ShipVideo").strip()[:24]
    bb = draw.textbbox((0, 0), label, font=font)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    pad_x, pad_y = 12, 7
    w, h = img.size
    box_w, box_h = tw + pad_x * 2, th + pad_y * 2
    margin = 16
    if corner == "tl":
        x0, y0 = margin, margin
    elif corner == "tr":
        x0, y0 = w - box_w - margin, margin
    elif corner == "bl":
        x0, y0 = margin, h - box_h - margin
    else:
        x0, y0 = w - box_w - margin, h - box_h - margin

    draw.rounded_rectangle(
        (x0, y0, x0 + box_w, y0 + box_h),
        radius=10,
        fill=theme.bg_rgba(170),
        outline=theme.primary_rgba(200),
        width=1,
    )
    draw.text((x0 + pad_x, y0 + pad_y), label, font=font, fill=theme.text_rgba(255))
    composed = Image.alpha_composite(img, overlay).convert("RGB")
    composed.save(dest, format="PNG")
    return dest
