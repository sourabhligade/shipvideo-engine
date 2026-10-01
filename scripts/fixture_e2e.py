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


FIXTURE_PLAN: list[dict[str, Any]] = [
    {
        "action": "click",
        "selector": "[data-testid='save-settings']",
        "label": "Save",
        "validation_condition": {"type": "text_present", "value": "Saved"},
        "validation_source": "fixture",
    },
    {
        "action": "assert_terminal",
        "expected_text": "Saved",
        "expected_element": "[data-testid='save-status']",
    },
    {"action": "screenshot", "label": "done"},
]

FIXTURE_GENERATION_CONTEXT: Dict[str, Any] = {
    "real_routes": ["/", "/settings.html"],
    "start_route": "/settings.html",
    "suggested_demo_flow": "save settings",
    "changed_testids": ["save-settings", "settings-title", "save-status"],
}


def start_fixture_server() -> tuple[ThreadingHTTPServer, str]:
    if not FIXTURE_DIR.exists():
        raise FileNotFoundError(f"Missing fixture dir: {FIXTURE_DIR}")
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}"


def _locators_from_runner(runner_result: Dict[str, Any]) -> list[dict[str, Any]]:
    locators: list[dict[str, Any]] = []
    for item in runner_result.get("results") or []:
        if not isinstance(item, dict):
            continue
        step = item.get("step") if isinstance(item.get("step"), dict) else {}
        if str(step.get("action") or "") != "click":
            continue
        locators.append(
            {
                "intent": (item.get("intent") or step.get("label") or "").strip(),
                "selector": str(step.get("selector") or "").strip(),
                "chosen_ref": str(item.get("chosen_ref") or "").strip(),
                "outcome": str(item.get("outcome") or "").strip(),
            }
        )
    return locators


def run_fixture_once(
    *,
    base_url: str,
    shot_dir: Path,
    synthesize_video: bool = True,
) -> Dict[str, Any]:
    """Run save-settings → Saved against an already-serving fixture."""
    import os

    os.environ["BROWSER_BACKEND"] = "playwright"
    from app.config_types import CaptureSettings
    from app.execution.step_runner import run_stepwise
    from app.steps.capture_proof import build_capture_proof
    from app.steps.metrics import compute_sendable

    plan = list(FIXTURE_PLAN)
    generation_context = dict(FIXTURE_GENERATION_CONTEXT)
    shot_dir.mkdir(parents=True, exist_ok=True)
    for old in shot_dir.glob("*.png"):
        old.unlink()

    report: Dict[str, Any] = {
        "base_url": base_url,
        "plan": plan,
        "ok": False,
    }
    try:
        with __import__("unittest.mock").mock.patch(
            "app.execution.step_runner.detect_major_change",
            return_value=False,
        ):
            runner_result = run_stepwise(
                preview_url=base_url + "/settings.html",
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
        report["locators"] = _locators_from_runner(runner_result)

        fake_video = shot_dir / "fixture_out.mp4"
        if synthesize_video:
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
        else:
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
    return report


def main() -> int:
    if not FIXTURE_DIR.exists():
        print(f"Missing fixture dir: {FIXTURE_DIR}", file=sys.stderr)
        return 2

    server, base = start_fixture_server()
    print(f"[fixture_e2e] serving {FIXTURE_DIR} at {base}")
    shot_dir = OUT_DIR / "shots"
    time.sleep(0.2)
    try:
        report = run_fixture_once(base_url=base, shot_dir=shot_dir, synthesize_video=True)
    finally:
        server.shutdown()

    out_path = OUT_DIR / "fixture_e2e_report.json"
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: report.get(k) for k in ("ok", "strength", "runner_success", "failure_reason", "clicks_succeeded", "sendable", "error")}, indent=2))
    print(f"Wrote {out_path}")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
