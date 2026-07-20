"""Tests for remaining audit priorities #5–#12 and related reliability fixes."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from app.policy.selector_validator import validate_step_against_dom
from app.steps.step_normalizer import _extract_routes_from_diff
from app.steps.pr_extraction import MAX_PATCH_CHARS
from app.steps.dom_crawler import _merge_snapshots
from app.product.audio_timing import align_texts_to_speech_segments


class MultiMatchTests(unittest.TestCase):
    def test_selector_count_gt_1_rejected(self):
        page = MagicMock()
        loc = MagicMock()
        loc.count.return_value = 3
        page.locator.return_value = loc
        ok, reason = validate_step_against_dom(
            {"action": "click", "selector": "[data-testid='x']"},
            {"routes": ["/"]},
            page=page,
        )
        self.assertFalse(ok)
        self.assertIn("selector_not_unique", reason)

    def test_label_count_gt_1_rejected(self):
        page = MagicMock()
        loc = MagicMock()
        loc.count.return_value = 2
        page.get_by_text.return_value = loc
        ok, reason = validate_step_against_dom(
            {"action": "click", "label": "Save"},
            {"routes": ["/"], "buttons": []},
            page=page,
        )
        self.assertFalse(ok)
        self.assertIn("label_not_unique", reason)

    def test_unique_selector_ok(self):
        page = MagicMock()
        loc = MagicMock()
        loc.count.return_value = 1
        page.locator.return_value = loc
        ok, reason = validate_step_against_dom(
            {"action": "click", "selector": "[data-testid='x']"},
            {"routes": ["/"]},
            page=page,
        )
        self.assertTrue(ok)


class CueAlignTests(unittest.TestCase):
    def test_equal_speech_segments_map_one_to_one(self):
        texts = ["a", "b", "c"]
        segs = [
            {"start": 0.0, "end": 6.5, "duration": 6.5},
            {"start": 7.0, "end": 9.9, "duration": 2.9},
            {"start": 10.3, "end": 14.8, "duration": 4.5},
        ]
        cues = align_texts_to_speech_segments(texts, segs, total_duration=14.8)
        self.assertEqual(len(cues), 3)
        self.assertAlmostEqual(cues[0]["start"], 0.0)
        self.assertAlmostEqual(cues[1]["start"], 7.0)
        self.assertAlmostEqual(cues[2]["start"], 10.3)
        # last cue must not restart at 0
        self.assertGreater(cues[2]["start"], cues[1]["start"])

    def test_more_segments_than_lines_reserves_last(self):
        texts = ["a", "b"]
        segs = [
            {"start": 0.0, "end": 1.0, "duration": 1.0},
            {"start": 1.0, "end": 2.0, "duration": 1.0},
            {"start": 2.0, "end": 3.0, "duration": 1.0},
            {"start": 3.0, "end": 4.0, "duration": 1.0},
        ]
        cues = align_texts_to_speech_segments(texts, segs, total_duration=4.0)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["start"], 0.0)
        self.assertGreaterEqual(cues[1]["start"], cues[0]["end"] - 0.01)
        self.assertAlmostEqual(cues[1]["end"], 4.0)


class PatchAndRoutesTests(unittest.TestCase):
    def test_max_patch_at_least_4000(self):
        self.assertGreaterEqual(MAX_PATCH_CHARS, 4000)

    def test_deleted_routes_skipped(self):
        routes = _extract_routes_from_diff(
            [
                {"path": "app/old/page.tsx", "status": "removed"},
                {"path": "app/new/page.tsx", "status": "added"},
            ]
        )
        self.assertEqual(routes, {"/new"})


class InputMergeTests(unittest.TestCase):
    def test_testid_only_input_kept(self):
        merged = _merge_snapshots(
            {
                "/": {
                    "buttons": [],
                    "links": [],
                    "inputs": [
                        {
                            "name": "",
                            "placeholder": "",
                            "testid": "email",
                            "aria": "Email",
                            "id": "e1",
                            "input_type": "email",
                        },
                        {
                            "name": "q",
                            "placeholder": "Search",
                            "testid": "",
                            "aria": "",
                            "id": "",
                            "input_type": "text",
                        },
                    ],
                    "data_testids": [],
                }
            }
        )
        self.assertEqual(len(merged["inputs"]), 2)
        testids = {i.get("testid") for i in merged["inputs"]}
        self.assertIn("email", testids)


class PreviewAndWebhookSmoke(unittest.TestCase):
    def test_preview_uses_get(self):
        src = Path("app/preview_url_resolver.py").read_text()
        self.assertIn('"GET"', src)
        self.assertIn("Range", src)

    def test_record_run_after_success_not_before_preview(self):
        src = Path("app/webhook.py").read_text()
        # early record_run removed from start path
        start = src.find("check_already_ran")
        window = src[start : start + 250]
        self.assertNotIn("record_run(", window)
        self.assertIn("record_run(repo_full_name, pr_number, commit_sha)", src)
        self.assertNotIn('trigger_mode == "smart" and not force', src)

    def test_ffmpeg_timeouts_present(self):
        self.assertIn("timeout=120", Path("app/render.py").read_text())
        self.assertIn("timeout=120", Path("app/recorder/video_processor.py").read_text())
        self.assertIn("timeout=120", Path("app/product/video.py").read_text())

    def test_retry_engine_broad(self):
        src = Path("app/llm/retry_engine.py").read_text()
        self.assertIn("JSONDecodeError", src)

    def test_script_timeout_enforced(self):
        src = Path("app/recorder/playwright_runner.py").read_text()
        self.assertIn("ThreadPoolExecutor", src)
        self.assertIn("timeout_seconds", src)
        self.assertIn("fut.result", src)


if __name__ == "__main__":
    unittest.main()
