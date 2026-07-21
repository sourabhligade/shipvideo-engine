from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


# Minimum playable demo length (seconds). Below this, file is not publishable.
MIN_VIDEO_DURATION_SEC = 2.0

# Failure reasons that always block sendable / video_usable.
HARD_FAIL_REASON_MARKERS: Tuple[str, ...] = (
    "navigation_reanchor_failed",
    "no_validated_frames",
    "render_not_sendable",
    "target_unreachable",
    "ab_execution_failed",
    "stepwise_execution_failed",
    "wrong_click",
    "expected_proof_not_satisfied",
    "no_approved_frames",
    "target_route_not_reached",
)


@dataclass
class RunMetrics:
    run_id: str
    pr_number: int
    started_at: str
    finished_at: str = ""
    success: bool = False
    error_type: str = ""
    error_message: str = ""
    pipeline: str = ""
    capture_browser: str = ""
    preflight_passed: bool = False
    terminal_condition_reached: bool = False
    steps_validated: int = 0
    steps_unvalidated: int = 0
    wrong_clicks: int = 0
    video_usable: bool = False
    sendable: bool = False
    sendable_reasons: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


def new_run_metrics(pr_number: int) -> RunMetrics:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return RunMetrics(
        run_id=f"pr{pr_number}_{ts}",
        pr_number=pr_number,
        started_at=datetime.now(timezone.utc).isoformat(),
    )


