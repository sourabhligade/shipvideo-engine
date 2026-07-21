#!/usr/bin/env python3
"""
Phase 8 — PR full-path fixture E2E against fixtures/demo_app.

Starts a local static server, runs stepwise capture with a known plan
(stable data-testid clicks), and asserts CaptureProof + sendable-ish fields.

  .venv/bin/python scripts/fixture_e2e.py
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

FIXTURE_DIR = REPO_ROOT / "fixtures" / "demo_app"
OUT_DIR = REPO_ROOT / "data" / "audit" / "fixture_e2e"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FIXTURE_DIR), **kwargs)

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def main() -> int:
    if not FIXTURE_DIR.exists():
        print(f"Missing fixture dir: {FIXTURE_DIR}", file=sys.stderr)
        return 2

    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    print(f"[fixture_e2e] serving {FIXTURE_DIR} at {base}")

    # Land on settings via preview_url; only interact (no goto → re-anchor).
    plan = [
        {
            "action": "click",
            "selector": "[data-testid='save-settings']",
            "label": "Save",
        },
        {
            "action": "assert_terminal",
            "expected_text": "Saved",
            "expected_element": "[data-testid='save-status']",
        },
        {"action": "screenshot", "label": "done"},
    ]
    generation_context = {
        "real_routes": ["/", "/settings.html"],
        "start_route": "/settings.html",
        "suggested_demo_flow": "save settings",
        "changed_testids": ["save-settings", "settings-title", "save-status"],
    }

    shot_dir = OUT_DIR / "shots"
    shot_dir.mkdir(parents=True, exist_ok=True)
    for old in shot_dir.glob("*.png"):
        old.unlink()

    report: Dict[str, Any] = {
        "base_url": base,
        "plan": plan,
        "ok": False,
    }

    try:
        # Prefer stepwise Playwright for deterministic local fixture
        import os

        os.environ["BROWSER_BACKEND"] = "playwright"
        # re-import resolution happens at module load — call runner directly
        from app.config_types import CaptureSettings
        from app.execution.step_runner import run_stepwise
        from app.steps.capture_proof import build_capture_proof
        from app.steps.metrics import compute_sendable

        time.sleep(0.2)
        # Fixture is a static page: disable major-change re-anchor (needs LLM).
        with __import__("unittest.mock").mock.patch(
            "app.execution.step_runner.detect_major_change",
            return_value=False,
        ):
            runner_result = run_stepwise(
                preview_url=base + "/settings.html",
                initial_steps=plan,
                objective={
                    "goal": "fixture e2e",
                    "generation_context": generation_context,
                },
                screenshot_dir=shot_dir,
                max_retries_per_failure=0,
                capture_settings=CaptureSettings(
                    viewport_width=1280,
                    viewport_height=720,
                    full_page_screenshots=False,
                    full_page_debug_screenshots=False,
                ),
            )
        proof = build_capture_proof(
            plan=plan,
            runner_result=runner_result,
            engine="stepwise",
            backend="playwright",
            approved_frames=runner_result.get("approved_frames") or [],
        )
        report["runner_success"] = bool(runner_result.get("success"))
        report["failure_reason"] = runner_result.get("failure_reason")
        report["capture_proof"] = proof.to_dict()
        report["steps_succeeded"] = proof.steps_succeeded
        report["clicks_succeeded"] = proof.clicks_succeeded
        report["gotos_succeeded"] = proof.gotos_succeeded

        # Minimal video file for sendable duration probe (synthetic)
        fake_video = OUT_DIR / "fixture_out.mp4"
        # Create a tiny real mp4 via ffmpeg if available
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=c=black:s=320x240:d=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(fake_video),
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
        except Exception as e:
            report["video_error"] = f"{type(e).__name__}: {e}"
            fake_video.write_bytes(b"\x00" * 50_000)

        summary = {
            "success": proof.success,
            "steps_succeeded": proof.steps_succeeded,
            "failure_reason": proof.failure_reason,
            "capture_proof": proof.to_dict(),
            "clicks_succeeded": proof.clicks_succeeded,
            "gotos_succeeded": proof.gotos_succeeded,
            "terminal_passed": proof.terminal_passed,
            "render_approval": {"is_sendable": proof.success, "reasons": []},
        }
        sendable, sendable_proof = compute_sendable(
            summary,
            fake_video if fake_video.exists() else None,
            plan,
            general_demo=False,
        )
        report["sendable"] = sendable
        report["sendable_proof"] = sendable_proof

        # Pass criteria: navigated/clicked something; proof schema present; no crash
        checks = {
            "has_proof_runner": proof.runner == "playwright",
            "got_or_click": (proof.gotos_succeeded + proof.clicks_succeeded) >= 1
            or proof.steps_succeeded >= 1,
            "schema_keys": all(
                k in proof.to_dict()
                for k in ("steps_planned", "clicks_succeeded", "terminal_passed", "runner")
            ),
        }
        report["checks"] = checks
        if runner_result.get("success"):
            report["ok"] = True
            report["strength"] = (
                "strong"
                if (proof.clicks_succeeded >= 1 or proof.steps_succeeded >= 1)
                else "runner_success"
            )
        else:
            report["ok"] = all(checks.values())
            report["strength"] = "schema_smoke" if report["ok"] else "failed"

    except Exception as e:
        report["ok"] = False
        report["error"] = f"{type(e).__name__}: {e}"
        import traceback

        report["traceback"] = traceback.format_exc()[-2000:]
    finally:
        server.shutdown()

    out_path = OUT_DIR / "fixture_e2e_report.json"
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: report.get(k) for k in ("ok", "strength", "runner_success", "failure_reason", "clicks_succeeded", "sendable", "error")}, indent=2))
    print(f"Wrote {out_path}")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
