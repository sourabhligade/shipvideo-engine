from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.browser.agent_browser_types import ABActionabilityResult, ABTargetResolution
from app.execution.constants import (
    AB_SCROLL_RETRY_COUNT,
    AB_SCROLL_RETRY_PX,
    AB_SCROLL_SETTLE_TIMEOUT_S,
)
from app.execution.ab_settle import settle_ab_page


def resolve_ab_ref_with_commands(
    cli: Any,
    *,
    intent: str,
    selector: str = "",
) -> str:
    selector_norm = (selector or "").strip()
    testid_match = re.search(r"""\[data-testid=['"]([^'"]+)['"]\]""", selector_norm)
    if testid_match:
        found_ref = cli.find_testid_ref(testid_match.group(1))
        if found_ref:
            return found_ref

    if not intent:
        return ""

    for role in ("button", "link"):
        found_ref = cli.find_role_ref(role, intent)
        if found_ref:
            return found_ref

    found_ref = cli.find_label_ref(intent)
    if found_ref:
        return found_ref

    return cli.find_ref(intent)


def scroll_to_find(
    cli: Any,
    *,
    intent: str,
    selector: str = "",
) -> str:
    for _ in range(AB_SCROLL_RETRY_COUNT):
        found_ref = resolve_ab_ref_with_commands(
            cli,
            intent=intent,
            selector=selector,
        )
        if found_ref:
            try:
                cli.scroll_into_view(found_ref)
            except Exception:
                pass
            return found_ref
        try:
            cli.scroll("down", AB_SCROLL_RETRY_PX)
        except Exception:
            break
        try:
            cli.wait_for_load_state(
                "networkidle",
                timeout=AB_SCROLL_SETTLE_TIMEOUT_S,
            )
        except Exception:
            try:
                cli.wait_for_load_state(
                    "domcontentloaded",
                    timeout=AB_SCROLL_SETTLE_TIMEOUT_S,
                )
            except Exception:
                pass
    return ""


def snapshot_element_by_ref(snapshot: Dict[str, Any], ref: str) -> Dict[str, Any]:
    for element in snapshot.get("interactive_elements") or []:
        if not isinstance(element, dict):
            continue
        if str(element.get("ref") or "").strip() == ref:
            return element
    return {}


def passes_preclick_safety_check(
    *,
    step: Dict[str, Any],
    snapshot: Dict[str, Any],
    chosen_ref: str,
) -> tuple[bool, str]:
    element = snapshot_element_by_ref(snapshot, chosen_ref)
    if not element:
        return False, "chosen_ref_missing_from_snapshot"

    preferred_surface = str(step.get("preferred_surface") or "").strip().lower()
    element_surface = str(element.get("surface") or "").strip().lower()
    if preferred_surface and element_surface and preferred_surface != element_surface:
        return False, f"surface_mismatch:{preferred_surface}:{element_surface}"

    selector = str(step.get("selector") or "").strip()
    testid_match = re.search(r"""\[data-testid=['"]([^'"]+)['"]\]""", selector)
    if testid_match:
        expected_testid = str(testid_match.group(1) or "").strip().lower()
        actual_testid = str(element.get("testid") or "").strip().lower()
        if expected_testid and actual_testid and expected_testid != actual_testid:
            return False, f"testid_mismatch:{expected_testid}:{actual_testid}"

    return True, "ok"


def unique_visible_role_ref(
    cli: Any,
    *,
    snapshot: Dict[str, Any],
) -> tuple[str, int]:
    """Unique on-screen button/link by get_box. Never snapshot list order."""
    get_box = getattr(cli, "get_box", None)
    if not callable(get_box):
        return "", 0

    visible_by_role: Dict[str, List[str]] = {"button": [], "link": []}
    for element in snapshot.get("interactive_elements") or []:
        if not isinstance(element, dict):
            continue
        role = str(element.get("role") or "").strip().lower()
        if role not in visible_by_role:
            continue
        ref = str(element.get("ref") or "").strip()
        if not ref:
            continue
        try:
            box = get_box(ref) or {}
        except Exception:
            box = {}
        width = float(box.get("width") or 0)
        height = float(box.get("height") or 0)
        if width <= 0 or height <= 0:
            continue
        visible_by_role[role].append(ref)

    buttons = visible_by_role["button"]
    links = visible_by_role["link"]
    visible_count = len(buttons) + len(links)
    if len(buttons) == 1 and not links:
        return buttons[0], visible_count
    if len(links) == 1 and not buttons:
        return links[0], visible_count
    return "", visible_count


