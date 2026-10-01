from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional


def _capture_ab_screenshot(
    cli: Any,
    *,
    screenshot_dir: Path,
    shot_idx: int,
    step_result: Dict[str, Any],
    step_result_key: str,
    attempt_screenshots: List[Path],
) -> int:
    path = screenshot_dir / f"shot{shot_idx}.png"
    try:
        cli.screenshot(path)
        attempt_screenshots.append(path)
        step_result[step_result_key] = str(path)
        return shot_idx + 1
    except Exception as exc:
        step_result[f"{step_result_key}_error"] = f"screenshot_failed:{exc}"
        raise


def _run_ab_click_attempt(
    *,
    cli: Any,
    step: Dict[str, Any],
    step_result: Dict[str, Any],
    screenshot_dir: Path,
    shot_idx: int,
    attempt: int,
    click_attempt_limit: int,
    mode: str,
    extract_snapshot: Any,
    post_click_wait_ms: int = 0,
    remaining_steps: Optional[List[Dict[str, Any]]] = None,
    used_labels: Optional[set[str]] = None,
) -> Dict[str, Any]:
    from app.execution import step_runner as sr
    _settle_ab_page = sr._settle_ab_page
    _log = sr._log
    bind_click_step = sr.bind_click_step
    _resolve_ab_click_target = sr._resolve_ab_click_target
    _passes_preclick_safety_check = sr._passes_preclick_safety_check
    _ensure_ab_target_actionable = sr._ensure_ab_target_actionable
    _capture_ab_screenshot = sr._capture_ab_screenshot
    _is_stale_ref_error = sr._is_stale_ref_error
    _extract_validation_condition = sr._extract_validation_condition
    _detect_state_change = sr._detect_state_change
    _evaluate_click_validation = sr._evaluate_click_validation
    _validation_from_successful_text_wait = sr._validation_from_successful_text_wait
    resolve_failed_confirm_proof = sr.resolve_failed_confirm_proof
    attempt_screenshots: List[Path] = []
    result: Dict[str, Any] = {
        "attempt_screenshots": attempt_screenshots,
        "retry": False,
        "retry_reason": "",
        "outcome": "click_failed",
        "error": "",
        "stale_ref_error": False,
        "snap_before": None,
        "snap_after": None,
        "validation": None,
        "action_key": "",
        "state_changed": False,
        "click_target": "",
        "shot_idx": shot_idx,
        "drop_labels": [],
        "bound_step": None,
        "skipped": False,
        "skip_reason": "",
    }

    step_result["pre_snapshot_settle"] = _settle_ab_page(cli, require_networkidle=False)
    if step_result["pre_snapshot_settle"].get("fallback_wait_used"):
        _log(
            "ab_runner.page_settle_fallback",
            {
                "index": step_result["index"],
                "attempt": attempt,
                "phase": "pre_snapshot",
                "intent": str(step_result.get("intent") or ""),
            },
        )
    snap = extract_snapshot(save_raw=(attempt == 1))
    result["snap_before"] = snap
    step_result["raw_snapshot_path"] = snap.get("raw_snapshot_path", "")

    bind = bind_click_step(
        step,
        snap,
        remaining_steps=remaining_steps or [],
        used_labels=used_labels or set(),
    )
    result["drop_labels"] = list(bind.drop_labels)
    result["bound_step"] = bind.step
    step = bind.step
    step_result["step"] = step
    if bind.reason:
        step_result["bind_reason"] = bind.reason
    if bind.skip:
        result["skipped"] = True
        result["skip_reason"] = bind.reason
        result["outcome"] = "success"
        step_result["skipped"] = True
        step_result["skip_reason"] = bind.reason
        step_result["intent"] = str(step.get("label") or step_result.get("intent") or "")
        _log(
            "ab_runner.skip_unbound_step",
            {
                "index": step_result["index"],
                "attempt": attempt,
                "reason": bind.reason,
                "planned": str(step.get("planned_label") or ""),
            },
        )
        return result

    intent = str(step.get("label") or step_result.get("intent") or "")
    if intent:
        step_result["intent"] = intent
    resolution = _resolve_ab_click_target(
        cli,
        intent=intent,
        snapshot=snap,
        mode=mode,
        allow_scroll_retry=(attempt < click_attempt_limit),
        selector=str(step.get("selector") or ""),
        preferred_testids=list(step.get("preferred_testids") or []),
        preferred_surface=str(step.get("preferred_surface") or ""),
        preferred_texts=list(step.get("preferred_texts") or []),
    )
    step_result.update({
        "chosen_ref": resolution["chosen_ref"],
        "selection_reason": resolution["selection_reason"],
        "selection_source": resolution["selection_source"],
        "scroll_retry_used": resolution["scroll_retry_used"],
        "candidate_count": int(resolution.get("candidate_count") or 0),
    })

    if resolution["selection_source"] == "semantic_find":
        _log(
            "ab_runner.ab_find_recovered",
            {
                "index": step_result["index"],
                "attempt": attempt,
                "intent": intent,
                "ref": resolution["chosen_ref"],
            },
        )

    if resolution["should_retry"]:
        result["retry"] = True
        result["retry_reason"] = "scroll_retry"
        return result

    if not resolution["chosen_ref"]:
        result["error"] = f"selection_failed:{resolution['selection_reason']}"
        return result

    click_target = resolution["chosen_ref"]
    result["click_target"] = click_target
    result["action_key"] = f"{snap['current_url']}:{click_target}"

    safe_to_click, safety_reason = _passes_preclick_safety_check(
        step=step,
        snapshot=snap,
        chosen_ref=click_target,
    )
    step_result["preclick_safety"] = {
        "passed": safe_to_click,
        "reason": safety_reason,
    }
    if not safe_to_click:
        result["error"] = f"preclick_safety_failed:{safety_reason}"
        _log(
            "ab_runner.preclick_safety_failed",
            {
                "index": step_result["index"],
                "attempt": attempt,
                "ref": click_target,
                "reason": safety_reason,
            },
        )
        return result

    actionability = _ensure_ab_target_actionable(cli, click_target)
    step_result.update(actionability)
    if not actionability["target_visible"] or not actionability["target_enabled"]:
        result["error"] = (
            "target_not_actionable:"
            f"visible={actionability['target_visible']}:"
            f"enabled={actionability['target_enabled']}"
        )
        _log("ab_runner.target_not_actionable", {
            "index": step_result["index"],
            "attempt": attempt,
            "ref": click_target,
            "visible": actionability["target_visible"],
            "enabled": actionability["target_enabled"],
        })
        return result

    try:
        shot_idx = _capture_ab_screenshot(
            cli,
            screenshot_dir=screenshot_dir,
            shot_idx=shot_idx,
            step_result=step_result,
            step_result_key="before_screenshot",
            attempt_screenshots=attempt_screenshots,
        )
    except Exception as exc:
        result["error"] = f"before_screenshot_failed:{exc}"
        return result
    result["shot_idx"] = shot_idx

    try:
        cli.click(click_target)
    except Exception as exc:
        error_message = str(exc)
        result["error"] = error_message
        result["stale_ref_error"] = _is_stale_ref_error(error_message, click_target)
        return result

    if post_click_wait_ms > 0:
        try:
            cli.wait(post_click_wait_ms)
            step_result["post_click_wait_ms"] = int(post_click_wait_ms)
        except Exception as exc:
            result["error"] = f"post_click_wait_failed:{exc}"
            return result

    validation_condition = _extract_validation_condition(step)
    step_result["post_click_settle"] = _settle_ab_page(
        cli,
        validation_condition=validation_condition,
    )
    if step_result["post_click_settle"].get("fallback_wait_used"):
        _log(
            "ab_runner.page_settle_fallback",
            {
                "index": step_result["index"],
                "attempt": attempt,
                "phase": "post_click",
                "intent": str(step_result.get("intent") or ""),
            },
        )

    try:
        shot_idx = _capture_ab_screenshot(
            cli,
            screenshot_dir=screenshot_dir,
            shot_idx=shot_idx,
            step_result=step_result,
            step_result_key="after_screenshot",
            attempt_screenshots=attempt_screenshots,
        )
    except Exception as exc:
        result["error"] = f"after_screenshot_failed:{exc}"
        return result
    result["shot_idx"] = shot_idx

    snap_after = extract_snapshot(save_raw=False)
    result["snap_after"] = snap_after
    url_before = snap["current_url"]
    url_after = snap_after["current_url"]
    state_changed = _detect_state_change(
        url_before,
        url_after,
        snap["snapshot_text"],
        snap_after["snapshot_text"],
    )
    result["state_changed"] = state_changed
    step_result.update({
        "url_before": url_before,
        "url_after": url_after,
        "state_changed": state_changed,
    })

    validation = _evaluate_click_validation(
        step=step,
        snap_before=snap,
        snap_after=snap_after,
    )
    if not validation["passed"]:
        waited_validation = _validation_from_successful_text_wait(
            step=step,
            step_result=step_result,
        )
        if waited_validation is not None:
            validation = waited_validation
    if not validation["passed"]:
        rebound = resolve_failed_confirm_proof(
            step,
            snap_after,
            remaining_steps=remaining_steps or [],
        )
        if rebound.drop_labels:
            result["drop_labels"] = list(rebound.drop_labels)
        if rebound.step.get("validation_source") == "snapshot_bound_terminal":
            step = rebound.step
            result["bound_step"] = step
            step_result["step"] = step
            step_result["bind_reason"] = "confirm_proof_rebound_to_terminal"
            validation = _evaluate_click_validation(
                step=step,
                snap_before=snap,
                snap_after=snap_after,
            )
    result["validation"] = validation
    ui_diff = cli.compare_snapshots(snap, snap_after)
    native_diff: Dict[str, Any] = {}
    diff_snapshot = getattr(cli, "diff_snapshot", None)
    if callable(diff_snapshot):
        try:
            native_diff = diff_snapshot() or {}
        except Exception as exc:
            native_diff = {"error": f"{type(exc).__name__}: {exc}"}
    condition = validation["condition"]
    step_result.update({
        "validation_result": validation,
        "validation_type": condition["type"] if condition else "",
        "validation_value": condition["value"] if condition else "",
        "validation_source": validation["source"],
        "validation_passed": validation["passed"],
        "validation_actual": validation["actual"],
        "ui_diff": ui_diff,
        "ui_change_summary": ui_diff.get("summary", ""),
        "native_snapshot_diff": native_diff,
    })

    if condition is None:
        result["outcome"] = "unvalidated"
        return result
    if validation["passed"]:
        result["outcome"] = "success"
        return result

    result["outcome"] = "wrong_click"
    step_result["validation_failure_reason"] = validation["failure_reason"]
    return result


def _is_stale_ref_error(error_message: str, click_target: str) -> bool:
    if not click_target.startswith("@"):
        return False
    lowered = error_message.lower()
    stale_markers = (
        "stale",
        "unknown ref",
        "invalid ref",
        "could not find element",
        "element not found",
        "no such element",
    )
    return any(marker in lowered for marker in stale_markers)
