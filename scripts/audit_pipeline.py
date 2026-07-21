#!/usr/bin/env python3
"""
Phase 7 — Automated pipeline audit harness.

Runs stage probes + optional short product jobs. Exit code 1 if any P0 probe fails.

  .venv/bin/python scripts/audit_pipeline.py
  .venv/bin/python scripts/audit_pipeline.py --skip-product
  .venv/bin/python scripts/audit_pipeline.py --product-only
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

AUDIT_DIR = REPO_ROOT / "data" / "audit"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)


def _ok(name: str, detail: Any = None) -> Dict[str, Any]:
    return {"name": name, "ok": True, "detail": detail}


def _fail(name: str, detail: Any = None) -> Dict[str, Any]:
    return {"name": name, "ok": False, "detail": detail}


def probe_trigger() -> List[Dict[str, Any]]:
    from app.trigger import evaluate_trigger

    cfg = {"trigger": {"mode": "on-demand", "threshold": 5}}
    files = [{"path": "app/x/page.tsx", "status": "modified", "patch": "+x\n"}]
    out: List[Dict[str, Any]] = []

    d0 = evaluate_trigger(files, cfg, force=False, comment_triggered=False)
    out.append(
        _ok("on_demand_no_comment", d0.reason)
        if d0.should_run is False
        else _fail("on_demand_no_comment", {"should_run": d0.should_run, "reason": d0.reason})
    )

    d1 = evaluate_trigger(files, cfg, force=False, comment_triggered=True)
    out.append(
        _ok("on_demand_comment", d1.reason)
        if d1.should_run is True
        else _fail("on_demand_comment", {"should_run": d1.should_run, "reason": d1.reason})
    )
    return out


def probe_normalize() -> List[Dict[str, Any]]:
    from app.steps.step_normalizer import normalize_steps

    steps = normalize_steps(
        [
            {
                "action": "click",
                "label": "Save",
                "selector": "[data-testid='save-btn']",
            }
        ]
    )
    if not steps:
        return [_fail("normalize_keeps_testid", "empty output")]
    sel = str(steps[0].get("selector") or "")
    if "data-testid" in sel or "save-btn" in sel:
        return [_ok("normalize_keeps_testid", steps[0])]
    return [_fail("normalize_keeps_testid", steps[0])]


def probe_deleted_routes() -> List[Dict[str, Any]]:
    from app.steps.step_normalizer import _extract_routes_from_diff

    routes = _extract_routes_from_diff(
        [
            {"path": "app/old/page.tsx", "status": "removed"},
            {"path": "app/new/page.tsx", "status": "added"},
        ]
    )
    if "/old" in routes:
        return [_fail("deleted_routes_skipped", sorted(routes))]
    if "/new" not in routes:
        return [_fail("deleted_routes_skipped", {"missing_new": sorted(routes)})]
    return [_ok("deleted_routes_skipped", sorted(routes))]


def probe_multi_match() -> List[Dict[str, Any]]:
    from app.policy.selector_validator import validate_step_against_dom

    class _Loc:
        def __init__(self, n: int):
            self._n = n

        def count(self) -> int:
            return self._n

    class _Page:
        def locator(self, sel: str) -> _Loc:
            return _Loc(3)

        def wait_for_selector(self, *a, **k):
            return None

        def get_by_text(self, *a, **k) -> _Loc:
            return _Loc(1)

    step = {"action": "click", "selector": "[data-testid='x']"}
    ok, reason = validate_step_against_dom(step, {"routes": ["/"]}, page=_Page())
    if ok is False and "not_unique" in str(reason):
        return [_ok("multi_match_rejected", reason)]
    if ok is False:
        return [_ok("multi_match_rejected", reason)]
    return [_fail("multi_match_rejected", {"ok": ok, "reason": reason})]


def probe_terminal_fail_closed() -> List[Dict[str, Any]]:
    from app.execution.step_runner import _assert_ab_terminal_condition
    from app.policy.selector_validator import validate_step_against_dom

    # Static validation: empty terminal is invalid
    ok, reason = validate_step_against_dom(
        {"action": "assert_terminal"},
        {"routes": ["/"]},
        page=None,
    )
    out: List[Dict[str, Any]] = []
    if ok is False and "missing_terminal" in str(reason):
        out.append(_ok("empty_terminal_validator", reason))
    else:
        out.append(_fail("empty_terminal_validator", {"ok": ok, "reason": reason}))

    # AB assert helper: missing condition => found False
    class _Cli:
        pass

    result = _assert_ab_terminal_condition(
        _Cli(),
        condition={},
        expected_element="",
        extract_snapshot=lambda _cli: {"nodes": []},
    )
    if result.get("found") is False:
        out.append(_ok("empty_terminal_ab_found_false", result.get("source")))
    else:
        out.append(_fail("empty_terminal_ab_found_false", result))
    return out

def probe_frame_holds_and_mux() -> List[Dict[str, Any]]:
    from app.product.video import build_av_mux_command, compute_frame_holds

    holds = compute_frame_holds(
        3,
        [
            {"start": 0.0, "end": 2.0},
            {"start": 2.0, "end": 4.0},
            {"start": 4.0, "end": 6.0},
        ],
        10.0,
    )
    out: List[Dict[str, Any]] = []
    if sum(holds) + 1e-6 >= 10.0:
        out.append(_ok("frame_holds_cover_audio", holds))
    else:
        out.append(_fail("frame_holds_cover_audio", holds))

    cmd = build_av_mux_command(
        Path("s.mp4"), Path("a.wav"), Path("o.mp4"),
        video_duration_sec=12.0,
        audio_duration_sec=10.0,
    )
    if "-shortest" in cmd:
        out.append(_fail("mux_no_shortest", cmd))
    else:
        out.append(_ok("mux_no_shortest", cmd[:8]))
    return out


def probe_sendable_not_size_only() -> List[Dict[str, Any]]:
    import tempfile
    from unittest.mock import patch

    from app.steps.metrics import compute_sendable

    with tempfile.TemporaryDirectory() as td:
        vid = Path(td) / "x.mp4"
        vid.write_bytes(b"\x00" * 5000)
        with patch("app.steps.metrics._probe_duration_sec", return_value=5.0):
            ok, proof = compute_sendable(
                {"success": True, "steps_succeeded": 0},
                vid,
                [{"action": "screenshot"}],
                general_demo=False,
            )
    if ok is False and "no_interaction_proof" in (proof.get("reasons") or []):
        return [_ok("sendable_not_size_only", proof.get("reasons"))]
    return [_fail("sendable_not_size_only", proof)]


def probe_generation_fail_closed() -> List[Dict[str, Any]]:
    from app.steps.step_generation import _collapse_result

    hard = _collapse_result(general_demo=False, narration="n", reason="x")
    soft = _collapse_result(general_demo=True, narration="n", reason="x")
    out = []
    if hard.get("generation_hard_fail") and hard.get("steps") == []:
        out.append(_ok("generation_hard_fail_empty", hard.get("error")))
    else:
        out.append(_fail("generation_hard_fail_empty", hard))
    if soft.get("generation_soft_fallback") and soft.get("steps"):
        out.append(_ok("generation_soft_general_demo", soft.get("steps")))
    else:
        out.append(_fail("generation_soft_general_demo", soft))
    return out


def probe_max_patch() -> List[Dict[str, Any]]:
    from app.steps.pr_extraction import MAX_PATCH_CHARS

    if MAX_PATCH_CHARS >= 4000:
        return [_ok("max_patch_chars", MAX_PATCH_CHARS)]
    return [_fail("max_patch_chars", MAX_PATCH_CHARS)]


def probe_full_page_debug_wired() -> List[Dict[str, Any]]:
    from app.config_types import CaptureSettings

    cs = CaptureSettings(full_page_screenshots=False, full_page_debug_screenshots=True)
    if cs.effective_full_page is True:
        return [_ok("full_page_debug_wired", True)]
    return [_fail("full_page_debug_wired", cs.effective_full_page)]


def probe_capture_proof_keys() -> List[Dict[str, Any]]:
    from app.steps.capture_proof import build_capture_proof

    p = build_capture_proof(
        plan=[{"action": "click"}],
        runner_result={"success": True, "steps_succeeded": 1, "results": []},
        engine="stepwise",
    )
    d = p.to_dict()
    required = {
        "steps_planned",
        "steps_succeeded",
        "clicks_succeeded",
        "gotos_succeeded",
        "terminal_passed",
        "runner",
        "success",
    }
    missing = sorted(required - set(d))
    if missing:
        return [_fail("capture_proof_keys", missing)]
    return [_ok("capture_proof_keys", sorted(required))]


P0_PROBES: List[Tuple[str, Callable[[], List[Dict[str, Any]]]]] = [
    ("trigger", probe_trigger),
    ("normalize", probe_normalize),
    ("deleted_routes", probe_deleted_routes),
    ("multi_match", probe_multi_match),
    ("terminal", probe_terminal_fail_closed),
    ("av_contract", probe_frame_holds_and_mux),
    ("sendable", probe_sendable_not_size_only),
    ("generation", probe_generation_fail_closed),
    ("max_patch", probe_max_patch),
    ("config_hygiene", probe_full_page_debug_wired),
    ("capture_proof", probe_capture_proof_keys),
]


def run_probes() -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    for name, fn in P0_PROBES:
        try:
            results.extend(fn())
        except Exception as e:
            results.append(
                _fail(f"{name}_exception", f"{type(e).__name__}: {e}\n{traceback.format_exc()[-500:]}")
            )
    failed = [r for r in results if not r["ok"]]
    return {
        "ts": time.time(),
        "probes": results,
        "p0_failed": failed,
        "ok": len(failed) == 0,
    }


def run_product_jobs(max_steps: int = 2) -> Dict[str, Any]:
    from app.product.pipeline import run_link_to_video

    jobs = []
    for job_id, url in (
        ("audit_example", "https://example.com"),
        ("audit_playwright", "https://playwright.dev"),
    ):
        job_dir = AUDIT_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        try:
            result = run_link_to_video(
                url,
                job_dir,
                job_id=job_id,
                max_steps=max_steps,
                use_azure_subtitles=False,
            )
            elapsed = time.time() - t0
            # SRT cue overlap check when multi-step
            srt_path = job_dir / f"journey_{job_id}.srt"
            srt_ok = True
            srt_detail: Any = None
            if srt_path.exists() and max_steps > 1:
                text = srt_path.read_text(encoding="utf-8", errors="ignore")
                # crude: third cue must not start at 00:00:00 if 3 cues
                blocks = [b.strip() for b in text.strip().split("\n\n") if b.strip()]
                if len(blocks) >= 3:
                    third = blocks[2].splitlines()
                    timing = next((ln for ln in third if "-->" in ln), "")
                    if timing.strip().startswith("00:00:00"):
                        # only fail if it also spans near full narration (heuristic)
                        srt_ok = "00:00:00,000 --> 00:00:" not in timing or len(blocks) < 3
                        # stricter: cue index 3 starting at 0 is the known bug
                        if third and third[0].strip() == "3" and timing.startswith("00:00:00"):
                            srt_ok = False
                            srt_detail = timing
            entry = {
                "job_id": job_id,
                "url": url,
                "ok": bool(result.get("ok")) and srt_ok,
                "elapsed_s": round(elapsed, 2),
                "result": {
                    k: result.get(k)
                    for k in (
                        "ok",
                        "error",
                        "end_reason",
                        "video",
                        "silent_duration_sec",
                        "audio_duration_sec",
                        "total_duration_sec",
                    )
                    if k in result or k in ("ok", "error")
                },
                "srt_ok": srt_ok,
                "srt_detail": srt_detail,
            }
            # A/V contract soft check when both durations present
            sd = result.get("silent_duration_sec")
            ad = result.get("audio_duration_sec")
            if sd is not None and ad is not None:
                entry["av_delta"] = abs(float(sd) - float(ad))
            jobs.append(entry)
            (AUDIT_DIR / f"product_job_{job_id}.json").write_text(
                json.dumps(entry, indent=2, default=str), encoding="utf-8"
            )
        except Exception as e:
            jobs.append(
                {
                    "job_id": job_id,
                    "url": url,
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}",
                }
            )
    return {"jobs": jobs, "ok": all(j.get("ok") for j in jobs)}


def main() -> int:
    parser = argparse.ArgumentParser(description="ShipVideo pipeline audit harness")
    parser.add_argument("--skip-product", action="store_true", help="Skip live product jobs")
    parser.add_argument("--product-only", action="store_true", help="Only product jobs")
    parser.add_argument("--max-steps", type=int, default=2)
    args = parser.parse_args()

    report: Dict[str, Any] = {"ts": time.time(), "branch_hint": "see git"}
    p0_ok = True

    if not args.product_only:
        probe_report = run_probes()
        report["probes"] = probe_report
        p0_ok = bool(probe_report.get("ok"))
        (AUDIT_DIR / "probe_results.json").write_text(
            json.dumps(probe_report, indent=2, default=str), encoding="utf-8"
        )
        print("=== Stage probes ===")
        for r in probe_report["probes"]:
            flag = "PASS" if r["ok"] else "FAIL"
            print(f"  [{flag}] {r['name']}: {r.get('detail')!r}"[:200])
        if not p0_ok:
            print(f"\nP0 failures: {len(probe_report['p0_failed'])}")

    product_ok = True
    if not args.skip_product:
        print("\n=== Product jobs ===")
        try:
            product_report = run_product_jobs(max_steps=args.max_steps)
        except Exception as e:
            product_report = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        report["product"] = product_report
        product_ok = bool(product_report.get("ok"))
        for j in product_report.get("jobs") or []:
            flag = "PASS" if j.get("ok") else "FAIL"
            print(f"  [{flag}] {j.get('job_id')} {j.get('url')} {j.get('error') or j.get('elapsed_s')}")

    out_path = AUDIT_DIR / "audit_report.json"
    report["ok"] = p0_ok and product_ok
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out_path}")
    if not report["ok"]:
        print("AUDIT FAILED")
        return 1
    print("AUDIT PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
