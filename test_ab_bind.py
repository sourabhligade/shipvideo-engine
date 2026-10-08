"""Snapshot slot-bind: catalog pins resolve against live interactive names."""
from __future__ import annotations

import unittest

from app.execution.ab_bind import (
    bind_click_step,
    drop_labels_from_queue,
    infer_step_kind,
    resolve_failed_confirm_proof,
    rising_confirm_rebound,
    snapshot_has_label,
)
from app.execution.ab_proof import (
    evaluate_click_validation,
    stamp_amount_control_state,
    validation_from_successful_text_wait,
)
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

    def test_plain_nav_label_is_not_a_cta(self):
        self.assertEqual(infer_step_kind({"label": "Settings"}), "unknown")


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

    def test_rebinds_catalog_confirm_to_exact_live_name(self):
        result = bind_click_step(
            {"action": "click", "label": "Proceed Recharge"},
            _snap("₹2000", "Proceed"),
        )
        self.assertFalse(result.skip)
        self.assertEqual(result.step["label"], "Proceed")
        self.assertEqual(result.step["bound_from"], "snapshot_confirm")

    def test_does_not_bind_unrelated_continue_as_proceed(self):
        result = bind_click_step(
            {"action": "click", "label": "Proceed Recharge"},
            _snap("₹2000", "Continue"),
        )
        self.assertTrue(result.skip)
        self.assertEqual(result.reason, "confirm_absent_from_snapshot")

    def test_nav_does_not_rebind_to_unique_cta(self):
        result = bind_click_step(
            {"action": "click", "label": "Settings", "kind": "nav"},
            _snap("Recharge Now"),
        )
        self.assertEqual(result.step["label"], "Settings")
        self.assertNotEqual(result.step.get("bound_from"), "snapshot_cta")

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

    def test_rebinds_missing_proceed_proof_to_live_confirm_name(self):
        step = {
            "action": "click",
            "label": "Recharge Now",
            "validation_condition": {"type": "element_present", "value": "Proceed Recharge"},
        }
        result = resolve_failed_confirm_proof(
            step,
            _snap("Recharge Now", "Proceed"),
            remaining_steps=[{"action": "click", "label": "Proceed Recharge"}],
        )
        self.assertEqual(result.step["validation_condition"]["value"], "Proceed")
        self.assertEqual(result.step["validation_source"], "snapshot_bound_confirm")
        self.assertEqual(result.drop_labels, [])

    def test_does_not_rebind_proof_to_unrelated_continue(self):
        step = {
            "action": "click",
            "label": "Recharge Now",
            "validation_condition": {"type": "element_present", "value": "Proceed Recharge"},
        }
        result = resolve_failed_confirm_proof(
            step,
            _snap("Recharge Now", "Continue"),
            remaining_steps=[{"action": "click", "label": "Proceed Recharge"}],
        )
        self.assertNotEqual(result.step.get("validation_source"), "snapshot_bound_confirm")
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


class RisingConfirmRebindTests(unittest.TestCase):
    def _step(self) -> dict:
        return {
            "action": "click",
            "label": "Recharge Now",
            "kind": "cta",
            "validation_condition": {"type": "element_present", "value": "Proceed Recharge"},
            "success_condition": {"type": "element_present", "value": "Proceed Recharge"},
        }

    def test_sticky_success_text_does_not_drop_confirm(self):
        before = _snap("Recharge Now")
        after = _snap("Recharge Now")
        before["snapshot_text"] = "Recharge Successful"
        after["snapshot_text"] = "Recharge Successful"
        remaining = [
            {"action": "click", "label": "Proceed Recharge"},
            {
                "action": "assert_terminal",
                "condition": {"type": "text_present", "value": "Recharge Successful"},
            },
        ]
        result = rising_confirm_rebound(
            self._step(),
            before,
            after,
            remaining_steps=remaining,
            evaluate=evaluate_click_validation,
        )
        self.assertNotIn("validation_source", result.step)
        self.assertEqual(result.drop_labels, [])
        self.assertEqual(result.step["label"], "Recharge Now")

    def test_rising_success_text_drops_confirm_only_after_it_passes(self):
        before = _snap("Recharge Now")
        after = _snap("Recharge Now")
        before["snapshot_text"] = "settings"
        after["snapshot_text"] = "Recharge Successful"
        remaining = [
            {"action": "click", "label": "Proceed Recharge"},
            {
                "action": "assert_terminal",
                "condition": {"type": "text_present", "value": "Recharge Successful"},
            },
        ]
        result = rising_confirm_rebound(
            self._step(),
            before,
            after,
            remaining_steps=remaining,
            evaluate=evaluate_click_validation,
        )
        self.assertEqual(result.step["validation_source"], "snapshot_bound_terminal")
        self.assertIn("Proceed Recharge", result.drop_labels)


class TextWaitRisingEdgeTests(unittest.TestCase):
    def test_sticky_text_wait_does_not_pass(self):
        snap = _snap("Proceed")
        snap["snapshot_text"] = "Recharge Successful"
        result = validation_from_successful_text_wait(
            step={
                "validation_condition": {
                    "type": "text_present",
                    "value": "Recharge Successful",
                }
            },
            step_result={"post_click_settle": {"validation_wait": "text_present"}},
            snap_before=snap,
        )
        self.assertIsNone(result)

    def test_text_wait_passes_when_text_was_absent_before(self):
        snap = _snap("Proceed")
        snap["snapshot_text"] = "settings"
        result = validation_from_successful_text_wait(
            step={
                "validation_condition": {
                    "type": "text_present",
                    "value": "Recharge Successful",
                }
            },
            step_result={"post_click_settle": {"validation_wait": "text_present"}},
            snap_before=snap,
        )
        self.assertIsNotNone(result)
        self.assertTrue(result["passed"])


