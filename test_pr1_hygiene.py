"""PR1 hygiene: webhook secret, /glimpse default, preview timeout, product port."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import DEFAULT_COMMENT_COMMAND
from app.steps.errors import ContractIntegrityError
from app.trigger import evaluate_trigger


def _ensure_observability_stub() -> None:
    obs = sys.modules.get("observability")
    if obs is None:
        return
    if not hasattr(obs, "init_tracing"):
        obs.init_tracing = lambda: None
    if not hasattr(obs, "pipeline_run_span"):
        obs.pipeline_run_span = lambda *a, **k: None
    if not hasattr(obs, "print_pipeline_summary"):
        obs.print_pipeline_summary = lambda *a, **k: None
    if not hasattr(obs, "set_current_span_error"):
        obs.set_current_span_error = lambda *a, **k: None
    if not hasattr(obs, "pipeline_step"):
        obs.pipeline_step = lambda name: (lambda fn: fn)


def _webhook():
    _ensure_observability_stub()
    import app.webhook as wh

    return wh


class TestWebhookSecretRequired(unittest.TestCase):
    def test_startup_requires_secret(self):
        require_webhook_secret = _webhook().require_webhook_secret
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GITHUB_WEBHOOK_SECRET", None)
            with self.assertRaises(RuntimeError):
                require_webhook_secret()

    def test_missing_secret_is_invalid(self):
        verify_signature = _webhook().verify_signature
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GITHUB_WEBHOOK_SECRET", None)
            self.assertFalse(verify_signature("sha256=abc", b'{"ok":true}'))

    def test_empty_secret_is_invalid(self):
        verify_signature = _webhook().verify_signature
        with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": "   "}, clear=False):
            self.assertFalse(verify_signature("sha256=abc", b'{"ok":true}'))

    def test_valid_secret_matches(self):
        payload = b'{"ok":true}'
        verify_signature = _webhook().verify_signature
        with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": "test-secret"}, clear=False):
            mac = hmac.new(b"test-secret", payload, hashlib.sha256)
            sig = f"sha256={mac.hexdigest()}"
            self.assertTrue(verify_signature(sig, payload))

    def test_wrong_secret_is_invalid(self):
        payload = b'{"ok":true}'
        verify_signature = _webhook().verify_signature
        with patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": "test-secret"}, clear=False):
            mac = hmac.new(b"other-secret", payload, hashlib.sha256)
            sig = f"sha256={mac.hexdigest()}"
            self.assertFalse(verify_signature(sig, payload))


class TestCommentCommandDefault(unittest.TestCase):
    def test_shared_default_is_glimpse(self):
        self.assertEqual(DEFAULT_COMMENT_COMMAND, "/glimpse")
        webhook_src = Path("app/webhook.py").read_text(encoding="utf-8")
        trigger_src = Path("app/trigger.py").read_text(encoding="utf-8")
        self.assertIn("DEFAULT_COMMENT_COMMAND", webhook_src)
        self.assertIn("DEFAULT_COMMENT_COMMAND", trigger_src)
        self.assertNotIn('or "/demo"', trigger_src)

    def test_webhook_parses_glimpse_from_config_default(self):
        parse = _webhook()._parse_glimpse_command
        parsed = parse(
            "/glimpse --force --route /settings",
            DEFAULT_COMMENT_COMMAND,
        )
        self.assertEqual(
            parsed,
            {"force": True, "route": "/settings", "intent_text": ""},
        )
        self.assertIsNone(parse("/demo --force", DEFAULT_COMMENT_COMMAND))

    def test_injected_demo_command_still_parses(self):
        parse = _webhook()._parse_glimpse_command
        parsed = parse("/demo --route=/billing", "/demo")
        self.assertEqual(
            parsed,
            {"force": False, "route": "/billing", "intent_text": ""},
        )

    def test_glimpse_keeps_free_text_intent(self):
        parse = _webhook()._parse_glimpse_command
        parsed = parse("/glimpse recharge", DEFAULT_COMMENT_COMMAND)
        self.assertEqual(
            parsed,
            {"force": False, "route": None, "intent_text": "recharge"},
        )

    def test_trigger_default_comment_command_is_glimpse(self):
        files = [{"path": "app/page.tsx", "patch": "+x"}]
        decision = evaluate_trigger(
            files,
            {"trigger": {"mode": "on-demand"}},
            force=False,
            comment_triggered=False,
        )
        self.assertFalse(decision.should_run)
        self.assertIn("/glimpse", decision.reason)


class TestPreviewTimeoutAndProductPort(unittest.TestCase):
    def test_preview_timeout_covers_poll_interval(self):
        cfg = json.loads(Path("project_config.json").read_text(encoding="utf-8"))
        timeout = int(cfg["preview_ready_timeout_seconds"])
        interval = int(cfg["preview_ready_poll_interval_seconds"])
        self.assertGreaterEqual(timeout, 90)
        self.assertGreaterEqual(timeout, interval)

    def test_run_product_default_port_is_8001(self):
        src = Path("run_product.sh").read_text(encoding="utf-8")
        self.assertIn('PORT:-8001', src)
        self.assertNotIn("PORT:-8080", src)


class TestContractIntegrityErrorTyping(unittest.TestCase):
    def test_constructs_with_any_values(self):
        err = ContractIntegrityError(
            "normalize",
            "validation_condition",
            {"type": "text_present"},
            None,
            "cid-1",
        )
        self.assertIn("normalize", str(err))
        self.assertEqual(err.missing_targets, [])


if __name__ == "__main__":
    unittest.main()
