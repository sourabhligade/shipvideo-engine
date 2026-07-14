"""Per-customer brand theme for demo polish (colors, name, accent)."""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Tuple


def _parse_hex(value: str, default: Tuple[int, int, int]) -> Tuple[int, int, int]:
    raw = (value or "").strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{6}", raw):
        return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
    if re.fullmatch(r"[0-9a-fA-F]{3}", raw):
        return int(raw[0] * 2, 16), int(raw[1] * 2, 16), int(raw[2] * 2, 16)
    return default


@dataclass(frozen=True)
class BrandTheme:
    name: str = "ShipVideo"
    primary: Tuple[int, int, int] = (91, 140, 255)
    accent: Tuple[int, int, int] = (124, 92, 255)
    text: Tuple[int, int, int] = (232, 238, 252)
    bg: Tuple[int, int, int] = (8, 12, 24)
    success: Tuple[int, int, int] = (61, 214, 140)

    def primary_rgba(self, a: int = 230) -> Tuple[int, int, int, int]:
        return (*self.primary, a)

    def accent_rgba(self, a: int = 200) -> Tuple[int, int, int, int]:
        return (*self.accent, a)

    def bg_rgba(self, a: int = 170) -> Tuple[int, int, int, int]:
        return (*self.bg, a)

    def text_rgba(self, a: int = 255) -> Tuple[int, int, int, int]:
        return (*self.text, a)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["primary"] = list(self.primary)
        d["accent"] = list(self.accent)
        d["text"] = list(self.text)
        d["bg"] = list(self.bg)
        d["success"] = list(self.success)
        d["primary_hex"] = "#{:02x}{:02x}{:02x}".format(*self.primary)
        d["accent_hex"] = "#{:02x}{:02x}{:02x}".format(*self.accent)
        return d

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "BrandTheme":
        if not data:
            return cls.from_env()
        name = str(data.get("name") or data.get("brand") or "ShipVideo").strip() or "ShipVideo"
        primary = data.get("primary") or data.get("primary_hex") or data.get("color")
        accent = data.get("accent") or data.get("accent_hex")
        text = data.get("text") or data.get("text_hex")
        bg = data.get("bg") or data.get("bg_hex")
        success = data.get("success") or data.get("success_hex")

        def as_rgb(v: Any, default: Tuple[int, int, int]) -> Tuple[int, int, int]:
            if isinstance(v, (list, tuple)) and len(v) >= 3:
                return int(v[0]), int(v[1]), int(v[2])
            if isinstance(v, str):
                return _parse_hex(v, default)
            return default

        return cls(
            name=name[:32],
            primary=as_rgb(primary, (91, 140, 255)),
            accent=as_rgb(accent, (124, 92, 255)),
            text=as_rgb(text, (232, 238, 252)),
            bg=as_rgb(bg, (8, 12, 24)),
            success=as_rgb(success, (61, 214, 140)),
        )

    @classmethod
    def from_env(cls) -> "BrandTheme":
        return cls(
            name=(os.getenv("SHIPVIDEO_BRAND_NAME") or "ShipVideo").strip()[:32] or "ShipVideo",
            primary=_parse_hex(os.getenv("SHIPVIDEO_BRAND_PRIMARY") or "", (91, 140, 255)),
            accent=_parse_hex(os.getenv("SHIPVIDEO_BRAND_ACCENT") or "", (124, 92, 255)),
            text=_parse_hex(os.getenv("SHIPVIDEO_BRAND_TEXT") or "", (232, 238, 252)),
            bg=_parse_hex(os.getenv("SHIPVIDEO_BRAND_BG") or "", (8, 12, 24)),
            success=_parse_hex(os.getenv("SHIPVIDEO_BRAND_SUCCESS") or "", (61, 214, 140)),
        )


DEFAULT_BRAND = BrandTheme()
