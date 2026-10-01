from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.sync_api import Page

from app.config_types import CaptureSettings


def _execute_one(
    page: Page,
    base_url: str,
    step: Dict[str, Any],
    out_dir: Path,
    shot_idx: int,
    *,
    full_page: bool = False,
) -> tuple[bool, int, str | None]:
    from app.execution import step_runner as sr
    _resolve_url = sr._resolve_url
    _extract_validation_condition = sr._extract_validation_condition
    _wait_for_playwright_validation = sr._wait_for_playwright_validation
    _assert_playwright_terminal_condition = sr._assert_playwright_terminal_condition
    action = step.get("action")
    if action == "goto":
        page.goto(_resolve_url(base_url, step.get("url") or "/"), wait_until="domcontentloaded", timeout=15000)
        return True, shot_idx, None
    if action == "click":
        selector = (step.get("selector") or "").strip()
        text = (step.get("label") or step.get("text") or "").strip()
        validation_condition = _extract_validation_condition(step)
        loc = None
        if selector:
            loc = page.locator(selector)
            try:
                count = loc.count()
            except Exception:
                count = 0
            if count == 0:
                return False, shot_idx, f"selector_not_found_on_page:{selector}"
            if count > 1:
                return False, shot_idx, f"selector_not_unique:{selector}:count={count}"
        elif text:
            loc = page.get_by_text(text, exact=True)
            try:
                count = loc.count()
            except Exception:
                count = 0
            if count == 0:
                return False, shot_idx, f"label_not_found_on_page:{text}"
            if count > 1:
                return False, shot_idx, f"label_not_unique:{text}:count={count}"
        else:
            return False, shot_idx, "missing_click_target"
        if validation_condition is None:
            return False, shot_idx, "missing_validation_condition"
        loc.first.click(timeout=8000)
        try:
            _wait_for_playwright_validation(page, validation_condition)
        except Exception:
            return (
                False,
                shot_idx,
                f"validation_failed:{validation_condition['type']}:{validation_condition['value']}",
            )
        return True, shot_idx, None
    if action == "screenshot":
        path = out_dir / f"shot{shot_idx}.png"
        page.screenshot(path=str(path), full_page=full_page)
        return True, shot_idx + 1, None
    if action == "assert_terminal":
        ok, reason = _assert_playwright_terminal_condition(page, step)
        if ok:
            return True, shot_idx, None
        return False, shot_idx, reason or "terminal_not_reached"
    return False, shot_idx, f"unknown_action:{action}"


