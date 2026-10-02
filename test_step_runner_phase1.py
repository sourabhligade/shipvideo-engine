import unittest
import sys
import types
import tempfile
from unittest.mock import patch
from pathlib import Path

playwright_module = types.ModuleType("playwright")
playwright_sync_api = types.ModuleType("playwright.sync_api")
playwright_async_api = types.ModuleType("playwright.async_api")
playwright_sync_api.Page = object
playwright_sync_api.sync_playwright = lambda: None
playwright_async_api.async_playwright = lambda: None
playwright_module.sync_api = playwright_sync_api
playwright_module.async_api = playwright_async_api
sys.modules.setdefault("playwright", playwright_module)
sys.modules.setdefault("playwright.sync_api", playwright_sync_api)
sys.modules.setdefault("playwright.async_api", playwright_async_api)

observability_module = types.ModuleType("observability")
observability_module.record_agent_browser_diagnostics = lambda **kwargs: None
observability_module.pipeline_step = lambda name: (lambda fn: fn)
observability_module.init_tracing = lambda: None
observability_module.pipeline_run_span = lambda *a, **k: None
observability_module.print_pipeline_summary = lambda *a, **k: None
observability_module.set_current_span_error = lambda *a, **k: None
sys.modules.setdefault("observability", observability_module)

from app.config_types import CaptureSettings
from app.steps.contract_extraction import extract_contract_static
from app.steps.demo_contract import DemoContract, TargetRef, TerminalCondition
from app.steps.preflight import preflight_gate
from app.steps.step_generation import (
    _inject_sequential_click_validations,
    _insert_missing_setup_clicks,
    _sanitize_terminal_assertions,
    _synthesize_click_steps,
)
from app.execution.step_runner import (
    AB_NETWORKIDLE_TIMEOUT_S,
    _assert_ab_terminal_condition,
    _collect_ab_failure_diagnostics,
    _discard_step_screenshots,
    _recover_ab_prerequisite_steps,
    _ensure_ab_target_actionable,
    _configure_ab_session,
    _resolve_ab_click_target,
    _resolve_terminal_expectation,
    _scroll_to_find,
    _should_keep_click_screenshots,
    _settle_ab_page,
)


class _FakeCLI:
    def __init__(
        self,
        *,
        fail_load_states=None,
        fail_text=False,
        fail_url=False,
        found_ref="",
        found_testid_ref="",
        found_label_ref="",
        found_role_button_ref="",
        found_role_link_ref="",
        visible=True,
        enabled=True,
    ):
        self.calls = []
        self.fail_load_states = set(fail_load_states or [])
        self.fail_text = fail_text
        self.fail_url = fail_url
        self.found_ref = found_ref
        self.found_testid_ref = found_testid_ref
        self.found_label_ref = found_label_ref
        self.found_role_button_ref = found_role_button_ref
        self.found_role_link_ref = found_role_link_ref
        self.visible = visible
        self.enabled = enabled
        self.console_entries = []
        self.page_error_entries = []
        self.network_entries = []
        self.scroll_ref_sequence = []

    def set_viewport(self, width, height):
        self.calls.append(("set_viewport", width, height))

    def wait_for_load_state(self, state, *, timeout):
        self.calls.append(("wait_for_load_state", state, timeout))
        if state in self.fail_load_states:
            raise RuntimeError(f"load wait failed: {state}")

    def wait_for_text(self, text, *, timeout):
        self.calls.append(("wait_for_text", text, timeout))
        if self.fail_text:
            raise RuntimeError("text wait failed")

    def wait_for_url(self, pattern, *, timeout):
        self.calls.append(("wait_for_url", pattern, timeout))
        if self.fail_url:
            raise RuntimeError("url wait failed")

    def wait(self, ms):
        self.calls.append(("wait", ms))

    def find_ref(self, intent):
        self.calls.append(("find_ref", intent))
        return self.found_ref

    def find_testid_ref(self, testid):
        self.calls.append(("find_testid_ref", testid))
        return self.found_testid_ref

    def find_label_ref(self, label):
        self.calls.append(("find_label_ref", label))
        return self.found_label_ref

    def find_role_ref(self, role, name):
        self.calls.append(("find_role_ref", role, name))
        if role == "button":
            return self.found_role_button_ref
        if role == "link":
            return self.found_role_link_ref
        return ""

    def scroll_into_view(self, target):
        self.calls.append(("scroll_into_view", target))

    def scroll(self, direction="down", px=700):
        self.calls.append(("scroll", direction, px))

    def is_visible(self, target):
        self.calls.append(("is_visible", target))
        return self.visible

    def is_enabled(self, target):
        self.calls.append(("is_enabled", target))
        return self.enabled

    def console_messages(self):
        self.calls.append(("console_messages",))
        return list(self.console_entries)

    def page_errors(self):
        self.calls.append(("page_errors",))
        return list(self.page_error_entries)

    def network_requests(self):
        self.calls.append(("network_requests",))
        return list(self.network_entries)

    def get_count(self, selector):
        self.calls.append(("get_count", selector))
        return 1 if self.found_testid_ref or self.found_ref else 0

    def get_url(self):
        self.calls.append(("get_url",))
        return getattr(self, "current_url", "https://example.com/done")

    def find_element(self, selector):
        self.calls.append(("find_element", selector))
        return ""


