"""Intro / outro title cards for demo videos."""

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


def make_title_card(
    out_path: PathLike,
    *,
    title: str,
    subtitle: str = "",
    footer: str = "",
    size: Tuple[int, int] = (1280, 720),
    style: str = "intro",
    theme: Optional["BrandTheme"] = None,
) -> Path:
    theme = theme or DEFAULT_BRAND
    dest = Path(out_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    img = Image.new("RGB", size, theme.bg)
    draw = ImageDraw.Draw(img)
    footer = footer or theme.name

    # Soft gradient blobs from brand colors
    for i, col in enumerate((theme.primary, theme.accent)):
        blob = Image.new("RGBA", size, (0, 0, 0, 0))
        bd = ImageDraw.Draw(blob)
        if i == 0:
            bd.ellipse((-200, -100, 600, 500), fill=(*col, 90))
        else:
            bd.ellipse((w - 700, h - 500, w + 100, h + 100), fill=(*col, 80))
        img = Image.alpha_composite(img.convert("RGBA"), blob).convert("RGB")
        draw = ImageDraw.Draw(img)

    draw.rectangle((0, 0, 10, h), fill=theme.primary)

    title_font = _font(52 if style == "intro" else 44)
    sub_font = _font(26)
    foot_font = _font(18)

    title = (title or "Product demo").strip()
    lines = textwrap.wrap(title, width=28) or ["Product demo"]
    block_h = len(lines) * 60
    y = (h - block_h) // 2 - 20
    for line in lines[:3]:
        bb = draw.textbbox((0, 0), line, font=title_font)
        tw = bb[2] - bb[0]
        draw.text(((w - tw) // 2, y), line, font=title_font, fill=theme.text)
        y += 60

    if subtitle:
        sub = subtitle.strip()[:100]
        bb = draw.textbbox((0, 0), sub, font=sub_font)
        tw = bb[2] - bb[0]
        draw.text(((w - tw) // 2, y + 16), sub, font=sub_font, fill=(150, 170, 210))

    if footer:
        bb = draw.textbbox((0, 0), footer, font=foot_font)
        tw = bb[2] - bb[0]
        draw.text(((w - tw) // 2, h - 48), footer, font=foot_font, fill=(120, 140, 180))

    img.save(dest, format="PNG")
    return dest
