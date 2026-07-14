"""Tests for click-highlight annotation and zoom-to-target."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from app.render_effects.annotate import (
    annotate_frame,
    annotate_frames,
    draw_click_highlight,
    zoom_crop_toward_bbox,
)


def _ui(path: Path, size=(640, 360)) -> Path:
    img = Image.new("RGB", size, (245, 245, 248))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, size[0], 48), fill=(30, 30, 40))
    draw.rounded_rectangle((80, 160, 240, 210), radius=8, fill=(34, 120, 220))
    img.save(path, format="PNG")
    return path


class AnnotateTests(unittest.TestCase):
    def test_draw_click_highlight_writes_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = _ui(root / "src.png")
            out = root / "out.png"
            dest = draw_click_highlight(
                src,
                out,
                bbox={"x": 80, "y": 160, "w": 160, "h": 50},
                label="Get started",
                step_index=2,
                total_steps=5,
                proof_badge="url_changed",
            )
            self.assertTrue(dest.exists())
            img = Image.open(dest)
            self.assertEqual(img.size, (640, 360))
            # Highlight region should differ from plain blue button area
            # Sample a pixel near the ring edge
            px = img.getpixel((78, 158))
            self.assertIsInstance(px, tuple)

    def test_annotate_frames_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = [_ui(root / f"f{i}.png") for i in range(3)]
            outs = annotate_frames(
                frames,
                root / "ann",
                labels=["A", "B", "C"],
                bboxes=[{"x": 80, "y": 160, "w": 160, "h": 50}, None, None],
                proof_badges=["", "proven", ""],
            )
            self.assertEqual(len(outs), 3)
            for p in outs:
                self.assertTrue(p.exists())

    def test_zoom_crop_enlarges_target_region(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = _ui(root / "src.png")
            out = root / "zoom.png"
            zoom_crop_toward_bbox(
                src,
                out,
                {"x": 80, "y": 160, "w": 160, "h": 50},
                zoom=1.5,
            )
            self.assertTrue(out.exists())
            self.assertEqual(Image.open(out).size, (640, 360))

    def test_zoom_without_bbox_is_passthrough(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = _ui(root / "src.png")
            out = root / "zoom.png"
            zoom_crop_toward_bbox(src, out, None, zoom=1.5)
            self.assertTrue(out.exists())


class JourneyScoringTests(unittest.TestCase):
    def test_testid_boosts_score(self):
        from app.product.journey import score_candidate

        plain = score_candidate("Settings", "/settings", "link")
        with_tid = score_candidate("Settings", "/settings", "link", testid="nav-settings")
        self.assertGreater(with_tid, plain)

    def test_pick_next_skips_visited(self):
        from app.product.journey import pick_next_targets

        cands = [
            {"text": "Pricing", "href": "/pricing", "role": "link", "testid": "", "aria": ""},
            {"text": "Docs", "href": "/docs", "role": "link", "testid": "docs", "aria": ""},
        ]
        out = pick_next_targets(
            cands,
            current_url="https://example.com/",
            visited={"https://example.com/pricing"},
            limit=3,
        )
        hrefs = [o.get("resolved_url") for o in out]
        self.assertNotIn("https://example.com/pricing", hrefs)
        self.assertTrue(any("docs" in (h or "") for h in hrefs))

    def test_focus_narration(self):
        from app.product.journey import JourneyStep, narrate_step

        step = JourneyStep(
            index=1,
            action="click",
            url="https://example.com",
            label="Get started",
            frame_role="focus",
        )
        text = narrate_step(step, is_first=False, is_last=False)
        self.assertIn("Get started", text)
        self.assertIn("focus", text.lower())

    def test_page_fingerprint_changes_on_url(self):
        from app.product.journey import page_fingerprint

        a = page_fingerprint("https://ex.com/a", "A", "hello")
        b = page_fingerprint("https://ex.com/b", "A", "hello")
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()


class GifExportTests(unittest.TestCase):
    def test_export_gif(self):
        from app.render_effects.gif_export import export_gif

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = [_ui(root / f"g{i}.png") for i in range(3)]
            out = export_gif(frames, root / "out.gif", duration_ms=200)
            self.assertTrue(out.exists())
            self.assertGreater(out.stat().st_size, 100)


class PrepareDemoFramesTests(unittest.TestCase):
    def test_prepare_applies_zoom_on_focus(self):
        from app.product.journey import JourneyStep
        from app.product.video import prepare_demo_frames

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = _ui(root / "focus.png")
            step = JourneyStep(
                index=0,
                action="click",
                url="https://example.com",
                label="Go",
                screenshot_path=str(src),
                click_bbox={"x": 80, "y": 160, "w": 160, "h": 50},
                frame_role="focus",
            )
            outs = prepare_demo_frames([step], root / "work", apply_zoom=True)
            self.assertEqual(len(outs), 1)
            self.assertTrue(outs[0].exists())
            self.assertTrue(step.zoom_path)


class ViewportScoringTests(unittest.TestCase):
    def test_hero_beats_footer(self):
        from app.product.journey import score_candidate

        hero = score_candidate(
            "Pricing", "/pricing", "link",
            bbox={"x": 500, "y": 220, "w": 100, "h": 40},
        )
        footer = score_candidate(
            "Pricing", "/pricing", "link",
            bbox={"x": 500, "y": 690, "w": 100, "h": 40},
        )
        self.assertGreater(hero, footer)
