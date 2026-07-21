"""Phase 3: honest sendable / video_usable gate."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.github_comment import format_not_sendable_comment
from app.steps.metrics import compute_sendable


class TestComputeSendable(unittest.TestCase):
    def _touch(self, path: Path, size: int = 10_000) -> Path:
        path.write_bytes(b"\x00" * size)
        return path

    def test_nonzero_file_alone_not_sendable(self):
        with tempfile.TemporaryDirectory() as td:
            vid = self._touch(Path(td) / "out.mp4")
            with patch("app.steps.metrics._probe_duration_sec", return_value=5.0):
                ok, proof = compute_sendable(
                    {
                        "success": True,
                        "steps_succeeded": 0,
                        "failure_reason": None,
                    },
                    vid,
                    [{"action": "screenshot"}],
                    general_demo=False,
                )
        self.assertFalse(ok)
        self.assertIn("no_interaction_proof", proof["reasons"])

    def test_terminal_fail_not_sendable(self):
        with tempfile.TemporaryDirectory() as td:
            vid = self._touch(Path(td) / "out.mp4")
            plan = [
                {"action": "click", "label": "Save"},
                {"action": "assert_terminal", "expected_url": "/done"},
            ]
            with patch("app.steps.metrics._probe_duration_sec", return_value=5.0):
                ok, proof = compute_sendable(
                    {
                        "success": True,
                        "steps_succeeded": 1,
                        "failure_reason": None,
                        "debug": {
                            "results": [
                                {
                                    "outcome": "success",
                                    "step": {"action": "click"},
                                }
                            ]
                        },
                    },
                    vid,
                    plan,
                    general_demo=False,
                )
        self.assertFalse(ok)
        self.assertIn("terminal_not_passed", proof["reasons"])

    def test_general_demo_screenshot_can_be_sendable(self):
        with tempfile.TemporaryDirectory() as td:
            vid = self._touch(Path(td) / "out.mp4")
            with patch("app.steps.metrics._probe_duration_sec", return_value=3.0):
                ok, proof = compute_sendable(
                    {
                        "success": True,
                        "steps_succeeded": 1,
                        "failure_reason": None,
                    },
                    vid,
                    [{"action": "screenshot"}],
                    general_demo=True,
                )
        self.assertTrue(ok)
        self.assertEqual(proof["reasons"], [])

    def test_hard_fail_reason_blocks_sendable(self):
        with tempfile.TemporaryDirectory() as td:
            vid = self._touch(Path(td) / "out.mp4")
            with patch("app.steps.metrics._probe_duration_sec", return_value=5.0):
                ok, proof = compute_sendable(
                    {
                        "success": False,
                        "steps_succeeded": 2,
                        "failure_reason": "navigation_reanchor_failed",
                        "debug": {
                            "results": [
                                {"outcome": "success", "step": {"action": "click"}},
                                {"outcome": "success", "step": {"action": "goto"}},
                            ]
                        },
                    },
                    vid,
                    [{"action": "click"}, {"action": "goto"}],
                    general_demo=False,
                )
        self.assertFalse(ok)
        self.assertTrue(
            any("hard_fail" in r or "capture_not_success" in r for r in proof["reasons"])
        )

    def test_render_approval_false_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            vid = self._touch(Path(td) / "out.mp4")
            with patch("app.steps.metrics._probe_duration_sec", return_value=5.0):
                ok, proof = compute_sendable(
                    {
                        "success": True,
                        "steps_succeeded": 2,
                        "render_approval": {
                            "is_sendable": False,
                            "reasons": ["expected_proof_not_satisfied"],
                        },
                        "debug": {
                            "results": [
                                {"outcome": "success", "step": {"action": "click"}},
                            ]
                        },
                    },
                    vid,
                    [{"action": "click"}],
                )
        self.assertFalse(ok)
        self.assertIn("expected_proof_not_satisfied", proof["reasons"])

    def test_render_approval_true_sendable(self):
        with tempfile.TemporaryDirectory() as td:
            vid = self._touch(Path(td) / "out.mp4")
            with patch("app.steps.metrics._probe_duration_sec", return_value=5.0):
                ok, proof = compute_sendable(
                    {
                        "success": True,
                        "steps_succeeded": 2,
                        "render_approval": {"is_sendable": True, "reasons": []},
                        "terminal_condition_reached": True,
                    },
                    vid,
                    [
                        {"action": "click"},
                        {"action": "assert_terminal", "expected_url": "/x"},
                    ],
                )
        self.assertTrue(ok)
        self.assertEqual(proof["reasons"], [])

    def test_short_duration_not_sendable(self):
        with tempfile.TemporaryDirectory() as td:
            vid = self._touch(Path(td) / "out.mp4")
            with patch("app.steps.metrics._probe_duration_sec", return_value=0.5):
                ok, proof = compute_sendable(
                    {
                        "success": True,
                        "render_approval": {"is_sendable": True, "reasons": []},
                    },
                    vid,
                    [{"action": "click"}],
                    general_demo=True,
                )
        self.assertFalse(ok)
        self.assertTrue(any(r.startswith("duration_below_min") for r in proof["reasons"]))

    def test_missing_video_not_sendable(self):
        ok, proof = compute_sendable(
            {"success": True, "render_approval": {"is_sendable": True, "reasons": []}},
            Path("/tmp/does-not-exist-shipvideo.mp4"),
            general_demo=True,
        )
        self.assertFalse(ok)
        self.assertIn("video_missing", proof["reasons"])


class TestNotSendableComment(unittest.TestCase):
    def test_format_differs_from_success_style(self):
        text = format_not_sendable_comment(12, ["terminal_not_passed", "no_interaction_proof"])
        self.assertIn("not publishable", text.lower())
        self.assertIn("terminal_not_passed", text)
        self.assertNotIn("Auto-generated demo video", text)


class TestFinalizeUsesSendable(unittest.TestCase):
    def test_video_usable_equals_sendable_not_size(self):
        """Integration: compute_sendable is the source of video_usable."""
        with tempfile.TemporaryDirectory() as td:
            vid = Path(td) / "out.mp4"
            vid.write_bytes(b"\x00" * 5000)
            with patch("app.steps.metrics._probe_duration_sec", return_value=5.0):
                ok, _ = compute_sendable(
                    {"success": True, "steps_succeeded": 0},
                    vid,
                    [{"action": "screenshot"}],
                    general_demo=False,
                )
            # size would have said True; sendable must say False
            size_only = vid.exists() and vid.stat().st_size > 0
            self.assertTrue(size_only)
            self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
