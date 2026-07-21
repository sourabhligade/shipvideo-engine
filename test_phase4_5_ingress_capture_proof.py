"""Phase 4 (ingress/job deadlines) + Phase 5 (CaptureProof parity)."""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.llm_guards import check_already_ran, clear_run, record_run
from app.product.jobs import (
    DEFAULT_JOB_MAX_SECONDS,
    _mark_stale_if_needed,
    job_max_seconds,
)
from app.steps.capture_proof import (
    apply_proof_to_summary,
    build_capture_proof,
    normalize_runner_name,
)


class TestPhase4Dedupe(unittest.TestCase):
    def test_failed_run_does_not_block_retry(self):
        """Only record_run (success) blocks; without it, same SHA is free."""
        repo, pr, sha = "o/r", 99, "deadbeefcafebabe0123456789abcdef01234567"
        clear_run(repo, pr, sha)
        self.assertFalse(check_already_ran(repo, pr, sha))
        # Simulate failure path: never call record_run
        self.assertFalse(check_already_ran(repo, pr, sha))
        # Success path records
        record_run(repo, pr, sha)
        self.assertTrue(check_already_ran(repo, pr, sha))
        clear_run(repo, pr, sha)
        self.assertFalse(check_already_ran(repo, pr, sha))

    def test_webhook_record_run_only_on_sendable_success(self):
        src = Path("app/webhook.py").read_text()
        # failure / hard-fail branches return before record_run without recording
        self.assertIn("generation_hard_fail", src)
        self.assertIn("sendable=True", src)
        # record_run only in sendable success branch
        true_i = src.find("sendable=True")
        self.assertGreater(true_i, 0)
        self.assertIn(
            "record_run(repo_full_name, pr_number, commit_sha)",
            src[true_i : true_i + 300],
        )


class TestPhase4JobDeadline(unittest.TestCase):
    def test_default_max_seconds(self):
        with patch.dict("os.environ", {}, clear=False):
            # may inherit env; at least function returns int >= 30
            self.assertGreaterEqual(job_max_seconds(), 30)
        self.assertEqual(DEFAULT_JOB_MAX_SECONDS, 900)

    def test_stale_running_marked_failed(self):
        job = {
            "id": "abc123",
            "status": "running",
            "started_at": time.time() - 10_000,
            "deadline_at": time.time() - 100,
            "max_seconds": 60,
            "created_at": time.time() - 10_000,
            "updated_at": time.time() - 10_000,
        }
        with patch("app.product.jobs._persist"), patch(
            "app.product.jobs._jobs", {}
        ):
            out = _mark_stale_if_needed(job)
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["error"], "job_deadline_exceeded")

    def test_running_within_deadline_unchanged(self):
        job = {
            "id": "abc124",
            "status": "running",
            "started_at": time.time(),
            "deadline_at": time.time() + 500,
            "max_seconds": 900,
        }
        out = _mark_stale_if_needed(job)
        self.assertEqual(out["status"], "running")


class TestPhase4PreviewTimeoutMessage(unittest.TestCase):
    def test_timeout_message_includes_url_and_seconds(self):
        src = Path("app/preview_url_resolver.py").read_text()
        self.assertIn("not ready after timeout=", src)
        self.assertIn("url=", src)
        self.assertIn("polled every", src)


class TestPhase5CaptureProof(unittest.TestCase):
    def test_normalize_runner_names(self):
        self.assertEqual(normalize_runner_name("agent_browser_cli"), "agent_browser")
        self.assertEqual(normalize_runner_name("playwright"), "playwright")
        self.assertEqual(normalize_runner_name("stepwise"), "playwright")

    def test_ab_and_pw_shaped_results_same_keys(self):
        plan = [
            {"action": "goto", "url": "/"},
            {"action": "click", "label": "Save"},
            {"action": "assert_terminal", "expected_url": "/done"},
        ]
        ab_results = [
            {"outcome": "success", "step": {"action": "goto", "url": "/"}},
            {"outcome": "success", "step": {"action": "click", "label": "Save"}},
            {
                "outcome": "success",
                "step": {"action": "assert_terminal"},
                "terminal_condition_reached": True,
            },
        ]
        pw_results = [
            {"outcome": "success", "step": {"action": "goto", "url": "/"}},
            {
                "outcome": "success",
                "step": {"action": "click", "label": "Save"},
                "validation_passed": True,
            },
            {
                "outcome": "success",
                "step": {"action": "assert_terminal"},
                "terminal_condition_reached": True,
            },
        ]
        ab = build_capture_proof(
            plan=plan,
            runner_result={
                "success": True,
                "steps_succeeded": 3,
                "steps_failed": 0,
                "results": ab_results,
                "final_outcome": "success",
                "approved_frames": ["a.png", "b.png"],
            },
            engine="agent_browser_cli:deterministic",
            backend="agent_browser_cli",
        )
        pw = build_capture_proof(
            plan=plan,
            runner_result={
                "success": True,
                "steps_succeeded": 3,
                "steps_failed": 0,
                "results": pw_results,
                "final_outcome": "success",
                "approved_frames": ["a.png", "b.png"],
            },
            engine="stepwise",
            backend="playwright",
        )
        ab_d, pw_d = ab.to_dict(), pw.to_dict()
        for key in (
            "steps_planned",
            "steps_succeeded",
            "steps_failed",
            "clicks_succeeded",
            "gotos_succeeded",
            "terminal_passed",
            "validation_passed",
            "failure_reason",
            "runner",
            "success",
            "approved_frame_count",
        ):
            self.assertIn(key, ab_d)
            self.assertIn(key, pw_d)
        self.assertEqual(ab.steps_planned, pw.steps_planned)
        self.assertEqual(ab.clicks_succeeded, 1)
        self.assertEqual(pw.clicks_succeeded, 1)
        self.assertEqual(ab.gotos_succeeded, 1)
        self.assertEqual(pw.gotos_succeeded, 1)
        self.assertTrue(ab.terminal_passed)
        self.assertTrue(pw.terminal_passed)
        self.assertEqual(ab.runner, "agent_browser")
        self.assertEqual(pw.runner, "playwright")

    def test_apply_proof_sets_canonical_summary_fields(self):
        proof = build_capture_proof(
            plan=[{"action": "click"}],
            runner_result={
                "success": False,
                "steps_succeeded": 0,
                "steps_failed": 1,
                "failure_reason": "click_failed",
                "results": [],
            },
            engine="stepwise",
            backend="playwright",
        )
        summary: dict = {}
        apply_proof_to_summary(summary, proof)
        self.assertIn("capture_proof", summary)
        self.assertEqual(summary["runner"], "playwright")
        self.assertEqual(summary["steps_failed"], 1)
        self.assertEqual(summary["failure_reason"], "click_failed")
        self.assertFalse(summary["success"])

    def test_engine_switch_does_not_drop_keys(self):
        """Sparse PW result still exposes all CaptureProof keys."""
        proof = build_capture_proof(
            plan=[{"action": "screenshot"}],
            runner_result={"success": True, "steps_succeeded": 1, "results": []},
            engine="stepwise",
        )
        keys = set(proof.to_dict())
        required = {
            "steps_planned",
            "steps_succeeded",
            "steps_failed",
            "clicks_succeeded",
            "gotos_succeeded",
            "terminal_passed",
            "validation_passed",
            "failure_reason",
            "runner",
            "success",
        }
        self.assertTrue(required.issubset(keys))


if __name__ == "__main__":
    unittest.main()