class HtmlDisabledAmountProofTests(unittest.TestCase):
    def test_is_enabled_records_html_boolean_disabled_edge(self):
        class _Cli:
            def __init__(self):
                self.enabled = False

            def get_attr(self, ref, attr):
                return ""

            def is_enabled(self, ref):
                return self.enabled

        step = {
            "action": "click",
            "label": "₹2000",
            "validation_condition": {"type": "element_present", "value": "Recharge Now"},
        }
        before = _snap("₹2000", "Recharge Now")
        after = _snap("₹2000", "Recharge Now")
        cli = _Cli()
        stamp_amount_control_state(cli, before, step)
        cli.enabled = True
        stamp_amount_control_state(cli, after, step)
        result = evaluate_click_validation(step=step, snap_before=before, snap_after=after)
        self.assertTrue(before["interactive_elements"][1]["disabled"])
        self.assertFalse(after["interactive_elements"][1]["disabled"])
        self.assertTrue(result["passed"])


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

    def test_untyped_amount_rejects_sticky_presence(self):
        result = evaluate_click_validation(
            step={
                "action": "click",
                "label": "₹2000",
                "validation_condition": {"type": "element_present", "value": "Recharge Now"},
            },
            snap_before=_snap("₹2000", "Recharge Now"),
            snap_after=_snap("₹2000", "Recharge Now"),
        )
        self.assertFalse(result["passed"])

    def test_amount_passes_when_chip_becomes_selected(self):
        before = _snap("₹2000", "Recharge Now")
        after = _snap("₹2000", "Recharge Now")
        after["interactive_elements"][0]["pressed"] = True
        result = evaluate_click_validation(
            step={
                "action": "click",
                "label": "₹2000",
                "validation_condition": {"type": "element_present", "value": "Recharge Now"},
            },
            snap_before=before,
            snap_after=after,
        )
        self.assertTrue(result["passed"])

    def test_amount_passes_when_chip_gains_active_class(self):
        before = _snap("₹2000", "Recharge Now")
        after = _snap("₹2000", "Recharge Now")
        before["interactive_elements"][0]["class"] = "amount-btn"
        after["interactive_elements"][0]["class"] = "amount-btn active"
        result = evaluate_click_validation(
            step={
                "action": "click",
                "label": "₹2000",
                "validation_condition": {"type": "element_present", "value": "Recharge Now"},
            },
            snap_before=before,
            snap_after=after,
        )
        self.assertTrue(result["passed"])

    def test_amount_passes_when_recharge_button_becomes_enabled(self):
        before = _snap("₹2000", "Recharge Now")
        after = _snap("₹2000", "Recharge Now")
        before["interactive_elements"][1]["disabled"] = True
        after["interactive_elements"][1]["disabled"] = False
        result = evaluate_click_validation(
            step={
                "action": "click",
                "label": "₹2000",
                "validation_condition": {"type": "element_present", "value": "Recharge Now"},
            },
            snap_before=before,
            snap_after=after,
        )
        self.assertTrue(result["passed"])


class StateUnchangedChipTests(unittest.TestCase):
    def test_current_amount_click_is_already_used(self):
        from unittest.mock import patch

        from app.execution.ab_recovery import recover_ab_prerequisite_steps

        steps = [
            {"action": "click", "label": "Settings", "kind": "nav"},
            {"action": "click", "label": "₹2000", "kind": "amount"},
            {"action": "click", "label": "Recharge Now", "kind": "cta"},
        ]
        with patch(
            "app.execution.step_runner.regenerate_with_feedback",
            return_value=([], []),
        ) as regenerate:
            result = recover_ab_prerequisite_steps(
                objective={"goal": "recharge"},
                steps=steps,
                step_index=1,
                current_step=steps[1],
                current_intent="₹2000",
                snap_after=_snap("₹2000", "₹10000"),
                mode="deterministic",
                trigger_reason="state_unchanged",
            )
        regenerate.assert_called_once()
        self.assertFalse(result["recovered"])


class RestartFrameTests(unittest.TestCase):
    def test_restore_requires_a_frame_for_every_milestone_slot(self):
        from pathlib import Path
        import tempfile

        from app.execution.ab_recovery import (
            milestone_screenshot_slots,
            restore_restart_frames,
        )

        results = [
            {"status": "ok", "outcome": "success", "step": {"action": "goto"}},
            {
                "status": "ok",
                "outcome": "success",
                "step": {"action": "screenshot"},
                "screenshot_path": "",
            },
            {
                "status": "ok",
                "outcome": "success",
                "step": {"action": "click", "label": "Settings"},
            },
            {
                "status": "ok",
                "outcome": "success",
                "step": {"action": "screenshot"},
                "screenshot_path": "",
            },
        ]
        slots = milestone_screenshot_slots(results)
        self.assertEqual(slots, [1, 3])
        self.assertFalse(restore_restart_frames(results, slots, ["", ""]))
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "shot1.png"
            second = Path(tmp) / "shot2.png"
            first.write_bytes(b"a")
            second.write_bytes(b"b")
            self.assertTrue(
                restore_restart_frames(results, slots, [str(first), str(second)])
            )
        self.assertEqual(Path(results[1]["screenshot_path"]).name, "shot1.png")
        self.assertEqual(results[3]["outcome"], "success")


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
        self.assertFalse(snapshot_has_label(_snap("Proceed"), "Proceed Recharge"))


if __name__ == "__main__":
    unittest.main()
