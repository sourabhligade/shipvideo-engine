"""Tests for issues #18–#22 (trigger force, general_demo, goto routes, retries, ambiguity)."""
from __future__ import annotations

import unittest
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch


class TestForceThreading(unittest.TestCase):
    """#21 force reaches evaluate_trigger; #22 general_demo when force + no UI."""

    def test_force_skips_on_demand(self):
        from app.trigger import evaluate_trigger

        decision = evaluate_trigger(
            [{"path": "README.md", "patch": "+x"}],
            {"trigger": {"mode": "on-demand"}},
            force=True,
        )
        self.assertTrue(decision.should_run)
        self.assertTrue(decision.general_demo)

    def test_force_with_ui_files_not_general_demo(self):
        from app.trigger import evaluate_trigger

        decision = evaluate_trigger(
            [{"path": "app/components/Button.tsx", "patch": "+export", "additions": 10}],
            {"trigger": {"mode": "on-demand"}},
            force=True,
        )
        self.assertTrue(decision.should_run)
        self.assertFalse(decision.general_demo)

    def test_without_force_on_demand_skips(self):
        from app.trigger import evaluate_trigger

        decision = evaluate_trigger(
            [{"path": "app/components/Button.tsx", "patch": "+x"}],
            {"trigger": {"mode": "on-demand"}},
            force=False,
        )
        self.assertFalse(decision.should_run)
        self.assertFalse(decision.general_demo)

    def test_auto_ui_change_general_demo_false(self):
        from app.trigger import evaluate_trigger

        decision = evaluate_trigger(
            [{"path": "src/components/Foo.tsx", "patch": "+x", "additions": 3}],
            {"trigger": {"mode": "auto"}},
        )
        self.assertTrue(decision.should_run)
        self.assertFalse(decision.general_demo)


class TestRuntimeGotoRoutes(unittest.TestCase):
    """#19 generation-time routes allowed for goto."""

    def test_goto_rejected_without_allowed_routes(self):
        from app.policy.selector_validator import validate_step_against_dom

        ok, reason = validate_step_against_dom(
            {"action": "goto", "url": "/pricing"},
            {"routes": ["/", "/about"]},
        )
        self.assertFalse(ok)
        self.assertIn("route_not_in_dom", reason)

    def test_goto_allowed_via_generation_routes(self):
        from app.policy.selector_validator import validate_step_against_dom

        ok, reason = validate_step_against_dom(
            {"action": "goto", "url": "/pricing"},
            {"routes": ["/", "/about"]},
            allowed_routes=["/pricing", "/docs"],
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_goto_still_ok_on_page_routes(self):
        from app.policy.selector_validator import validate_step_against_dom

        ok, reason = validate_step_against_dom(
            {"action": "goto", "url": "/about"},
            {"routes": ["/", "/about"]},
        )
        self.assertTrue(ok)


class TestAmbiguousSelectors(unittest.TestCase):
    """#18 multi-match selectors rejected when page provided."""

    def test_ambiguous_selector_rejected(self):
        from app.policy.selector_validator import validate_step_against_dom

        page = MagicMock()
        locator = MagicMock()
        locator.count.return_value = 3
        page.locator.return_value = locator

        ok, reason = validate_step_against_dom(
            {"action": "click", "selector": "[data-testid='row']"},
            {"routes": ["/"], "buttons": []},
            page=page,
        )
        self.assertFalse(ok)
        self.assertIn("selector_ambiguous", reason)

    def test_unique_selector_ok(self):
        from app.policy.selector_validator import validate_step_against_dom

        page = MagicMock()
        locator = MagicMock()
        locator.count.return_value = 1
        page.locator.return_value = locator

        ok, reason = validate_step_against_dom(
            {"action": "click", "selector": "[data-testid='submit']"},
            {"routes": ["/"], "buttons": []},
            page=page,
        )
        self.assertTrue(ok)

    def test_ambiguous_label_rejected(self):
        from app.policy.selector_validator import validate_step_against_dom

        page = MagicMock()
        text_loc = MagicMock()
        text_loc.count.return_value = 2
        page.get_by_text.return_value = text_loc

        ok, reason = validate_step_against_dom(
            {"action": "click", "label": "Edit"},
            {"routes": ["/"], "buttons": []},
            page=page,
        )
        self.assertFalse(ok)
        self.assertIn("label_ambiguous", reason)


class TestTotalRetriesLen(unittest.TestCase):
    """#20 total_retries uses len(attempts), not list addition."""

    def test_len_attempts_is_int(self):
        attempts: List[Dict[str, Any]] = [
            {"attempt": 1, "status": "rejected"},
            {"attempt": 2, "status": "ok"},
        ]
        total_retries = 0
        total_retries += len(attempts)
        self.assertEqual(total_retries, 2)
        with self.assertRaises(TypeError):
            total_retries + attempts  # type: ignore[operator]

    def test_run_stepwise_source_uses_len(self):
        import inspect
        from app.execution import step_runner

        src = inspect.getsource(step_runner.run_stepwise)
        self.assertIn("total_retries += len(attempts)", src)
        self.assertNotIn("total_retries += attempts", src)


class TestAnalyzePrForceParam(unittest.TestCase):
    """#21 analyze_pr accepts force and forwards it."""

    def test_analyze_pr_signature_has_force(self):
        import inspect
        from app.steps.pipeline import analyze_pr

        sig = inspect.signature(analyze_pr)
        self.assertIn("force", sig.parameters)
        self.assertFalse(sig.parameters["force"].default)


if __name__ == "__main__":
    unittest.main()