def write_run_metrics(metrics: RunMetrics) -> Path:
    base_dir = Path(__file__).resolve().parent.parent / "data" / "run_metrics"
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / f"{metrics.run_id}.json"
    path.write_text(
        json.dumps(asdict(metrics), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _probe_duration_sec(video_path: Path) -> Optional[float]:
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            return None
        return float((proc.stdout or "").strip() or 0.0)
    except Exception:
        return None


def _count_actions(
    capture_summary: Dict[str, Any],
    plan: Optional[Sequence[Dict[str, Any]]],
) -> Dict[str, int]:
    # Prefer shared CaptureProof fields (Phase 5)
    proof = capture_summary.get("capture_proof")
    if isinstance(proof, dict) and (
        proof.get("clicks_succeeded") is not None
        or proof.get("gotos_succeeded") is not None
    ):
        return {
            "clicks_succeeded": int(proof.get("clicks_succeeded") or 0),
            "gotos_succeeded": int(proof.get("gotos_succeeded") or 0),
        }
    if capture_summary.get("clicks_succeeded") is not None or capture_summary.get(
        "gotos_succeeded"
    ) is not None:
        return {
            "clicks_succeeded": int(capture_summary.get("clicks_succeeded") or 0),
            "gotos_succeeded": int(capture_summary.get("gotos_succeeded") or 0),
        }

    clicks = 0
    gotos = 0
    debug = capture_summary.get("debug") or {}
    results = debug.get("results") if isinstance(debug, dict) else None
    if isinstance(results, list):
        for r in results:
            if not isinstance(r, dict):
                continue
            outcome = str(r.get("outcome") or "").lower()
            ok = (
                outcome in {"success", "ok", "passed", "validated"}
                or r.get("terminal_condition_reached") is True
                or r.get("validation_passed") is True
            )
            if not ok:
                continue
            step = r.get("step") if isinstance(r.get("step"), dict) else {}
            action = str(step.get("action") or r.get("action") or "").lower()
            if action == "click":
                clicks += 1
            elif action == "goto":
                gotos += 1

    # Fall back to summary counters when per-step results are sparse
    if clicks == 0 and gotos == 0:
        succeeded = int(capture_summary.get("steps_succeeded") or 0)
        plan_list = list(plan or [])
        plan_clicks = sum(
            1 for s in plan_list if isinstance(s, dict) and s.get("action") == "click"
        )
        plan_gotos = sum(
            1 for s in plan_list if isinstance(s, dict) and s.get("action") == "goto"
        )
        if succeeded > 0 and (plan_clicks or plan_gotos):
            if plan_clicks:
                clicks = 1
            if plan_gotos:
                gotos = 1
        elif succeeded > 0:
            screenshot_only = bool(plan_list) and all(
                isinstance(s, dict) and str(s.get("action") or "") == "screenshot"
                for s in plan_list
            )
            if not screenshot_only:
                clicks = 1

    return {"clicks_succeeded": clicks, "gotos_succeeded": gotos}


def _plan_requires_terminal(plan: Optional[Sequence[Dict[str, Any]]]) -> bool:
    for s in plan or []:
        if isinstance(s, dict) and str(s.get("action") or "") == "assert_terminal":
            return True
    return False


def _terminal_passed(capture_summary: Dict[str, Any]) -> bool:
    proof = capture_summary.get("capture_proof")
    if isinstance(proof, dict) and proof.get("terminal_passed") is True:
        return True
    if capture_summary.get("terminal_passed") is True:
        return True
    if capture_summary.get("terminal_condition_reached") is True:
        return True
    debug = capture_summary.get("debug") or {}
    results = debug.get("results") if isinstance(debug, dict) else None
    if isinstance(results, list):
        for r in results:
            if isinstance(r, dict) and r.get("terminal_condition_reached") is True:
                return True
            step = r.get("step") if isinstance(r, dict) else None
            if (
                isinstance(r, dict)
                and isinstance(step, dict)
                and str(step.get("action") or "") == "assert_terminal"
                and str(r.get("outcome") or "") in {"success", "ok", "passed"}
            ):
                return True
    return False


def _is_hard_fail_reason(reason: Optional[str]) -> bool:
    text = (reason or "").strip().lower()
    if not text:
        return False
    return any(marker in text for marker in HARD_FAIL_REASON_MARKERS)


def compute_sendable(
    capture_summary: Optional[Dict[str, Any]],
    video_path: Optional[Path],
    plan: Optional[Sequence[Dict[str, Any]]] = None,
    *,
    general_demo: bool = False,
    min_duration_sec: float = MIN_VIDEO_DURATION_SEC,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Decide whether a demo video is publishable (sendable).

    video_usable must equal this result — never size-only.
    """
    summary = capture_summary if isinstance(capture_summary, dict) else {}
    reasons: List[str] = []
    proof: Dict[str, Any] = {
        "general_demo": bool(general_demo),
        "min_duration_sec": float(min_duration_sec),
    }

    path = Path(video_path) if video_path else None
    exists = bool(path and path.exists())
    size = int(path.stat().st_size) if exists and path is not None else 0
    proof["video_exists"] = exists
    proof["video_size"] = size
    if not exists:
        reasons.append("video_missing")
    elif size <= 0:
        reasons.append("video_empty")

    duration: Optional[float] = None
    if exists and path is not None:
        duration = _probe_duration_sec(path)
    if duration is None:
        for key in ("total_duration_sec", "video_duration_sec", "duration_sec"):
            if summary.get(key) is not None:
                try:
                    duration = float(summary[key])
                    break
                except (TypeError, ValueError):
                    pass
    proof["duration_sec"] = duration
    if exists and size > 0:
        if duration is not None and duration + 1e-6 < float(min_duration_sec):
            reasons.append(f"duration_below_min:{duration:.3f}<{min_duration_sec}")
        elif duration is None and size < 2048:
            # No duration probe and tiny file — not a real demo clip
            reasons.append("video_too_small_without_duration")

    failure_reason = summary.get("failure_reason")
    proof["failure_reason"] = failure_reason
    if _is_hard_fail_reason(str(failure_reason or "")):
        reasons.append(f"hard_fail:{failure_reason}")

    # Prefer explicit render approval when present (capture-time sendable gate)
    render_approval = summary.get("render_approval")
    render_says_sendable: Optional[bool] = None
    if isinstance(render_approval, dict) and "is_sendable" in render_approval:
        render_says_sendable = bool(render_approval.get("is_sendable"))
        proof["render_approval_is_sendable"] = render_says_sendable
        if not render_says_sendable:
            for r in render_approval.get("reasons") or ["render_not_sendable"]:
                tag = str(r)
                if tag not in reasons:
                    reasons.append(tag)

    if summary.get("success") is False:
        reasons.append("capture_not_success")

    actions = _count_actions(summary, plan)
    proof.update(actions)
    interaction_ok = (
        actions["clicks_succeeded"] >= 1 or actions["gotos_succeeded"] >= 1
    )
    screenshot_only = bool(plan) and all(
        isinstance(s, dict) and str(s.get("action") or "") == "screenshot"
        for s in (plan or [])
    )
    proof["screenshot_only_plan"] = screenshot_only
    # When capture already approved sendable, trust that interaction/proof path.
    # Otherwise require interaction (or explicit general_demo).
    if render_says_sendable is not True and not general_demo and not interaction_ok:
        if screenshot_only or int(summary.get("steps_succeeded") or 0) <= 0:
            reasons.append("no_interaction_proof")

    requires_terminal = _plan_requires_terminal(plan)
    terminal_ok = _terminal_passed(summary)
    proof["requires_terminal"] = requires_terminal
    proof["terminal_passed"] = terminal_ok
    if requires_terminal and not terminal_ok and render_says_sendable is not True:
        reasons.append("terminal_not_passed")

    # Deduplicate while preserving order
    seen: set = set()
    uniq: List[str] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            uniq.append(r)

    sendable = len(uniq) == 0
    proof["reasons"] = uniq
    proof["sendable"] = sendable
    return sendable, proof

