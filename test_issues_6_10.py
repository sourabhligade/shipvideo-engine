from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.steps.step_normalizer import _extract_routes_from_diff
from app.steps.pr_extraction import MAX_PATCH_CHARS
from app.steps.diff_budget import _TIER_BUDGET
from app.render import run_ffmpeg_with_retry
from app.recorder.video_processor import convert_webm_to_mp4, FFMPEG_TIMEOUT_SECONDS


class Issues610Tests(unittest.TestCase):
    def test_deleted_not_in_routes(self):
        routes = _extract_routes_from_diff([
            {"path": "src/app/pricing/page.tsx", "status": "removed", "patch": "-x"},
            {"path": "src/app/demo/page.tsx", "status": "modified", "patch": "+y"},
            {"path": "src/app/old/page.tsx", "status": "deleted", "patch": "-z"},
        ])
        self.assertNotIn("/pricing", routes)
        self.assertNotIn("/old", routes)
        self.assertIn("/demo", routes)

    def test_ffmpeg_retries(self):
        fail = subprocess.CompletedProcess(args=["ffmpeg"], returncode=1, stdout="", stderr="e")
        ok = subprocess.CompletedProcess(args=["ffmpeg"], returncode=0, stdout="", stderr="")
        with patch("app.render.subprocess.run", side_effect=[fail, ok]):
            r = run_ffmpeg_with_retry(["ffmpeg", "-y"], timeout=5, max_attempts=3)
        self.assertEqual(r.returncode, 0)

    def test_ffmpeg_timeout_retries_then_raises(self):
        with patch(
            "app.render.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=1),
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                run_ffmpeg_with_retry(["ffmpeg", "-y"], timeout=1, max_attempts=2)

    def test_webm_mp4_uses_timeout(self):
        webm = Path("/tmp/fake_in.webm")
        out_dir = Path("/tmp/fake_out_dir")
        ok = subprocess.CompletedProcess(args=["ffmpeg"], returncode=0, stdout="", stderr="")
        with patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "mkdir"), \
             patch("app.recorder.video_processor.subprocess.run", return_value=ok) as run:
            convert_webm_to_mp4(webm, out_dir)
        self.assertEqual(run.call_args.kwargs.get("timeout"), FFMPEG_TIMEOUT_SECONDS)

    def test_webm_mp4_timeout_raises(self):
        webm = Path("/tmp/fake_in.webm")
        out_dir = Path("/tmp/fake_out_dir")
        with patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "mkdir"), \
             patch(
                 "app.recorder.video_processor.subprocess.run",
                 side_effect=subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=1),
             ):
            with self.assertRaises(RuntimeError):
                convert_webm_to_mp4(webm, out_dir)

    def test_max_patch_meets_primary_budget_tier(self):
        self.assertGreaterEqual(MAX_PATCH_CHARS, _TIER_BUDGET[2])

    def test_button_title_mapped_from_meta(self):
        import asyncio
        from app.steps import dom_crawler

        async def fake_eval(selector, script):
            if "button" in selector:
                return [{
                    "text": "Save",
                    "testid": "",
                    "aria": "",
                    "title": "Save changes",
                    "id": "",
                    "classes": "",
                }]
            return []

        page = MagicMock()
        page.eval_on_selector_all = fake_eval
        out = asyncio.run(dom_crawler._extract_ui_from_current_page(page))
        self.assertEqual(out["buttons"][0]["title"], "Save changes")


if __name__ == "__main__":
    unittest.main()
