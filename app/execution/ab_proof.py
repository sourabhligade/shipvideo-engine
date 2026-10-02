from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.browser.agent_browser_types import (
    StepValidationResult,
    ValidationCondition,
)
from app.steps.demo_contract import looks_like_amount_chip


def detect_state_change(
    url_before: str,
    url_after: str,
    snap_text_before: str,
    snap_text_after: str,
) -> bool:
    if url_before != url_after:
        return True
    if snap_text_before != snap_text_after:
        return True
    return False


def normalize_validation_condition(raw: Any) -> Optional[ValidationCondition]:
    if not isinstance(raw, dict):
        return None
    cond_type = str(raw.get("type") or "").strip()
    cond_value = str(raw.get("value") or "").strip()
    if cond_type not in {"url_match", "text_present", "element_present"}:
        return None
    if not cond_value:
        return None
    return ValidationCondition(type=cond_type, value=cond_value)


def extract_validation_condition(step: Dict[str, Any]) -> Optional[ValidationCondition]:
    return normalize_validation_condition(
        step.get("success_condition") or step.get("validation_condition")
    )


def contains_ci(haystack: str, needle: str) -> bool:
    return needle.lower() in haystack.lower()


def matches_validation_condition(
    condition: ValidationCondition,
    *,
    current_url: str,
    snapshot_text: str,
    element_names: List[str],
) -> bool:
    expected = condition["value"]
    cond_type = condition["type"]
    if cond_type == "url_match":
        return contains_ci(current_url, expected)
    if cond_type == "text_present":
        return contains_ci(snapshot_text, expected)
    if cond_type == "element_present":
        return any(contains_ci(name, expected) for name in element_names)
    return False


