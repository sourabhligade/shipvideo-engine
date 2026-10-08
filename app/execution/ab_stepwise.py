from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config_types import CaptureSettings
from app.execution.constants import (
    MAX_STEPS_PER_RUN,
    MAX_RETRIES_PER_STEP,
    MAX_AB_REPLANS_PER_RUN,
    MAX_AB_FLOW_RESTARTS,
)


def run_ab_stepwise(
    *,
    preview_url: str,
    initial_steps: List[Dict[str, Any]],
    screenshot_dir: Path,
    objective: Optional[Dict[str, Any]] = None,
    max_steps_per_run: int = MAX_STEPS_PER_RUN,
    max_retries_per_step: int = MAX_RETRIES_PER_STEP,
    mode: str = "deterministic",
    capture_settings: Optional[CaptureSettings] = None,
    session: str = "ab_exp",
) -> Dict[str, Any]:
    from app.execution import step_runner as sr
    _log = sr._log
    _configure_ab_session = sr._configure_ab_session
    _settle_ab_page = sr._settle_ab_page
    _should_use_testid_search = sr._should_use_testid_search
    _objective_changed_testids = sr._objective_changed_testids
    _objective_start_route = sr._objective_start_route
    _run_ab_changed_testid_search = sr._run_ab_changed_testid_search
    _resolve_url = sr._resolve_url
    _attach_ab_failure_diagnostics = sr._attach_ab_failure_diagnostics
    _classify_final_outcome = sr._classify_final_outcome
    _build_metrics = sr._build_metrics
    _approved_frame_paths = sr._approved_frame_paths
    _resolve_terminal_expectation = sr._resolve_terminal_expectation
    _assert_ab_terminal_condition = sr._assert_ab_terminal_condition
    _should_keep_click_screenshots = sr._should_keep_click_screenshots
    _discard_step_screenshots = sr._discard_step_screenshots
    _extract_validation_condition = sr._extract_validation_condition
    _run_ab_click_attempt = sr._run_ab_click_attempt
    used_click_labels = sr.used_click_labels
    drop_labels_from_queue = sr.drop_labels_from_queue
    _scroll_to_find = sr._scroll_to_find
    _discard_screenshots = sr._discard_screenshots
    _recover_ab_prerequisite_steps = sr._recover_ab_prerequisite_steps
    _infer_runtime_validation = sr._infer_runtime_validation
    _next_click_intent = sr._next_click_intent
    _validated_milestone_steps = sr._validated_milestone_steps
    _replay_ab_milestones = sr._replay_ab_milestones


    from app.browser.agent_browser_cli import AgentBrowserCLI, AgentBrowserError
    from app.browser.ref_selector import derive_intent
    from app.context.dom_extractor import extract_ab_context

    cs = capture_settings or CaptureSettings()

    for old in screenshot_dir.glob("shot*.png"):
        old.unlink()

    results: List[Dict[str, Any]] = []
    queue: List[Dict[str, Any]] = list(initial_steps[:max_steps_per_run])
    steps_succeeded = 0
    shot_idx = 1

    last_action_key: Optional[str] = None
    _total_retries = 0                                                             
    _replans_used = 0
    _flow_restarts_used = 0

    _log("ab_runner.start", {
        "preview_url": preview_url,
        "total_steps": len(initial_steps),
        "mode": mode,
        "session": session,
    })

    cli = AgentBrowserCLI(session=session)

    try:
        cli.open(preview_url)
        session_config = _configure_ab_session(cli, cs)
        _settle_ab_page(cli, require_networkidle=False)

        if _should_use_testid_search(
            objective=objective,
            initial_steps=queue,
        ):
            _log(
                "ab_runner.changed_testid_search_start",
                {
                    "changed_testids": _objective_changed_testids(objective),
                    "start_route": _objective_start_route(objective) or "/",
                },
            )
            return _run_ab_changed_testid_search(
                cli=cli,
                preview_url=preview_url,
                screenshot_dir=screenshot_dir,
                objective=objective,
                mode=mode,
                max_retries_per_step=max_retries_per_step,
                session_config=session_config,
            )

        step_idx = 0
        while step_idx < len(queue) and step_idx < max_steps_per_run:
            step = queue[step_idx]
            action = step.get("action")
            _step_t0 = time.monotonic()                                  
            step_result: Dict[str, Any] = {
                "index": step_idx,
                "step": step,
                "status": "failed",
                "outcome": "pending",
                "backend": "agent_browser_cli",
                "mode": mode,
            }




            if action == "goto":
                url = step.get("url") or "/"
                full_url = _resolve_url(preview_url, url)
                try:
                    cli.open(full_url)
                    step_result["page_settle"] = _settle_ab_page(cli, require_networkidle=False)
                    step_result["session_viewport"] = session_config
                    step_result.update({"status": "ok", "outcome": "success"})
                    steps_succeeded += 1
                    last_action_key = None                                      
                except AgentBrowserError as exc:
                    step_result.update({"outcome": "click_failed", "error": str(exc)})
                    _attach_ab_failure_diagnostics(cli, step_result)
                    step_result["step_latency_ms"] = int((time.monotonic() - _step_t0) * 1000)
                    results.append(step_result)
                    _log("ab_runner.goto_failed", {"index": step_idx, "url": full_url, "error": str(exc)})
                    return {
                        "success": False,
                        "final_outcome": _classify_final_outcome(
                            success=False,
                            failure_reason=f"goto_failed:{full_url}",
                        ),
                        "steps_succeeded": steps_succeeded,
                        "steps_failed": 1,
                        "failure_reason": f"goto_failed:{full_url}",
                        "results": results,
                        "metrics": _build_metrics(results, len(initial_steps), _total_retries),
                    }
                step_result["step_latency_ms"] = int((time.monotonic() - _step_t0) * 1000)
                results.append(step_result)
                step_idx += 1
                continue




            if action == "screenshot":
                path = screenshot_dir / f"shot{shot_idx}.png"
                try:
                    cli.screenshot(path)
                    shot_idx += 1
                    step_result["screenshot_path"] = str(path)
                except AgentBrowserError as exc:
                    step_result.update({
                        "status": "failed",
                        "outcome": "click_failed",
                        "error": f"screenshot_failed:{exc}",
                    })
                    _attach_ab_failure_diagnostics(cli, step_result)
                    step_result["step_latency_ms"] = int((time.monotonic() - _step_t0) * 1000)
                    results.append(step_result)
                    return {
                        "success": False,
                        "final_outcome": _classify_final_outcome(
                            success=False,
                            failure_reason=f"screenshot_failed:{exc}",
                        ),
                        "steps_succeeded": steps_succeeded,
                        "steps_failed": 1,
                        "failure_reason": f"screenshot_failed:{exc}",
                        "results": results,
                        "approved_frames": _approved_frame_paths(results),
                        "metrics": _build_metrics(results, len(initial_steps), _total_retries),
                    }
                step_result.update({"status": "ok", "outcome": "success"})
                steps_succeeded += 1
                step_result["step_latency_ms"] = int((time.monotonic() - _step_t0) * 1000)
                results.append(step_result)
                step_idx += 1
                continue




            if action == "assert_terminal":
                condition, expected_element = _resolve_terminal_expectation(step)
                found = False
                terminal_source = ""
                terminal_actual = ""
                try:
                    terminal_result = _assert_ab_terminal_condition(
                        cli,
                        condition=condition,
                        expected_element=str(expected_element or ""),
                        extract_snapshot=lambda **kwargs: extract_ab_context(cli, **kwargs),
                    )
                    found = bool(terminal_result.get("found"))
                    terminal_source = str(terminal_result.get("source") or "")
                    terminal_actual = str(terminal_result.get("actual") or "")
                except AgentBrowserError as exc:
                    step_result.update(
                        {
                            "status": "failed",
                            "outcome": "click_failed",
                            "error": f"snapshot_failed:{exc}",
                        }
                    )
                    _attach_ab_failure_diagnostics(cli, step_result)
                    step_result["step_latency_ms"] = int((time.monotonic() - _step_t0) * 1000)
                    results.append(step_result)
                    return {
                        "success": False,
                        "final_outcome": _classify_final_outcome(
                            success=False,
                            failure_reason=f"snapshot_failed:{exc}",
                        ),
                        "steps_succeeded": steps_succeeded,
                        "steps_failed": 1,
                        "failure_reason": f"snapshot_failed:{exc}",
                        "results": results,
                        "metrics": _build_metrics(results, len(initial_steps), _total_retries),
                    }

                step_result["terminal_condition_reached"] = found
                step_result["terminal_validation_source"] = terminal_source
                step_result["terminal_validation_actual"] = terminal_actual
                step_result["outcome"] = "success" if found else "terminal_not_reached"
                if not found:
                    expected_repr = (
                        expected_element
                        or condition.get("value")
                        or condition.get("type")
                        or ""
                    )
                    print(
                        f"[step_runner] terminal condition not reached: "
                        f"expected={expected_repr!r} source={terminal_source!r}",
                        flush=True,
                    )
                step_result["status"] = "ok" if found else "failed"
                step_result["step_latency_ms"] = int((time.monotonic() - _step_t0) * 1000)
                if found:
                    terminal_frame_path = screenshot_dir / f"shot{shot_idx}.png"
                    try:
                        cli.screenshot(terminal_frame_path)
                        step_result["screenshot_path"] = str(terminal_frame_path)
                        print(
                            f"[step_runner] terminal frame captured: {terminal_frame_path}",
                            flush=True,
                        )
                        shot_idx += 1
                    except AgentBrowserError as exc:
                        step_result.update(
                            {
                                "status": "failed",
                                "outcome": "click_failed",
                                "error": f"terminal_screenshot_failed:{exc}",
                            }
                        )
                        _attach_ab_failure_diagnostics(cli, step_result)
                        results.append(step_result)
                        return {
                            "success": False,
                            "final_outcome": _classify_final_outcome(
                                success=False,
                                failure_reason=f"terminal_screenshot_failed:{exc}",
                            ),
                            "steps_succeeded": steps_succeeded,
                            "steps_failed": 1,
                            "failure_reason": f"terminal_screenshot_failed:{exc}",
                            "results": results,
                            "approved_frames": _approved_frame_paths(results),
                            "metrics": _build_metrics(results, len(initial_steps), _total_retries),
                        }
                    steps_succeeded += 1
                results.append(step_result)
                if not found:
                    if results[:-1]:
                        previous_result = results[-2]
                        if (
                            str(previous_result.get("step", {}).get("action") or "") == "click"
                            and not _should_keep_click_screenshots(previous_result)
                        ):
                            _discard_step_screenshots(previous_result)
                    _attach_ab_failure_diagnostics(cli, step_result)
                    return {
                        "success": False,
                        "final_outcome": _classify_final_outcome(
                            success=False,
                            failure_reason="terminal_not_reached",
                        ),
                        "steps_succeeded": steps_succeeded,
                        "steps_failed": 1,
                        "failure_reason": "terminal_not_reached",
                        "results": results,
                            "metrics": _build_metrics(results, len(initial_steps), _total_retries),
                        }
                step_idx += 1
                continue




            if action == "click":
                intent = derive_intent(step)
                if not intent:
                    step_result.update({
                        "outcome": "click_failed",
                        "status": "failed",
                        "error": "no_intent",
                    })
                    step_result["step_latency_ms"] = int((time.monotonic() - _step_t0) * 1000)
                    results.append(step_result)
                    _log("ab_runner.no_intent", {"index": step_idx, "step": step})
                    return {
                        "success": False,
                        "final_outcome": _classify_final_outcome(
                            success=False,
                            failure_reason="click_failed:no_intent",
                        ),
                        "steps_succeeded": steps_succeeded,
                        "steps_failed": 1,
                        "failure_reason": "click_failed:no_intent",
                        "results": results,
                        "metrics": _build_metrics(results, len(initial_steps), _total_retries),
                    }

                step_result["intent"] = intent
                validation_condition = _extract_validation_condition(step)
                if validation_condition is not None:
                    step_result["validation_condition"] = validation_condition

                outcome = "click_failed"
                attempts_used = 0
                stale_ref_retry_used = False
                click_attempt_limit = min(max_retries_per_step, MAX_RETRIES_PER_STEP)
                step_result["stale_ref_count"] = 0

                runtime_recovered = False
                previous_step_unvalidated = bool(
                    results and str(results[-1].get("outcome") or "") == "unvalidated"
                )

                for attempt in range(1, click_attempt_limit + 1):
                    attempts_used = attempt
                    _log("ab_runner.click_attempt", {
                        "index": step_idx, "attempt": attempt, "intent": intent,
                    })

                    next_step = queue[step_idx + 1] if step_idx + 1 < len(queue) else {}
                    post_click_wait_ms = (
                        2000
                        if str(next_step.get("action") or "") == "assert_terminal"
                        else 0
                    )

                    try:
                        attempt_result = _run_ab_click_attempt(
                            cli=cli,
                            step=step,
                            step_result=step_result,
                            screenshot_dir=screenshot_dir,
                            shot_idx=shot_idx,
                            attempt=attempt,
                            click_attempt_limit=click_attempt_limit,
                            mode=mode,
                            extract_snapshot=lambda **kwargs: extract_ab_context(cli, **kwargs),
                            post_click_wait_ms=post_click_wait_ms,
                            remaining_steps=queue[step_idx + 1 :],
                            used_labels=used_click_labels(queue, before_index=step_idx),
                        )
                    except AgentBrowserError as exc:
                        outcome = "stale_ref_unrecovered" if stale_ref_retry_used else "click_failed"
                        step_result["error"] = f"snapshot_failed:{exc}"
                        _attach_ab_failure_diagnostics(cli, step_result)
                        _log("ab_runner.snapshot_failed", {"index": step_idx, "attempt": attempt})
                        break

                    attempt_screenshots = list(attempt_result["attempt_screenshots"])
                    shot_idx = int(attempt_result["shot_idx"])
                    if attempt_result.get("bound_step"):
                        step = attempt_result["bound_step"]
                        queue[step_idx] = step
                        step_result["step"] = step
                        intent = str(step.get("label") or intent)
                        step_result["intent"] = intent
                    if attempt_result.get("drop_labels"):
                        queue = drop_labels_from_queue(
                            queue,
                            after_index=step_idx,
                            labels=list(attempt_result.get("drop_labels") or []),
                        )
                    if attempt_result.get("skipped"):
                        outcome = "skipped"
                        step_result["skipped"] = True
                        step_result["skip_reason"] = attempt_result.get("skip_reason") or ""
                        step_result["validation_passed"] = False
                        break

                    if attempt_result["retry"]:
                        found_ref = _scroll_to_find(
                            cli,
                            intent=intent,
                            selector=str(step.get("selector") or ""),
                        )
                        if found_ref:
                            _log(
                                "ab_runner.no_match_scroll_retry",
                                {
                                    "index": step_idx,
                                    "attempt": attempt,
                                    "intent": intent,
                                    "ref": found_ref,
                                },
                            )
                            continue
                        attempt_result["error"] = "selection_failed:no_match"
                        step_result["error"] = "selection_failed:no_match"
                        outcome = "click_failed"

                    if attempt_result["stale_ref_error"]:
                        click_target = str(attempt_result["click_target"] or "")
                        error_message = str(attempt_result["error"])
                        step_result["error"] = error_message
                        step_result["stale_ref_count"] = int(step_result.get("stale_ref_count", 0)) + 1
                        _discard_screenshots(attempt_screenshots)
                        _log("ab_runner.stale_ref", {
                            "index": step_idx,
                            "attempt": attempt,
                            "ref": click_target,
                            "error": error_message,
                            "stale_ref_count": step_result["stale_ref_count"],
                        })
                        if stale_ref_retry_used or attempt >= click_attempt_limit:
                            _attach_ab_failure_diagnostics(cli, step_result)
                            outcome = "stale_ref_unrecovered"
                            break
                        stale_ref_retry_used = True
                        outcome = "stale_ref"
                        continue

                    if attempt_result["error"]:
                        error_message = str(attempt_result["error"])
                        if (
                            error_message == "selection_failed:no_match"
                            and _replans_used < MAX_AB_REPLANS_PER_RUN
                        ):
                            recovery = _recover_ab_prerequisite_steps(
                                objective=objective,
                                steps=queue,
                                step_index=step_idx,
                                current_step=step,
                                current_intent=intent,
                                snap_after=attempt_result["snap_before"] or {},
                                mode=mode,
                                trigger_reason=(
                                    "selection_failed_after_unvalidated"
                                    if previous_step_unvalidated
                                    else "selection_failed_current_step"
                                ),
                                current_step_completed_unvalidated=previous_step_unvalidated,
                                state_changed=None,
                            )
                            _total_retries += int(recovery.get("attempts_used", 0))
                            if recovery.get("recovered"):
                                _replans_used += 1
                                _discard_screenshots(attempt_screenshots)
                                step_result["runtime_recovery"] = {
                                    "triggered": True,
                                    "trigger_reason": (
                                        "selection_failed_after_unvalidated"
                                        if previous_step_unvalidated
                                        else "selection_failed_current_step"
                                    ),
                                    "blocked_intent": recovery.get("blocked_intent", ""),
                                    "next_intent": recovery.get("next_intent", ""),
                                }
                                queue[step_idx:step_idx + 1] = list(
                                    recovery.get("replacement_steps") or []
                                )
                                runtime_recovered = True
                                _log("ab_runner.prerequisite_recovered", {
                                    "index": step_idx,
                                    "attempt": attempt,
                                    "intent": intent,
                                    "trigger_reason": step_result["runtime_recovery"]["trigger_reason"],
                                    "blocked_intent": recovery.get("blocked_intent", ""),
                                    "next_intent": recovery.get("next_intent", ""),
                                })
                                break
                        outcome = "stale_ref_unrecovered" if stale_ref_retry_used else "click_failed"
                        step_result["error"] = error_message
                        _attach_ab_failure_diagnostics(cli, step_result)
                        _log("ab_runner.selection_failed", {
                            "index": step_idx,
                            "attempt": attempt,
                            "reason": step_result["error"],
                            "intent": intent,
                        })
                        break

                    click_target = str(attempt_result["click_target"] or "")
                    action_key = str(attempt_result["action_key"] or "")
                    if action_key == last_action_key:
                        outcome = "stale_ref_unrecovered" if stale_ref_retry_used else "click_failed"
                        step_result["error"] = "repeated_action"
                        _attach_ab_failure_diagnostics(cli, step_result)
                        _log("ab_runner.repeated_action", {
                            "index": step_idx,
                            "ref": click_target,
                            "url": str(step_result.get("url_before") or ""),
                        })
                        break

                    snap_after = attempt_result["snap_after"]
                    state_changed = bool(attempt_result["state_changed"])
                    validation = attempt_result["validation"]
                    condition = validation["condition"] if validation else None
                    if condition is None:
                        inferred_validation = _infer_runtime_validation(
                            steps=queue,
                            step_index=step_idx,
                            snap_before=attempt_result["snap_before"] or {},
                            snap_after=snap_after or {},
                            mode=mode,
                        )
                        if inferred_validation["passed"]:
                            validation = inferred_validation
                            condition = inferred_validation["condition"]
                            step_result.update({
                                "validation_result": inferred_validation,
                                "validation_type": condition["type"] if condition else "",
                                "validation_value": condition["value"] if condition else "",
                                "validation_source": inferred_validation["source"],
                                "validation_passed": True,
                                "validation_actual": inferred_validation["actual"],
                            })

                    validation_already_passed = bool(validation and validation.get("passed"))
                    if (
                        not state_changed
                        and not validation_already_passed
                        and _replans_used < MAX_AB_REPLANS_PER_RUN
                        and (
                            validation_condition is not None
                            or bool(_next_click_intent(queue, step_idx))
                        )
                    ):
                        recovery = _recover_ab_prerequisite_steps(
                            objective=objective,
                            steps=queue,
                            step_index=step_idx,
                            current_step=step,
                            current_intent=intent,
                            snap_after=snap_after,
                            mode=mode,
                            trigger_reason="state_unchanged",
                            current_step_completed_unvalidated=False,
                            state_changed=state_changed,
                        )
                        _total_retries += int(recovery.get("attempts_used", 0))
                        if recovery.get("recovered"):
                            _replans_used += 1
                            _discard_screenshots(attempt_screenshots)
                            step_result["runtime_recovery"] = {
                                "triggered": True,
                                "trigger_reason": "state_unchanged",
                                "blocked_intent": recovery.get("blocked_intent", ""),
                                "next_intent": recovery.get("next_intent", ""),
                            }
                            queue[step_idx:step_idx + 1] = list(
                                recovery.get("replacement_steps") or []
                            )
                            runtime_recovered = True
                            _log("ab_runner.prerequisite_recovered", {
                                "index": step_idx,
                                "attempt": attempt,
                                "intent": intent,
                                "next_intent": recovery.get("next_intent", ""),
                            })
                            break

                    if condition is None:
                        if _replans_used < MAX_AB_REPLANS_PER_RUN:
                            recovery = _recover_ab_prerequisite_steps(
                                objective=objective,
                                steps=queue,
                                step_index=step_idx,
                                current_step=step,
                                current_intent=intent,
                                snap_after=snap_after,
                                mode=mode,
                                trigger_reason="unvalidated_click",
                                current_step_completed_unvalidated=False,
                                state_changed=state_changed,
                            )
                            _total_retries += int(recovery.get("attempts_used", 0))
                            if recovery.get("recovered"):
                                _replans_used += 1
                                _discard_screenshots(attempt_screenshots)
                                step_result["runtime_recovery"] = {
                                    "triggered": True,
                                    "trigger_reason": "unvalidated_click",
                                    "blocked_intent": recovery.get("blocked_intent", ""),
                                    "next_intent": recovery.get("next_intent", ""),
                                }
                                queue[step_idx:step_idx + 1] = list(
                                    recovery.get("replacement_steps") or []
                                )
                                runtime_recovered = True
                                _log("ab_runner.prerequisite_recovered", {
                                    "index": step_idx,
                                    "attempt": attempt,
                                    "intent": intent,
                                    "trigger_reason": "unvalidated_click",
                                    "next_intent": recovery.get("next_intent", ""),
                                })
                                break
                        outcome = "wrong_click"
                        step_result["validation_failure_reason"] = (
                            "validation_failed:no_runtime_validation_signal"
                        )
                        _discard_screenshots(attempt_screenshots)
                        _log("ab_runner.unvalidated", {
                            "index": step_idx,
                            "attempt": attempt,
                            "ref": click_target,
                            "state_changed": state_changed,
                        })
                        break

                    if validation and validation["passed"]:
                        attempt_outcome = str(attempt_result["outcome"] or "")
                        outcome = "success" if attempt_outcome == "unvalidated" else attempt_outcome
                        last_action_key = action_key
                        _log("ab_runner.validation_passed", {
                            "index": step_idx, "attempt": attempt,
                            "url_before": step_result.get("url_before", ""),
                            "url_after": step_result.get("url_after", ""),
                            "validation_type": condition["type"],
                            "validation_source": validation["source"],
                        })
                        break
                    outcome = str(attempt_result["outcome"])
                    _discard_screenshots(attempt_screenshots)
                    _log("ab_runner.wrong_click", {
                        "index": step_idx, "attempt": attempt,
                        "ref": click_target,
                        "intent": intent,
                        "validation_type": condition["type"],
                        "validation_failure_reason": validation["failure_reason"] if validation else "",
                    })
                    break


                if runtime_recovered:
                    continue
                _total_retries += max(attempts_used - 1, 0)
                step_result["outcome"] = outcome
                step_result["step_latency_ms"] = int((time.monotonic() - _step_t0) * 1000)
                if outcome == "skipped":
                    step_result["status"] = "skipped"
                    step_result["validation_passed"] = False
                    results.append(step_result)
                    step_idx += 1
                    continue


                _FATAL_OUTCOMES = frozenset({
                    "click_failed",
                    "stale_ref",
                    "stale_ref_unrecovered",
                    "wrong_click",
                })
                if outcome in _FATAL_OUTCOMES:
                    if outcome == "wrong_click" and _flow_restarts_used < MAX_AB_FLOW_RESTARTS:
                        replay_steps = _validated_milestone_steps(results)
                        if replay_steps:
                            from app.execution.ab_recovery import (
                                milestone_screenshot_slots,
                                restore_restart_frames,
                            )

                            frame_slots = milestone_screenshot_slots(results)
                            _flow_restarts_used += 1
                            _log("ab_runner.flow_restart", {
                                "index": step_idx,
                                "restart_number": _flow_restarts_used,
                                "validated_milestones": len(replay_steps),
                                "intent": str(step_result.get("intent") or ""),
                            })
                            for existing_result in results:
                                existing_result.pop("screenshot_path", None)
                                existing_result["before_screenshot"] = ""
                                existing_result["after_screenshot"] = ""
                            for old in screenshot_dir.glob("shot*.png"):
                                old.unlink()
                            shot_idx = 1
                            try:
                                cli.close()
                            except Exception:
                                pass
                            cli = AgentBrowserCLI(session=session)
                            try:
                                replay = _replay_ab_milestones(
                                    cli=cli,
                                    preview_url=preview_url,
                                    steps=replay_steps,
                                    mode=mode,
                                    capture_settings=cs,
                                    screenshot_dir=screenshot_dir,
                                )
                            except AgentBrowserError as exc:
                                replay = {
                                    "success": False,
                                    "error": f"restart_failed:{exc}",
                                }
                            frames_restored = bool(replay.get("success")) and restore_restart_frames(
                                results,
                                frame_slots,
                                list(replay.get("frames") or []),
                            )
                            if frames_restored:
                                shot_idx = int(replay.get("shot_idx") or 1)
                                last_action_key = None
                                step_result["restart_recovery"] = {
                                    "triggered": True,
                                    "restart_number": _flow_restarts_used,
                                    "replayed_steps": len(replay_steps),
                                }
                                continue
                            step_result["restart_recovery"] = {
                                "triggered": True,
                                "restart_number": _flow_restarts_used,
                                "replayed_steps": len(replay_steps),
                                "error": (
                                    replay.get("error")
                                    if not replay.get("success")
                                    else "restart_frames_missing"
                                ),
                            }
                    step_result["status"] = "failed"
                    if not _should_keep_click_screenshots(step_result):
                        _discard_step_screenshots(step_result)
                    if "diagnostics" not in step_result:
                        _attach_ab_failure_diagnostics(cli, step_result)
                    results.append(step_result)
                    return {
                        "success": False,
                        "final_outcome": _classify_final_outcome(
                            success=False,
                            failure_reason=outcome,
                        ),
                        "steps_succeeded": steps_succeeded,
                        "steps_failed": 1,
                        "failure_reason": outcome,
                        "results": results,
                        "metrics": _build_metrics(results, len(initial_steps), _total_retries),
                    }

                step_result["status"] = "ok"
                if not _should_keep_click_screenshots(step_result):
                    _discard_step_screenshots(step_result)
                steps_succeeded += 1
                results.append(step_result)
                step_idx += 1
                continue




            step_result.update({"outcome": "click_failed", "status": "failed", "error": f"unknown_action:{action}"})
            _attach_ab_failure_diagnostics(cli, step_result)
            step_result["step_latency_ms"] = int((time.monotonic() - _step_t0) * 1000)
            _log("ab_runner.unknown_action", {"index": step_idx, "action": action})
            results.append(step_result)
            return {
                "success": False,
                "final_outcome": _classify_final_outcome(
                    success=False,
                    failure_reason=f"click_failed:unknown_action:{action}",
                ),
                "steps_succeeded": steps_succeeded,
                "steps_failed": 1,
                "failure_reason": f"click_failed:unknown_action:{action}",
                "results": results,
                "metrics": _build_metrics(results, len(initial_steps), _total_retries),
            }


    except AgentBrowserError as exc:
        _log("ab_runner.fatal_error", {"error": str(exc)})
        return {
            "success": False,
            "final_outcome": _classify_final_outcome(
                success=False,
                failure_reason=f"agent_browser_error:{exc}",
            ),
            "steps_succeeded": steps_succeeded,
            "steps_failed": 1,
            "failure_reason": f"agent_browser_error:{exc}",
            "results": results,
            "metrics": _build_metrics(results, len(initial_steps), _total_retries),
        }
    finally:

        try:
            cli.close()
        except Exception:
            pass

    _log("ab_runner.complete", {
        "steps_succeeded": steps_succeeded, "total_results": len(results),
    })
    return {
        "success": True,
        "final_outcome": _classify_final_outcome(success=True),
        "steps_succeeded": steps_succeeded,
        "steps_failed": 0,
        "results": results,
        "approved_frames": _approved_frame_paths(results),
        "metrics": _build_metrics(results, len(initial_steps), _total_retries),
    }
