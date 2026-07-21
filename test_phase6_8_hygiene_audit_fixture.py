"""Phase 6 config hygiene + harness presence + fixture smoke."""
from __future__ import annotations

import unittest
from pathlib import Path

from app.config_types import CaptureSettings, load_capture_settings


class TestPhase6ConfigHygiene(unittest.TestCase):
    def test_full_page_debug_wired_via_effective(self):
        cs = CaptureSettings(
            full_page_screenshots=False,
            full_page_debug_screenshots=True,
        )
        self.assertTrue(cs.effective_full_page)
        cs2 = CaptureSettings(
            full_page_screenshots=True,
            full_page_debug_screenshots=False,
        )
        self.assertTrue(cs2.effective_full_page)
        cs3 = CaptureSettings(
            full_page_screenshots=False,
            full_page_debug_screenshots=False,
        )
        self.assertFalse(cs3.effective_full_page)

    def test_step_runner_uses_effective_full_page(self):
        src = Path("app/execution/step_runner.py").read_text()
        self.assertIn("effective_full_page", src)

    def test_no_dual_smart_prefilter(self):
        src = Path("app/webhook.py").read_text()
        self.assertNotIn('trigger_mode == "smart" and not force', src)
        self.assertIn("evaluate_trigger", src) or self.assertIn("analyze_pr", src)

    def test_load_capture_settings_has_debug_flag(self):
        # should not crash without config file quirks
        cs = CaptureSettings()
        self.assertTrue(hasattr(cs, "full_page_debug_screenshots"))


class TestPhase7AuditHarness(unittest.TestCase):
    def test_script_exists(self):
        self.assertTrue(Path("scripts/audit_pipeline.py").exists())

    def test_probes_pass(self):
        import scripts.audit_pipeline as audit

        report = audit.run_probes()
        failed = report.get("p0_failed") or []
        self.assertTrue(
            report.get("ok"),
            msg=f"P0 probe failures: {failed}",
        )


class TestPhase8Fixture(unittest.TestCase):
    def test_fixture_app_exists(self):
        root = Path("fixtures/demo_app")
        self.assertTrue((root / "index.html").exists())
        self.assertTrue((root / "settings.html").exists())
        html = (root / "index.html").read_text()
        self.assertIn("data-testid=\"nav-settings\"", html)

    def test_fixture_script_exists(self):
        self.assertTrue(Path("scripts/fixture_e2e.py").exists())


if __name__ == "__main__":
    unittest.main()
