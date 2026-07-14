"""Draw step labels, click-target rings, and proof badges on frames."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from PIL import Image, ImageDraw, ImageFont

PathLike = Union[str, Path]
BBox = Dict[str, float]

HIGHLIGHT_COLOR = (64, 156, 255, 230)
HIGHLIGHT_FILL = (64, 156, 255, 45)
CALLOUT_BG = (0, 0, 0, 180)
PROOF_OK = (34, 170, 80, 220)
PROOF_WARN = (230, 160, 40, 220)
PROOF_BAD = (220, 60, 60, 220)


def _font(size: int = 22) -> ImageFont.ImageFont:
    for path in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _normalize_bbox(
    bbox: Optional[BBox],
    img_w: int,
    img_h: int,
) -> Optional[Tuple[int, int, int, int]]:
    if not bbox:
        return None
    try:
        x = float(bbox.get("x", bbox.get("left", 0)))
        y = float(bbox.get("y", bbox.get("top", 0)))
        w = float(bbox.get("w", bbox.get("width", 0)))
        h = float(bbox.get("h", bbox.get("height", 0)))
    except (TypeError, ValueError):
        return None
    if w <= 1 and h <= 1 and (x <= 1 and y <= 1):
        x, y, w, h = x * img_w, y * img_h, w * img_w, h * img_h
    if w < 2 or h < 2:
        return None
    pad = 6
    x0 = max(0, int(x - pad))
    y0 = max(0, int(y - pad))
    x1 = min(img_w - 1, int(x + w + pad))
    y1 = min(img_h - 1, int(y + h + pad))
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def draw_click_highlight(
    image_path: PathLike,
    out_path: PathLike,
    *,
    bbox: Optional[BBox] = None,
    label: str = "",
    step_index: int = 0,
    total_steps: int = 0,
    proof_badge: str = "",
) -> Path:
    """Draw a click ring + optional step callout + proof badge onto a frame."""
    src = Path(image_path)
    dest = Path(out_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    img = Image.open(src).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    w, h = img.size

    rect = _normalize_bbox(bbox, w, h)
    if rect:
        x0, y0, x1, y1 = rect
        draw.rounded_rectangle(
            (x0, y0, x1, y1),
            radius=10,
            outline=HIGHLIGHT_COLOR,
            width=4,
            fill=HIGHLIGHT_FILL,
        )
        # corner ticks for extra visibility
        tick = 14
        for (ax, ay, bx, by) in (
            (x0, y0, x0 + tick, y0),
            (x0, y0, x0, y0 + tick),
            (x1, y0, x1 - tick, y0),
            (x1, y0, x1, y0 + tick),
            (x0, y1, x0 + tick, y1),
            (x0, y1, x0, y1 - tick),
            (x1, y1, x1 - tick, y1),
            (x1, y1, x1, y1 - tick),
        ):
            draw.line((ax, ay, bx, by), fill=HIGHLIGHT_COLOR, width=5)

    font = _font(22)
    parts: List[str] = []
    if total_steps > 0 and step_index > 0:
        parts.append(f"Step {step_index}/{total_steps}")
    elif step_index > 0:
        parts.append(f"Step {step_index}")
    if label:
        parts.append(label.strip()[:80])
    if parts:
        text = "  ·  ".join(parts)
        pad_x, pad_y = 16, 10
        tb = draw.textbbox((0, 0), text, font=font)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        bar_h = th + pad_y * 2
        draw.rectangle((0, 0, w, bar_h + 4), fill=CALLOUT_BG)
        draw.text((pad_x, pad_y), text, font=font, fill=(255, 255, 255, 255))

    badge = (proof_badge or "").strip().lower()
    if badge:
        badge_font = _font(16)
        badge_text = {
            "proven": "PROVEN",
            "url_changed": "URL ✓",
            "same_page": "UI ✓",
            "unproven": "UNPROVEN",
            "failed": "FAILED",
        }.get(badge, badge.upper()[:12])
        color = PROOF_OK
        if badge in ("unproven",):
            color = PROOF_WARN
        elif badge in ("failed", "wrong"):
            color = PROOF_BAD
        bb = draw.textbbox((0, 0), badge_text, font=badge_font)
        bw, bh = bb[2] - bb[0], bb[3] - bb[1]
        bx0 = w - bw - 28
        by0 = h - bh - 28
        draw.rounded_rectangle(
            (bx0 - 10, by0 - 6, bx0 + bw + 10, by0 + bh + 6),
            radius=8,
            fill=color,
        )
        draw.text((bx0, by0), badge_text, font=badge_font, fill=(255, 255, 255, 255))

    composed = Image.alpha_composite(img, overlay).convert("RGB")
    composed.save(dest, format="PNG")
    return dest


def annotate_frame(
    src: PathLike,
    dest: PathLike,
    *,
    label: str = "",
    step_index: int = 0,
    total_steps: int = 0,
    proof_badge: str = "",
    bbox: Optional[BBox] = None,
) -> Path:
    return draw_click_highlight(
        src,
        dest,
        bbox=bbox,
        label=label,
        step_index=step_index,
        total_steps=total_steps,
        proof_badge=proof_badge,
    )


def annotate_frames(
    frames: Sequence[PathLike],
    dest_dir: PathLike,
    labels: Optional[Iterable[str]] = None,
    *,
    bboxes: Optional[Sequence[Optional[BBox]]] = None,
    proof_badges: Optional[Sequence[str]] = None,
) -> List[Path]:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    label_list = list(labels) if labels is not None else []
    out: List[Path] = []
    total = len(frames)
    for i, frame in enumerate(frames):
        label = label_list[i] if i < len(label_list) else ""
        bbox = bboxes[i] if bboxes and i < len(bboxes) else None
        badge = proof_badges[i] if proof_badges and i < len(proof_badges) else ""
        dest = dest_dir / f"ann_{i:03d}.png"
        out.append(
            annotate_frame(
                frame,
                dest,
                label=label,
                step_index=i + 1,
                total_steps=total,
                proof_badge=badge,
                bbox=bbox,
            )
        )
    return out


def zoom_crop_toward_bbox(
    image_path: PathLike,
    out_path: PathLike,
    bbox: Optional[BBox],
    *,
    zoom: float = 1.35,
) -> Path:
    """Mild crop+zoom toward a click region (Ken Burns-style focus still)."""
    src = Path(image_path)
    dest = Path(out_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img = Image.open(src).convert("RGB")
    w, h = img.size
    rect = _normalize_bbox(bbox, w, h)
    if not rect or zoom <= 1.01:
        img.save(dest, format="PNG")
        return dest

    x0, y0, x1, y1 = rect
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    zoom = min(max(zoom, 1.05), 2.0)
    crop_w = w / zoom
    crop_h = h / zoom
    left = max(0.0, min(w - crop_w, cx - crop_w / 2))
    top = max(0.0, min(h - crop_h, cy - crop_h / 2))
    cropped = img.crop((int(left), int(top), int(left + crop_w), int(top + crop_h)))
    cropped = cropped.resize((w, h), Image.Resampling.LANCZOS)
    cropped.save(dest, format="PNG")
    return dest
