"""Accuracy gates: normalizer locator preservation + sendable approval."""

from __future__ import annotations

import unittest

from app.steps.step_normalizer import normalize_steps
from app.steps.step_execution import _build_render_approval
from app.github_comment import _accuracy_note


class NormalizerLocatorTests(unittest.TestCase):
    def test_preserves_validation_and_testid(self):
        steps = [
            {
                "action": "click",
                "label": "Save",
                "selector": "[data-testid='save']",
                "testid": "save",
                "validation_condition": {"type": "text_present", "value": "Saved"},
                "success_condition": {"type": "text_present", "value": "Saved"},
            }
        ]
        out = normalize_steps(steps)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["label"], "Save")
        self.assertEqual(out[0]["testid"], "save")
        self.assertEqual(out[0]["selector"], "[data-testid='save']")
        self.assertEqual(out[0]["validation_condition"]["value"], "Saved")

    def test_builds_selector_from_testid(self):
        steps = [{"action": "click", "label": "Go", "testid": "go-btn"}]
        out = normalize_steps(steps)
        self.assertEqual(out[0]["selector"], "[data-testid='go-btn']")


class RenderApprovalTests(unittest.TestCase):
    def test_all_unvalidated_not_sendable(self):
        results = [
            {
                "outcome": "unvalidated",
                "step": {"action": "click", "label": "A"},
                "validation_passed": False,
            },
            {
                "outcome": "unvalidated",
                "step": {"action": "click", "label": "B"},
                "validation_passed": False,
            },
        ]
        approval = _build_render_approval(
            generation_context={"start_route": ""},
            results=results,
            approved_frames=["a.png"],
        )
        self.assertFalse(approval["is_sendable"])
        self.assertIn("all_clicks_unvalidated", approval["reasons"])

    def test_proven_click_can_be_sendable(self):
        results = [
            {
                "outcome": "success",
                "step": {"action": "click", "label": "A"},
                "validation_passed": True,
                "terminal_condition_reached": True,
            },
        ]
        approval = _build_render_approval(
            generation_context={"start_route": ""},
            results=results,
            approved_frames=["a.png"],
        )
        self.assertTrue(approval["is_sendable"])


class GithubAccuracyNoteTests(unittest.TestCase):
    def test_accuracy_note_renders(self):
        note = _accuracy_note(
            {"proven_clicks": 2, "failed_clicks": 1, "accuracy_ok": True}
        )
        self.assertIn("Proven clicks", note)
        self.assertIn("pass", note)


if __name__ == "__main__":
    unittest.main()


class InferClickProofTests(unittest.TestCase):
    def test_infers_url_match(self):
        from app.steps.step_execution import _attach_test_case_success_conditions

        steps = [
            {"action": "click", "label": "Pricing", "expected_url": "/pricing"},
        ]
        out = _attach_test_case_success_conditions(steps, "")
        self.assertEqual(out[0]["validation_condition"]["type"], "url_match")
        self.assertEqual(out[0]["validation_condition"]["value"], "/pricing")
        self.assertEqual(out[0].get("validation_source"), "inferred")

    def test_infers_testid_element(self):
        from app.steps.step_execution import _attach_test_case_success_conditions

        steps = [{"action": "click", "label": "Save", "testid": "save-btn"}]
        out = _attach_test_case_success_conditions(steps, "")
        self.assertEqual(out[0]["validation_condition"]["type"], "element_present")
        self.assertIn("save-btn", out[0]["validation_condition"]["value"])