def element_names(snapshot: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    for bucket in ("interactive_elements", "context_elements"):
        for element in snapshot.get(bucket) or []:
            if isinstance(element, dict):
                for key in ("name", "testid", "aria_label", "element_id", "nearby_text", "surface"):
                    value = str(element.get(key) or "").strip()
                    if value:
                        names.append(value)
    for heading in snapshot.get("headings") or []:
        value = str(heading or "").strip()
        if value:
            names.append(value)
    return names


def describe_validation_actual(
    condition: Optional[ValidationCondition],
    snap_after: Dict[str, Any],
) -> str:
    if condition is None:
        return "no_validation_condition"

    if condition["type"] == "url_match":
        return str(snap_after.get("current_url") or "")
    if condition["type"] == "text_present":
        return str(snap_after.get("snapshot_text") or "")

    matched_names = [
        name for name in element_names(snap_after)
        if contains_ci(name, condition["value"])
    ]
    if matched_names:
        return ", ".join(matched_names[:5])
    all_names = element_names(snap_after)
    return ", ".join(all_names[:5]) if all_names else "no_matching_element"


_SELECTED_TRUE = {"true", "pressed", "checked", "selected", "on"}
_SELECTED_CLASS_TOKENS = {"active", "selected", "is-active", "is-selected"}


def _element_selected(element: Dict[str, Any]) -> bool:
    for key in (
        "pressed",
        "checked",
        "selected",
        "aria-pressed",
        "aria-checked",
        "ariaPressed",
        "ariaChecked",
    ):
        value = element.get(key)
        if value is True:
            return True
        if str(value or "").strip().lower() in _SELECTED_TRUE:
            return True
    return False


def _named_element_selected(snapshot: Dict[str, Any], label: str) -> bool:
    needle = (label or "").strip().casefold()
    if not needle:
        return False
    for bucket in ("interactive_elements", "context_elements"):
        for element in snapshot.get(bucket) or []:
            if not isinstance(element, dict):
                continue
            name = str(element.get("name") or "").strip().casefold()
            if name == needle and _element_selected(element):
                return True
    return False


def _named_element(snapshot: Dict[str, Any], label: str) -> Optional[Dict[str, Any]]:
    needle = (label or "").strip().casefold()
    if not needle:
        return None
    for bucket in ("interactive_elements", "context_elements"):
        for element in snapshot.get(bucket) or []:
            if not isinstance(element, dict):
                continue
            name = str(element.get("name") or "").strip().casefold()
            if name == needle:
                return element
    return None


def _class_tokens(element: Dict[str, Any]) -> set[str]:
    raw = str(element.get("class") or element.get("className") or "")
    return {token.strip().lower() for token in raw.replace(",", " ").split() if token.strip()}


def _class_selected(element: Dict[str, Any]) -> bool:
    return bool(_class_tokens(element) & _SELECTED_CLASS_TOKENS)


def _is_disabled(element: Dict[str, Any]) -> bool:
    value = element.get("disabled")
    if value is True:
        return True
    return str(value or "").strip().lower() in {"true", "disabled"}


def _amount_selection_rose(
    step: Dict[str, Any],
    snap_before: Dict[str, Any],
    snap_after: Dict[str, Any],
) -> bool:
    label = str(step.get("label") or step.get("text") or "").strip()
    if not looks_like_amount_chip(label):
        return False
    if _named_element_selected(snap_after, label) and not _named_element_selected(
        snap_before, label
    ):
        return True
    before = _named_element(snap_before, label)
    after = _named_element(snap_after, label)
    if before is not None and after is not None:
        if _class_selected(after) and not _class_selected(before):
            return True
    condition = extract_validation_condition(step)
    if condition is None or condition["type"] != "element_present":
        return False
    proof_before = _named_element(snap_before, condition["value"])
    proof_after = _named_element(snap_after, condition["value"])
    if proof_before is None or proof_after is None:
        return False
    return _is_disabled(proof_before) and not _is_disabled(proof_after)


def stamp_amount_control_state(
    cli: Any,
    snapshot: Dict[str, Any],
    step: Dict[str, Any],
    *,
    chip_ref: str = "",
) -> None:
    """Copy class and disabled from the live page onto amount-chip snapshots.

    The demo marks the chosen chip with class ``active`` and enables Recharge
    Now. The accessibility snapshot does not include either fact.
    """
    label = str(step.get("label") or step.get("text") or "").strip()
    if not looks_like_amount_chip(label):
        return
    get_attr = getattr(cli, "get_attr", None)
    if not callable(get_attr):
        return
    chip = _named_element(snapshot, label)
    ref = (chip_ref or "").strip() or str((chip or {}).get("ref") or "").strip()
    if chip is not None and ref:
        css = str(get_attr(ref, "class") or "")
        if css:
            chip["class"] = css
        pressed = str(get_attr(ref, "aria-pressed") or "")
        if pressed:
            chip["aria-pressed"] = pressed
    condition = extract_validation_condition(step)
    if condition is None or condition["type"] != "element_present":
        return
    proof = _named_element(snapshot, condition["value"])
    proof_ref = str((proof or {}).get("ref") or "").strip()
    if proof is None or not proof_ref:
        return
    disabled = str(get_attr(proof_ref, "disabled") or "").strip().lower()
    aria_disabled = str(get_attr(proof_ref, "aria-disabled") or "").strip().lower()
    if disabled in {"true", "disabled"} or aria_disabled == "true":
        proof["disabled"] = True
    elif aria_disabled == "false":
        proof["disabled"] = False


def evaluate_click_validation(
    *,
    step: Dict[str, Any],
    snap_before: Dict[str, Any],
    snap_after: Dict[str, Any],
) -> StepValidationResult:
    condition = extract_validation_condition(step)
    if condition is None:
        return StepValidationResult(
            passed=False,
            condition=None,
            actual="no_validation_condition",
            source="",
            failure_reason="",
        )

    source = str(step.get("validation_source") or "step")
    before_matches = matches_validation_condition(
        condition,
        current_url=str(snap_before.get("current_url") or ""),
        snapshot_text=str(snap_before.get("snapshot_text") or ""),
        element_names=element_names(snap_before),
    )
    after_matches = matches_validation_condition(
        condition,
        current_url=str(snap_after.get("current_url") or ""),
        snapshot_text=str(snap_after.get("snapshot_text") or ""),
        element_names=element_names(snap_after),
    )
    passed = after_matches and not before_matches
    if not passed and _amount_selection_rose(step, snap_before, snap_after):
        passed = True
    allowed_sources = {"step", "test_case"}
    mapped_source = source if source in allowed_sources else "step"
    return StepValidationResult(
        passed=passed,
        condition=condition,
        actual=describe_validation_actual(condition, snap_after),
        source=mapped_source,
        failure_reason="" if passed else f"validation_failed:{condition['type']}:{condition['value']}",
    )


def validation_from_successful_text_wait(
    *,
    step: Dict[str, Any],
    step_result: Dict[str, Any],
) -> Optional[StepValidationResult]:
    condition = extract_validation_condition(step)
    if condition is None or condition["type"] != "text_present":
        return None

    post_click_settle = step_result.get("post_click_settle") or {}
    if str(post_click_settle.get("validation_wait") or "") != "text_present":
        return None

    return StepValidationResult(
        passed=True,
        condition=condition,
        actual=condition["value"],
        source="wait_for_text",
        failure_reason="",
    )


def next_click_intent(steps: List[Dict[str, Any]], start_index: int) -> str:
    from app.browser.ref_selector import derive_intent

    for next_step in steps[start_index + 1:]:
        if str(next_step.get("action") or "").strip() != "click":
            continue
        intent = derive_intent(next_step)
        if intent:
            return intent
    return ""


def snapshot_has_intent(
    snapshot: Dict[str, Any],
    *,
    intent: str,
    mode: str,
) -> bool:
    if not intent:
        return False
    from app.browser.ref_selector import select_ref

    selected = select_ref(intent, snapshot, mode=mode)
    return bool(selected.get("chosen_ref"))


def planned_url_needles(steps: List[Dict[str, Any]], step_index: int) -> List[str]:
    needles: List[str] = []
    seen: set[str] = set()

    def _add(raw: Any) -> None:
        value = str(raw or "").strip()
        if not value or value in seen:
            return
        seen.add(value)
        needles.append(value)

    if 0 <= step_index < len(steps):
        current = steps[step_index]
        current_cond = extract_validation_condition(current)
        if current_cond and current_cond["type"] == "url_match":
            _add(current_cond["value"])
        _add(current.get("expected_url"))

    for later in steps[step_index + 1 :]:
        if str(later.get("action") or "") == "goto":
            _add(later.get("url"))
        later_cond = extract_validation_condition(later)
        if later_cond and later_cond["type"] == "url_match":
            _add(later_cond["value"])
        _add(later.get("expected_url"))
        raw_condition = later.get("condition") if isinstance(later.get("condition"), dict) else {}
        if str(raw_condition.get("type") or "").strip() == "url_match":
            _add(raw_condition.get("value"))
    return needles


def infer_runtime_validation(
    *,
    steps: List[Dict[str, Any]],
    step_index: int,
    snap_before: Dict[str, Any],
    snap_after: Dict[str, Any],
    mode: str,
) -> StepValidationResult:
    nxt = next_click_intent(steps, step_index)
    if nxt:
        before_has = snapshot_has_intent(snap_before, intent=nxt, mode=mode)
        after_has = snapshot_has_intent(snap_after, intent=nxt, mode=mode)
        if after_has and not before_has:
            condition: ValidationCondition = {
                "type": "element_present",
                "value": nxt,
            }
            return StepValidationResult(
                passed=True,
                condition=condition,
                actual=nxt,
                source="runtime_inferred",
                failure_reason="",
            )

    url_before = str(snap_before.get("current_url") or "")
    url_after = str(snap_after.get("current_url") or "")
    planned_urls = planned_url_needles(steps, step_index)
    if url_before and url_after and url_before != url_after and planned_urls:
        if any(contains_ci(url_after, needle) for needle in planned_urls):
            condition = {"type": "url_match", "value": url_after}
            return StepValidationResult(
                passed=True,
                condition=condition,
                actual=url_after,
                source="runtime_inferred",
                failure_reason="",
            )
        return StepValidationResult(
            passed=False,
            condition={"type": "url_match", "value": planned_urls[0]},
            actual=url_after,
            source="runtime_inferred",
            failure_reason=f"validation_failed:url_match:{planned_urls[0]}",
        )

    return StepValidationResult(
        passed=False,
        condition=None,
        actual="no_runtime_validation_signal",
        source="runtime_inferred",
        failure_reason="validation_failed:no_runtime_validation_signal",
    )


def terminal_match_in_snapshot(snapshot: Dict[str, Any], expected: str) -> bool:
    needle = (expected or "").strip().lower()
    if not needle:
        return False
    interactive_elements = snapshot.get("interactive_elements") or []
    context_elements = snapshot.get("context_elements") or []
    snapshot_text = str(snapshot.get("snapshot_text") or "")
    print(
        f"[terminal_check] looking for '{expected}' "
        f"in {len(interactive_elements)} interactive, "
        f"{len(context_elements)} context elements, "
        f"snapshot_text_length={len(snapshot_text)}",
        flush=True,
    )
    print(
        f"[terminal_check] snapshot_text excerpt: "
        f"{snapshot_text[:500]}",
        flush=True,
    )
    for element in interactive_elements:
        if not isinstance(element, dict):
            continue
        name = str(element.get("name") or "").strip().lower()
        if needle and needle in name:
            return True
    for element in context_elements:
        if not isinstance(element, dict):
            continue
        name = str(element.get("name") or "").strip().lower()
        if needle and needle in name:
            return True
    if needle in snapshot_text.lower():
        return True
    return False
