"""PR6: Agent Browser evidence wrappers and fail-closed proof waits."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.browser.agent_browser_cli import AgentBrowserCLI, AgentBrowserError
from app.browser.agent_browser_types import CommandResult
from app.execution.ab_diagnostics import attach_ab_failure_diagnostics
from app.execution.ab_settle import wait_for_ab_element_present
from app.execution.ab_target import ensure_ab_target_actionable, resolve_ab_click_target, unique_visible_role_ref
from app.execution.ab_terminal import assert_ab_terminal_condition


def _ok(data=None, stdout="") -> CommandResult:
    return CommandResult(
        success=True,
        stdout=stdout or "{}",
        stderr="",
        exit_code=0,
        data=data or {},
    )


class _RecordingCLI(AgentBrowserCLI):
    def __init__(self):
        super().__init__(session="evidence")
        self.cmds: list[tuple] = []
        self.payloads: dict[tuple, CommandResult] = {}

    def _run(self, *args, json_output=True, timeout=60):
        self.cmds.append((tuple(args), json_output, timeout))
        key = tuple(args)
        if key in self.payloads:
            return self.payloads[key]
        return _ok()


class CliArgvTests(unittest.TestCase):
    def test_wait_for_function_argv(self):
        cli = _RecordingCLI()
        cli.wait_for_function("window.ready === true", timeout=8)
        self.assertEqual(cli.cmds[0][0], ("wait", "--fn", "window.ready === true"))
        self.assertEqual(cli.cmds[0][2], 8)

    def test_get_box_argv_and_parse(self):
        cli = _RecordingCLI()
        cli.payloads[("get", "box", "@e3")] = _ok(
            {"x": 10, "y": 20, "width": 40, "height": 12}
        )
        box = cli.get_box("@e3")
        self.assertEqual(cli.cmds[0][0], ("get", "box", "@e3"))
        self.assertEqual(box, {"x": 10.0, "y": 20.0, "width": 40.0, "height": 12.0})

    def test_diff_snapshot_argv(self):
        cli = _RecordingCLI()
        cli.payloads[("diff", "snapshot", "-c")] = _ok({"changed": True, "added": ["Pay"]})
        diff = cli.diff_snapshot()
        self.assertEqual(cli.cmds[0][0], ("diff", "snapshot", "-c"))
        self.assertEqual(diff["added"], ["Pay"])

    def test_annotated_screenshot_argv(self):
        cli = _RecordingCLI()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "fail.png"
            cli.annotated_screenshot(path)
        self.assertEqual(cli.cmds[0][0], ("screenshot", "--annotate", str(path)))
        self.assertFalse(cli.cmds[0][1])

    def test_wait_for_function_empty_raises(self):
        cli = _RecordingCLI()
        with self.assertRaises(ValueError):
            cli.wait_for_function("  ")


class GeometryFallbackTests(unittest.TestCase):
    def test_unique_visible_button_wins_over_zero_box(self):
        class _Cli:
            def get_box(self, ref):
                if ref == "@hidden":
                    return {"x": 0, "y": 0, "width": 0, "height": 0}
                return {"x": 10, "y": 10, "width": 80, "height": 24}

        ref, count = unique_visible_role_ref(
            _Cli(),
            snapshot={
                "interactive_elements": [
                    {"ref": "@hidden", "role": "button", "name": "Ghost"},
                    {"ref": "@pay", "role": "button", "name": "Pay"},
                ]
            },
            intent="Pay",
        )
        self.assertEqual(ref, "@pay")
        self.assertEqual(count, 1)

    def test_two_visible_roles_do_not_pick_list_order(self):
        class _Cli:
            def get_box(self, ref):
                return {"x": 1, "y": 1, "width": 10, "height": 10}

        ref, count = unique_visible_role_ref(
            _Cli(),
            snapshot={
                "interactive_elements": [
                    {"ref": "@a", "role": "button", "name": "Pay"},
                    {"ref": "@b", "role": "button", "name": "Pay"},
                ]
            },
            intent="Pay",
        )
        self.assertEqual(ref, "")
        self.assertEqual(count, 2)

    def test_resolve_rejects_substring_find_ref(self):
        class _Cli:
            def find_testid_ref(self, testid):
                return ""

            def find_role_ref(self, role, name, exact=False):
                return "" if exact else "@substring"

            def find_label_ref(self, intent):
                return ""

            def find_ref(self, intent):
                return "@list-order"

            def get_box(self, ref):
                return {"x": 4, "y": 4, "width": 40, "height": 16}

        result = resolve_ab_click_target(
            _Cli(),
            intent="Pay now",
            snapshot={
                "interactive_elements": [
                    {"ref": "@pay", "role": "button", "name": "Unrelated"},
                ],
                "context_elements": [],
                "current_url": "https://example.test",
                "snapshot_text": "",
            },
            mode="deterministic",
            allow_scroll_retry=False,
        )
        self.assertEqual(result["chosen_ref"], "")
        self.assertEqual(result["selection_reason"], "no_match")


class ZeroBoxActionabilityTests(unittest.TestCase):
    def test_zero_box_marks_target_not_visible(self):
        class _Cli:
            def __init__(self):
                self.calls = []

            def scroll_into_view(self, target):
                self.calls.append(("scroll_into_view", target))

            def wait_for_load_state(self, state, *, timeout):
                self.calls.append(("wait_for_load_state", state, timeout))

            def is_visible(self, target):
                return True

            def is_enabled(self, target):
                return True

            def get_box(self, target):
                self.calls.append(("get_box", target))
                return {"x": 0, "y": 0, "width": 0, "height": 0}

        result = ensure_ab_target_actionable(_Cli(), "@e9")
        self.assertFalse(result["target_visible"])
        self.assertTrue(result["target_enabled"])


class WaitFnFailClosedTests(unittest.TestCase):
    def test_text_present_fails_closed_when_wait_fn_raises(self):
        class _Cli:
            def wait_for_text(self, text, *, timeout):
                raise RuntimeError("text missing")

            def wait_for_function(self, expr, *, timeout):
                raise RuntimeError("fn missing")

        result = assert_ab_terminal_condition(
            _Cli(),
            condition={"type": "text_present", "value": "Recharge Successful"},
            expected_element="",
            extract_snapshot=lambda **kwargs: {},
        )
        self.assertFalse(result["found"])
        self.assertEqual(result["source"], "text_present_failed")

    def test_element_present_wait_fn_hit_still_needs_find(self):
        class _Cli:
            def __init__(self):
                self.fn_called = False

            def wait_for_function(self, expr, *, timeout):
                self.fn_called = True

            def find_testid_ref(self, testid):
                return ""

            def get_count(self, selector):
                return 0

            def find_ref(self, intent):
                return ""

            def wait(self, ms):
                return None

            def find_element(self, selector):
                return ""

            def is_visible(self, target):
                return False

        cli = _Cli()
        with patch("app.execution.ab_settle.time.monotonic", side_effect=[0, 0.1, 10]):
            found = wait_for_ab_element_present(cli, "Proceed Recharge", timeout_s=1)
        self.assertTrue(cli.fn_called)
        self.assertFalse(found)

        with patch(
            "app.execution.step_runner._wait_for_ab_element_present",
            return_value=False,
        ):
            result = assert_ab_terminal_condition(
                cli,
                condition={"type": "element_present", "value": "Proceed Recharge"},
                expected_element="Proceed Recharge",
                extract_snapshot=lambda **kwargs: {
                    "interactive_elements": [],
                    "context_elements": [],
                    "snapshot_text": "Proceed Recharge is mentioned in copy",
                },
            )
        self.assertFalse(result["found"])
        self.assertEqual(result["source"], "element_present_failed")
        self.assertTrue(result.get("snapshot_substring_hit"))


class FailureEvidenceTests(unittest.TestCase):
    def test_attach_includes_annotate_and_diff_keys(self):
        class _Cli:
            def console_messages(self):
                return []

            def page_errors(self):
                return []

            def network_requests(self):
                return []

            def annotated_screenshot(self, path):
                Path(path).write_bytes(b"png")

            def diff_snapshot(self, *, compact=True):
                return {"changed": True, "added": ["Recharge Successful"]}

        with tempfile.TemporaryDirectory() as td:
            step_result = {"index": 3, "before_screenshot": str(Path(td) / "shot1.png")}
            attach_ab_failure_diagnostics(_Cli(), step_result, screenshot_dir=Path(td))
            diagnostics = step_result["diagnostics"]
            self.assertTrue(diagnostics["annotated_screenshot"].endswith("fail_annotated_3.png"))
            self.assertTrue(Path(diagnostics["annotated_screenshot"]).exists())
            self.assertEqual(diagnostics["snapshot_diff"]["added"], ["Recharge Successful"])


class AgentBrowserErrorOnWaitFnTests(unittest.TestCase):
    def test_wait_for_function_propagates_cli_error(self):
        cli = _RecordingCLI()

        def boom(*args, **kwargs):
            raise AgentBrowserError("fn failed", command=["wait"], stderr="nope", exit_code=1)

        cli._run = boom
        with self.assertRaises(AgentBrowserError):
            cli.wait_for_function("false")


if __name__ == "__main__":
    unittest.main()
