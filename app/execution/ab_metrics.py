from __future__ import annotations

from typing import Any, Dict, List


def _build_metrics(
    results: List[Dict[str, Any]],
    total_initial_steps: int,
    total_retries: int,
) -> Dict[str, Any]:
    succeeded = sum(1 for r in results if r.get("status") == "ok")
    wrong_click_count = sum(
        1 for r in results if r.get("outcome") == "wrong_click"
    )
    unvalidated_count = sum(
        1 for r in results if r.get("outcome") == "unvalidated"
    )
    failure_counts: Dict[str, int] = {}
    for r in results:
        outcome = (r.get("outcome") or "").strip()
        if outcome and outcome not in ("success", "pending", "ok", "unvalidated"):
            failure_counts[outcome] = failure_counts.get(outcome, 0) + 1

    latencies = [
        r.get("step_latency_ms", 0)
        for r in results
        if r.get("step_latency_ms", 0) > 0
    ]
    avg_latency = sum(latencies) / len(latencies) if latencies else 0.0

    return {
        "success_rate": succeeded / max(total_initial_steps, 1),
        "retries_per_run": float(total_retries),
        "failure_type_counts": failure_counts,
        "wrong_click_count": wrong_click_count,
        "steps_unvalidated": unvalidated_count,
        "avg_step_latency_ms": round(avg_latency, 1),
    }


def _classify_final_outcome(*, success: bool, failure_reason: str = "") -> str:
    if success:
        return "passed"

    reason = (failure_reason or "").strip().lower()
    if "ambiguous" in reason:
        return "ambiguous"

    regression_prefixes = (
        "validation_failed",
        "execution_failed",
        "missing_click_target",
        "unknown_action",
        "goto_failed",
        "click_failed",
        "snapshot_failed",
        "agent_browser_error",
        "wrong_click",
        "stale_ref",
        "stale_ref_unrecovered",
    )
    if reason.startswith(regression_prefixes):
        return "regressed"

    return "inconclusive"