def resolve_ab_click_target(
    cli: Any,
    *,
    intent: str,
    snapshot: Dict[str, Any],
    mode: str,
    allow_scroll_retry: bool,
    selector: str = "",
    preferred_testids: Optional[List[str]] = None,
    preferred_surface: str = "",
    preferred_texts: Optional[List[str]] = None,
) -> ABTargetResolution:
    from app.browser.ref_selector import select_ref

    resolved: ABTargetResolution = {
        "chosen_ref": "",
        "selection_reason": "no_match",
        "selection_source": "deterministic",
        "scroll_retry_used": False,
        "should_retry": False,
        "candidate_count": 0,
    }

    selector_norm = (selector or "").strip()
    testid_match = re.search(r"""\[data-testid=['"]([^'"]+)['"]\]""", selector_norm)
    if testid_match:
        found_ref = cli.find_testid_ref(testid_match.group(1))
        if found_ref:
            resolved.update({
                "chosen_ref": found_ref,
                "selection_reason": "ab_find_testid",
                "selection_source": "semantic_testid",
                "candidate_count": 1,
            })
            return resolved

    if intent:
        for role in ("button", "link"):
            found_ref = cli.find_role_ref(role, intent)
            if found_ref:
                resolved.update({
                    "chosen_ref": found_ref,
                    "selection_reason": f"ab_find_role_{role}",
                    "selection_source": "semantic_role",
                    "candidate_count": 1,
                })
                return resolved

    sel = select_ref(
        intent,
        snapshot,
        mode=mode,
        preferred_testids=preferred_testids,
        preferred_surface=preferred_surface,
        preferred_texts=preferred_texts,
    )
    resolved.update({
        "chosen_ref": sel["chosen_ref"],
        "selection_reason": sel["selection_reason"],
        "selection_source": "deterministic",
        "candidate_count": len(sel.get("candidates") or []),
    })
    if sel["chosen_ref"]:
        return resolved

    if str(sel.get("selection_reason") or "") == "ambiguous":
        resolved["selection_source"] = "ambiguous"
        return resolved

    found_ref = cli.find_label_ref(intent)
    if found_ref:
        resolved.update({
            "chosen_ref": found_ref,
            "selection_reason": "ab_find_label",
            "selection_source": "semantic_label",
            "candidate_count": max(int(resolved.get("candidate_count") or 0), 1),
        })
        return resolved

    geo_ref, geo_count = unique_visible_role_ref(cli, snapshot=snapshot)
    if geo_count:
        resolved["candidate_count"] = max(int(resolved.get("candidate_count") or 0), geo_count)
    if geo_ref:
        resolved.update({
            "chosen_ref": geo_ref,
            "selection_reason": "geometry_box",
            "selection_source": "geometry",
        })
        return resolved

    found_ref = cli.find_ref(intent)
    if found_ref:
        resolved.update({
            "chosen_ref": found_ref,
            "selection_reason": "ab_find",
            "selection_source": "semantic_find",
            "candidate_count": max(int(resolved.get("candidate_count") or 0), 1),
        })
        return resolved

    if allow_scroll_retry:
        resolved["scroll_retry_used"] = True
        resolved["should_retry"] = True
    return resolved


def ensure_ab_target_actionable(cli: Any, click_target: str) -> ABActionabilityResult:
    try:
        cli.scroll_into_view(click_target)
    except Exception:
        pass
    settle_ab_page(cli, require_networkidle=False)
    visible = cli.is_visible(click_target)
    enabled = cli.is_enabled(click_target)
    get_box = getattr(cli, "get_box", None)
    if callable(get_box):
        try:
            box = get_box(click_target) or {}
        except Exception:
            box = {}
        width = float(box.get("width") or 0)
        height = float(box.get("height") or 0)
        if box and (width <= 0 or height <= 0):
            visible = False
    return {
        "target_visible": visible,
        "target_enabled": enabled,
    }
