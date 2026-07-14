"""Brand theme, i18n captions, vertical sizzle frames."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from app.product.brand_theme import BrandTheme
from app.render_effects.brand import stamp_brand
from app.render_effects.i18n_captions import (
    normalize_lang,
    translate_lines,
    translate_phrase,
    write_translated_srt,
)
from app.render_effects.progress import draw_progress_bar
from app.render_effects.sizzle import make_vertical_frame
from app.render_effects.thumbnail import make_thumbnail
from app.render_effects.title_card import make_title_card


def _frame(path: Path, size=(1280, 720)) -> Path:
    img = Image.new("RGB", size, (30, 40, 55))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((500, 300, 780, 380), radius=12, fill=(60, 140, 240))
    img.save(path, format="PNG")
    return path


class BrandThemeTests(unittest.TestCase):
    def test_from_hex_dict(self):
        t = BrandTheme.from_dict(
            {"name": "Acme", "primary": "#ff5500", "accent": "#00aaff"}
        )
        self.assertEqual(t.name, "Acme")
        self.assertEqual(t.primary, (255, 85, 0))
        self.assertEqual(t.accent[0], 0)

    def test_stamp_and_progress_use_theme(self):
        theme = BrandTheme.from_dict({"name": "Acme", "primary": "#e11d48"})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = _frame(root / "f.png")
            out1 = stamp_brand(src, root / "b.png", theme=theme)
            out2 = draw_progress_bar(src, root / "p.png", step_index=2, total_steps=5, theme=theme)
            out3 = make_title_card(root / "c.png", title="Hi", theme=theme)
            out4 = make_thumbnail(src, root / "t.jpg", title="Hi", theme=theme)
            for p in (out1, out2, out3, out4):
                self.assertTrue(p.exists())


class I18nTests(unittest.TestCase):
    def test_normalize_lang(self):
        self.assertEqual(normalize_lang("ES-mx"), "es")
        self.assertEqual(normalize_lang("zz"), "en")

    def test_translate_focus_phrase(self):
        en = 'We focus on “Pricing” — the next control to click.'
        es = translate_phrase(en, "es")
        self.assertIn("Pricing", es)
        self.assertNotEqual(es, en)

    def test_translate_lines_batch(self):
        lines = [
            "We open the starting link and land on Home.",
            "Journey complete on Home.",
        ]
        fr = translate_lines(lines, "fr")
        self.assertEqual(len(fr), 2)
        self.assertIn("Home", fr[0])

    def test_write_translated_srt(self):
        cues = [
            {"index": 1, "start": 0.0, "end": 2.0, "text": "Journey complete on Home."},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            p = write_translated_srt(cues, Path(tmp) / "x.es.srt", "es")
            body = p.read_text(encoding="utf-8")
            self.assertIn("-->", body)
            self.assertIn("Home", body)


class SizzleFrameTests(unittest.TestCase):
    def test_vertical_frame_aspect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = _frame(root / "wide.png")
            out = make_vertical_frame(
                src,
                root / "v.png",
                size=(1080, 1920),
                focus_bbox={"x": 500, "y": 300, "w": 280, "h": 80},
            )
            im = Image.open(out)
            self.assertEqual(im.size, (1080, 1920))


if __name__ == "__main__":
    unittest.main()