def run_stepwise(
    *,
    preview_url: str,
    initial_steps: List[Dict[str, Any]],
    objective: Dict[str, Any],
    screenshot_dir: Path,
    max_retries_per_failure: int = 3,
    capture_settings: Optional[CaptureSettings] = None,
) -> Dict[str, Any]:
    from app.execution import step_runner as sr
    sync_playwright = sr.sync_playwright
    wait_stable_after_navigation = sr.wait_stable_after_navigation
    extract_dom_context = sr.extract_dom_context
    capture_state = sr.capture_state
    detect_major_change = sr.detect_major_change
    _execute_one = sr._execute_one
    validate_step_against_dom = sr.validate_step_against_dom
    regenerate_with_feedback = sr.regenerate_with_feedback
    _log = sr._log
    _classify_final_outcome = sr._classify_final_outcome
    _build_metrics = sr._build_metrics
    _approved_frame_paths = sr._approved_frame_paths
    _merge_allowed_routes_into_dom_ctx = sr._merge_allowed_routes_into_dom_ctx
    _allowed_routes_from_objective = sr._allowed_routes_from_objective
    cs = capture_settings or CaptureSettings()

    for old in screenshot_dir.glob("shot*.png"):
        old.unlink()

    results: List[Dict[str, Any]] = []
    queue: List[Dict[str, Any]] = list(initial_steps)
    shot_idx = 1
    total_retries = 0                                                  

    allowed_routes = _allowed_routes_from_objective(objective)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": cs.viewport_width, "height": cs.viewport_height})
        page.goto(preview_url, wait_until="domcontentloaded", timeout=15000)
        wait_stable_after_navigation(page)
        dom_ctx = _merge_allowed_routes_into_dom_ctx(extract_dom_context(page), allowed_routes)

        i = 0
        while i < len(queue):
            step = queue[i]
            _step_t0 = time.monotonic()                                  

            ok, reason = validate_step_against_dom(
                step, dom_ctx, page=page, allowed_routes=allowed_routes
            )
            if not ok:

                regenerated, attempts = regenerate_with_feedback(
                    objective=objective,
                    dom_context=dom_ctx,
                    error_context={"error": reason, "failed_step": step},
                    max_attempts=max_retries_per_failure,
                    page=page,
                    allowed_routes=allowed_routes,
                )
                total_retries += len(attempts)           
                _log("step.regenerated_on_validation_failure", {"index": i, "reason": reason, "attempts": attempts})
                if not regenerated:
                    browser.close()
                    return {
                        "success": False,
                        "final_outcome": _classify_final_outcome(
                            success=False,
                            failure_reason=f"validation_failed:{reason}",
                        ),
                        "steps_succeeded": len(results),
                        "steps_failed": 1,
                        "failure_reason": f"validation_failed:{reason}",
                        "results": results,
                        "metrics": _build_metrics(results, len(initial_steps), total_retries),
                    }
                queue[i : i + 1] = regenerated
                step = queue[i]

            prev = capture_state(page)
            ok_exec, shot_idx, err = _execute_one(
                page,
                preview_url,
                step,
                screenshot_dir,
                shot_idx,
                full_page=cs.effective_full_page,
            )
            if not ok_exec:
                regenerated, attempts = regenerate_with_feedback(
                    objective=objective,
                    dom_context=dom_ctx,
                    error_context={"error": err or "execution_failed", "failed_step": step},
                    max_attempts=max_retries_per_failure,
                    page=page,
                    allowed_routes=allowed_routes,
                )
                total_retries += len(attempts)           
                _log("step.regenerated_on_execution_failure", {"index": i, "error": err, "attempts": attempts})
                if not regenerated:
                    browser.close()
                    return {
                        "success": False,
                        "final_outcome": _classify_final_outcome(
                            success=False,
                            failure_reason=err or "execution_failed",
                        ),
                        "steps_succeeded": len(results),
                        "steps_failed": 1,
                        "failure_reason": err or "execution_failed",
                        "results": results,
                        "metrics": _build_metrics(results, len(initial_steps), total_retries),
                    }
                queue[i : i + 1] = regenerated
                continue

            _step_latency_ms = int((time.monotonic() - _step_t0) * 1000)           
            step_result = {"index": i, "step": step, "status": "ok", "step_latency_ms": _step_latency_ms}
            action_name = str(step.get("action") or "")
            if action_name == "goto":
                step_result["outcome"] = "success"
            elif action_name == "click":
                step_result["outcome"] = "success"
                step_result["validation_passed"] = True
            elif action_name == "screenshot":
                shot_path = screenshot_dir / f"shot{shot_idx - 1}.png"
                step_result["screenshot_path"] = str(shot_path)
                step_result["outcome"] = "success"
            elif action_name == "assert_terminal":
                step_result["outcome"] = "success"
                step_result["terminal_condition_reached"] = True
            results.append(step_result)

            now = capture_state(page)
            nav_changed = detect_major_change(prev, now)
            if nav_changed:
                wait_stable_after_navigation(page)
                dom_ctx = _merge_allowed_routes_into_dom_ctx(
                    extract_dom_context(page), allowed_routes
                )

                remaining_objective = {**objective, "remaining_from_index": i + 1}
                regenerated, attempts = regenerate_with_feedback(
                    objective=remaining_objective,
                    dom_context=dom_ctx,
                    error_context={"event": "navigation_boundary", "at_index": i},
                    max_attempts=max_retries_per_failure,
                    page=page,
                    allowed_routes=allowed_routes,
                )
                total_retries += len(attempts)
                _log("navigation.reanchored", {"index": i, "attempts": attempts})
                if not regenerated:
                    browser.close()
                    return {
                        "success": False,
                        "final_outcome": _classify_final_outcome(
                            success=False,
                            failure_reason="navigation_reanchor_failed",
                        ),
                        "steps_succeeded": len(results),
                        "steps_failed": 1,
                        "failure_reason": "navigation_reanchor_failed",
                        "results": results,
                        "metrics": _build_metrics(results, len(initial_steps), total_retries),
                    }
                queue = queue[: i + 1] + regenerated
            i += 1

        browser.close()

    return {
        "success": True,
        "final_outcome": _classify_final_outcome(success=True),
        "steps_succeeded": len(results),
        "steps_failed": 0,
        "results": results,
        "approved_frames": _approved_frame_paths(results),
        "metrics": _build_metrics(results, len(initial_steps), total_retries),
    }
