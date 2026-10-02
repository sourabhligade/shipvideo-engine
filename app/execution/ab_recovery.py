from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.execution.ab_bind import (
    bind_click_step,
    looks_like_cta_intent,
    looks_like_confirm_intent,
    used_click_labels,
)
from app.execution.ab_proof import (
    evaluate_click_validation,
    extract_validation_condition,
    next_click_intent,
    snapshot_has_intent,
    stamp_amount_control_state,
)
from app.execution.ab_settle import configure_ab_session, settle_ab_page
from app.execution.ab_target import resolve_ab_click_target
from app.execution.ab_url import resolve_url
from app.config_types import CaptureSettings
from app.steps.demo_contract import looks_like_amount_chip


def snapshot_to_dom_context(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    current_url = str(snapshot.get("current_url") or "")
    current_path = urlparse(current_url).path or "/"
    buttons: List[Dict[str, Any]] = []
    links: List[Dict[str, Any]] = []

    for element in snapshot.get("interactive_elements") or []:
        if not isinstance(element, dict):
            continue
        role = str(element.get("role") or "").strip().lower()
        name = str(element.get("name") or "").strip()
        if not name:
            continue
        if role == "link":
            links.append({
                "text": name,
                "href": str(element.get("href") or "").strip(),
                "testid": str(element.get("testid") or "").strip(),
                "aria": str(element.get("aria_label") or "").strip(),
                "id": str(element.get("element_id") or "").strip(),
            })
        else:
            buttons.append({
                "text": name,
                "testid": str(element.get("testid") or "").strip(),
                "aria": str(element.get("aria_label") or "").strip(),
                "title": "",
                "id": str(element.get("element_id") or "").strip(),
                "selector": "",
            })

    return {
        "current_path": current_path,
        "routes": [current_path, "/"],
        "buttons": buttons,
        "links": links,
        "inputs": [],
        "data_testids": [],
        "headings": list(snapshot.get("headings") or []),
        "active_surfaces": list(snapshot.get("active_surfaces") or []),
    }


def deterministic_setup_chip_from_snapshot(
    snap: Dict[str, Any],
    *,
    blocked_intent: str,
    existing_labels: Optional[set[str]] = None,
) -> Optional[Dict[str, Any]]:
    seen = {item.casefold() for item in (existing_labels or set()) if item}
    if any(looks_like_amount_chip(label) for label in (existing_labels or set())):
        return None
    blocked_l = (blocked_intent or "").strip().casefold()
    amount_names: List[str] = []
    role_names: List[str] = []
    for bucket in ("interactive_elements", "context_elements"):
        for element in snap.get(bucket) or []:
            if not isinstance(element, dict):
                continue
            name = str(element.get("name") or "").strip()
            role = str(element.get("role") or "").strip().lower()
            if not name:
                continue
            key = name.casefold()
            if not key or key == blocked_l or key in seen:
                continue
            if looks_like_amount_chip(name):
                amount_names.append(name)
                seen.add(key)
                continue
            if role in {"radio", "option", "tab"}:
                role_names.append(name)
                seen.add(key)
    chosen = amount_names[0] if amount_names else (role_names[0] if role_names else "")
    if not chosen:
        return None
    proof = {
        "type": "element_present",
        "value": blocked_intent,
    }
    return {
        "action": "click",
        "label": chosen,
        "kind": "amount",
        "validation_condition": dict(proof),
        "success_condition": dict(proof),
        "validation_source": "deterministic_setup_recovery",
    }


def _regenerate_with_feedback(**kwargs: Any) -> Any:
    from app.execution.step_runner import regenerate_with_feedback

    return regenerate_with_feedback(**kwargs)


def recover_ab_prerequisite_steps(
    *,
    objective: Optional[Dict[str, Any]],
    steps: List[Dict[str, Any]],
    step_index: int,
    current_step: Dict[str, Any],
    current_intent: str,
    snap_after: Dict[str, Any],
    mode: str,
    trigger_reason: str = "state_unchanged",
    current_step_completed_unvalidated: bool = False,
    state_changed: Optional[bool] = None,
) -> Dict[str, Any]:
    if not objective or current_step.get("_ab_recovery_attempted"):
        return {"recovered": False, "attempts_used": 0}

    next_intent = next_click_intent(steps, step_index)
    blocked_intent = (
        next_intent or current_intent
        if trigger_reason == "state_unchanged"
        else current_intent
    )
    if not blocked_intent:
        return {"recovered": False, "attempts_used": 0}
    if snapshot_has_intent(snap_after, intent=blocked_intent, mode=mode):
        return {
            "recovered": False,
            "attempts_used": 0,
            "next_intent": next_intent,
            "blocked_intent": blocked_intent,
            "blocked_target_present": True,
        }

    existing_labels = {
        str(step.get("label") or "").strip()
        for step in steps[:step_index]
        if str(step.get("action") or "") in {"click", "select", "check"}
    }

    if looks_like_cta_intent(blocked_intent):
        if looks_like_confirm_intent(blocked_intent) and any(
            looks_like_amount_chip(label) for label in existing_labels
        ):
            return {
                "recovered": False,
                "attempts_used": 0,
                "next_intent": next_intent,
                "blocked_intent": blocked_intent,
                "blocked_target_present": False,
                "skip_blocked": True,
            }
        chip_step = deterministic_setup_chip_from_snapshot(
            snap_after,
            blocked_intent=blocked_intent,
            existing_labels=existing_labels,
        )
        if chip_step:
            retried_step = dict(current_step)
            retried_step["_ab_recovery_attempted"] = True
            return {
                "recovered": True,
                "attempts_used": 0,
                "next_intent": next_intent,
                "blocked_intent": blocked_intent,
                "blocked_target_present": False,
                "replacement_steps": [chip_step, retried_step],
                "recovery_source": "deterministic_setup_chip",
            }

    from app.execution.ab_objective import (
        allowed_routes_from_objective,
        merge_allowed_routes_into_dom_ctx,
    )

    ab_allowed = allowed_routes_from_objective(objective)
    ab_dom = merge_allowed_routes_into_dom_ctx(
        snapshot_to_dom_context(snap_after), ab_allowed
    )
    regenerated, attempts = _regenerate_with_feedback(
        objective=objective,
        dom_context=ab_dom,
        error_context={
            "error": "prerequisite_failure",
            "trigger_reason": trigger_reason,
            "failed_step": current_step,
            "current_intent": current_intent,
            "blocked_intent": blocked_intent,
            "next_intent": next_intent,
            "current_step_completed_unvalidated": current_step_completed_unvalidated,
            "state_changed": state_changed,
            "current_url": str(snap_after.get("current_url") or ""),
        },
        max_attempts=1,
        page=None,
        allowed_routes=ab_allowed,
    )
    if not regenerated:
        return {
            "recovered": False,
            "attempts_used": len(attempts),
            "next_intent": next_intent,
            "blocked_intent": blocked_intent,
            "blocked_target_present": False,
        }

    retried_step = dict(current_step)
    retried_step["_ab_recovery_attempted"] = True
    return {
        "recovered": True,
        "attempts_used": len(attempts),
        "next_intent": next_intent,
        "blocked_intent": blocked_intent,
        "blocked_target_present": False,
        "replacement_steps": regenerated + [retried_step],
    }


def validated_milestone_steps(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    milestones: List[Dict[str, Any]] = []
    for result in results:
        if str(result.get("status") or "") != "ok":
            continue
        if str(result.get("outcome") or "") != "success":
            continue
        step = result.get("step") or {}
        action = str(step.get("action") or "")
        if action in {"goto", "click"}:
            milestones.append(dict(step))
    return milestones


def replay_ab_milestones(
    *,
    cli: Any,
    preview_url: str,
    steps: List[Dict[str, Any]],
    mode: str,
    capture_settings: CaptureSettings,
) -> Dict[str, Any]:
    from app.browser.ref_selector import derive_intent
    from app.context.dom_extractor import extract_ab_context

    cli.open(preview_url)
    configure_ab_session(cli, capture_settings)
    settle_ab_page(cli, require_networkidle=False)

    for idx, step in enumerate(steps):
        action = str(step.get("action") or "")
        if action == "goto":
            url = step.get("url") or "/"
            cli.open(resolve_url(preview_url, url))
            settle_ab_page(cli, require_networkidle=False)
            continue
        if action != "click":
            continue

        snap_before = extract_ab_context(cli, save_raw=False)
        bind = bind_click_step(
            step,
            snap_before,
            remaining_steps=steps[idx + 1 :],
            used_labels=used_click_labels(steps, before_index=idx),
        )
        if bind.skip:
            continue
        step = bind.step
        intent = derive_intent(step)
        if not intent:
            return {"success": False, "error": "replay_missing_intent", "index": idx}

        resolution = resolve_ab_click_target(
            cli,
            intent=intent,
            snapshot=snap_before,
            mode=mode,
            allow_scroll_retry=True,
            selector=str(step.get("selector") or ""),
        )
        click_target = str(resolution.get("chosen_ref") or "")
        if not click_target:
            return {
                "success": False,
                "error": f"replay_selection_failed:{resolution.get('selection_reason', 'no_match')}",
                "index": idx,
                "intent": intent,
            }

        stamp_amount_control_state(cli, snap_before, step, chip_ref=click_target)
        cli.click(click_target)
        settle_ab_page(
            cli,
            validation_condition=extract_validation_condition(step),
        )
        snap_after = extract_ab_context(cli, save_raw=False)
        stamp_amount_control_state(cli, snap_after, step)
        validation = evaluate_click_validation(
            step=step,
            snap_before=snap_before,
            snap_after=snap_after,
        )
        if not validation["passed"]:
            return {
                "success": False,
                "error": validation.get("failure_reason") or "replay_validation_failed",
                "index": idx,
                "intent": intent,
            }
    return {"success": True}
