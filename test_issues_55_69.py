"""Regression tests for closed-but-unmerged issues #55–#69."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from app.browser.ref_selector import select_ref
from app.execution.step_runner import (
    AB_NETWORKIDLE_TIMEOUT_S,
    _assert_ab_terminal_condition,
    _evaluate_click_validation,
    _execute_one,
    _infer_runtime_validation,
    _settle_ab_page,
    _terminal_match_in_snapshot,
    _wait_for_ab_element_present,
)
from app.policy.selector_validator import validate_step_against_dom
from app.steps.step_normalizer import _normalize_route_key, validate_against_dom


def _snap(*, url: str, text: str = "", names: list[str] | None = None) -> dict:
    elements = [
        {"ref": f"@e{i}", "role": "button", "name": name}
        for i, name in enumerate(names or [])
    ]
    return {
        "current_url": url,
        "snapshot_text": text,
        "interactive_elements": elements,
        "context_elements": [],
    }


class _WaitCLI:
    def __init__(self, *, count: int = 0, visible: bool = True, testid_ref: str = ""):
        self.calls = []
        self.count = count
        self.visible = visible
        self.testid_ref = testid_ref

    def find_testid_ref(self, testid):
        self.calls.append(("find_testid_ref", testid))
        return self.testid_ref

    def is_visible(self, target):
        self.calls.append(("is_visible", target))
        return self.visible

    def get_count(self, selector):
        self.calls.append(("get_count", selector))
        return self.count

    def find_ref(self, intent):
        self.calls.append(("find_ref", intent))
        return ""

    def wait(self, ms):
        self.calls.append(("wait", ms))


class Issue55StickyPresenceTests(unittest.TestCase):
    def test_same_page_element_present_still_passes(self):
        result = _evaluate_click_validation(
            step={
                "action": "click",
                "label": "₹2000",
                "validation_condition": {"type": "element_present", "value": "Recharge Now"},
            },
            snap_before=_snap(url="https://app.example/settings", names=["₹2000", "Recharge Now"]),
            snap_after=_snap(url="https://app.example/settings", names=["₹2000", "Recharge Now"]),
        )
        self.assertTrue(result["passed"])

    def test_rising_edge_still_required_for_text_present(self):
        result = _evaluate_click_validation(
            step={
                "action": "click",
                "label": "Save",
                "validation_condition": {"type": "text_present", "value": "Saved"},
            },
            snap_before=_snap(url="https://app.example/settings", text="Saved already"),
            snap_after=_snap(url="https://app.example/settings", text="Saved already"),
        )
        self.assertFalse(result["passed"])

    def test_text_present_passes_on_rising_edge(self):
        result = _evaluate_click_validation(
            step={
                "action": "click",
                "label": "Save",
                "validation_condition": {"type": "text_present", "value": "Saved"},
            },
            snap_before=_snap(url="https://app.example/settings", text="Settings"),
            snap_after=_snap(url="https://app.example/settings", text="Settings Saved"),
        )
        self.assertTrue(result["passed"])


class Issue57SnapshotTextMatchTests(unittest.TestCase):
    def test_terminal_match_reads_snapshot_text(self):
        self.assertTrue(
            _terminal_match_in_snapshot(
                {
                    "interactive_elements": [],
                    "context_elements": [],
                    "snapshot_text": "Recharge Successful",
                },
                "Recharge Successful",
            )
        )

    def test_terminal_match_empty_expected_is_false(self):
        self.assertFalse(
            _terminal_match_in_snapshot({"snapshot_text": "hello"}, "")
        )


class Issue58AriaIdAmbiguityTests(unittest.TestCase):
    def test_aria_multi_match_is_ambiguous(self):
        snapshot = {
            "interactive_elements": [
                {"ref": "@e1", "role": "button", "name": "One", "aria_label": "edit"},
                {"ref": "@e2", "role": "button", "name": "Two", "aria_label": "edit"},
            ]
        }
        result = select_ref("edit", snapshot)
        self.assertEqual(result["selection_reason"], "ambiguous")
        self.assertEqual(result["chosen_ref"], "")

    def test_id_multi_match_is_ambiguous(self):
        snapshot = {
            "interactive_elements": [
                {"ref": "@e1", "role": "button", "name": "One", "element_id": "save"},
                {"ref": "@e2", "role": "button", "name": "Two", "element_id": "save"},
            ]
        }
        result = select_ref("save", snapshot)
        self.assertEqual(result["selection_reason"], "ambiguous")
        self.assertEqual(result["chosen_ref"], "")


class Issue60PlaywrightProofTests(unittest.TestCase):
    def test_click_without_validation_fail_closes(self):
        page = MagicMock()
        loc = MagicMock()
        loc.count.return_value = 1
        page.locator.return_value = loc
        ok, _, err = _execute_one(
            page,
            "https://ex.com",
            {"action": "click", "selector": "[data-testid='save']"},
            Path("/tmp"),
            1,
        )
        self.assertFalse(ok)
        self.assertEqual(err, "missing_validation_condition")
        loc.first.click.assert_not_called()

    def test_click_with_validation_clicks(self):
        page = MagicMock()
        loc = MagicMock()
        loc.count.return_value = 1
        loc.first = loc
        page.locator.return_value = loc
        ok, _, err = _execute_one(
            page,
            "https://ex.com",
            {
                "action": "click",
                "selector": "[data-testid='save']",
                "validation_condition": {"type": "text_present", "value": "Saved"},
            },
            Path("/tmp"),
            1,
        )
        self.assertTrue(ok)
        self.assertIsNone(err)
        loc.first.click.assert_called_once()


class Issue61ForceRetryTests(unittest.TestCase):
    def test_force_skips_already_ran_check(self):
        src = Path("app/webhook.py").read_text()
        self.assertIn(
            "if not force and check_already_ran(repo_full_name, pr_number, commit_sha):",
            src,
        )


class Issue63NetworkidleTimeoutTests(unittest.TestCase):
    def test_settle_uses_full_networkidle_timeout(self):
        src = Path("app/execution/step_runner.py").read_text()
        self.assertNotIn("min(AB_NETWORKIDLE_TIMEOUT_S, 1)", src)
        self.assertGreaterEqual(AB_NETWORKIDLE_TIMEOUT_S, 8)

    def test_settle_passes_constant_to_cli(self):
        cli = MagicMock()
        _settle_ab_page(cli)
        cli.wait_for_load_state.assert_any_call(
            "networkidle",
            timeout=AB_NETWORKIDLE_TIMEOUT_S,
        )


class Issue64InferredUrlMatchTests(unittest.TestCase):
    def test_unplanned_redirect_does_not_validate(self):
        result = _infer_runtime_validation(
            steps=[
                {"action": "click", "label": "Save"},
                {
                    "action": "assert_terminal",
                    "expected_url": "/settings",
                    "condition": {"type": "url_match", "value": "/settings"},
                },
            ],
            step_index=0,
            snap_before=_snap(url="https://app.example/settings"),
            snap_after=_snap(url="https://app.example/login"),
            mode="deterministic",
        )
        self.assertFalse(result["passed"])

    def test_planned_url_change_validates(self):
        result = _infer_runtime_validation(
            steps=[
                {"action": "click", "label": "Settings"},
                {
                    "action": "assert_terminal",
                    "expected_url": "/settings",
                    "condition": {"type": "url_match", "value": "/settings"},
                },
            ],
            step_index=0,
            snap_before=_snap(url="https://app.example/"),
            snap_after=_snap(url="https://app.example/settings"),
            mode="deterministic",
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["source"], "runtime_inferred")


class Issue66TitlePlanningTests(unittest.TestCase):
    def test_title_only_button_is_accepted_click_label(self):
        accepted = validate_against_dom(
            [{"action": "click", "label": "Open recharge"}],
            {
                "routes": ["/"],
                "buttons": [{"text": "", "title": "Open recharge", "selector": "button.tip"}],
            },
        )
        self.assertEqual(len(accepted), 1)


class Issue67RouteNormalizationTests(unittest.TestCase):
    def test_normalize_strips_slash_and_abs_url(self):
        self.assertEqual(_normalize_route_key("/settings/"), "/settings")
        self.assertEqual(_normalize_route_key("https://demo.example/settings"), "/settings")
        self.assertEqual(_normalize_route_key("settings"), "/settings")

    def test_goto_trailing_slash_matches_crawled_route(self):
        accepted = validate_against_dom(
            [{"action": "goto", "url": "/settings/"}],
            {"routes": ["/settings"]},
        )
        self.assertEqual(len(accepted), 1)

    def test_runtime_goto_accepts_trailing_slash(self):
        ok, reason = validate_step_against_dom(
            {"action": "goto", "url": "/settings/"},
            {"routes": ["/settings"]},
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_runtime_goto_accepts_absolute_url_path(self):
        ok, reason = validate_step_against_dom(
            {"action": "goto", "url": "https://demo.example/settings"},
            {"routes": ["/settings"]},
        )
        self.assertTrue(ok)


class Issue68SuccessfulCrawlRoutesTests(unittest.TestCase):
    def test_crawl_routes_come_from_snapshots(self):
        src = Path("app/steps/dom_crawler.py").read_text()
        self.assertIn("crawled_routes = sorted(route_snapshots.keys())", src)


class Issue69HiddenCountIsNotPresentTests(unittest.TestCase):
    def test_hidden_count_does_not_count_as_present(self):
        cli = _WaitCLI(count=2, visible=False)
        found = _wait_for_ab_element_present(cli, "modal", timeout_s=1)
        self.assertFalse(found)
        self.assertTrue(any(call[0] == "is_visible" for call in cli.calls))

    def test_visible_count_counts_as_present(self):
        cli = _WaitCLI(count=1, visible=True)
        found = _wait_for_ab_element_present(cli, "modal", timeout_s=1)
        self.assertTrue(found)


class Issue56TerminalEmptyStillFailClosed(unittest.TestCase):
    def test_empty_terminal_is_not_found(self):
        result = _assert_ab_terminal_condition(
            MagicMock(),
            condition={},
            expected_element="",
            extract_snapshot=lambda **kwargs: {},
        )
        self.assertFalse(result["found"])
        self.assertEqual(result["source"], "missing_terminal_condition")


class Issue62FfmpegTimeoutStillPresent(unittest.TestCase):
    def test_render_paths_pass_timeout(self):
        for path in (
            Path("app/render.py"),
            Path("app/product/video.py"),
            Path("app/recorder/video_processor.py"),
            Path("app/product/audio_timing.py"),
        ):
            src = path.read_text()
            self.assertIn("timeout=", src)


if __name__ == "__main__":
    unittest.main()
