"""Bind planned click kinds/labels to the live Agent Browser snapshot.

Catalog pins such as ``₹2000`` / ``Proceed Recharge`` stay as hints. Runtime
resolves each slot against interactive names currently on the page.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.steps.demo_contract import looks_like_amount_chip


CTA_INTENT_RE = re.compile(
    r"\b(recharge|proceed|submit|pay|confirm|continue|next|buy|checkout)\b",
    re.IGNORECASE,
)
CONFIRM_INTENT_RE = re.compile(
    r"\b(proceed|confirm|continue|next)\b",
    re.IGNORECASE,
)
SLOT_KINDS = {"cta", "option", "toggle", "tab", "amount", "confirm", "nav"}


@dataclass
class BindResult:
    step: Dict[str, Any]
    skip: bool = False
    reason: str = ""
    drop_labels: List[str] = field(default_factory=list)


def looks_like_cta_intent(intent: str) -> bool:
    return bool(CTA_INTENT_RE.search(intent or ""))


def looks_like_confirm_intent(intent: str) -> bool:
    return bool(CONFIRM_INTENT_RE.search(intent or ""))


def _label_tokens(label: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", (label or "").casefold())


def confirm_name_fits(planned: str, live: str) -> bool:
    planned_tokens = _label_tokens(planned)
    live_tokens = _label_tokens(live)
    if not planned_tokens or not live_tokens:
        return False
    if planned_tokens == live_tokens:
        return True
    if planned_tokens[0] == live_tokens[0]:
        return True
    return bool(set(planned_tokens) & set(live_tokens))


def snapshot_interactive_names(snapshot: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    seen: set[str] = set()
    for bucket in ("interactive_elements", "context_elements"):
        for element in snapshot.get(bucket) or []:
            if not isinstance(element, dict):
                continue
            name = str(element.get("name") or "").strip()
            if not name:
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            names.append(name)
    return names


def snapshot_has_label(snapshot: Dict[str, Any], label: str) -> bool:
    needle = (label or "").strip().casefold()
    if not needle:
        return False
    return any(
        name.casefold() == needle for name in snapshot_interactive_names(snapshot)
    )


def _confirm_names(snapshot: Dict[str, Any], *, used: Optional[set[str]] = None) -> List[str]:
    used_names = used or set()
    return [
        name
        for name in snapshot_interactive_names(snapshot)
        if looks_like_confirm_intent(name) and name.casefold() not in used_names
    ]


def infer_step_kind(step: Dict[str, Any]) -> str:
    kind = str(step.get("kind") or "").strip().lower()
    if kind in SLOT_KINDS:
        return kind
    label = str(step.get("label") or step.get("text") or "").strip()
    if looks_like_amount_chip(label):
        return "amount"
    if looks_like_confirm_intent(label):
        return "confirm"
    if looks_like_cta_intent(label):
        return "cta"
    return "cta"


def _first_amount_chip(snapshot: Dict[str, Any], *, used: set[str]) -> str:
    for name in snapshot_interactive_names(snapshot):
        if name.casefold() in used:
            continue
        if looks_like_amount_chip(name):
            return name
    return ""


def _unique_cta_name(snapshot: Dict[str, Any], *, used: set[str]) -> str:
    matches: List[str] = []
    for name in snapshot_interactive_names(snapshot):
        if name.casefold() in used:
            continue
        if looks_like_amount_chip(name):
            continue
        if looks_like_cta_intent(name):
            matches.append(name)
    if len(matches) == 1:
        return matches[0]
    return ""


def _terminal_condition_from_remaining(remaining: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    for later in remaining:
        if str(later.get("action") or "") != "assert_terminal":
            continue
        raw = later.get("condition") if isinstance(later.get("condition"), dict) else {}
        cond_type = str(raw.get("type") or "").strip()
        cond_value = str(raw.get("value") or "").strip()
        if not cond_type:
            if str(later.get("expected_text") or "").strip():
                cond_type, cond_value = "text_present", str(later.get("expected_text") or "").strip()
            elif str(later.get("expected_url") or "").strip():
                cond_type, cond_value = "url_match", str(later.get("expected_url") or "").strip()
            elif str(later.get("expected_element") or "").strip():
                cond_type, cond_value = "element_present", str(later.get("expected_element") or "").strip()
        if cond_type and cond_value:
            return {"type": cond_type, "value": cond_value}
    return None


def _absent_confirm_labels(remaining: List[Dict[str, Any]], snapshot: Dict[str, Any]) -> List[str]:
    dropped: List[str] = []
    for later in remaining:
        if str(later.get("action") or "") != "click":
            continue
        label = str(later.get("label") or later.get("text") or "").strip()
        if not label:
            continue
        kind = infer_step_kind(later)
        if kind != "confirm" and not looks_like_confirm_intent(label):
            continue
        if snapshot_has_label(snapshot, label):
            continue
        if any(confirm_name_fits(label, name) for name in _confirm_names(snapshot)):
            continue
        dropped.append(label)
    return dropped


def bind_click_step(
    step: Dict[str, Any],
    snapshot: Dict[str, Any],
    *,
    remaining_steps: Optional[List[Dict[str, Any]]] = None,  # reserved: proof rebind is post-click
    used_labels: Optional[set[str]] = None,
) -> BindResult:
    """Resolve a planned click against the live snapshot.

    Exact planned labels that are on the page stay. Missing amount chips bind
    to a visible unmatched chip. A missing confirm binds to one live control
    whose name shares the planned label, such as Proceed for Proceed Recharge.
    Any other confirm-like control is left alone and the step is skipped.
    """
    used = {item.casefold() for item in (used_labels or set()) if item}
    bound = dict(step)
    planned = str(bound.get("label") or bound.get("text") or "").strip()
    kind = infer_step_kind(bound)
    bound["kind"] = kind
    bound.setdefault("planned_label", planned)

    if planned and snapshot_has_label(snapshot, planned):
        return BindResult(step=bound)

    if kind == "amount":
        chip = _first_amount_chip(snapshot, used=used)
        if chip:
            bound["label"] = chip
            bound["bound_from"] = "snapshot_amount"
            return BindResult(step=bound)
        return BindResult(step=bound, skip=False, reason="amount_slot_empty")

    if kind == "confirm" or looks_like_confirm_intent(planned):
        matches = [
            name
            for name in _confirm_names(snapshot, used=used)
            if confirm_name_fits(planned, name)
        ]
        if len(matches) == 1:
            bound["label"] = matches[0]
            bound["bound_from"] = "snapshot_confirm"
            return BindResult(step=bound)
        return BindResult(
            step=bound,
            skip=True,
            reason="confirm_absent_from_snapshot",
            drop_labels=[planned] if planned else [],
        )

    if kind == "cta":
        cta = _unique_cta_name(snapshot, used=used)
        if cta:
            bound["label"] = cta
            bound["bound_from"] = "snapshot_cta"
            return BindResult(step=bound)

    return BindResult(step=bound, skip=False, reason="planned_label_unbound")


def resolve_failed_confirm_proof(
    step: Dict[str, Any],
    snap_after: Dict[str, Any],
    *,
    remaining_steps: Optional[List[Dict[str, Any]]] = None,
) -> BindResult:
    """After a click, drop catalog confirm pins that the live page never grew.

    If the planned proof was a confirm label that is still absent, rebind to
    the terminal condition when one exists. The click itself is not marked
    passed here; the caller re-evaluates the rebound proof.
    """
    remaining = list(remaining_steps or [])
    drop_labels = _absent_confirm_labels(remaining, snap_after)
    raw = step.get("success_condition") or step.get("validation_condition")
    if not isinstance(raw, dict):
        return BindResult(step=dict(step), drop_labels=drop_labels)
    cond_type = str(raw.get("type") or "").strip()
    cond_value = str(raw.get("value") or "").strip()
    if cond_type != "element_present" or not looks_like_confirm_intent(cond_value):
        return BindResult(step=dict(step), drop_labels=drop_labels)
    if snapshot_has_label(snap_after, cond_value):
        return BindResult(step=dict(step), drop_labels=drop_labels)

    live_confirms = [
        name
        for name in _confirm_names(snap_after)
        if confirm_name_fits(cond_value, name)
    ]
    if len(live_confirms) == 1:
        updated = dict(step)
        rebound = {"type": "element_present", "value": live_confirms[0]}
        updated["validation_condition"] = dict(rebound)
        updated["success_condition"] = dict(rebound)
        updated["validation_source"] = "snapshot_bound_confirm"
        updated["unbound_proof_label"] = cond_value
        return BindResult(step=updated, drop_labels=drop_labels)

    terminal = _terminal_condition_from_remaining(remaining)
    updated = dict(step)
    if terminal is not None:
        updated["validation_condition"] = dict(terminal)
        updated["success_condition"] = dict(terminal)
        updated["validation_source"] = "snapshot_bound_terminal"
        updated["unbound_proof_label"] = cond_value
    if cond_value not in drop_labels:
        drop_labels.append(cond_value)
    return BindResult(step=updated, drop_labels=drop_labels)


def drop_labels_from_queue(
    queue: List[Dict[str, Any]],
    *,
    after_index: int,
    labels: List[str],
) -> List[Dict[str, Any]]:
    needles = {item.strip().casefold() for item in labels if item and item.strip()}
    if not needles:
        return queue
    kept: List[Dict[str, Any]] = []
    for index, step in enumerate(queue):
        if index <= after_index:
            kept.append(step)
            continue
        if str(step.get("action") or "") != "click":
            kept.append(step)
            continue
        label = str(step.get("label") or step.get("text") or "").strip().casefold()
        if label in needles:
            continue
        kept.append(step)
    return kept


def used_click_labels(steps: List[Dict[str, Any]], *, before_index: int) -> set[str]:
    used: set[str] = set()
    for step in steps[: max(before_index, 0)]:
        if str(step.get("action") or "") not in {"click", "select", "check"}:
            continue
        label = str(step.get("label") or "").strip()
        if label:
            used.add(label)
    return used