class StepRunnerPhase1Tests(unittest.TestCase):
    def setUp(self):
        # Silence expected selection-miss diagnostics during unit tests
        self._ref_log = patch("app.browser.ref_selector._log_result", lambda *a, **k: None)
        self._ref_log.start()
        self.addCleanup(self._ref_log.stop)

    def test_configure_ab_session_sets_capture_viewport(self):
        cli = _FakeCLI()
        settings = CaptureSettings(viewport_width=1440, viewport_height=900)

        result = _configure_ab_session(cli, settings)

        self.assertEqual(
            cli.calls,
            [("set_viewport", 1440, 900)],
        )
        self.assertEqual(
            result,
            {"viewport_width": 1440, "viewport_height": 900},
        )

    def test_settle_ab_page_prefers_load_states_and_validation_wait(self):
        cli = _FakeCLI()

        result = _settle_ab_page(
            cli,
            validation_condition={"type": "text_present", "value": "Saved"},
        )

        self.assertEqual(
            cli.calls,
            [
                ("wait_for_load_state", "domcontentloaded", 15),
                ("wait_for_text", "Saved", 8),
            ],
        )
        self.assertFalse(result["networkidle"])
        self.assertTrue(result.get("networkidle_skipped"))
        self.assertTrue(result["domcontentloaded"])
        self.assertEqual(result["validation_wait"], "text_present")
        self.assertFalse(result["fallback_wait_used"])

    def test_settle_ab_page_marks_fallback_when_networkidle_fails(self):
        cli = _FakeCLI(fail_load_states={"networkidle"})

        result = _settle_ab_page(cli)

        self.assertEqual(
            cli.calls,
            [
                ("wait_for_load_state", "domcontentloaded", 15),
                ("wait_for_load_state", "networkidle", AB_NETWORKIDLE_TIMEOUT_S),
            ],
        )
        self.assertFalse(result["networkidle"])
        self.assertTrue(result["domcontentloaded"])
        self.assertTrue(result["fallback_wait_used"])

    def test_resolve_ab_click_target_prefers_testid_lookup_from_selector(self):
        cli = _FakeCLI(found_testid_ref="@e11")
        snapshot = {
            "interactive_elements": [
                {"ref": "@e2", "role": "button", "name": "Proceed Recharge"},
            ],
            "context_elements": [],
            "current_url": "https://example.test",
            "snapshot_text": "",
        }

        result = _resolve_ab_click_target(
            cli,
            intent="Proceed Recharge",
            selector="[data-testid='proceed-recharge']",
            snapshot=snapshot,
            mode="deterministic",
            allow_scroll_retry=True,
        )

        self.assertEqual(
            cli.calls,
            [("find_testid_ref", "proceed-recharge")],
        )
        self.assertEqual(result["chosen_ref"], "@e11")
        self.assertEqual(result["selection_source"], "semantic_testid")
        self.assertEqual(result["candidate_count"], 1)

    def test_resolve_ab_click_target_prefers_role_lookup_before_snapshot_matching(self):
        cli = _FakeCLI(found_role_button_ref="@e55")
        snapshot = {
            "interactive_elements": [
                {"ref": "@e2", "role": "button", "name": "Proceed Recharge"},
            ],
            "context_elements": [],
            "current_url": "https://example.test",
            "snapshot_text": "",
        }

        result = _resolve_ab_click_target(
            cli,
            intent="Proceed Recharge",
            snapshot=snapshot,
            mode="deterministic",
            allow_scroll_retry=True,
        )

        self.assertEqual(
            cli.calls,
            [("find_role_ref", "button", "Proceed Recharge")],
        )
        self.assertEqual(result["chosen_ref"], "@e55")
        self.assertEqual(result["selection_source"], "semantic_role")
        self.assertEqual(result["candidate_count"], 1)

    def test_resolve_ab_click_target_uses_semantic_find_after_command_lookups_miss(self):
        cli = _FakeCLI(found_ref="@e99")
        snapshot = {
            "interactive_elements": [],
            "context_elements": [],
            "current_url": "https://example.test",
            "snapshot_text": "",
        }

        result = _resolve_ab_click_target(
            cli,
            intent="Proceed Recharge",
            snapshot=snapshot,
            mode="deterministic",
            allow_scroll_retry=True,
        )

        self.assertEqual(result["chosen_ref"], "@e99")
        self.assertEqual(result["selection_reason"], "ab_find")
        self.assertEqual(result["selection_source"], "semantic_find")
        self.assertEqual(result["candidate_count"], 1)
        self.assertFalse(result["should_retry"])
        self.assertEqual(
            cli.calls,
            [
                ("find_role_ref", "button", "Proceed Recharge"),
                ("find_role_ref", "link", "Proceed Recharge"),
                ("find_label_ref", "Proceed Recharge"),
                ("find_ref", "Proceed Recharge"),
            ],
        )

    def test_resolve_ab_click_target_requests_scroll_retry_after_find_miss(self):
        cli = _FakeCLI(found_ref="")
        snapshot = {
            "interactive_elements": [],
            "context_elements": [],
            "current_url": "https://example.test",
            "snapshot_text": "",
        }

        result = _resolve_ab_click_target(
            cli,
            intent="Proceed Recharge",
            snapshot=snapshot,
            mode="deterministic",
            allow_scroll_retry=True,
        )

        self.assertEqual(result["chosen_ref"], "")
        self.assertTrue(result["scroll_retry_used"])
        self.assertTrue(result["should_retry"])

    def test_ensure_ab_target_actionable_checks_visibility_and_enabled_state(self):
        cli = _FakeCLI(visible=False, enabled=True)

        result = _ensure_ab_target_actionable(cli, "@e5")

        self.assertEqual(
            cli.calls,
            [
                ("scroll_into_view", "@e5"),
                ("wait_for_load_state", "domcontentloaded", 15),
                ("is_visible", "@e5"),
                ("is_enabled", "@e5"),
            ],
        )
        self.assertEqual(
            result,
            {"target_visible": False, "target_enabled": True},
        )

    def test_recover_ab_prerequisite_steps_inserts_recovery_before_retry(self):
        steps = [
            {"action": "click", "label": "Recharge Now"},
            {"action": "click", "label": "Proceed Recharge"},
        ]
        snapshot = {
            "interactive_elements": [],
            "context_elements": [],
            "current_url": "https://example.test/settings",
            "snapshot_text": "",
        }

        with patch(
            "app.execution.step_runner.regenerate_with_feedback",
            return_value=(
                [{"action": "click", "label": "₹2000"}],
                [{"attempt": 1, "status": "ok"}],
            ),
        ) as regenerate:
            result = _recover_ab_prerequisite_steps(
                objective={"goal": "recover"},
                steps=steps,
                step_index=0,
                current_step=steps[0],
                current_intent="Recharge Now",
                snap_after=snapshot,
                mode="deterministic",
                trigger_reason="state_unchanged",
                current_step_completed_unvalidated=False,
                state_changed=False,
            )

        regenerate.assert_called_once()
        self.assertTrue(result["recovered"])
        self.assertEqual(result["attempts_used"], 1)
        self.assertEqual(result["next_intent"], "Proceed Recharge")
        self.assertEqual(result["blocked_intent"], "Proceed Recharge")
        self.assertEqual(
            [step.get("label") for step in result["replacement_steps"]],
            ["₹2000", "Recharge Now"],
        )
        self.assertTrue(result["replacement_steps"][1]["_ab_recovery_attempted"])

    def test_recover_ab_prerequisite_steps_inserts_visible_amount_chip_without_llm(self):
        steps = [
            {"action": "click", "label": "Recharge Now"},
            {"action": "click", "label": "Proceed Recharge"},
        ]
        snapshot = {
            "interactive_elements": [
                {"ref": "@e1", "role": "button", "name": "₹2000"},
                {"ref": "@e2", "role": "button", "name": "₹5000"},
            ],
            "context_elements": [],
            "current_url": "https://example.test/settings",
            "snapshot_text": "",
        }

        with patch("app.execution.step_runner.regenerate_with_feedback") as regenerate:
            result = _recover_ab_prerequisite_steps(
                objective={"goal": "recover"},
                steps=steps,
                step_index=0,
                current_step=steps[0],
                current_intent="Recharge Now",
                snap_after=snapshot,
                mode="deterministic",
                trigger_reason="selection_failed_current_step",
                current_step_completed_unvalidated=False,
                state_changed=None,
            )

        regenerate.assert_not_called()
        self.assertTrue(result["recovered"])
        self.assertEqual(result["attempts_used"], 0)
        self.assertEqual(result["recovery_source"], "deterministic_setup_chip")
        self.assertEqual(result["blocked_intent"], "Recharge Now")
        self.assertEqual(
            [step.get("label") for step in result["replacement_steps"]],
            ["₹2000", "Recharge Now"],
        )
        self.assertEqual(
            result["replacement_steps"][0].get("validation_condition"),
            {"type": "element_present", "value": "Recharge Now"},
        )
        self.assertTrue(result["replacement_steps"][1]["_ab_recovery_attempted"])

    def test_recover_ab_prerequisite_steps_skips_when_next_target_exists(self):
        steps = [
            {"action": "click", "label": "Recharge Now"},
            {"action": "click", "label": "Proceed Recharge"},
        ]
        snapshot = {
            "interactive_elements": [
                {"ref": "@e2", "role": "button", "name": "Proceed Recharge"},
            ],
            "context_elements": [],
            "current_url": "https://example.test/settings",
            "snapshot_text": "",
        }

        with patch("app.execution.step_runner.regenerate_with_feedback") as regenerate:
            result = _recover_ab_prerequisite_steps(
                objective={"goal": "recover"},
                steps=steps,
                step_index=0,
                current_step=steps[0],
                current_intent="Recharge Now",
                snap_after=snapshot,
                mode="deterministic",
                trigger_reason="state_unchanged",
                current_step_completed_unvalidated=False,
                state_changed=False,
            )

        regenerate.assert_not_called()
        self.assertFalse(result["recovered"])
        self.assertTrue(result["blocked_target_present"])

    def test_recover_ab_prerequisite_steps_uses_current_intent_for_selection_failed_current_step(self):
        steps = [
            {"action": "click", "label": "Proceed Recharge"},
            {"action": "assert_terminal", "expected_element": "done"},
        ]
        snapshot = {
            "interactive_elements": [],
            "context_elements": [],
            "current_url": "https://example.test/settings",
            "snapshot_text": "",
        }

        with patch(
            "app.execution.step_runner.regenerate_with_feedback",
            return_value=(
                [{"action": "click", "label": "₹2000"}],
                [{"attempt": 1, "status": "ok"}],
            ),
        ) as regenerate:
            result = _recover_ab_prerequisite_steps(
                objective={"goal": "recover"},
                steps=steps,
                step_index=0,
                current_step=steps[0],
                current_intent="Proceed Recharge",
                snap_after=snapshot,
                mode="deterministic",
                trigger_reason="selection_failed_current_step",
                current_step_completed_unvalidated=False,
                state_changed=None,
            )

        regenerate.assert_called_once()
        self.assertTrue(result["recovered"])
        self.assertEqual(result["blocked_intent"], "Proceed Recharge")

    def test_recover_ab_prerequisite_steps_marks_unvalidated_context_for_next_step_miss(self):
        steps = [
            {"action": "click", "label": "Recharge Now"},
            {"action": "click", "label": "Proceed Recharge"},
        ]
        snapshot = {
            "interactive_elements": [],
            "context_elements": [],
            "current_url": "https://example.test/settings",
            "snapshot_text": "",
        }

        with patch(
            "app.execution.step_runner.regenerate_with_feedback",
            return_value=(
                [{"action": "click", "label": "₹2000"}],
                [{"attempt": 1, "status": "ok"}],
            ),
        ) as regenerate:
            result = _recover_ab_prerequisite_steps(
                objective={"goal": "recover"},
                steps=steps,
                step_index=1,
                current_step=steps[1],
                current_intent="Proceed Recharge",
                snap_after=snapshot,
                mode="deterministic",
                trigger_reason="selection_failed_after_unvalidated",
                current_step_completed_unvalidated=True,
                state_changed=None,
            )

        regenerate.assert_called_once()
        regenerate_args = regenerate.call_args.kwargs["error_context"]
        self.assertTrue(result["recovered"])
        self.assertEqual(result["blocked_intent"], "Proceed Recharge")
        self.assertTrue(regenerate_args["current_step_completed_unvalidated"])
        self.assertEqual(regenerate_args["trigger_reason"], "selection_failed_after_unvalidated")

    def test_extract_contract_static_records_interaction_hints_from_diff(self):
        diff_files = [
            {
                "path": "src/app/recharge/page.tsx",
                "status": "modified",
                "patch": '\n'.join(
                    [
                        '+<button>Select amount</button>',
                        '+<div className="tabs">Plan Tabs</div>',
                        '+<button>Proceed Recharge</button>',
                    ]
                ),
            }
        ]

        contract = extract_contract_static(diff_files)

        self.assertIn("interaction_hint_high:select amount", contract.extraction_notes)
        self.assertIn("interaction_hint_low:switch tab", contract.extraction_notes)

    def test_extract_amount_chip_as_setup_step_not_cta(self):
        diff_files = [
            {
                "path": "src/app/settings/page.tsx",
                "status": "modified",
                "patch": "\n".join(
                    [
                        '+<button data-testid="amount-2000">₹2000</button>',
                        "+<button>Recharge Now</button>",
                        "+<div>Recharge Successful</div>",
                    ]
                ),
            }
        ]

        contract = extract_contract_static(diff_files)

        self.assertEqual([ref.label for ref in contract.setup_steps], ["₹2000"])
        self.assertEqual(contract.setup_steps[0].kind, "amount")
        self.assertEqual(
            contract.setup_steps[0].selector,
            "[data-testid='amount-2000']",
        )
        self.assertNotIn("₹2000", [target.label for target in contract.targets])
        self.assertIn("Recharge Now", [target.label for target in contract.targets])
        self.assertEqual(contract.confidence, "high")

    def test_ungrounded_high_setup_hint_caps_confidence_medium(self):
        diff_files = [
            {
                "path": "src/app/settings/page.tsx",
                "status": "modified",
                "patch": "\n".join(
                    [
                        "+<button>Select amount</button>",
                        "+<button>Proceed Recharge</button>",
                        "+<div>Recharge Successful</div>",
                    ]
                ),
            }
        ]

        contract = extract_contract_static(diff_files)

        self.assertEqual(contract.setup_steps, [])
        self.assertIn("interaction_hint_high:select amount", contract.extraction_notes)
        self.assertEqual(contract.confidence, "medium")
        self.assertIn("setup_hint_ungrounded_confidence_capped", contract.extraction_notes)

    def test_extract_contract_static_limits_required_targets_to_interactive_lines(self):
        diff_files = [
            {
                "path": "src/app/security/page.tsx",
                "status": "modified",
                "patch": '\n'.join(
                    [
                        '+<div>Security Check</div>',
                        '+<button>Continue</button>',
                    ]
                ),
            }
        ]

        contract = extract_contract_static(diff_files)

        self.assertEqual([target.label for target in contract.targets], ["Continue"])

    def test_preflight_rejects_missing_prerequisite_step_from_contract_hint(self):
        contract = types.SimpleNamespace(
            start_route="/settings",
            targets=[types.SimpleNamespace(label="Proceed Recharge", required=True)],
            setup_steps=[
                types.SimpleNamespace(
                    label="₹2000",
                    selector="",
                    required=True,
                    kind="amount",
                )
            ],
            terminal=types.SimpleNamespace(value="done"),
            extraction_notes=["interaction_hint_high:select amount"],
        )
        steps = [
            {"action": "goto", "url": "/settings"},
            {"action": "click", "label": "Proceed Recharge", "validation_condition": {"type": "text_present", "value": "done"}},
            {"action": "assert_terminal", "condition": {"value": "done"}},
        ]

        result = preflight_gate(steps, contract)

        self.assertFalse(result.passed)
        self.assertTrue(
            any(
                "Required setup step missing before CTA: '₹2000'" in err
                for err in result.errors
            ),
            result.errors,
        )

    def test_preflight_accepts_amount_chip_before_cta(self):
        contract = types.SimpleNamespace(
            start_route="/settings",
            targets=[types.SimpleNamespace(label="Proceed Recharge", required=True)],
            setup_steps=[
                types.SimpleNamespace(
                    label="₹2000",
                    selector="",
                    required=True,
                    kind="amount",
                )
            ],
            terminal=types.SimpleNamespace(value="done"),
            extraction_notes=["interaction_hint_high:select amount"],
        )
        steps = [
            {"action": "goto", "url": "/settings"},
            {"action": "click", "label": "₹2000", "validation_condition": {"type": "element_present", "value": "Proceed Recharge"}},
            {"action": "click", "label": "Proceed Recharge", "validation_condition": {"type": "text_present", "value": "done"}},
            {"action": "assert_terminal", "condition": {"value": "done"}},
        ]

        result = preflight_gate(steps, contract)

        self.assertTrue(result.passed, result.errors)

    def test_preflight_ungrounded_hint_is_warning_when_chip_present(self):
        contract = types.SimpleNamespace(
            start_route="/settings",
            targets=[types.SimpleNamespace(label="Proceed Recharge", required=True)],
            setup_steps=[],
            terminal=types.SimpleNamespace(value="done"),
            extraction_notes=["interaction_hint_high:select amount"],
        )
        steps = [
            {"action": "goto", "url": "/settings"},
            {"action": "click", "label": "₹2000", "validation_condition": {"type": "element_present", "value": "Proceed Recharge"}},
            {"action": "click", "label": "Proceed Recharge", "validation_condition": {"type": "text_present", "value": "done"}},
            {"action": "assert_terminal", "condition": {"value": "done"}},
        ]

        result = preflight_gate(steps, contract)

        self.assertTrue(result.passed, result.errors)
        self.assertTrue(any("select amount" in w for w in result.warnings), result.warnings)

    def test_synthesize_inserts_setup_chip_before_cta(self):
        contract = DemoContract(
            start_route="/settings",
            targets=[TargetRef(label="Recharge Now")],
            terminal=TerminalCondition(type="text_present", value="Recharge Successful"),
            setup_steps=[TargetRef(label="₹2000", kind="amount")],
            confidence="high",
        )
        extraction = {
            "start_route": "/settings",
            "click_labels": ["Recharge Now"],
        }

        steps = _synthesize_click_steps(extraction, contract, "/settings")
        steps = _inject_sequential_click_validations(steps)
        clicks = [s for s in steps if s.get("action") == "click"]

        self.assertEqual(
            [s.get("label") for s in clicks[:2]],
            ["₹2000", "Recharge Now"],
        )
        self.assertEqual(
            clicks[0].get("validation_condition"),
            {"type": "element_present", "value": "Recharge Now"},
        )
        result = preflight_gate(steps, contract)
        self.assertTrue(result.passed, result.errors)

    def test_preflight_accepts_nav_then_chip_then_cta(self):
        contract = DemoContract(
            start_route="/",
            targets=[
                TargetRef(label="Settings", kind="nav"),
                TargetRef(label="Recharge Now"),
                TargetRef(label="Proceed Recharge"),
            ],
            terminal=TerminalCondition(type="text_present", value="Recharge Successful"),
            setup_steps=[TargetRef(label="₹2000", kind="amount")],
            confidence="high",
        )
        steps = [
            {"action": "goto", "url": "/"},
            {"action": "click", "label": "Settings", "kind": "nav", "validation_condition": {"type": "element_present", "value": "₹2000"}},
            {"action": "click", "label": "₹2000", "kind": "amount", "validation_condition": {"type": "element_present", "value": "Recharge Now"}},
            {"action": "click", "label": "Recharge Now", "validation_condition": {"type": "element_present", "value": "Proceed Recharge"}},
            {"action": "click", "label": "Proceed Recharge", "validation_condition": {"type": "text_present", "value": "Recharge Successful"}},
            {"action": "assert_terminal", "condition": {"type": "text_present", "value": "Recharge Successful"}},
        ]
        result = preflight_gate(steps, contract)
        self.assertTrue(result.passed, result.errors)

    def test_preflight_rejects_chip_before_settings(self):
        contract = DemoContract(
            start_route="/",
            targets=[
                TargetRef(label="Settings", kind="nav"),
                TargetRef(label="Recharge Now"),
            ],
            terminal=TerminalCondition(type="text_present", value="done"),
            setup_steps=[TargetRef(label="₹2000", kind="amount")],
        )
        steps = [
            {"action": "goto", "url": "/"},
            {"action": "click", "label": "₹2000", "validation_condition": {"type": "element_present", "value": "Recharge Now"}},
            {"action": "click", "label": "Settings", "kind": "nav", "validation_condition": {"type": "element_present", "value": "₹2000"}},
            {"action": "click", "label": "Recharge Now", "validation_condition": {"type": "text_present", "value": "done"}},
            {"action": "assert_terminal", "condition": {"type": "text_present", "value": "done"}},
        ]
        result = preflight_gate(steps, contract)
        self.assertFalse(result.passed)
        self.assertTrue(
            any("must follow navigation 'Settings'" in err for err in result.errors),
            result.errors,
        )

    def test_insert_places_chip_after_settings_before_recharge(self):
        contract = DemoContract(
            start_route="/",
            targets=[
                TargetRef(label="Settings", kind="nav"),
                TargetRef(label="Recharge Now"),
            ],
            terminal=TerminalCondition(type="text_present", value="done"),
            setup_steps=[TargetRef(label="₹2000", kind="amount")],
        )
        steps = [
            {"action": "goto", "url": "/"},
            {"action": "click", "label": "Settings", "kind": "nav"},
            {"action": "click", "label": "Recharge Now"},
        ]
        out = _insert_missing_setup_clicks(steps, contract)
        clicks = [s.get("label") for s in out if s.get("action") == "click"]
        self.assertEqual(clicks, ["Settings", "₹2000", "Recharge Now"])

    def test_insert_missing_setup_clicks_on_cta_only_plan(self):
        contract = DemoContract(
            start_route="/settings",
            targets=[TargetRef(label="Proceed Recharge")],
            terminal=TerminalCondition(type="text_present", value="done"),
            setup_steps=[TargetRef(label="₹2000", kind="amount")],
        )
        steps = [
            {"action": "goto", "url": "/settings"},
            {"action": "click", "label": "Proceed Recharge", "validation_condition": {"type": "text_present", "value": "done"}},
            {"action": "assert_terminal", "condition": {"type": "text_present", "value": "done"}},
        ]

        out = _insert_missing_setup_clicks(steps, contract)
        clicks = [s.get("label") for s in out if s.get("action") == "click"]
        self.assertEqual(clicks[:2], ["₹2000", "Proceed Recharge"])

    def test_preflight_ungrounded_hint_without_chip_is_warning(self):
        contract = types.SimpleNamespace(
            start_route="/settings",
            targets=[types.SimpleNamespace(label="Proceed Recharge", required=True)],
            setup_steps=[],
            terminal=types.SimpleNamespace(value="done"),
            extraction_notes=["interaction_hint_high:select amount"],
        )
        steps = [
            {"action": "goto", "url": "/settings"},
            {"action": "click", "label": "Proceed Recharge", "validation_condition": {"type": "text_present", "value": "done"}},
            {"action": "assert_terminal", "condition": {"value": "done"}},
        ]

        result = preflight_gate(steps, contract)

        self.assertTrue(result.passed, result.errors)
        self.assertIn(
            "Missing prerequisite setup step implied by contract hint: 'select amount'",
            result.warnings,
        )

    def test_preflight_warns_for_weak_interaction_hint_instead_of_blocking(self):
        contract = types.SimpleNamespace(
            start_route="/settings",
            targets=[types.SimpleNamespace(label="Proceed Recharge", required=True)],
            terminal=types.SimpleNamespace(value="done"),
            extraction_notes=["interaction_hint_low:choose option"],
        )
        steps = [
            {"action": "goto", "url": "/settings"},
            {"action": "click", "label": "Proceed Recharge", "validation_condition": {"type": "text_present", "value": "done"}},
            {"action": "assert_terminal", "condition": {"value": "done"}},
        ]

        result = preflight_gate(steps, contract)

        self.assertTrue(result.passed)
        self.assertIn(
            "Weak prerequisite setup hint not covered explicitly: 'choose option'",
            result.warnings,
        )

    def test_sanitize_terminal_assertions_drops_ungrounded_terminal_when_contract_is_silent(self):
        steps = [
            {"action": "goto", "url": "/settings"},
            {"action": "click", "label": "Start security flow"},
            {"action": "assert_terminal", "expected_element": "new-feature"},
        ]

        result = _sanitize_terminal_assertions(
            steps,
            contract=types.SimpleNamespace(terminal=None),
            real_data_testids=[{"testid": "security-flow-modal"}],
            diff_text='{"path":"src/app/settings/page.tsx","patch":"+<button>Start security flow</button>"}',
        )

        self.assertEqual(
            [step.get("action") for step in result],
            ["goto", "click"],
        )

    def test_preflight_rejects_last_click_before_terminal_without_validation(self):
        contract = types.SimpleNamespace(
            start_route="/settings",
            targets=[types.SimpleNamespace(label="Proceed Recharge", required=True)],
            terminal=types.SimpleNamespace(value="done"),
            extraction_notes=[],
        )
        steps = [
            {"action": "goto", "url": "/settings"},
            {"action": "click", "label": "Proceed Recharge"},
            {"action": "assert_terminal", "condition": {"value": "done"}},
        ]

        result = preflight_gate(steps, contract)

        self.assertFalse(result.passed)
        self.assertTrue(
            any(
                "validation" in err.lower() or "proof condition" in err.lower()
                for err in result.errors
            ),
            result.errors,
        )

    def test_should_keep_click_screenshots_only_for_success(self):
        self.assertTrue(_should_keep_click_screenshots({"outcome": "success"}))
        self.assertFalse(
            _should_keep_click_screenshots({"outcome": "unvalidated", "state_changed": True})
        )
        self.assertFalse(
            _should_keep_click_screenshots({"outcome": "unvalidated", "state_changed": False})
        )
        self.assertFalse(_should_keep_click_screenshots({"outcome": "click_failed"}))

    def test_scroll_to_find_uses_incremental_scroll_until_target_found(self):
        cli = _FakeCLI()
        call_count = {"find_ref": 0}

        def fake_find_ref(intent):
            cli.calls.append(("find_ref", intent))
            call_count["find_ref"] += 1
            return "@e42" if call_count["find_ref"] == 3 else ""

        cli.find_ref = fake_find_ref

        result = _scroll_to_find(cli, intent="Proceed Recharge")

        self.assertEqual(result, "@e42")
        self.assertEqual(
            cli.calls,
            [
                ("find_role_ref", "button", "Proceed Recharge"),
                ("find_role_ref", "link", "Proceed Recharge"),
                ("find_label_ref", "Proceed Recharge"),
                ("find_ref", "Proceed Recharge"),
                ("scroll", "down", 400),
                ("wait_for_load_state", "networkidle", 1),
                ("find_role_ref", "button", "Proceed Recharge"),
                ("find_role_ref", "link", "Proceed Recharge"),
                ("find_label_ref", "Proceed Recharge"),
                ("find_ref", "Proceed Recharge"),
                ("scroll", "down", 400),
                ("wait_for_load_state", "networkidle", 1),
                ("find_role_ref", "button", "Proceed Recharge"),
                ("find_role_ref", "link", "Proceed Recharge"),
                ("find_label_ref", "Proceed Recharge"),
                ("find_ref", "Proceed Recharge"),
                ("scroll_into_view", "@e42"),
            ],
        )

    def test_assert_terminal_infers_element_present_from_expected_element(self):
        cli = _FakeCLI(found_testid_ref="@e99")

        result = _assert_ab_terminal_condition(
            cli,
            condition={},
            expected_element="security-flow-modal",
            extract_snapshot=lambda **kwargs: {
                "interactive_elements": [],
                "context_elements": [],
                "snapshot_text": "",
            },
        )

        self.assertTrue(result["found"])
        self.assertIn(
            result["source"],
            {
                "find_testid",
                "find_testid_visible",
                "wait_for_element_present",
            },
        )
        self.assertIn(result["actual"], {"@e99", "security-flow-modal"})
        self.assertIn(("find_testid_ref", "security-flow-modal"), cli.calls)

    def test_discard_step_screenshots_removes_files_and_clears_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            before_path = Path(tmpdir) / "before.png"
            after_path = Path(tmpdir) / "after.png"
            before_path.write_text("before", encoding="utf-8")
            after_path.write_text("after", encoding="utf-8")
            step_result = {
                "before_screenshot": str(before_path),
                "after_screenshot": str(after_path),
            }

            _discard_step_screenshots(step_result)

            self.assertFalse(before_path.exists())
            self.assertFalse(after_path.exists())
            self.assertEqual(step_result["before_screenshot"], "")
            self.assertEqual(step_result["after_screenshot"], "")

    def test_collect_ab_failure_diagnostics_counts_console_errors_and_network(self):
        cli = _FakeCLI()
        cli.console_entries = ["warn one", "warn two"]
        cli.page_error_entries = ["TypeError"]
        cli.network_entries = [
            {"url": "/api/a", "status": 200},
            {"url": "/api/b", "status": 500},
            {"url": "/api/c", "error": "timeout"},
        ]

        diagnostics = _collect_ab_failure_diagnostics(cli)

        self.assertEqual(
            diagnostics,
            {
                "console_messages": ["warn one", "warn two"],
                "page_errors": ["TypeError"],
                "network_request_count": 3,
                "network_error_count": 2,
                "network_requests_preview": cli.network_entries,
            },
        )



    def test_assert_terminal_empty_defaults_to_not_found(self):
        cli = _FakeCLI()
        result = _assert_ab_terminal_condition(
            cli,
            condition={},
            expected_element="",
            extract_snapshot=lambda **kwargs: {
                "interactive_elements": [],
                "context_elements": [],
                "snapshot_text": "",
            },
        )
        self.assertFalse(result["found"])
        self.assertEqual(result["source"], "missing_terminal_condition")

    def test_resolve_terminal_expectation_from_expected_url(self):
        condition, expected = _resolve_terminal_expectation(
            {"action": "assert_terminal", "expected_url": "/settings"}
        )
        self.assertEqual(condition["type"], "url_match")
        self.assertEqual(condition["value"], "/settings")

    def test_resolve_terminal_expectation_from_expected_text(self):
        condition, expected = _resolve_terminal_expectation(
            {"action": "assert_terminal", "expected_text": "Success"}
        )
        self.assertEqual(condition["type"], "text_present")
        self.assertEqual(condition["value"], "Success")

    def test_assert_terminal_url_match_success(self):
        cli = _FakeCLI()
        cli.current_url = "https://app.example.com/settings"
        condition, expected = _resolve_terminal_expectation(
            {"action": "assert_terminal", "expected_url": "/settings"}
        )
        result = _assert_ab_terminal_condition(
            cli,
            condition=condition,
            expected_element=expected,
            extract_snapshot=lambda **kwargs: {},
        )
        self.assertTrue(result["found"])
        self.assertIn(result["source"], {"wait_for_url", "url_match_substring"})

    def test_assert_terminal_text_fail_is_not_success(self):
        cli = _FakeCLI(fail_text=True)
        condition, expected = _resolve_terminal_expectation(
            {"action": "assert_terminal", "expected_text": "Missing"}
        )
        result = _assert_ab_terminal_condition(
            cli,
            condition=condition,
            expected_element=expected,
            extract_snapshot=lambda **kwargs: {},
        )
        self.assertFalse(result["found"])
        self.assertEqual(result["source"], "text_present_failed")

    def test_assert_terminal_expected_url_without_element_does_not_auto_pass(self):
        """Regression: empty expected_element must not leave found=True."""
        condition, expected = _resolve_terminal_expectation(
            {"action": "assert_terminal", "expected_url": "/done"}
        )
        self.assertEqual(condition["type"], "url_match")
        cli = _FakeCLI(fail_url=True)
        cli.current_url = "https://example.com/other"
        result = _assert_ab_terminal_condition(
            cli,
            condition=condition,
            expected_element=expected,
            extract_snapshot=lambda **kwargs: {},
        )
        self.assertFalse(result["found"])

    def test_assert_terminal_snapshot_substring_is_not_success(self):
        """element_present must not pass on incidental snapshot text."""
        cli = _FakeCLI()
        snapshot = {
            "interactive_elements": [{"name": "Recharge Successful"}],
            "context_elements": [],
            "snapshot_text": "Recharge Successful modal is visible",
        }
        with patch(
            "app.execution.step_runner._wait_for_ab_element_present",
            return_value=False,
        ):
            result = _assert_ab_terminal_condition(
                cli,
                condition={"type": "element_present", "value": "Recharge Successful"},
                expected_element="Recharge Successful",
                extract_snapshot=lambda **kwargs: snapshot,
            )
        self.assertFalse(result["found"])
        self.assertEqual(result["source"], "element_present_failed")
        self.assertTrue(result.get("snapshot_substring_hit"))

    def test_assert_terminal_missing_find_element_does_not_auto_pass(self):
        """A CLI without find_element must still fail closed, not snapshot-pass."""

        class _CliWithoutFindElement:
            def __init__(self):
                self.calls = []

            def find_testid_ref(self, testid):
                self.calls.append(("find_testid_ref", testid))
                return ""

            def find_ref(self, intent):
                self.calls.append(("find_ref", intent))
                return ""

            def is_visible(self, target):
                self.calls.append(("is_visible", target))
                return False

            def get_count(self, selector):
                self.calls.append(("get_count", selector))
                return 0

            def wait(self, ms):
                self.calls.append(("wait", ms))

        cli = _CliWithoutFindElement()
        self.assertFalse(hasattr(cli, "find_element"))
        snapshot = {
            "interactive_elements": [{"name": "security-flow-modal"}],
            "context_elements": [{"name": "security-flow-modal"}],
            "snapshot_text": "security-flow-modal",
        }
        with patch(
            "app.execution.step_runner._wait_for_ab_element_present",
            return_value=False,
        ):
            result = _assert_ab_terminal_condition(
                cli,
                condition={"type": "element_present", "value": "security-flow-modal"},
                expected_element="security-flow-modal",
                extract_snapshot=lambda **kwargs: snapshot,
            )
        self.assertFalse(result["found"])
        self.assertEqual(result["source"], "element_present_failed")

    def test_assert_terminal_find_element_visible_is_success(self):
        cli = _FakeCLI()
        cli.find_element = lambda selector: (
            cli.calls.append(("find_element", selector)) or "@e7"
        )
        with patch(
            "app.execution.step_runner._wait_for_ab_element_present",
            return_value=False,
        ):
            result = _assert_ab_terminal_condition(
                cli,
                condition={"type": "element_present", "value": "modal"},
                expected_element="modal",
                extract_snapshot=lambda **kwargs: {
                    "interactive_elements": [],
                    "context_elements": [],
                    "snapshot_text": "",
                },
            )
        self.assertTrue(result["found"])
        self.assertEqual(result["source"], "find_element_visible")
        self.assertEqual(result["actual"], "@e7")

    def test_agent_browser_cli_exposes_find_element(self):
        from app.browser.agent_browser_cli import AgentBrowserCLI

        self.assertTrue(callable(getattr(AgentBrowserCLI, "find_element", None)))


if __name__ == "__main__":
    unittest.main()
