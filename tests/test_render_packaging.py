"""Chapters, thumbnail, brand, progress, title cards, YouTube description."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from app.product.journey import JourneyStep
from app.render_effects.brand import stamp_brand
from app.render_effects.chapters import (
    chapter_entries_from_steps,
    write_webvtt_chapters,
    write_youtube_chapters_text,
)
from app.render_effects.progress import draw_click_ripple, draw_progress_bar
from app.render_effects.thumbnail import make_thumbnail
from app.render_effects.title_card import make_title_card
from app.render_effects.youtube_desc import build_youtube_description


def _frame(path: Path, size=(640, 360)) -> Path:
    img = Image.new("RGB", size, (40, 50, 70))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((80, 140, 220, 190), radius=8, fill=(50, 130, 230))
    img.save(path, format="PNG")
    return path


class ChaptersTests(unittest.TestCase):
    def test_chapter_entries_and_youtube_format(self):
        steps = [
            JourneyStep(0, "goto", "https://ex.com", title="Home", frame_role="result"),
            JourneyStep(1, "click", "https://ex.com", label="Pricing", frame_role="focus"),
            JourneyStep(2, "click", "https://ex.com/pricing", label="Pricing", frame_role="result", proof_status="url_changed"),
        ]
        for s in steps:
            s.duration_sec = 2.0
        ch = chapter_entries_from_steps(steps)
        self.assertGreaterEqual(len(ch), 2)
        self.assertEqual(ch[0]["start"], 0.0)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vtt = write_webvtt_chapters(ch, root / "c.vtt", total_duration=6.0)
            txt = write_youtube_chapters_text(ch, root / "c.txt")
            body = txt.read_text()
            self.assertTrue(body.startswith("0:00 ") or body.startswith("00:00 "))
            self.assertIn("WEBVTT", vtt.read_text())

    def test_youtube_description_contains_chapters(self):
        steps = [
            JourneyStep(0, "goto", "https://ex.com", title="Home", frame_role="result", duration_sec=2),
            JourneyStep(1, "click", "https://ex.com", label="Docs", frame_role="focus", duration_sec=2),
        ]
        desc = build_youtube_description(
            title="My Demo",
            steps=steps,
            start_url="https://ex.com",
            proven_clicks=1,
        )
        self.assertIn("Chapters:", desc)
        self.assertIn("My Demo", desc)
        self.assertIn("Proven interactions", desc)


class VisualPolishTests(unittest.TestCase):
    def test_progress_ripple_brand_thumb_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = _frame(root / "f.png")
            bbox = {"x": 80, "y": 140, "w": 140, "h": 50}
            p1 = draw_progress_bar(src, root / "p.png", step_index=2, total_steps=5)
            p2 = draw_click_ripple(src, root / "r.png", bbox)
            p3 = stamp_brand(src, root / "b.png")
            p4 = make_thumbnail(src, root / "t.jpg", title="Pricing demo")
            p5 = make_title_card(root / "intro.png", title="Welcome", subtitle="A walkthrough")
            for p in (p1, p2, p3, p4, p5):
                self.assertTrue(p.exists())
                self.assertGreater(p.stat().st_size, 100)


if __name__ == "__main__":
    unittest.main()
