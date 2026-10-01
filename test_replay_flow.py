"""PR7: replay harness 20× fixture; locator_history sidecar; no manifest rewrite."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.replay_flow import (
    MANIFEST_PATH,
    replay_fixture,
    replay_live_recharge,
    write_locator_history,
)


class TestLocatorHistoryGate(unittest.TestCase):
    def test_writes_only_after_min_pass(self):
        with tempfile.TemporaryDirectory() as td:
            history_dir = Path(td)
            locators = [{"intent": "Save", "selector": "[data-testid='save-settings']"}]
            skipped = write_locator_history(
                target="fixture_save_settings",
                locators=locators,
                passed=18,
                runs=20,
                min_pass=19,
                history_dir=history_dir,
            )
            self.assertIsNone(skipped)
            self.assertFalse((history_dir / "fixture_save_settings.json").exists())

            written = write_locator_history(
                target="fixture_save_settings",
                locators=locators,
                passed=19,
                runs=20,
                min_pass=19,
                history_dir=history_dir,
            )
            self.assertIsNotNone(written)
            payload = json.loads(written.read_text(encoding="utf-8"))
            self.assertEqual(payload["passed"], 19)
            self.assertEqual(payload["runs"], 20)
            self.assertFalse(payload["manifest_rewritten"])
            self.assertEqual(payload["locators"], locators)

    def test_never_rewrites_manifest(self):
        before = MANIFEST_PATH.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as td:
            write_locator_history(
                target="fixture_save_settings",
                locators=[{"intent": "Save"}],
                passed=20,
                runs=20,
                min_pass=19,
                history_dir=Path(td),
            )
        after = MANIFEST_PATH.read_text(encoding="utf-8")
        self.assertEqual(before, after)


class TestReplayFixtureAggregation(unittest.TestCase):
    def test_exit_ok_at_19_of_20(self):
        reports = [{"ok": True, "locators": [{"intent": "Save", "selector": "[data-testid='save-settings']", "chosen_ref": "e12"}]}] * 19
        reports.append({"ok": False, "failure_reason": "click_failed", "locators": []})
        idx = [0]

        def counting_once(**kwargs):
            i = idx[0]
            idx[0] = i + 1
            return reports[i]

        class _Srv:
            def shutdown(self):
                return None

        with tempfile.TemporaryDirectory() as td:
            summary = replay_fixture(
                runs=20,
                min_pass=19,
                history_dir=Path(td),
                run_once=counting_once,
                start_server=lambda: (_Srv(), "http://127.0.0.1:9"),
            )
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["passed"], 19)
        self.assertFalse(summary["manifest_rewritten"])
        self.assertIsNotNone(summary["locator_history"])

    def test_exit_not_ok_below_gate(self):
        def failing_once(**kwargs):
            return {"ok": False, "failure_reason": "click_failed", "locators": []}

        class _Srv:
            def shutdown(self):
                return None

        with tempfile.TemporaryDirectory() as td:
            summary = replay_fixture(
                runs=20,
                min_pass=19,
                history_dir=Path(td),
                run_once=failing_once,
                start_server=lambda: (_Srv(), "http://127.0.0.1:9"),
            )
        self.assertFalse(summary["ok"])
        self.assertEqual(summary["passed"], 0)
        self.assertIsNone(summary["locator_history"])


class TestLiveRechargeOptIn(unittest.TestCase):
    def test_refuses_without_confirm(self):
        summary = replay_live_recharge(confirm_live=False)
        self.assertTrue(summary["skipped"])
        self.assertFalse(summary["ok"])
        self.assertFalse(summary["manifest_rewritten"])

    def test_confirm_still_does_not_hit_vercel(self):
        summary = replay_live_recharge(confirm_live=False)
        self.assertTrue(summary["skipped"])
        self.assertIn("opt-in", summary["reason"])

    def test_confirm_prepares_plan_without_executing(self):
        summary = replay_live_recharge(confirm_live=True)
        self.assertTrue(summary["skipped"])
        self.assertFalse(summary["ok"])
        self.assertGreater(int(summary.get("steps_planned") or 0), 0)
        self.assertIn("not executed", summary["reason"])


class TestReplayScriptPresence(unittest.TestCase):
    def test_script_exists(self):
        self.assertTrue(Path("scripts/replay_flow.py").exists())
        src = Path("scripts/replay_flow.py").read_text(encoding="utf-8")
        self.assertIn("shipvideodemo.json is never rewritten", src)
        self.assertNotIn("MANIFEST_PATH.write_text", src)

    def test_fixture_clicks_carry_proof(self):
        from scripts.fixture_e2e import FIXTURE_PLAN

        clicks = [s for s in FIXTURE_PLAN if s.get("action") == "click"]
        self.assertTrue(clicks)
        for step in clicks:
            cond = step.get("validation_condition") or {}
            self.assertEqual(cond.get("type"), "text_present")
            self.assertTrue(str(cond.get("value") or "").strip())


if __name__ == "__main__":
    unittest.main()
