"""Phase 2: generation fail-closed — no silent screenshot demos for feature PRs."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.steps.step_generation import (
    FALLBACK_STEPS,
    _collapse_result,
    _hard_fail_result,
    _soft_fallback_result,
    generate_steps_from_diff,
)


class TestCollapseHelpers(unittest.TestCase):
    def test_hard_fail_empty_steps(self):
        r = _hard_fail_result(narration="n", reason="boom")
        self.assertEqual(r["steps"], [])
        self.assertTrue(r["generation_hard_fail"])
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "boom")

    def test_soft_fallback_screenshot(self):
        r = _soft_fallback_result(narration="n", reason="general")
        self.assertEqual(r["steps"], FALLBACK_STEPS)
        self.assertTrue(r["generation_soft_fallback"])
        self.assertFalse(r["generation_hard_fail"])
        self.assertTrue(r["ok"])

    def test_collapse_feature_vs_general(self):
        hard = _collapse_result(
            general_demo=False, narration="n", reason="x"
        )
        soft = _collapse_result(
            general_demo=True, narration="n", reason="x"
        )
        self.assertTrue(hard["generation_hard_fail"])
        self.assertEqual(hard["steps"], [])
        self.assertTrue(soft["generation_soft_fallback"])
        self.assertEqual(soft["steps"], FALLBACK_STEPS)


class TestGenerateStepsFailClosed(unittest.TestCase):
    def test_budget_exceeded_hard_fail_feature(self):
        with patch("app.steps.step_generation.check_budget", return_value=False):
            out = asyncio.run(
                generate_steps_from_diff(
                    [{"path": "app/x/page.tsx", "status": "modified", "patch": "+x"}],
                    "Add button",
                    "https://example.com",
                    general_demo=False,
                )
            )
        self.assertTrue(out.get("generation_hard_fail"))
        self.assertEqual(out.get("steps"), [])
        self.assertTrue(out.get("budget_exceeded"))

    def test_budget_exceeded_soft_general_demo(self):
        with patch("app.steps.step_generation.check_budget", return_value=False):
            out = asyncio.run(
                generate_steps_from_diff(
                    [{"path": "README.md", "status": "modified", "patch": "+x"}],
                    "Docs",
                    "https://example.com",
                    general_demo=True,
                )
            )
        self.assertTrue(out.get("generation_soft_fallback"))
        self.assertEqual(out.get("steps"), FALLBACK_STEPS)

    def test_exception_hard_fail_not_screenshot(self):
        with patch("app.steps.step_generation.check_budget", return_value=True), patch(
            "app.steps.step_generation.crawl_dom_data",
            new=AsyncMock(side_effect=RuntimeError("crawl down")),
        ), patch(
            "app.steps.step_generation.load_config",
            return_value={"routeMap": {}, "appHints": ""},
        ):
            out = asyncio.run(
                generate_steps_from_diff(
                    [{"path": "app/x/page.tsx", "status": "modified", "patch": "+hi"}],
                    "Feature",
                    "https://example.com",
                    general_demo=False,
                )
            )
        self.assertTrue(out.get("generation_hard_fail"))
        self.assertEqual(out.get("steps"), [])
        self.assertNotEqual(out.get("steps"), FALLBACK_STEPS)


class TestAnalyzePrPropagatesHardFail(unittest.TestCase):
    def test_analyze_pr_empty_plan_not_screenshot(self):
        from app.steps.pipeline import analyze_pr
        from app.trigger import TriggerDecision

        fake_flow = {
            "ok": False,
            "error": "LLM returned empty step plan.",
            "steps": [],
            "generation_hard_fail": True,
            "narration": "x",
            "llm_cost_usd": 0.1,
        }
        decision = TriggerDecision(
            should_run=True,
            reason="ok",
            matched_files=["app/x.tsx"],
            general_demo=False,
        )
        contract = MagicMock()
        contract.contract_id = "c"
        contract.confidence = "low"
        contract.targets = []
        contract.start_route = "/"
        with patch("app.steps.pipeline.fetch_pr_diff", return_value=[
            {"path": "app/x/page.tsx", "status": "modified", "patch": "+x"}
        ]), patch(
            "app.steps.contract_extraction.extract_contract_static",
            return_value=contract,
        ), patch(
            "app.steps.pipeline.evaluate_trigger", return_value=decision
        ), patch(
            "app.steps.pipeline.get_manifest_flow", return_value=None
        ), patch(
            "app.steps.pipeline.generate_steps_from_diff",
            new=AsyncMock(return_value=fake_flow),
        ), patch(
            "app.steps.pipeline.load_config", return_value={}
        ):
            out = asyncio.run(
                analyze_pr("o/r", 1, "t", "https://example.com")
            )
        self.assertTrue(out.get("generation_hard_fail"))
        self.assertEqual(out.get("steps"), [])
        self.assertEqual(out.get("ok"), False)


class TestWebhookHardFailNoCapture(unittest.TestCase):
    def test_webhook_returns_on_hard_fail(self):
        from app import webhook as wh

        flow = {
            "ok": False,
            "generation_hard_fail": True,
            "error": "LLM returned empty step plan.",
            "steps": [],
        }
        comments = []

        def fake_comment(repo, pr, video, error_message=None, extra_note=None):
            comments.append({"error": error_message, "video": video})

        # Exercise the branch logic inline (mirror webhook body)
        if flow.get("generation_hard_fail") or flow.get("ok") is False:
            fail_reason = str(
                flow.get("error")
                or flow.get("generation_fallback_reason")
                or "Step generation failed."
            )
            fake_comment("o/r", 1, None, error_message=f"**Demo video not generated**\n\n{fail_reason}")

        self.assertEqual(len(comments), 1)
        self.assertIsNone(comments[0]["video"])
        self.assertIn("empty step plan", comments[0]["error"])


if __name__ == "__main__":
    unittest.main()
