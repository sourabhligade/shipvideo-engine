"""PR4: structured recharge manifest + /glimpse intent selector."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.manifest import get_manifest_flow, select_manifest_flow
from app.manifest.runner import (
    ManifestFlow,
    effective_step_kinds,
    flow_to_generation_context,
    flow_to_steps,
)
from app.steps.demo_contract import TerminalCondition


class TestManifestRechargeFlow(unittest.TestCase):
    def test_settings_recharge_diff_selects_flow_4(self):
        diff_files = [
            {
                "path": "src/app/settings/page.tsx",
                "status": "modified",
                "patch": "\n".join(
                    [
                        "+<button>₹2000</button>",
                        "+<button>Recharge Now</button>",
                    ]
                ),
            }
        ]
        flow = get_manifest_flow(
            {
                "pr_title": "Add recharge from settings",
                "diff_files": diff_files,
                "start_route": "",
            }
        )
        self.assertIsNotNone(flow)
        self.assertEqual(flow.name, "Recharge account from settings")
        self.assertIn("₹2000", flow.click_labels)
        self.assertIn("Proceed Recharge", flow.click_labels)
        self.assertEqual(flow.click_labels[-1], "Proceed Recharge")

    def test_recharge_flow_has_causal_setup_in_contract(self):
        flow = get_manifest_flow(
            {
                "pr_title": "Add recharge from settings",
                "diff_files": [
                    {
                        "path": "src/app/settings/page.tsx",
                        "status": "modified",
                        "patch": "+<button>Recharge Now</button>",
                    }
                ],
            }
        )
        self.assertIsNotNone(flow)
        ctx = flow_to_generation_context(flow)
        contract = ctx["contract"]
        self.assertEqual([ref.label for ref in contract.setup_steps], ["₹2000"])
        self.assertEqual(contract.setup_steps[0].kind, "amount")
        self.assertEqual(contract.targets[0].label, "Settings")
        self.assertEqual(contract.targets[0].kind, "nav")
        from app.steps.preflight import preflight_gate

        steps = flow_to_steps(flow)
        preflight = preflight_gate(steps, contract)
        self.assertTrue(preflight.passed, preflight.errors)
        clicks = [s for s in steps if s.get("action") == "click"]
        rupee = next(s for s in clicks if s.get("label") == "₹2000")
        self.assertEqual(
            rupee.get("validation_condition"),
            {"type": "element_present", "value": "Recharge Now"},
        )

    def test_settings_recharge_does_not_call_llm_planner(self):
        import asyncio

        from app.steps.pipeline import analyze_pr
        from app.trigger import TriggerDecision

        decision = TriggerDecision(
            should_run=True,
            reason="ok",
            matched_files=["src/app/settings/page.tsx"],
            general_demo=False,
        )
        diff_files = [
            {
                "path": "src/app/settings/page.tsx",
                "status": "modified",
                "patch": "+<button>Recharge Now</button>\n+<div>Recharge Successful</div>",
            }
        ]
        generate = AsyncMock()
        with patch("app.steps.pipeline.fetch_pr_diff", return_value=diff_files), patch(
            "app.steps.pipeline.evaluate_trigger", return_value=decision
        ), patch(
            "app.steps.pipeline.generate_steps_from_diff", new=generate
        ), patch(
            "app.steps.pipeline.load_config", return_value={}
        ):
            out = asyncio.run(
                analyze_pr(
                    "o/r",
                    1,
                    "Add recharge from settings",
                    "https://example.com",
                    diff_files=diff_files,
                )
            )
        generate.assert_not_called()
        self.assertFalse(out.get("skipped"))
        click_labels = [
            step.get("label") for step in out["steps"] if step.get("action") == "click"
        ]
        self.assertEqual(click_labels[0], "Settings")
        self.assertIn("₹2000", click_labels)
        self.assertEqual(click_labels[-1], "Proceed Recharge")


class TestNavBeforeAmount(unittest.TestCase):
    def test_unmarked_step_before_amount_becomes_nav(self):
        flow = ManifestFlow(
            name="Open billing then pay",
            start_route="/billing",
            click_labels=["Open billing", "₹2000", "Pay"],
            step_conditions=[None, None, None],
            terminal_condition=TerminalCondition(type="text_present", value="Paid"),
            step_kinds=["cta", "amount", "cta"],
        )
        kinds = effective_step_kinds(flow.click_labels, flow.step_kinds)
        self.assertEqual(kinds, ["nav", "amount", "cta"])
        contract = flow_to_generation_context(flow)["contract"]
        self.assertEqual(contract.targets[0].label, "Open billing")
        self.assertEqual(contract.targets[0].kind, "nav")
        self.assertEqual([ref.label for ref in contract.setup_steps], ["₹2000"])


class TestGlimpseIntentSelector(unittest.TestCase):
    def test_glimpse_recharge_selects_flow_4(self):
        selection = select_manifest_flow(
            {
                "pr_title": "misc ui tweak",
                "diff_files": [
                    {
                        "path": "src/app/page.tsx",
                        "status": "modified",
                        "patch": "+<div>hello</div>",
                    }
                ],
                "intent_text": "recharge",
            }
        )
        self.assertFalse(selection.skipped)
        self.assertIsNotNone(selection.flow)
        self.assertEqual(selection.flow.name, "Recharge account from settings")

    def test_ambiguous_intent_skips_with_clarification(self):
        selection = select_manifest_flow(
            {
                "pr_title": "",
                "diff_files": [],
                "intent_text": "complete",
            }
        )
        self.assertTrue(selection.skipped, selection)
        self.assertIsNone(selection.flow)
        self.assertIn("Which flow", selection.reason)
        self.assertGreaterEqual(len(selection.candidates), 2)


if __name__ == "__main__":
    unittest.main()
