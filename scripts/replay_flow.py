#!/usr/bin/env python3
"""Replay harness — prove execution reliability at ≥19/20.

Default target is the local save-settings fixture (scripts/fixture_e2e.py).
Live catalog recharge is opt-in and refused without --confirm-live.

  .venv/bin/python scripts/replay_flow.py
  .venv/bin/python scripts/replay_flow.py --runs 20 --min-pass 19
  .venv/bin/python scripts/replay_flow.py --target live-recharge --confirm-live

After ≥ min_pass successes, proven locators are written to
data/locator_history/<target>.json. shipvideodemo.json is never rewritten.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

HISTORY_DIR = REPO_ROOT / "data" / "locator_history"
MANIFEST_PATH = REPO_ROOT / "shipvideodemo.json"
LIVE_DEMO_URL = "https://shipvideo-demo.vercel.app"

DEFAULT_RUNS = 20
DEFAULT_MIN_PASS = 19


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_locator_history(
    *,
    target: str,
    locators: List[Dict[str, Any]],
    passed: int,
    runs: int,
    min_pass: int,
    history_dir: Path = HISTORY_DIR,
) -> Optional[Path]:
    """Persist proven locators only after the 19/20 gate. Never rewrite the manifest."""
    if passed < min_pass:
        return None
    history_dir.mkdir(parents=True, exist_ok=True)
    path = history_dir / f"{target}.json"
    payload = {
        "target": target,
        "written_at": _now(),
        "runs": runs,
        "passed": passed,
        "min_pass": min_pass,
        "success_rate": round(passed / max(runs, 1), 4),
        "locators": locators,
        "manifest_rewritten": False,
        "manifest_path": str(MANIFEST_PATH),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _aggregate_locators(reports: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Dict[str, Dict[str, Any]] = {}
    for report in reports:
        if not report.get("ok"):
            continue
        for loc in report.get("locators") or []:
            if not isinstance(loc, dict):
                continue
            key = "|".join(
                [
                    str(loc.get("intent") or ""),
                    str(loc.get("selector") or ""),
                    str(loc.get("chosen_ref") or ""),
                ]
            )
            if key not in seen:
                seen[key] = dict(loc)
                seen[key]["hits"] = 1
            else:
                seen[key]["hits"] = int(seen[key].get("hits") or 0) + 1
    return list(seen.values())


def replay_fixture(
    *,
    runs: int = DEFAULT_RUNS,
    min_pass: int = DEFAULT_MIN_PASS,
    history_dir: Path = HISTORY_DIR,
    run_once=None,
    start_server=None,
) -> Dict[str, Any]:
    from scripts.fixture_e2e import OUT_DIR, start_fixture_server, run_fixture_once

    runner = run_once or run_fixture_once
    starter = start_server or start_fixture_server
    server, base = starter()
    time.sleep(0.15)
    reports: List[Dict[str, Any]] = []
    try:
        for i in range(runs):
            shot_dir = OUT_DIR / "replay" / f"run_{i:02d}"
            report = runner(
                base_url=base,
                shot_dir=shot_dir,
                synthesize_video=False,
            )
            reports.append(report)
            print(
                f"[replay_flow] fixture {i + 1}/{runs} "
                f"ok={report.get('ok')} reason={report.get('failure_reason')}",
                flush=True,
            )
    finally:
        try:
            server.shutdown()
        except Exception:
            pass

    passed = sum(1 for r in reports if r.get("ok"))
    locators = _aggregate_locators(reports)
    history_path = write_locator_history(
        target="fixture_save_settings",
        locators=locators,
        passed=passed,
        runs=runs,
        min_pass=min_pass,
        history_dir=history_dir,
    )
    summary: Dict[str, Any] = {
        "target": "fixture",
        "runs": runs,
        "passed": passed,
        "failed": runs - passed,
        "min_pass": min_pass,
        "ok": passed >= min_pass,
        "locator_history": str(history_path) if history_path else None,
        "manifest_rewritten": False,
        "reports": [
            {
                "ok": r.get("ok"),
                "failure_reason": r.get("failure_reason"),
                "error": r.get("error"),
                "clicks_succeeded": r.get("clicks_succeeded"),
            }
            for r in reports
        ],
    }
    return summary


def replay_live_recharge(*, confirm_live: bool) -> Dict[str, Any]:
    """Opt-in live catalog replay. Refused unless --confirm-live.

    Live shipvideo-demo currently has no Proceed Recharge control; this path
    exists so the 19/20 gate can run later without rewriting the manifest.
    """
    if not confirm_live:
        return {
            "target": "live-recharge",
            "ok": False,
            "skipped": True,
            "reason": (
                "live-recharge is opt-in. Pass --confirm-live to run against "
                f"{LIVE_DEMO_URL}. Do not use this to burn 20 Vercel sessions "
                "until bind succeeds on the live catalog."
            ),
            "manifest_rewritten": False,
        }
    from app.manifest.runner import _load_manifest_flows, flow_to_steps

    flow = None
    for candidate in _load_manifest_flows():
        if "recharge" in candidate.name.lower():
            flow = candidate
            break
    if flow is None:
        return {
            "target": "live-recharge",
            "ok": False,
            "error": "manifest recharge flow not found",
            "manifest_rewritten": False,
        }
    steps = flow_to_steps(flow)
    return {
        "target": "live-recharge",
        "ok": False,
        "skipped": True,
        "reason": (
            "live-recharge confirmed but not executed: catalog still missing "
            "Proceed Recharge. Plan is ready "
            f"({len(steps)} steps) against {LIVE_DEMO_URL}."
        ),
        "steps_planned": len(steps),
        "start_route": getattr(flow, "start_route", None),
        "manifest_rewritten": False,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Replay fixture or live recharge 20×")
    parser.add_argument("--target", choices=("fixture", "live-recharge"), default="fixture")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--min-pass", type=int, default=DEFAULT_MIN_PASS)
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Required to even prepare a live-recharge replay. Still does not fire 20 Vercel runs.",
    )
    args = parser.parse_args(argv)

    if args.target == "live-recharge":
        summary = replay_live_recharge(confirm_live=args.confirm_live)
    else:
        if args.runs < 1:
            print("[replay_flow] --runs must be >= 1", file=sys.stderr)
            return 2
        summary = replay_fixture(runs=args.runs, min_pass=args.min_pass)

    out_dir = REPO_ROOT / "data" / "audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"replay_{summary.get('target')}.json"
    out_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "reports"}, indent=2))
    print(f"Wrote {out_path}")
    if summary.get("skipped"):
        return 2
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
