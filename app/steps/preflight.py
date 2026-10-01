

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.steps.demo_contract import looks_like_amount_chip


@dataclass
class PreflightResult:
    passed: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    action: str = "proceed"                                      


_SETUP_ACTIONS = {"click", "select", "check"}


def _label_matches(expected: str, candidate: str) -> bool:
    a = (expected or "").strip().lower()
    b = (candidate or "").strip().lower()
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) > 4 and a in b:
        return True
    if len(b) > 4 and b in a:
        return True
    return False


def _step_identity(step: Dict[str, Any]) -> str:
    return (step.get("label") or step.get("selector") or "").strip()


def _step_matches_ref(step: Dict[str, Any], ref: Any) -> bool:
    want_label = (getattr(ref, "label", "") or "").strip()
    want_sel = (getattr(ref, "selector", "") or "").strip()
    step_label = (step.get("label") or "").strip()
    step_sel = (step.get("selector") or "").strip()
    if want_sel and step_sel and want_sel.lower() == step_sel.lower():
        return True
    return (
        _label_matches(want_label, step_label)
        or _label_matches(want_label, step_sel)
        or _label_matches(want_sel, step_label)
        or _label_matches(want_sel, step_sel)
    )


def _plan_has_likely_chip(steps: List[Dict[str, Any]]) -> bool:
    for step in steps:
        if str(step.get("action") or "") not in _SETUP_ACTIONS:
            continue
        if looks_like_amount_chip(_step_identity(step)):
            return True
    return False


def _parse_interaction_hints(contract: Any) -> Dict[str, List[str]]:
    hints: Dict[str, List[str]] = {"high": [], "low": []}
    for note in (getattr(contract, "extraction_notes", []) or []):
        if not isinstance(note, str):
            continue
        if note.startswith("interaction_hint_high:"):
            hint = note.split(":", 1)[1].strip().lower()
            if hint:
                hints["high"].append(hint)
        elif note.startswith("interaction_hint_low:"):
            hint = note.split(":", 1)[1].strip().lower()
            if hint:
                hints["low"].append(hint)
        elif note.startswith("interaction_hint:"):
            hint = note.split(":", 1)[1].strip().lower()
            if hint:
                hints["high"].append(hint)
    return hints


def preflight_gate(
    steps: List[Dict[str, Any]],
    contract: Optional[Any],
) -> PreflightResult:
    if contract is None:

        return PreflightResult(
            passed=True,
            warnings=["No contract supplied — running unguided"],
            action="proceed",
        )

    errors: List[str] = []
    warnings: List[str] = []




    start_route = getattr(contract, "start_route", None)
    if start_route:
        first_goto = next(
            (s for s in steps if s.get("action") == "goto"), None
        )
        if not first_goto:
            errors.append(f"No goto step found. Plan must start with goto {start_route}")
        elif (first_goto.get("url") or "").strip().rstrip("/") != start_route.rstrip("/"):
            errors.append(
                f"Plan starts at '{first_goto.get('url')}' "
                f"but contract requires '{start_route}'"
            )









    click_steps = [s for s in steps if s.get("action") == "click"]
    setup_action_steps = [
        s for s in steps if str(s.get("action") or "") in _SETUP_ACTIONS
    ]
    interaction_hints = _parse_interaction_hints(contract)
    setup_refs = list(getattr(contract, "setup_steps", None) or [])

    try:
        for target in contract.targets or []:
            if not getattr(target, "required", True):
                continue

            target_label = (target.label or "").strip()
            if not target_label:
                continue

            matched = any(_step_matches_ref(step, target) for step in click_steps)

            if not matched:
                errors.append(
                    f"Required contract target missing from plan: '{target.label}'"
                )
    except Exception as e:
        warnings.append(f"Could not validate contract targets: {e}")

    first_cta_idx = len(setup_action_steps)
    try:
        cta_refs = [
            target
            for target in (contract.targets or [])
            if getattr(target, "required", True) and (getattr(target, "label", "") or "").strip()
        ]
        for idx, step in enumerate(setup_action_steps):
            if any(_step_matches_ref(step, target) for target in cta_refs):
                first_cta_idx = idx
                break
    except Exception:
        first_cta_idx = len(setup_action_steps)

    try:
        for setup in setup_refs:
            if not getattr(setup, "required", True):
                continue
            setup_label = (getattr(setup, "label", "") or "").strip()
            if not setup_label:
                continue
            found_before_cta = False
            for idx, step in enumerate(setup_action_steps):
                if idx >= first_cta_idx:
                    break
                if _step_matches_ref(step, setup):
                    found_before_cta = True
                    break
            if not found_before_cta:
                errors.append(
                    f"Required setup step missing before CTA: '{setup_label}'"
                )
    except Exception as e:
        warnings.append(f"Could not validate contract setup steps: {e}")




    terminal = getattr(contract, "terminal", None)
    if terminal:
        terminal_steps = [s for s in steps if s.get("action") == "assert_terminal"]
        if not terminal_steps:
            errors.append(
                f"No assert_terminal step in plan. "
                f"Contract requires terminal condition: {terminal.value}"
            )
        else:
            last_terminal = terminal_steps[-1]
            condition = last_terminal.get("condition") or {}
            plan_value = (
                condition.get("value")
                or last_terminal.get("expected_element")
                or last_terminal.get("expected_text")
                or ""
            )
            if terminal.value not in str(plan_value):
                errors.append(
                    f"Terminal assertion value mismatch: "
                    f"plan has '{plan_value}', "
                    f"contract requires '{terminal.value}'"
                )

    for step in click_steps:
        has_validation = bool(
            step.get("validation_condition")
            or step.get("success_condition")
        )
        if not has_validation:
            errors.append(
                "Click step missing explicit proof condition. "
                "Every click must declare url_match, text_present, or element_present."
            )




    if len(click_steps) == 0:
        errors.append(
            "Degenerate plan: zero click steps after normalization. "
            "This plan cannot demonstrate any feature."
        )




    if interaction_hints["high"] or interaction_hints["low"]:
        earlier_setup = setup_action_steps[:first_cta_idx]
        earlier_labels = [_step_identity(s).lower() for s in earlier_setup]
        chip_present = _plan_has_likely_chip(earlier_setup)
        grounded_setup = bool(setup_refs)
        for hint in interaction_hints["high"]:
            if any(_label_matches(hint, label) for label in earlier_labels):
                continue
            if grounded_setup or chip_present:
                warnings.append(
                    f"Ungrounded contract hint '{hint}' is covered by a setup chip or setup_steps"
                )
                continue
            message = f"Missing prerequisite setup step implied by contract hint: '{hint}'"
            warnings.append(message)
        for hint in interaction_hints["low"]:
            if any(_label_matches(hint, label) for label in earlier_labels):
                continue
            warnings.append(
                f"Weak prerequisite setup hint not covered explicitly: '{hint}'"
            )

    if errors:
        return PreflightResult(
            passed=False,
            errors=errors,
            warnings=warnings,
            action="regenerate",
        )

    return PreflightResult(passed=True, warnings=warnings, action="proceed")
