from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from app.steps.dom_crawler import _merge_snapshots
from app.llm.retry_engine import regenerate_with_feedback, regenerate_single_step_toward_testid
from app.product.video import FFMPEG_TIMEOUT_SECONDS as VIDEO_FFMPEG_TIMEOUT
from app.product.audio_timing import FFMPEG_TIMEOUT_SECONDS as AUDIO_FFMPEG_TIMEOUT
from app.execution.step_runner import _classify_final_outcome


class Issues1216Tests(unittest.TestCase):
    def test_merge_keeps_testid_only_inputs(self):
        merged = _merge_snapshots({
            "/": {
                "buttons": [],
                "links": [],
                "inputs": [
                    {
                        "name": "",
                        "placeholder": "",
                        "testid": "email-input",
                        "aria": "Email",
                        "id": "email",
                        "input_type": "email",
                    },
                    {
                        "name": "password",
                        "placeholder": "Password",
                        "testid": "",
                        "aria": "",
                        "id": "",
                        "input_type": "password",
                    },
                ],
                "data_testids": [],
            }
        })
        testids = {i.get("testid") for i in merged["inputs"]}
        names = {i.get("name") for i in merged["inputs"]}
        self.assertIn("email-input", testids)
        self.assertIn("password", names)
        self.assertEqual(len(merged["inputs"]), 2)

    def test_merge_dedupes_by_testid(self):
        merged = _merge_snapshots({
            "/a": {
                "buttons": [],
                "links": [],
                "inputs": [{"name": "", "placeholder": "", "testid": "x", "aria": "", "id": "", "input_type": "text"}],
                "data_testids": [],
            },
            "/b": {
                "buttons": [],
                "links": [],
                "inputs": [{"name": "", "placeholder": "", "testid": "x", "aria": "Alt", "id": "", "input_type": "text"}],
                "data_testids": [],
            },
        })
        self.assertEqual(len(merged["inputs"]), 1)

    def test_retry_engine_retries_json_decode_error(self):
        calls = {"n": 0}

        def boom(**kwargs):
            calls["n"] += 1
            if calls["n"] < 2:
                raise json.JSONDecodeError("Expecting value", "", 0)
            return [{"action": "screenshot", "selector": "", "text": "", "url": "", "label": ""}]

        with patch("app.llm.retry_engine.generate_next_steps", side_effect=boom), \
             patch("app.llm.retry_engine.validate_step_against_dom", return_value=(True, "")):
            steps, attempts = regenerate_with_feedback(
                objective={},
                dom_context={"routes": ["/"], "buttons": [], "links": []},
                error_context={"error": "seed"},
                max_attempts=3,
            )
        self.assertEqual(len(steps), 1)
        self.assertEqual(attempts[0]["status"], "generation_error")
        self.assertEqual(attempts[0]["error_type"], "JSONDecodeError")
        self.assertEqual(attempts[1]["status"], "ok")

    def test_retry_engine_single_step_retries_value_error(self):
        calls = {"n": 0}

        def boom(**kwargs):
            calls["n"] += 1
            if calls["n"] < 2:
                raise ValueError("bad payload")
            return {"action": "click", "selector": "[data-testid='x']", "text": "", "url": "", "label": ""}

        with patch("app.llm.retry_engine.generate_single_step_toward_testid", side_effect=boom), \
             patch("app.llm.retry_engine.validate_step_against_dom", return_value=(True, "")):
            step, attempts = regenerate_single_step_toward_testid(
                objective={},
                target_testid="x",
                snapshot={},
                dom_context={},
                max_attempts=2,
            )
        self.assertIsNotNone(step)
        self.assertEqual(attempts[0]["error_type"], "ValueError")

    def test_video_ffmpeg_timeout_constant(self):
        self.assertGreaterEqual(VIDEO_FFMPEG_TIMEOUT, 60)

    def test_audio_ffmpeg_timeout_constant(self):
        self.assertGreaterEqual(AUDIO_FFMPEG_TIMEOUT, 60)

    def test_audio_silence_uses_timeout(self):
        from app.product import audio_timing as at

        src = Path(at.__file__).read_text(encoding="utf-8")
        # Every ffmpeg subprocess.run in the module should mention timeout=
        # Count ffmpeg invocations vs timeouts loosely by requiring the constant is used.
        self.assertIn("timeout=FFMPEG_TIMEOUT_SECONDS", src)
        self.assertGreaterEqual(src.count("timeout=FFMPEG_TIMEOUT_SECONDS"), 5)

    def test_video_slideshow_uses_timeout(self):
        from app.product import video as v
        src = Path(v.__file__).read_text(encoding="utf-8")
        self.assertIn("timeout=FFMPEG_TIMEOUT_SECONDS", src)
        self.assertGreaterEqual(src.count("timeout=FFMPEG_TIMEOUT_SECONDS"), 2)

    def test_reanchor_failure_is_regression_outcome(self):
        self.assertEqual(
            _classify_final_outcome(success=False, failure_reason="navigation_reanchor_failed"),
            "regressed",
        )

    def test_reanchor_abort_branch_present(self):
        src = Path("app/execution/step_runner.py").read_text(encoding="utf-8")
        self.assertIn("navigation_reanchor_failed", src)
        self.assertIn("if regenerated:", src)


if __name__ == "__main__":
    unittest.main()
