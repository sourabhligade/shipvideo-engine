"""Priority #3 empty re-anchor hard-fail; #4 runtime goto uses real_routes."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from app.policy.selector_validator import validate_step_against_dom
from app.execution.step_runner import (
    _allowed_routes_from_objective,
    _merge_allowed_routes_into_dom_ctx,
)


class Priority4RuntimeGotoRoutesTests(unittest.TestCase):
    def test_goto_rejected_without_allowed_routes(self):
        ok, reason = validate_step_against_dom(
            {"action": "goto", "url": "/settings"},
            {"routes": ["/", "/home"]},
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "route_not_in_dom:/settings")

    def test_goto_allowed_via_generation_routes(self):
        ok, reason = validate_step_against_dom(
            {"action": "goto", "url": "/settings"},
            {"routes": ["/", "/home"]},
            allowed_routes={"/", "/settings", "/billing"},
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_goto_still_requires_url(self):
        ok, reason = validate_step_against_dom(
            {"action": "goto", "url": ""},
            {"routes": ["/"]},
            allowed_routes={"/settings"},
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "missing_goto_url")

    def test_allowed_routes_from_objective(self):
        routes = _allowed_routes_from_objective(
            {
                "generation_context": {
                    "real_routes": ["/pricing", "/settings"],
                    "start_route": "/billing",
                    "start_route_candidates": ["/app"],
                }
            }
        )
        self.assertIn("/pricing", routes)
        self.assertIn("/settings", routes)
        self.assertIn("/billing", routes)
        self.assertIn("/app", routes)
        self.assertIn("/", routes)

    def test_merge_routes_into_dom_ctx(self):
        merged = _merge_allowed_routes_into_dom_ctx(
            {"routes": ["/"], "buttons": []},
            {"/settings", "/"},
        )
        self.assertEqual(set(merged["routes"]), {"/", "/settings"})
        self.assertEqual(merged["buttons"], [])


class Priority3ReanchorFailTests(unittest.TestCase):
    def test_empty_reanchor_returns_navigation_reanchor_failed(self):
        from app.execution import step_runner as sr

        class FakePage:
            def goto(self, *a, **k):
                return None

        class FakeBrowser:
            def new_page(self, **k):
                return FakePage()

            def close(self):
                return None

        class FakeChromium:
            def launch(self, **k):
                return FakeBrowser()

        class FakePW:
            def __init__(self):
                self.chromium = FakeChromium()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        # Sequence: after each step, detect major change True; regenerate returns empty
        states = [
            type("S", (), {"path": "/", "fingerprint": None, "dom_hash": "a"})(),
            type("S", (), {"path": "/next", "fingerprint": None, "dom_hash": "b"})(),
        ]
        call = {"i": 0}

        def fake_capture(page):
            # before step then after step
            idx = min(call["i"], len(states) - 1)
            s = states[idx]
            call["i"] += 1
            return s

        with patch.object(sr, "sync_playwright", return_value=FakePW()), \
             patch.object(sr, "wait_stable_after_navigation"), \
             patch.object(sr, "extract_dom_context", return_value={"routes": ["/"], "buttons": []}), \
             patch.object(sr, "capture_state", side_effect=fake_capture), \
             patch.object(sr, "detect_major_change", return_value=True), \
             patch.object(sr, "_execute_one", return_value=(True, 1, None)), \
             patch.object(sr, "validate_step_against_dom", return_value=(True, "ok")), \
             patch.object(sr, "regenerate_with_feedback", return_value=([], [{"attempt": 1, "status": "empty_steps"}])), \
             patch.object(sr, "_log", lambda *a, **k: None):
            result = sr.run_stepwise(
                preview_url="https://example.com",
                initial_steps=[{"action": "click", "label": "Go"}],
                objective={"generation_context": {"real_routes": ["/settings"]}},
                screenshot_dir=__import__("pathlib").Path("/tmp/sv-reanchor-test"),
                max_retries_per_failure=1,
            )

        self.assertFalse(result["success"])
        self.assertEqual(result["failure_reason"], "navigation_reanchor_failed")


if __name__ == "__main__":
    unittest.main()
