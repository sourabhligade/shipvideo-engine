"""Shared capture proof schema for Agent Browser and Playwright runners."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence


@dataclass
class CaptureProof:
    steps_planned: int = 0
    steps_succeeded: int = 0
    steps_failed: int = 0
    clicks_succeeded: int = 0
    gotos_succeeded: int = 0
    screenshots_succeeded: int = 0
    terminal_passed: Optional[bool] = None
    validation_passed: Optional[bool] = None
    failure_reason: Optional[str] = None
    runner: str = "unknown"  # "agent_browser" | "playwright" | other
    success: bool = False
    approved_frame_count: int = 0
    final_outcome: str = "inconclusive"
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _step_action(result: Dict[str, Any]) -> str:
    step = result.get("step") if isinstance(result.get("step"), dict) else {}
    return str(step.get("action") or result.get("action") or "").strip().lower()


def _result_succeeded(result: Dict[str, Any]) -> bool:
    if result.get("terminal_condition_reached") is True:
        return True
    if result.get("validation_passed") is True:
        return True
    outcome = str(result.get("outcome") or "").strip().lower()
    if outcome in {"success", "ok", "passed", "validated"}:
        return True
    # Playwright stepwise marks steps with status=ok without outcome
    status = str(result.get("status") or "").strip().lower()
    return status in {"ok", "success", "passed"}


def count_actions_from_results(results: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    clicks = gotos = shots = 0
    for r in results:
        if not isinstance(r, dict) or not _result_succeeded(r):
            continue
        action = _step_action(r)
        if action == "click":
            clicks += 1
        elif action == "goto":
            gotos += 1
        elif action == "screenshot":
            shots += 1
    return {
        "clicks_succeeded": clicks,
        "gotos_succeeded": gotos,
        "screenshots_succeeded": shots,
    }


def terminal_passed_from_results(results: Sequence[Dict[str, Any]]) -> Optional[bool]:
    saw_terminal = False
    passed = False
    for r in results:
        if not isinstance(r, dict):
            continue
        action = _step_action(r)
        if action == "assert_terminal" or r.get("terminal_condition_reached") is True:
            saw_terminal = True
            if r.get("terminal_condition_reached") is True or _result_succeeded(r):
                passed = True
    if not saw_terminal:
        return None
    return passed


def validation_passed_from_results(results: Sequence[Dict[str, Any]]) -> Optional[bool]:
    saw = False
    all_ok = True
    for r in results:
        if not isinstance(r, dict):
            continue
        if "validation_passed" not in r and not r.get("validation_failure_reason"):
            continue
        saw = True
        if r.get("validation_passed") is False:
            all_ok = False
    if not saw:
        return None
    return all_ok


def normalize_runner_name(backend_or_engine: str) -> str:
    raw = (backend_or_engine or "").strip().lower()
    if "agent_browser" in raw or raw.startswith("ab"):
        return "agent_browser"
    if "playwright" in raw or raw in {"stepwise", "script"}:
        return "playwright"
    return raw or "unknown"


def build_capture_proof(
    *,
    plan: Optional[Sequence[Dict[str, Any]]] = None,
    runner_result: Optional[Dict[str, Any]] = None,
    engine: str = "",
    backend: str = "",
    approved_frames: Optional[Sequence[Any]] = None,
) -> CaptureProof:
    """
    Normalize AB / Playwright runner output into one CaptureProof.

    Approval and sendable gates should read this shape only.
    """
    plan_list = [s for s in (plan or []) if isinstance(s, dict)]
    rr = runner_result if isinstance(runner_result, dict) else {}
    results_raw = rr.get("results") or []
    results: List[Dict[str, Any]] = [
        r for r in results_raw if isinstance(r, dict)
    ]
    actions = count_actions_from_results(results)
    frames = list(approved_frames if approved_frames is not None else rr.get("approved_frames") or [])
    runner = normalize_runner_name(engine or backend or str(rr.get("backend") or ""))
    success = bool(rr.get("success"))
    steps_succeeded = int(rr.get("steps_succeeded") or 0)
    # Prefer counting successes from results when available
    if results:
        steps_succeeded = max(
            steps_succeeded,
            sum(1 for r in results if _result_succeeded(r)),
        )
    steps_failed = int(rr.get("steps_failed") or 0)
    if not success and steps_failed <= 0:
        steps_failed = 1

    return CaptureProof(
        steps_planned=len(plan_list) or int(rr.get("steps_planned") or 0),
        steps_succeeded=steps_succeeded,
        steps_failed=steps_failed,
        clicks_succeeded=actions["clicks_succeeded"],
        gotos_succeeded=actions["gotos_succeeded"],
        screenshots_succeeded=actions["screenshots_succeeded"],
        terminal_passed=terminal_passed_from_results(results),
        validation_passed=validation_passed_from_results(results),
        failure_reason=(
            None
            if success
            else str(rr.get("failure_reason") or "capture_failed")
        ),
        runner=runner,
        success=success,
        approved_frame_count=len(frames),
        final_outcome=str(rr.get("final_outcome") or ("success" if success else "failed")),
        extra={
            "engine": engine or None,
            "backend": backend or None,
        },
    )


def apply_proof_to_summary(
    summary: Dict[str, Any],
    proof: CaptureProof,
) -> Dict[str, Any]:
    """Merge CaptureProof into a capture_summary dict (mutates and returns)."""
    d = proof.to_dict()
    summary["capture_proof"] = d
    # Canonical top-level fields both engines must expose
    summary["steps_planned"] = proof.steps_planned
    summary["steps_succeeded"] = proof.steps_succeeded
    summary["steps_failed"] = proof.steps_failed
    summary["clicks_succeeded"] = proof.clicks_succeeded
    summary["gotos_succeeded"] = proof.gotos_succeeded
    summary["terminal_passed"] = proof.terminal_passed
    summary["validation_passed"] = proof.validation_passed
    summary["failure_reason"] = proof.failure_reason
    summary["runner"] = proof.runner
    summary["success"] = proof.success
    summary["final_outcome"] = proof.final_outcome
    if proof.terminal_passed is True:
        summary["terminal_condition_reached"] = True
    return summary
