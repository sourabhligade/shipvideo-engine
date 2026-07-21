from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CaptureSettings:
    viewport_width: int = 1280
    viewport_height: int = 720
    full_page_screenshots: bool = False
    # When True, demo/debug frames capture full scrollable page (wired into runners).
    full_page_debug_screenshots: bool = True

    @property
    def effective_full_page(self) -> bool:
        """Full-page capture if either production or debug full-page is enabled."""
        return bool(self.full_page_screenshots or self.full_page_debug_screenshots)


def load_capture_settings() -> CaptureSettings:
    from app.config import load_config                                                   

    cfg = load_config()
    capture_cfg = cfg.get("capture") or {}
    viewport_cfg = capture_cfg.get("viewport") or {}
    return CaptureSettings(
        viewport_width=int(viewport_cfg.get("width", 1280)),
        viewport_height=int(viewport_cfg.get("height", 720)),
        full_page_screenshots=bool(capture_cfg.get("full_page_screenshots", False)),
        full_page_debug_screenshots=bool(capture_cfg.get("full_page_debug_screenshots", True)),
    )
