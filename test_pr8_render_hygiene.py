"""PR8: per-run render output + locked webhook routes."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.render import capture_dir_for_run, render_video, video_output_path_for_run
from app.steps.metrics import new_run_metrics


_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


class TestWebhookSummaryPath(unittest.TestCase):
    def test_summary_path_is_defined_under_data(self):
        import app.webhook as webhook

        path = webhook.run_summary_path()
        self.assertEqual(path.name, "pipeline_run_summary.json")
        self.assertEqual(path.parent.name, "data")
        src = Path("app/webhook.py").read_text(encoding="utf-8")
        self.assertNotIn("BASE_DIR", src)


class TestVideoOutputPathForRun(unittest.TestCase):
    def test_uses_run_id_not_shared_out_mp4(self):
        metrics = new_run_metrics(42)
        with tempfile.TemporaryDirectory() as td:
            dest = video_output_path_for_run(metrics.run_id, screenshot_dir=Path(td))
        self.assertTrue(dest.name.startswith("pr42_"))
        self.assertTrue(dest.name.endswith(".mp4"))
        self.assertNotEqual(dest.name, "out.mp4")
        self.assertIn(metrics.run_id, dest.name)

    def test_strips_path_separators(self):
        with tempfile.TemporaryDirectory() as td:
            dest = video_output_path_for_run("../evil/pr1", screenshot_dir=Path(td))
        self.assertEqual(dest.name, "evilpr1.mp4")
        self.assertEqual(dest.parent, Path(td))

    def test_empty_run_id_fails_closed(self):
        with self.assertRaises(ValueError):
            video_output_path_for_run("   ")

    def test_capture_dir_is_per_run(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            a = capture_dir_for_run("pr1_aaa", screenshot_dir=root)
            b = capture_dir_for_run("pr2_bbb", screenshot_dir=root)
            self.assertTrue(a.is_dir())
            self.assertTrue(b.is_dir())
            self.assertNotEqual(a, b)
            (a / "shot1.png").write_bytes(b"a")
            (b / "shot1.png").write_bytes(b"b")
            self.assertEqual((a / "shot1.png").read_bytes(), b"a")
            self.assertEqual((b / "shot1.png").read_bytes(), b"b")

    def test_capture_dir_strips_path_separators(self):
        with tempfile.TemporaryDirectory() as td:
            dest = capture_dir_for_run("../evil/pr1", screenshot_dir=Path(td))
        self.assertEqual(dest.name, "evilpr1")
        self.assertEqual(dest.parent, Path(td))


class TestRenderVideoOutputPath(unittest.TestCase):
    def test_two_runs_write_distinct_files(self):
        written: list[str] = []

        def fake_run(cmd, **kwargs):
            dest = Path(cmd[-1])
            dest.write_bytes(b"\x00" * 2048)
            written.append(str(dest))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            frame = root / "frame.png"
            frame.write_bytes(_TINY_PNG)
            a = root / "pr1_a.mp4"
            b = root / "pr2_b.mp4"
            with patch("app.render.subprocess.run", side_effect=fake_run):
                out_a = render_video(
                    [frame],
                    render_approval={"is_sendable": True, "reasons": []},
                    output_path=a,
                )
                out_b = render_video(
                    [frame],
                    render_approval={"is_sendable": True, "reasons": []},
                    output_path=b,
                )
            self.assertEqual(out_a, a)
            self.assertEqual(out_b, b)
            self.assertTrue(a.exists())
            self.assertTrue(b.exists())
            self.assertNotEqual(a.read_bytes(), b"")
            self.assertEqual(written, [str(a), str(b)])

    def test_default_path_is_shared_out_mp4_for_cli(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "out.mp4"
            frame = Path(td) / "frame.png"
            frame.write_bytes(_TINY_PNG)

            def fake_run(cmd, **kwargs):
                Path(cmd[-1]).write_bytes(b"\x00" * 64)
                return subprocess.CompletedProcess(cmd, 0, "", "")

            with patch("app.render.SCREENSHOT_DIR", Path(td)):
                with patch("app.render.subprocess.run", side_effect=fake_run):
                    out = render_video(
                        [frame],
                        render_approval={"is_sendable": True, "reasons": []},
                    )
            self.assertEqual(out, dest)


class TestPipelineWiresRunId(unittest.TestCase):
    def test_pipeline_does_not_hardcode_out_mp4(self):
        src = Path("app/steps/pipeline.py").read_text(encoding="utf-8")
        self.assertIn("video_output_path_for_run", src)
        self.assertIn("capture_dir_for_run", src)
        self.assertIn("output_path=video_path", src)
        self.assertNotIn('SCREENSHOT_DIR / "out.mp4"', src)
        self.assertNotIn("screenshot_dir=SCREENSHOT_DIR", src)


class TestWebhookRoutesLocked(unittest.TestCase):
    def test_source_drops_public_routes(self):
        src = Path("app/webhook.py").read_text(encoding="utf-8")
        self.assertNotIn('@app.get("/out.mp4")', src)
        self.assertNotIn('@app.get("/budget-status")', src)
        self.assertNotIn("VIDEO_PATH", src)

    def test_get_out_mp4_and_budget_are_404(self):
        import os

        from test_pr1_hygiene import _webhook
        from fastapi.testclient import TestClient

        with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": "test-secret"}, clear=False):
            client = TestClient(_webhook().app)
            video = client.get("/out.mp4")
            budget = client.get("/budget-status")
        self.assertEqual(video.status_code, 404)
        self.assertEqual(budget.status_code, 404)


if __name__ == "__main__":
    unittest.main()
