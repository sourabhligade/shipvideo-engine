"""Snapshot slot-bind: catalog pins resolve against live interactive names."""
from __future__ import annotations

import unittest

from app.execution.ab_bind import (
    bind_click_step,
    drop_labels_from_queue,
    infer_step_kind,
    resolve_failed_confirm_proof,
    snapshot_has_label,
)
from app.execution.ab_proof import evaluate_click_validation
from app.execution.ab_recovery import deterministic_setup_chip_from_snapshot


def _snap(*names: str, url: str = "https://shipvideo-demo.vercel.app/settings") -> dict:
    return {
        "current_url": url,
        "snapshot_text": "\n".join(names),
        "interactive_elements": [
            {"ref": f"@e{i}", "role": "button", "name": name}
            for i, name in enumerate(names, start=1)
        ],
        "context_elements": [],
    }


class InferKindTests(unittest.TestCase):
    def test_explicit_kind_wins(self):
        self.assertEqual(infer_step_kind({"label": "Pro", "kind": "option"}), "option")

    def test_amount_from_label(self):
        self.assertEqual(infer_step_kind({"label": "₹2000"}), "amount")

    def test_confirm_from_proceed(self):
        self.assertEqual(infer_step_kind({"label": "Proceed Recharge"}), "confirm")


class BindClickStepTests(unittest.TestCase):
    def test_keeps_planned_label_when_present(self):
        result = bind_click_step(
            {"action": "click", "label": "Recharge Now", "kind": "cta"},
            _snap("₹2000", "₹10000", "Recharge Now"),
        )
        self.assertFalse(result.skip)
        self.assertEqual(result.step["label"], "Recharge Now")

    def test_amount_binds_first_visible_chip_when_pin_missing(self):
        result = bind_click_step(
            {"action": "click", "label": "₹999", "kind": "amount"},
            _snap("₹2000", "₹10000", "Recharge Now"),
        )
        self.assertFalse(result.skip)
        self.assertEqual(result.step["label"], "₹2000")
        self.assertEqual(result.step["bound_from"], "snapshot_amount")

    def test_skips_phantom_confirm(self):
        result = bind_click_step(
            {"action": "click", "label": "Proceed Recharge"},
            _snap("₹2000", "Recharge Now"),
        )
        self.assertTrue(result.skip)
        self.assertEqual(result.reason, "confirm_absent_from_snapshot")

    def test_cta_binds_unique_live_button(self):
        result = bind_click_step(
            {"action": "click", "label": "Pay now", "kind": "cta"},
            _snap("₹2000", "Recharge Now"),
        )
        self.assertFalse(result.skip)
        self.assertEqual(result.step["label"], "Recharge Now")
        self.assertEqual(result.step["bound_from"], "snapshot_cta")


class ConfirmProofRebindTests(unittest.TestCase):
    def test_rebinds_missing_proceed_proof_to_terminal(self):
        step = {
            "action": "click",
            "label": "Recharge Now",
            "kind": "cta",
            "validation_condition": {"type": "element_present", "value": "Proceed Recharge"},
            "success_condition": {"type": "element_present", "value": "Proceed Recharge"},
        }
        remaining = [
            {"action": "click", "label": "Proceed Recharge"},
            {
                "action": "assert_terminal",
                "condition": {"type": "text_present", "value": "Recharge Successful"},
            },
        ]
        result = resolve_failed_confirm_proof(
            step,
            _snap("₹2000", "Recharge Now"),
            remaining_steps=remaining,
        )
        self.assertEqual(
            result.step["validation_condition"],
            {"type": "text_present", "value": "Recharge Successful"},
        )
        self.assertEqual(result.step["validation_source"], "snapshot_bound_terminal")
        self.assertIn("Proceed Recharge", result.drop_labels)

    def test_keeps_proceed_proof_when_confirm_is_visible(self):
        step = {
            "action": "click",
            "label": "Recharge Now",
            "validation_condition": {"type": "element_present", "value": "Proceed Recharge"},
        }
        result = resolve_failed_confirm_proof(
            step,
            _snap("Recharge Now", "Proceed Recharge"),
            remaining_steps=[{"action": "click", "label": "Proceed Recharge"}],
        )
        self.assertEqual(result.step["validation_condition"]["value"], "Proceed Recharge")
        self.assertEqual(result.drop_labels, [])


class DropQueueTests(unittest.TestCase):
    def test_drops_later_confirm_click(self):
        queue = [
            {"action": "click", "label": "Recharge Now"},
            {"action": "click", "label": "Proceed Recharge"},
            {"action": "assert_terminal", "condition": {"type": "text_present", "value": "ok"}},
        ]
        out = drop_labels_from_queue(queue, after_index=0, labels=["Proceed Recharge"])
        self.assertEqual(
            [s.get("label") or s.get("action") for s in out],
            ["Recharge Now", "assert_terminal"],
        )


class AmountKindRisingEdgeTests(unittest.TestCase):
    def test_explicit_amount_kind_requires_rising_edge(self):
        result = evaluate_click_validation(
            step={
                "action": "click",
                "label": "₹2000",
                "kind": "amount",
                "validation_condition": {"type": "element_present", "value": "Recharge Now"},
            },
            snap_before=_snap("₹2000", "Recharge Now"),
            snap_after=_snap("₹2000", "Recharge Now"),
        )
        self.assertFalse(result["passed"])

    def test_untyped_amount_keeps_sticky_presence(self):
        result = evaluate_click_validation(
            step={
                "action": "click",
                "label": "₹2000",
                "validation_condition": {"type": "element_present", "value": "Recharge Now"},
            },
            snap_before=_snap("₹2000", "Recharge Now"),
            snap_after=_snap("₹2000", "Recharge Now"),
        )
        self.assertTrue(result["passed"])


class ChipRecoverySkipTests(unittest.TestCase):
    def test_does_not_insert_second_chip_when_amount_already_clicked(self):
        snap = _snap("₹2000", "₹10000", "Recharge Now")
        inserted = deterministic_setup_chip_from_snapshot(
            snap,
            blocked_intent="Proceed Recharge",
            existing_labels={"Settings", "₹2000"},
        )
        self.assertIsNone(inserted)

    def test_inserts_chip_when_none_clicked_yet(self):
        snap = _snap("₹2000", "₹10000", "Recharge Now")
        inserted = deterministic_setup_chip_from_snapshot(
            snap,
            blocked_intent="Recharge Now",
            existing_labels={"Settings"},
        )
        self.assertIsNotNone(inserted)
        self.assertEqual(inserted["label"], "₹2000")
        self.assertEqual(inserted["kind"], "amount")


class SnapshotHasLabelTests(unittest.TestCase):
    def test_label_match(self):
        self.assertTrue(snapshot_has_label(_snap("Recharge Now"), "Recharge Now"))
        self.assertFalse(snapshot_has_label(_snap("Recharge Now"), "Proceed Recharge"))


if __name__ == "__main__":
    unittest.main()
