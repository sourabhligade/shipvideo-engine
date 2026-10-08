from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

from app.steps.demo_contract import (
    DemoContract,
    TargetKind,
    TargetRef,
    TerminalCondition,
    looks_like_amount_chip,
)
from app.steps.step_normalizer import _extract_routes_from_diff


def extract_contract_static(
    diff_files: List[Dict[str, str]],
) -> DemoContract:
    start_route = _infer_start_route(diff_files)
    targets, setup_steps = _extract_targets_and_setup(diff_files)
    terminal = _detect_terminal(diff_files)
    interaction_hints = _extract_interaction_hints(diff_files)

    notes: List[str] = []
    if not start_route:
        notes.append("no_start_route_inferred")
        start_route = "/"
    if not targets:
        notes.append("no_targets_extracted")
    if terminal is None:
        notes.append("no_terminal_detected")
    for confidence, hint in interaction_hints:
        notes.append(f"interaction_hint_{confidence}:{hint}")
    if setup_steps:
        notes.append(
            "setup_steps:" + ",".join(ref.label for ref in setup_steps if ref.label)
        )

    has_route = bool(start_route and start_route != "/")
    has_targets = bool(targets)
    has_terminal = terminal is not None
    confidence = "high" if (has_route and has_targets and has_terminal) else (
        "medium" if ((has_route and has_targets) or (has_targets and has_terminal)) else "low"
    )
    high_setup_hints = any(level == "high" for level, _ in interaction_hints)
    if high_setup_hints and not setup_steps and confidence == "high":
        confidence = "medium"
        notes.append("setup_hint_ungrounded_confidence_capped")

    return DemoContract(
        start_route=start_route,
        targets=targets,
        terminal=terminal,
        setup_steps=setup_steps,
        confidence=confidence,
        source_static=True,
        extraction_notes=notes,
    )


def _infer_start_route(diff_files: List[Dict[str, str]]) -> str:
    routes = _extract_routes_from_diff(diff_files)
    if routes:
        return sorted(routes)[0]                                 
    return ""


def _extract_targets(diff_files: List[Dict[str, str]]) -> List[TargetRef]:
    targets, _setup = _extract_targets_and_setup(diff_files)
    return targets


def _extract_targets_and_setup(
    diff_files: List[Dict[str, str]],
) -> Tuple[List[TargetRef], List[TargetRef]]:
    targets: List[TargetRef] = []
    setup_steps: List[TargetRef] = []
    seen_targets: Set[str] = set()
    seen_setup: Set[str] = set()

    for f in diff_files:
        patch = f.get("patch", "")
        if not patch:
            continue
        for line in patch.split("\n"):
            if not line.startswith("+"):
                continue

            normalized_line = line[1:]
            line_selector = ""

            for m in re.finditer(r'data-testid=["\']([^"\']+)["\']', line):
                tid = m.group(1)
                line_selector = f"[data-testid='{tid}']"
                label = tid.replace("-", " ").replace("_", " ")
                _append_ref(
                    targets,
                    setup_steps,
                    seen_targets,
                    seen_setup,
                    label=label,
                    selector=line_selector,
                    line=normalized_line,
                )

            for label in _extract_string_targets_from_line(normalized_line):
                _append_ref(
                    targets,
                    setup_steps,
                    seen_targets,
                    seen_setup,
                    label=label,
                    selector=line_selector,
                    line=normalized_line,
                )

            if _line_looks_interactive(line):
                for label in _extract_interactive_targets_from_line(normalized_line):
                    _append_ref(
                        targets,
                        setup_steps,
                        seen_targets,
                        seen_setup,
                        label=label,
                        selector=line_selector,
                        line=normalized_line,
                    )
                for label in _extract_amount_chips_from_line(normalized_line):
                    _append_ref(
                        targets,
                        setup_steps,
                        seen_targets,
                        seen_setup,
                        label=label,
                        selector=line_selector,
                        line=normalized_line,
                    )

    return targets, setup_steps


def _detect_terminal(diff_files: List[Dict[str, str]]) -> Optional[TerminalCondition]:
    terminal_patterns = re.compile(
        r'(complet|success|done|finish|confirm|submitted)',
        re.IGNORECASE,
    )
    for f in diff_files:
        patch = f.get("patch", "")
        for line in patch.split("\n"):
            if not line.startswith("+"):
                continue
            m = terminal_patterns.search(line)
            if m:

                text_match = re.search(r'>\s*([^<]{3,50})\s*<', line)
                if text_match:
                    return TerminalCondition(
                        type="text_present",
                        value=text_match.group(1).strip(),
                    )
    return None


