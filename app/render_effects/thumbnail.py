"""Poster / thumbnail generation from a key demo frame."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Optional, Tuple, Union

from PIL import Image, ImageDraw, ImageFont

from app.product.brand_theme import BrandTheme, DEFAULT_BRAND

PathLike = Union[str, Path]


def _font(size: int) -> ImageFont.ImageFont:
    for p in (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def make_thumbnail(
    frame_path: PathLike,
    out_path: PathLike,
    *,
    title: str = "Demo",
    subtitle: str = "",
    size: Tuple[int, int] = (1280, 720),
    brand: str = "",
    theme: Optional[BrandTheme] = None,
) -> Path:
    theme = theme or DEFAULT_BRAND
    brand = brand or theme.name
    src = Path(frame_path)
    dest = Path(out_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    base = Image.open(src).convert("RGB")
    base = base.resize(size, Image.Resampling.LANCZOS)
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Gradient-ish bottom bar
    w, h = size
    for y in range(h // 2, h):
        alpha = int(200 * ((y - h // 2) / max(1, h // 2)))
        draw.line([(0, y), (w, y)], fill=(8, 12, 24, min(220, alpha)))

    # Accent stripe
    draw.rectangle((0, 0, 12, h), fill=(*theme.primary, 240))

    title_font = _font(48)
    sub_font = _font(26)
    brand_font = _font(20)

    title = (title or "Demo").strip()[:80]
    lines = textwrap.wrap(title, width=32) or ["Demo"]
    y = h - 160
    for line in lines[:2]:
        draw.text((40, y), line, font=title_font, fill=(255, 255, 255, 255))
        y += 56

    if subtitle:
        draw.text((40, y + 4), subtitle.strip()[:90], font=sub_font, fill=(180, 200, 240, 255))

    # Brand pill
    brand = (brand or "ShipVideo").strip()
    bb = draw.textbbox((0, 0), brand, font=brand_font)
    bw, bh = bb[2] - bb[0], bb[3] - bb[1]
    bx, by = w - bw - 48, 28
    draw.rounded_rectangle(
        (bx - 14, by - 8, bx + bw + 14, by + bh + 8),
        radius=999,
        fill=theme.primary_rgba(230),
    )
    draw.text((bx, by), brand, font=brand_font, fill=(255, 255, 255, 255))

    out = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
    out.save(dest, format="JPEG", quality=90)
    return dest


def write_thumbnail(
    frame_path: PathLike,
    out_path: PathLike,
    **kwargs,
) -> Path:
    return make_thumbnail(frame_path, out_path, **kwargs)
