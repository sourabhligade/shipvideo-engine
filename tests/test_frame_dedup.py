"""Tests for multi-signal frame deduplication.

The critical accuracy case: a small localized UI change (button state, toast,
badge) must *not* be treated as a duplicate of the prior frame. Mean-only
thresholds falsely drop those frames; block + changed-pixel gates catch them.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from app.frame_dedup import (
    MEAN_ABS_DIFF_MAX,
    compute_frame_diff,
    dedupe_frames,
    frames_are_duplicates,
    mean_only_are_duplicates,
)


def _solid(path: Path, color: tuple[int, int, int], size=(640, 360)) -> Path:
    Image.new("RGB", size, color).save(path, format="PNG")
    return path


def _ui_base(path: Path, size=(640, 360)) -> Path:
    """Simple mock UI: chrome + content panel."""
    img = Image.new("RGB", size, (245, 245, 248))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, size[0], 48), fill=(30, 30, 40))
    draw.rectangle((24, 72, size[0] - 24, size[1] - 24), fill=(255, 255, 255))
    draw.rectangle((40, 100, 280, 140), fill=(220, 220, 230))  # text field stand-in
    # Primary CTA — disabled gray
    draw.rounded_rectangle((40, 180, 200, 230), radius=8, fill=(160, 160, 170))
    img.save(path, format="PNG")
    return path


def _ui_with_button_enabled(path: Path, size=(640, 360)) -> Path:
    """Same UI as base except the CTA is green (enabled) — small localized change."""
    img = Image.new("RGB", size, (245, 245, 248))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, size[0], 48), fill=(30, 30, 40))
    draw.rectangle((24, 72, size[0] - 24, size[1] - 24), fill=(255, 255, 255))
    draw.rectangle((40, 100, 280, 140), fill=(220, 220, 230))
    # Primary CTA — enabled green (only meaningful visual delta)
    draw.rounded_rectangle((40, 180, 200, 230), radius=8, fill=(34, 170, 80))
    img.save(path, format="PNG")
    return path


def _ui_with_toast(path: Path, size=(640, 360)) -> Path:
    """Same base UI plus a small success toast in the corner."""
    _ui_base(path, size=size)
    img = Image.open(path).convert("RGB")
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        (size[0] - 220, 60, size[0] - 24, 110),
        radius=6,
        fill=(20, 120, 60),
    )
    img.save(path, format="PNG")
    return path


class FrameDedupExactAndLargeChangeTests(unittest.TestCase):
    def test_identical_frames_are_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = _solid(root / "a.png", (40, 80, 120))
            b = _solid(root / "b.png", (40, 80, 120))
            self.assertTrue(frames_are_duplicates(a, b))
            stats = compute_frame_diff(a, b)
            self.assertEqual(stats.mean_abs_diff, 0.0)
            self.assertTrue(stats.is_duplicate)

    def test_copy_of_same_file_is_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = _ui_base(root / "a.png")
            b = root / "b.png"
            Image.open(a).save(b)
            self.assertTrue(frames_are_duplicates(a, b))

    def test_full_page_color_change_is_not_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = _solid(root / "a.png", (10, 10, 10))
            b = _solid(root / "b.png", (240, 240, 240))
            self.assertFalse(frames_are_duplicates(a, b))


class FrameDedupSmallUiChangeTests(unittest.TestCase):
    """Regression: small UI deltas must survive dedup (false-negative fix)."""

    def test_small_button_state_change_is_not_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = _ui_base(root / "before.png")
            after = _ui_with_button_enabled(root / "after.png")

            stats = compute_frame_diff(before, after)
            # Global mean stays low — this is exactly why mean-only fails.
            self.assertLess(stats.mean_abs_diff, MEAN_ABS_DIFF_MAX)
            self.assertTrue(
                mean_only_are_duplicates(before, after),
                "precondition: mean-only must false-negative this case",
            )
            # Multi-signal path must keep the change.
            self.assertFalse(
                frames_are_duplicates(before, after),
                f"small button enablement incorrectly treated as duplicate: {stats}",
            )
            self.assertGreater(stats.max_block_abs_diff, 12.0)
            self.assertGreater(stats.changed_pixel_fraction, 0.004)

    def test_small_toast_is_not_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = _ui_base(root / "before.png")
            after = _ui_with_toast(root / "after.png")
            self.assertFalse(frames_are_duplicates(before, after))

    def test_dedupe_frames_keeps_small_ui_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f1 = _ui_base(root / "1.png")
            f2 = _ui_with_button_enabled(root / "2.png")
            f3 = _ui_with_button_enabled(root / "3.png")  # true dup of f2
            kept = dedupe_frames([f1, f2, f3])
            self.assertEqual(len(kept), 2)
            self.assertEqual(Path(kept[0]).name, "1.png")
            self.assertEqual(Path(kept[1]).name, "2.png")


class FrameDedupPipelineTests(unittest.TestCase):
    def test_dedupe_drops_only_true_consecutive_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = _solid(root / "a.png", (0, 0, 0))
            b = _solid(root / "b.png", (0, 0, 0))
            c = _solid(root / "c.png", (255, 0, 0))
            d = _solid(root / "d.png", (255, 0, 0))
            kept = dedupe_frames([a, b, c, d])
            self.assertEqual([Path(p).name for p in kept], ["a.png", "c.png"])

    def test_dedupe_skips_missing_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = _solid(root / "a.png", (1, 2, 3))
            kept = dedupe_frames([a, root / "missing.png", a])
            self.assertEqual(len(kept), 1)


if __name__ == "__main__":
    unittest.main()
