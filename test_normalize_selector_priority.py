"""Priority #1: normalize_steps must keep stable selectors (testid/aria)."""
from __future__ import annotations

import unittest

from app.steps.step_normalizer import normalize_steps, _is_stable_selector


class NormalizeSelectorPriorityTests(unittest.TestCase):
    def test_keeps_testid_when_label_present(self):
        out = normalize_steps(
            [
                {
                    "action": "click",
                    "label": "Save",
                    "selector": "[data-testid='save-btn']",
                }
            ]
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["selector"], "[data-testid='save-btn']")
        self.assertEqual(out[0]["label"], "Save")

    def test_keeps_aria_when_label_present(self):
        out = normalize_steps(
            [
                {
                    "action": "click",
                    "label": "Close",
                    "selector": "[aria-label='Close dialog']",
                }
            ]
        )
        self.assertEqual(out[0]["selector"], "[aria-label='Close dialog']")
        self.assertEqual(out[0]["label"], "Close")

    def test_normalizes_double_quotes_on_testid(self):
        out = normalize_steps(
            [
                {
                    "action": "click",
                    "label": "Save",
                    "selector": '[data-testid="save-btn"]',
                }
            ]
        )
        self.assertEqual(out[0]["selector"], "[data-testid='save-btn']")

    def test_selector_only_click(self):
        out = normalize_steps(
            [{"action": "click", "selector": "[data-testid='only']"}]
        )
        self.assertEqual(out[0]["selector"], "[data-testid='only']")
        self.assertNotIn("label", out[0])

    def test_label_only_still_works(self):
        out = normalize_steps([{"action": "click", "label": "Continue"}])
        self.assertEqual(out[0]["label"], "Continue")
        self.assertNotIn("selector", out[0])

    def test_text_used_as_label_when_no_label(self):
        out = normalize_steps(
            [
                {
                    "action": "click",
                    "text": "Submit",
                    "selector": "[data-testid='submit']",
                }
            ]
        )
        self.assertEqual(out[0]["selector"], "[data-testid='submit']")
        self.assertEqual(out[0]["label"], "Submit")

    def test_id_selector_kept_with_label(self):
        out = normalize_steps(
            [{"action": "click", "label": "Go", "selector": "#primary-cta"}]
        )
        self.assertEqual(out[0]["selector"], "#primary-cta")
        self.assertEqual(out[0]["label"], "Go")

    def test_playwright_engine_selector_kept(self):
        out = normalize_steps(
            [
                {
                    "action": "click",
                    "label": "Settings",
                    "selector": "role=button[name='Settings']",
                }
            ]
        )
        self.assertEqual(out[0]["selector"], "role=button[name='Settings']")

    def test_weak_css_still_attached_with_label(self):
        out = normalize_steps(
            [{"action": "click", "label": "Save", "selector": "button.primary"}]
        )
        self.assertEqual(out[0]["label"], "Save")
        self.assertEqual(out[0]["selector"], "button.primary")

    def test_passthrough_validation_fields(self):
        out = normalize_steps(
            [
                {
                    "action": "click",
                    "label": "Next",
                    "selector": "[data-testid='next']",
                    "validation_condition": {"type": "text_present", "value": "Done"},
                    "dom_confirmed": True,
                }
            ]
        )
        self.assertEqual(
            out[0]["validation_condition"],
            {"type": "text_present", "value": "Done"},
        )
        self.assertTrue(out[0]["dom_confirmed"])

    def test_stable_selector_helper(self):
        self.assertTrue(_is_stable_selector("[data-testid='x']"))
        self.assertTrue(_is_stable_selector("[aria-label='y']"))
        self.assertTrue(_is_stable_selector("#id"))
        self.assertTrue(_is_stable_selector("role=button[name='A']"))
        self.assertFalse(_is_stable_selector("button.primary"))
        self.assertFalse(_is_stable_selector(""))


if __name__ == "__main__":
    unittest.main()
