from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional


def _run_ab_changed_testid_search(
    *,
    cli: Any,
    preview_url: str,
    screenshot_dir: Path,
    objective: Optional[Dict[str, Any]],
    mode: str,
    max_retries_per_step: int,
    session_config: Dict[str, Any],
) -> Dict[str, Any]:
    from app.execution import step_runner as sr
    _objective_changed_testids = sr._objective_changed_testids
    _objective_contract = sr._objective_contract
    _objective_start_route = sr._objective_start_route
    _resolve_url = sr._resolve_url
    _settle_ab_page = sr._settle_ab_page
    _snapshot_contains_testid = sr._snapshot_contains_testid
    _append_search_screenshot_result = sr._append_search_screenshot_result
    _make_search_validation = sr._make_search_validation
    _first_active_surface = sr._first_active_surface
    _run_ab_click_attempt = sr._run_ab_click_attempt
    _discard_step_screenshots = sr._discard_step_screenshots
    _attach_ab_failure_diagnostics = sr._attach_ab_failure_diagnostics
    _ab_snapshot_to_dom_context = sr._ab_snapshot_to_dom_context
    regenerate_single_step_toward_testid = sr.regenerate_single_step_toward_testid
    _classify_final_outcome = sr._classify_final_outcome
    _build_metrics = sr._build_metrics
    _approved_frame_paths = sr._approved_frame_paths
    MAX_TESTID_SEARCH_ACTIONS = sr.MAX_TESTID_SEARCH_ACTIONS
    from app.browser.agent_browser_cli import AgentBrowserError
    from app.context.dom_extractor import extract_ab_context

    changed_testids = _objective_changed_testids(objective)
    contract = _objective_contract(objective)
    start_route = _objective_start_route(objective) or "/"
    results: List[Dict[str, Any]] = []
    shot_idx = 1
    steps_succeeded = 0
    total_retries = 0
    actions_used = 0

    if start_route:
        full_url = _resolve_url(preview_url, start_route)
        cli.open(full_url)
        settle = _settle_ab_page(cli, require_networkidle=False)
        results.append(
            {
                "index": 0,
                "step": {"action": "goto", "url": start_route},
                "status": "ok",
                "outcome": "success",
                "backend": "agent_browser_cli",
                "mode": mode,
                "page_settle": settle,
                "session_viewport": session_config,
                "step_latency_ms": 0,
            }
        )
        steps_succeeded += 1

    for index, testid in enumerate(changed_testids):
        while actions_used < MAX_TESTID_SEARCH_ACTIONS:
            snapshot = extract_ab_context(cli, save_raw=(actions_used == 0))
            if _snapshot_contains_testid(snapshot, testid):
                ref = cli.find_testid(testid)
                if ref:
                    try:
                        cli.scroll_into_view(ref)
                    except Exception:
                        pass
                shot_idx = _append_search_screenshot_result(
                    cli=cli,
                    screenshot_dir=screenshot_dir,
                    shot_idx=shot_idx,
                    results=results,
                    label=f"Changed target visible: {testid}",
                    mode=mode,
                )
                steps_succeeded += 1

                next_proof = ""
                if index + 1 < len(changed_testids):
                    next_proof = changed_testids[index + 1]
                elif contract is not None and getattr(contract, "terminal", None) is not None:
                    next_proof = str(getattr(getattr(contract, "terminal"), "value", "") or "").strip()

                if not next_proof:
                    results.append(
                        {
                            "index": len(results),
                            "step": {
                                "action": "assert_terminal",
                                "expected_element": testid,
                            },
                            "status": "ok",
                            "outcome": "success",
                            "backend": "agent_browser_cli",
                            "mode": mode,
                            "terminal_condition_reached": True,
                            "terminal_validation_source": "changed_testid_visible",
                            "terminal_validation_actual": testid,
                            "step_latency_ms": 0,
                        }
                    )
                    steps_succeeded += 1
                    return {
                        "success": True,
                        "final_outcome": _classify_final_outcome(success=True),
                        "steps_succeeded": steps_succeeded,
                        "steps_failed": 0,
                        "results": results,
                        "approved_frames": _approved_frame_paths(results),
                        "metrics": _build_metrics(results, max(len(changed_testids), 1), total_retries),
                    }

                actions_used += 1
                click_step = {
                    "action": "click",
                    "selector": f"[data-testid='{testid}']",
                    "label": "",
                    "text": "",
                    "validation_condition": _make_search_validation(next_proof),
                    "success_condition": _make_search_validation(next_proof),
                    "validation_source": "changed_testid_search",
                    "preferred_testids": [testid],
                    "preferred_surface": _first_active_surface(snapshot),
                    "preferred_texts": [testid, next_proof],
                }
                step_result = {
                    "index": len(results),
                    "step": click_step,
                    "status": "failed",
                    "outcome": "pending",
                    "backend": "agent_browser_cli",
                    "mode": mode,
                    "intent": testid,
                    "validation_condition": click_step["validation_condition"],
                    "search_target_testid": testid,
                }
                attempt_result = _run_ab_click_attempt(
                    cli=cli,
                    step=click_step,
                    step_result=step_result,
                    screenshot_dir=screenshot_dir,
                    shot_idx=shot_idx,
                    attempt=1,
                    click_attempt_limit=1,
                    mode=mode,
                    extract_snapshot=lambda **kwargs: extract_ab_context(cli, **kwargs),
                    post_click_wait_ms=0,
                )
                shot_idx = int(attempt_result["shot_idx"])
                step_result["outcome"] = str(attempt_result["outcome"] or "click_failed")
                step_result["status"] = "ok" if step_result["outcome"] == "success" else "failed"
                step_result["step_latency_ms"] = 0
                if attempt_result["validation"] is not None:
                    validation = attempt_result["validation"]
                    condition = validation["condition"]
                    step_result.update(
                        {
                            "validation_result": validation,
                            "validation_type": condition["type"] if condition else "",
                            "validation_value": condition["value"] if condition else "",
                            "validation_source": validation["source"],
                            "validation_passed": validation["passed"],
                            "validation_actual": validation["actual"],
                        }
                    )
                if attempt_result["error"]:
                    step_result["error"] = str(attempt_result["error"])
                if step_result["status"] != "ok":
                    _discard_step_screenshots(step_result)
                    _attach_ab_failure_diagnostics(cli, step_result)
                    results.append(step_result)
                    return {
                        "success": False,
                        "final_outcome": _classify_final_outcome(
                            success=False,
                            failure_reason=str(step_result.get("outcome") or "click_failed"),
                        ),
                        "steps_succeeded": steps_succeeded,
                        "steps_failed": 1,
                        "failure_reason": str(step_result.get("outcome") or "click_failed"),
                        "results": results,
                        "metrics": _build_metrics(results, max(len(changed_testids), 1), total_retries),
                    }
                results.append(step_result)
                steps_succeeded += 1
                break

            dom_context = _ab_snapshot_to_dom_context(snapshot)
            suggested_step, attempts = regenerate_single_step_toward_testid(
                objective=objective or {},
                target_testid=testid,
                snapshot=snapshot,
                dom_context=dom_context,
                max_attempts=max_retries_per_step,
                page=None,
            )
            total_retries += len(attempts)
            if suggested_step is None:
                return {
                    "success": False,
                    "final_outcome": _classify_final_outcome(
                        success=False,
                        failure_reason=f"target_unreachable:{testid}",
                    ),
                    "steps_succeeded": steps_succeeded,
                    "steps_failed": 1,
                    "failure_reason": f"target_unreachable:{testid}",
                    "results": results,
                    "metrics": _build_metrics(results, max(len(changed_testids), 1), total_retries),
                }

            actions_used += 1
            action = str(suggested_step.get("action") or "")
            if action == "goto":
                route = str(suggested_step.get("url") or "").strip()
                full_url = _resolve_url(preview_url, route)
                try:
                    cli.open(full_url)
                    settle = _settle_ab_page(cli, require_networkidle=False)
                except AgentBrowserError as exc:
                    return {
                        "success": False,
                        "final_outcome": _classify_final_outcome(
                            success=False,
                            failure_reason=f"goto_failed:{route}",
                        ),
                        "steps_succeeded": steps_succeeded,
                        "steps_failed": 1,
                        "failure_reason": f"goto_failed:{exc}",
                        "results": results,
                        "metrics": _build_metrics(results, max(len(changed_testids), 1), total_retries),
                    }
                results.append(
                    {
                        "index": len(results),
                        "step": suggested_step,
                        "status": "ok",
                        "outcome": "success",
                        "backend": "agent_browser_cli",
                        "mode": mode,
                        "page_settle": settle,
                        "step_latency_ms": 0,
                    }
                )
                steps_succeeded += 1
                continue

            click_step = dict(suggested_step)
            click_step["validation_condition"] = _make_search_validation(testid)
            click_step["success_condition"] = _make_search_validation(testid)
            click_step["validation_source"] = "changed_testid_search"
            click_step["preferred_testids"] = [testid]
            click_step["preferred_surface"] = _first_active_surface(snapshot)
            click_step["preferred_texts"] = [testid]
            step_result = {
                "index": len(results),
                "step": click_step,
                "status": "failed",
                "outcome": "pending",
                "backend": "agent_browser_cli",
                "mode": mode,
                "intent": str(click_step.get("label") or click_step.get("text") or testid),
                "validation_condition": click_step["validation_condition"],
                "search_target_testid": testid,
            }
            attempt_result = _run_ab_click_attempt(
                cli=cli,
                step=click_step,
                step_result=step_result,
                screenshot_dir=screenshot_dir,
                shot_idx=shot_idx,
                attempt=1,
                click_attempt_limit=1,
                mode=mode,
                extract_snapshot=lambda **kwargs: extract_ab_context(cli, **kwargs),
            )
            shot_idx = int(attempt_result["shot_idx"])
            step_result["outcome"] = str(attempt_result["outcome"] or "click_failed")
            step_result["status"] = "ok" if step_result["outcome"] == "success" else "failed"
            step_result["step_latency_ms"] = 0
            if attempt_result["validation"] is not None:
                validation = attempt_result["validation"]
                condition = validation["condition"]
                step_result.update(
                    {
                        "validation_result": validation,
                        "validation_type": condition["type"] if condition else "",
                        "validation_value": condition["value"] if condition else "",
                        "validation_source": validation["source"],
                        "validation_passed": validation["passed"],
                        "validation_actual": validation["actual"],
                    }
                )
            if attempt_result["error"]:
                step_result["error"] = str(attempt_result["error"])
            if step_result["status"] != "ok":
                _discard_step_screenshots(step_result)
                _attach_ab_failure_diagnostics(cli, step_result)
                results.append(step_result)
                return {
                    "success": False,
                    "final_outcome": _classify_final_outcome(
                        success=False,
                        failure_reason=str(step_result.get("outcome") or "click_failed"),
                    ),
                    "steps_succeeded": steps_succeeded,
                    "steps_failed": 1,
                    "failure_reason": str(step_result.get("outcome") or "click_failed"),
                    "results": results,
                    "metrics": _build_metrics(results, max(len(changed_testids), 1), total_retries),
                }
            results.append(step_result)
            steps_succeeded += 1
        else:
            return {
                "success": False,
                "final_outcome": _classify_final_outcome(
                    success=False,
                    failure_reason=f"target_unreachable:{testid}",
                ),
                "steps_succeeded": steps_succeeded,
                "steps_failed": 1,
                "failure_reason": f"target_unreachable:{testid}",
                "results": results,
                "metrics": _build_metrics(results, max(len(changed_testids), 1), total_retries),
            }

    return {
        "success": False,
        "final_outcome": _classify_final_outcome(
            success=False,
            failure_reason="target_unreachable",
        ),
        "steps_succeeded": steps_succeeded,
        "steps_failed": 1,
        "failure_reason": "target_unreachable",
        "results": results,
        "metrics": _build_metrics(results, max(len(changed_testids), 1), total_retries),
    }