def _extract_interaction_hints(diff_files: List[Dict[str, str]]) -> List[Tuple[str, str]]:
    hints: List[Tuple[str, str]] = []
    seen: Set[Tuple[str, str]] = set()
    high_signal_patterns = (
        (re.compile(r"\bselect amount\b", re.IGNORECASE), "select amount"),
        (re.compile(r"\bchoose (plan|tier|option)\b", re.IGNORECASE), "choose option"),
        (re.compile(r"\b(plan|tier|option) selected\b", re.IGNORECASE), "choose option"),
        (re.compile(r"\bswitch (tab|tabs)\b", re.IGNORECASE), "switch tab"),
        (re.compile(r"\bopen (drawer|modal|sheet|panel)\b", re.IGNORECASE), "open panel"),
        (re.compile(r"\b(toggle|enable|disable) [A-Za-z]", re.IGNORECASE), "toggle option"),
        (re.compile(r"\b(check|uncheck) [A-Za-z]", re.IGNORECASE), "check option"),
        (re.compile(r"\bselect [A-Za-z].*(plan|tier|option|amount)\b", re.IGNORECASE), "choose option"),
    )
    low_signal_patterns = (
        (re.compile(r"\bamount\b", re.IGNORECASE), "select amount"),
        (re.compile(r"\b(tab|tabs)\b", re.IGNORECASE), "switch tab"),
        (re.compile(r"\b(plan|tier|option)\b", re.IGNORECASE), "choose option"),
        (re.compile(r"\b(toggle|switch)\b", re.IGNORECASE), "toggle option"),
        (re.compile(r"\b(checkbox|check)\b", re.IGNORECASE), "check option"),
        (re.compile(r"\b(radio)\b", re.IGNORECASE), "choose option"),
        (re.compile(r"\b(drawer|modal|sheet|panel)\b", re.IGNORECASE), "open panel"),
    )

    for f in diff_files:
        patch = f.get("patch", "")
        if not patch:
            continue
        for line in patch.split("\n"):
            if not line.startswith("+"):
                continue
            normalized = line[1:].strip()
            matched = False
            for pattern, hint in high_signal_patterns:
                if pattern.search(normalized):
                    entry = ("high", hint)
                    if entry not in seen:
                        seen.add(entry)
                        hints.append(entry)
                    matched = True
                    break
            if matched:
                continue
            for pattern, hint in low_signal_patterns:
                if pattern.search(normalized):
                    entry = ("low", hint)
                    if entry not in seen:
                        seen.add(entry)
                        hints.append(entry)
                    break
    return hints


def _line_looks_interactive(line: str) -> bool:
    return bool(
        re.search(r"<\s*(button|a)\b", line, re.IGNORECASE)
        or re.search(
            r'role=["\'](button|link|tab|menuitem|radio|option|switch|checkbox)["\']',
            line,
            re.IGNORECASE,
        )
        or re.search(
            r"<\s*[A-Za-z0-9_.:-]*(Button|Link|Tab|Checkbox|Radio|Option)\b",
            line,
        )
    )


def _infer_kind(label: str, selector: str, line: str) -> TargetKind:
    sel = (selector or "").lower()
    ln = (line or "").lower()
    if looks_like_amount_chip(label) or "amount" in sel:
        return "amount"
    if re.search(r'role=["\']tab["\']', ln) or re.search(r"\btab\b", sel):
        return "tab"
    if re.search(r'role=["\'](switch|checkbox)["\']', ln) or "toggle" in sel:
        return "toggle"
    if re.search(r'role=["\'](radio|option)["\']', ln):
        return "option"
    return "cta"


def _append_ref(
    targets: List[TargetRef],
    setup_steps: List[TargetRef],
    seen_targets: Set[str],
    seen_setup: Set[str],
    *,
    label: str,
    selector: str = "",
    line: str = "",
) -> None:
    cleaned = _clean_target_label(label)
    if not cleaned:
        return
    kind = _infer_kind(cleaned, selector, line)
    dest = setup_steps if kind != "cta" else targets
    seen = seen_setup if kind != "cta" else seen_targets
    key = cleaned.casefold()

    if kind != "cta" and selector:
        kept: List[TargetRef] = []
        for existing in dest:
            if existing.selector == selector and existing.label.casefold() != key:
                seen.discard(existing.label.casefold())
                continue
            kept.append(existing)
        dest[:] = kept

    if key in seen:
        return
    seen.add(key)
    dest.append(TargetRef(label=cleaned, selector=selector, kind=kind))


def _append_target(
    targets: List[TargetRef],
    seen_labels: Set[str],
    *,
    label: str,
    selector: str = "",
) -> None:
    dummy_setup: List[TargetRef] = []
    seen_setup: Set[str] = set()
    _append_ref(
        targets,
        dummy_setup,
        seen_labels,
        seen_setup,
        label=label,
        selector=selector,
    )


def _clean_target_label(label: str) -> str:
    cleaned = re.sub(r"\s+", " ", (label or "").strip())
    cleaned = cleaned.strip("(){}[]:,.;")
    if looks_like_amount_chip(cleaned) and len(cleaned) >= 2:
        return cleaned
    if len(cleaned) < 3 or len(cleaned.split()) > 6:
        return ""
    if not re.search(r"[A-Za-z]", cleaned):
        return ""
    lowered = cleaned.casefold()
    if lowered in {
        "true", "false", "null", "undefined", "button", "link",
        "div", "span", "label", "variant", "size", "type",
    }:
        return ""
    return cleaned


def _extract_amount_chips_from_line(line: str) -> List[str]:
    labels: List[str] = []
    for m in re.finditer(r"[₹$€£¥]\s?\d[\d,]*(?:\.\d+)?", line):
        cleaned = _clean_target_label(m.group(0))
        if cleaned:
            labels.append(cleaned)
    return labels


def _extract_interactive_targets_from_line(line: str) -> List[str]:
    labels: List[str] = []
    for m in re.finditer(r'>\s*([^<{][^<>{}]{1,60}?)\s*<', line):
        cleaned = _clean_target_label(m.group(1))
        if cleaned:
            labels.append(cleaned)
    return labels


def _extract_string_targets_from_line(line: str) -> List[str]:
    labels: List[str] = []
    patterns = (
        r'(?:label|title|text|children|ctaLabel|buttonLabel|linkLabel|aria-label)\s*[:=]\s*["\']([^"\']{3,60})["\']',
        r'["\']([^"\']{3,60})["\']\s*:\s*(?:true|false|null|undefined|[A-Za-z_][A-Za-z0-9_]*)',
    )
    for pattern in patterns:
        for m in re.finditer(pattern, line, re.IGNORECASE):
            cleaned = _clean_target_label(m.group(1))
            if cleaned:
                labels.append(cleaned)
    return labels
