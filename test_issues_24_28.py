"""Tests for issues #24–#28."""
from __future__ import annotations

import ast
import inspect
import unittest
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

from app.policy.selector_validator import validate_step_against_dom
from app.trigger import evaluate_trigger
from app.execution.step_runner import _assert_playwright_terminal_condition, _execute_one
from app.generator import script_generator as sg
from app.steps.dom_crawler import _merge_snapshots


class TestIssue25AssertTerminal(unittest.TestCase):
    def test_validator_accepts_assert_terminal(self):
        ok, reason = validate_step_against_dom(
            {"action": "assert_terminal", "expected_text": "Done"},
            {"routes": ["/"]},
        )
        self.assertTrue(ok)
        self.assertIn("assert_terminal", reason)

    def test_validator_rejects_empty_assert_terminal(self):
        ok, reason = validate_step_against_dom(
            {"action": "assert_terminal"},
            {"routes": ["/"]},
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "missing_terminal_condition")

    def test_execute_one_assert_terminal_success(self):
        page = MagicMock()
        locator = MagicMock()
        page.get_by_text.return_value = locator
        locator.first = locator
        locator.wait_for.return_value = None
        ok, shot, err = _execute_one(
            page,
            "https://example.com",
            {"action": "assert_terminal", "expected_text": "Welcome"},
            MagicMock(),
            1,
        )
        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_execute_one_assert_terminal_failure(self):
        page = MagicMock()
        locator = MagicMock()
        page.get_by_text.return_value = locator
        locator.first = locator
        locator.wait_for.side_effect = Exception("timeout")
        page.locator.return_value = locator
        ok, shot, err = _execute_one(
            page,
            "https://example.com",
            {"action": "assert_terminal", "expected_text": "Missing"},
            MagicMock(),
            1,
        )
        self.assertFalse(ok)
        self.assertTrue(err and "terminal" in err)


class TestIssue27OnDemandComment(unittest.TestCase):
    def _cfg(self, mode: str = "on-demand") -> Dict[str, Any]:
        return {"trigger": {"mode": mode, "threshold": 5, "commentCommand": "/demo"}}

    def test_on_demand_without_comment_skips(self):
        files = [{"path": "app/page.tsx", "patch": "+x"}]
        d = evaluate_trigger(files, self._cfg(), force=False, comment_triggered=False)
        self.assertFalse(d.should_run)

    def test_on_demand_with_comment_runs_ui(self):
        files = [{"path": "app/page.tsx", "patch": "+x"}]
        d = evaluate_trigger(files, self._cfg(), force=False, comment_triggered=True)
        self.assertTrue(d.should_run)
        self.assertIn("on-demand comment", d.reason)

    def test_on_demand_comment_no_ui_still_skips(self):
        files = [{"path": "README.md", "patch": "+x"}]
        d = evaluate_trigger(files, self._cfg(), force=False, comment_triggered=True)
        self.assertFalse(d.should_run)

    def test_force_sets_general_demo_without_ui(self):
        files = [{"path": "README.md", "patch": "+x"}]
        d = evaluate_trigger(files, self._cfg("auto"), force=True, comment_triggered=False)
        self.assertTrue(d.should_run)
        self.assertTrue(d.general_demo)

    def test_force_with_ui_not_general_demo(self):
        files = [{"path": "app/page.tsx", "patch": "+x"}]
        d = evaluate_trigger(files, self._cfg("auto"), force=True)
        self.assertTrue(d.should_run)
        self.assertFalse(d.general_demo)

    def test_smart_comment_bypasses_threshold(self):
        files = [{"path": "app/page.tsx", "patch": "+x\n"}]  # tiny magnitude
        d = evaluate_trigger(
            files,
            self._cfg("smart"),
            force=False,
            comment_triggered=True,
        )
        self.assertTrue(d.should_run)


class TestIssue26WebhookSkipped(unittest.TestCase):
    def test_webhook_handles_skipped_flow(self):
        import app.webhook as wh

        src = inspect.getsource(wh)
        self.assertIn('flow.get("skipped")', src)
        self.assertIn("comment_triggered", src)
        self.assertIn("force=force", src)
        self.assertIn("comment_triggered=comment_triggered", src)


class TestIssue28ScriptSpend(unittest.TestCase):
    def test_parse_records_spend_and_rejects_empty(self):
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"script":""}'))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )
        with patch.object(sg, "record_spend") as rec:
            with self.assertRaises(RuntimeError):
                sg._parse_script_payload('{"script":""}', completion)
            rec.assert_called_once_with(10, 5)

    def test_parse_returns_script_and_records(self):
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"script":"def run_demo(page, context):\\n  pass"}'))],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=7),
        )
        with patch.object(sg, "record_spend") as rec:
            script = sg._parse_script_payload(
                '{"script":"def run_demo(page, context):\\n  pass"}',
                completion,
            )
            self.assertIn("run_demo", script)
            rec.assert_called_once_with(3, 7)


class TestIssue24CrawledRoutesOnly(unittest.TestCase):
    def test_crawl_return_uses_snapshot_keys(self):
        import app.steps.dom_crawler as dc

        src = inspect.getsource(dc.crawl_dom_data)
        self.assertIn("route_snapshots.keys()", src)
        self.assertIn("crawled_routes", src)
        self.assertIn("discovered_routes", src)

    def test_merge_still_works(self):
        snaps = {
            "/": {"buttons": [{"text": "Home", "testid": "h"}], "links": [], "inputs": [], "data_testids": []},
            "/settings": {"buttons": [{"text": "Save", "testid": "s"}], "links": [], "inputs": [], "data_testids": []},
        }
        merged = _merge_snapshots(snaps)
        self.assertEqual(len(merged["buttons"]), 2)


class TestPipelineForceWiring(unittest.TestCase):
    def test_analyze_pr_signature_has_flags(self):
        from app.steps.pipeline import analyze_pr

        sig = inspect.signature(analyze_pr)
        self.assertIn("force", sig.parameters)
        self.assertIn("comment_triggered", sig.parameters)


if __name__ == "__main__":
    unittest.main()
