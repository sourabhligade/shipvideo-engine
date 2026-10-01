from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from app.browser.agent_browser_types import ValidationCondition


def get_generation_context(objective: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(objective, dict):
        return {}
    generation_context = objective.get("generation_context") or {}
    return generation_context if isinstance(generation_context, dict) else {}


def allowed_routes_from_objective(objective: Optional[Dict[str, Any]]) -> set:
    """Generation-time routes (crawl / real_routes / start_route) for runtime goto authority."""
    gc = get_generation_context(objective)
    routes: set = set()
    for raw in gc.get("real_routes") or []:
        r = str(raw or "").strip()
        if r:
            routes.add(r)
    start = str(gc.get("start_route") or "").strip()
    if start:
        routes.add(start)
    for raw in gc.get("start_route_candidates") or []:
        r = str(raw or "").strip()
        if r:
            routes.add(r)
    routes.add("/")
    return routes


def merge_allowed_routes_into_dom_ctx(
    dom_ctx: Dict[str, Any],
    allowed_routes: Optional[set],
) -> Dict[str, Any]:
    if not allowed_routes:
        return dom_ctx
    merged = dict(dom_ctx or {})
    existing = {str(r).strip() for r in (merged.get("routes") or []) if str(r).strip()}
    existing |= {str(r).strip() for r in allowed_routes if str(r).strip()}
    merged["routes"] = sorted(existing)
    return merged


def objective_changed_testids(objective: Optional[Dict[str, Any]]) -> List[str]:
    generation_context = get_generation_context(objective)
    changed_testids = generation_context.get("changed_testids") or []
    seen: set[str] = set()
    ordered: List[str] = []
    for item in changed_testids:
        testid = str(item or "").strip()
        if testid and testid not in seen:
            seen.add(testid)
            ordered.append(testid)
    return ordered


def objective_start_route(objective: Optional[Dict[str, Any]]) -> str:
    generation_context = get_generation_context(objective)
    route = str(generation_context.get("start_route") or "").strip()
    if route:
        return route
    for raw_route in generation_context.get("start_route_candidates") or []:
        route = str(raw_route or "").strip()
        if route:
            return route
    extraction = generation_context.get("extraction") or {}
    if isinstance(extraction, dict):
        return str(extraction.get("start_route") or "").strip()
    return ""


def objective_contract(objective: Optional[Dict[str, Any]]) -> Any:
    return get_generation_context(objective).get("contract")


def snapshot_contains_testid(snapshot: Dict[str, Any], testid: str) -> bool:
    needle = (testid or "").strip().lower()
    if not needle:
        return False
    for bucket in ("interactive_elements", "context_elements"):
        for element in snapshot.get(bucket) or []:
            if not isinstance(element, dict):
                continue
            if str(element.get("testid") or "").strip().lower() == needle:
                return True
    return False


def first_active_surface(snapshot: Dict[str, Any]) -> str:
    surfaces = snapshot.get("active_surfaces") or []
    for surface in surfaces:
        value = str(surface or "").strip()
        if value:
            return value
    return ""


def make_search_validation(value: str) -> ValidationCondition:
    return ValidationCondition(type="element_present", value=value)


def append_search_screenshot_result(
    *,
    cli: Any,
    screenshot_dir: Path,
    shot_idx: int,
    results: List[Dict[str, Any]],
    label: str,
    mode: str,
) -> int:
    path = screenshot_dir / f"shot{shot_idx}.png"
    cli.screenshot(path)
    results.append(
        {
            "index": len(results),
            "step": {"action": "screenshot", "label": label},
            "status": "ok",
            "outcome": "success",
            "backend": "agent_browser_cli",
            "mode": mode,
            "screenshot_path": str(path),
            "step_latency_ms": 0,
        }
    )
    return shot_idx + 1


def should_use_testid_search(
    *,
    objective: Optional[Dict[str, Any]],
    initial_steps: List[Dict[str, Any]],
) -> bool:
    generation_context = get_generation_context(objective)
    if not objective_changed_testids(objective):
        return False
    if bool(generation_context.get("discovery_mode")):
        return True
    click_steps = [
        step for step in initial_steps
        if str(step.get("action") or "") == "click"
    ]
    return len(click_steps) == 0
