from __future__ import annotations

import json
from typing import Any, Dict, Tuple

from playwright.sync_api import Page

from app.execution.ab_proof import terminal_match_in_snapshot
from app.execution.ab_settle import wait_for_ab_element_present
from app.execution.constants import AB_VALIDATION_WAIT_TIMEOUT_S


def resolve_terminal_expectation(step: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """Normalize assert_terminal fields into a condition + expected element/text/url value."""
    raw_condition = step.get("condition") if isinstance(step.get("condition"), dict) else {}
    condition: Dict[str, Any] = dict(raw_condition or {})
    cond_type = str(condition.get("type") or "").strip()
    cond_value = str(condition.get("value") or "").strip()
    expected_element = str(step.get("expected_element") or "").strip()
    expected_text = str(step.get("expected_text") or "").strip()
    expected_url = str(step.get("expected_url") or "").strip()

    if not cond_type:
        if expected_url:
            cond_type, cond_value = "url_match", expected_url
        elif expected_text:
            cond_type, cond_value = "text_present", expected_text
        elif expected_element or cond_value:
            cond_type = "element_present"
            cond_value = expected_element or cond_value
    else:
        if cond_type == "url_match" and not cond_value:
            cond_value = expected_url
        elif cond_type == "text_present" and not cond_value:
            cond_value = expected_text
        elif cond_type == "element_present" and not cond_value:
            cond_value = expected_element

    if cond_type == "element_present" and not expected_element:
        expected_element = cond_value

    condition["type"] = cond_type
    condition["value"] = cond_value
    return condition, expected_element


def assert_ab_terminal_condition(
    cli: Any,
    *,
    condition: Dict[str, Any],
    expected_element: str,
    extract_snapshot: Any,
) -> Dict[str, Any]:
    from app.execution import step_runner as sr

    wait_fn = getattr(sr, "_wait_for_ab_element_present", wait_for_ab_element_present)

    cond_type = str(condition.get("type") or "").strip()
    cond_value = str(condition.get("value") or "").strip()
    expected = (expected_element or cond_value or "").strip()
    if not cond_type and expected:
        cond_type = "element_present"
        cond_value = expected
    result: Dict[str, Any] = {
        "found": False,
        "source": "none",
        "actual": "",
    }

    if not cond_type or (cond_type != "element_present" and not cond_value) or (
        cond_type == "element_present" and not expected
    ):
        result["source"] = "missing_terminal_condition"
        return result

    if cond_type == "text_present" and cond_value:
        try:
            cli.wait_for_text(cond_value, timeout=AB_VALIDATION_WAIT_TIMEOUT_S)
            result["found"] = True
            result["source"] = "wait_for_text"
            result["actual"] = cond_value
            return result
        except Exception:
            wait_fn = getattr(cli, "wait_for_function", None)
            if callable(wait_fn):
                try:
                    wait_fn(
                        "document.body && document.body.innerText.includes("
                        f"{json.dumps(cond_value)})",
                        timeout=AB_VALIDATION_WAIT_TIMEOUT_S,
                    )
                    result["found"] = True
                    result["source"] = "wait_for_function"
                    result["actual"] = cond_value
                    return result
                except Exception:
                    pass
            result["source"] = "text_present_failed"
            result["actual"] = cond_value
            return result

    if cond_type == "url_match" and cond_value:
        try:
            cli.wait_for_url(cond_value, timeout=AB_VALIDATION_WAIT_TIMEOUT_S)
            result["found"] = True
            result["source"] = "wait_for_url"
            try:
                result["actual"] = cli.get_url()
            except Exception:
                result["actual"] = cond_value
            return result
        except Exception:
            try:
                current = cli.get_url()
            except Exception:
                current = ""
            if cond_value and cond_value in str(current or ""):
                result["found"] = True
                result["source"] = "url_match_substring"
                result["actual"] = current
                return result
            result["source"] = "url_match_failed"
            result["actual"] = current or cond_value
            return result

    if cond_type == "element_present" and expected:
        if wait_fn(
            cli,
            expected,
            timeout_s=AB_VALIDATION_WAIT_TIMEOUT_S,
        ):
            result["found"] = True
            result["source"] = "wait_for_element_present"
            result["actual"] = expected
            return result

        testid_ref = cli.find_testid_ref(expected)
        if testid_ref:
            try:
                if cli.is_visible(testid_ref):
                    result["found"] = True
                    result["source"] = "find_testid_visible"
                    result["actual"] = testid_ref
                    return result
            except Exception:
                pass

        for selector in (f"[data-testid='{expected}']", f"#{expected}"):
            try:
                semantic_ref = cli.find_element(selector)
            except Exception:
                semantic_ref = ""
            if semantic_ref:
                try:
                    if cli.is_visible(semantic_ref):
                        result["found"] = True
                        result["source"] = "find_element_visible"
                        result["actual"] = semantic_ref
                        return result
                except Exception:
                    pass

        semantic_ref = cli.find_ref(expected)
        if semantic_ref:
            try:
                if cli.is_visible(semantic_ref):
                    result["found"] = True
                    result["source"] = "semantic_find_visible"
                    result["actual"] = semantic_ref
                    return result
            except Exception:
                pass

        snapshot_hit = False
        try:
            terminal_snapshot = extract_snapshot(save_raw=False)
            snapshot_hit = terminal_match_in_snapshot(terminal_snapshot, expected)
        except Exception as exc:
            print(
                f"[terminal_check] snapshot extract failed expected={expected!r} "
                f"error={type(exc).__name__}: {exc}",
                flush=True,
            )
        print(
            f"[terminal_check] element_present fail-closed expected={expected!r} "
            f"snapshot_substring_hit={snapshot_hit}",
            flush=True,
        )
        result["found"] = False
        result["source"] = "element_present_failed"
        result["actual"] = expected if snapshot_hit else ""
        result["snapshot_substring_hit"] = bool(snapshot_hit)
        return result

    result["source"] = "missing_terminal_condition"
    return result


def assert_playwright_terminal_condition(page: Page, step: Dict[str, Any]) -> tuple[bool, str]:
    condition = step.get("condition") if isinstance(step.get("condition"), dict) else {}
    cond_type = str(condition.get("type") or "").strip()
    cond_value = str(condition.get("value") or "").strip()
    expected_element = (
        str(step.get("expected_element") or "").strip()
        or cond_value
    )
    expected_text = str(step.get("expected_text") or "").strip()
    expected_url = str(step.get("expected_url") or "").strip()

    if not cond_type:
        if expected_url:
            cond_type = "url_match"
            cond_value = expected_url
        elif expected_text:
            cond_type = "text_present"
            cond_value = expected_text
        elif expected_element:
            cond_type = "element_present"
            cond_value = expected_element

    if cond_type == "url_match" and (cond_value or expected_url):
        needle = cond_value or expected_url
        try:
            page.wait_for_url(f"**{needle}**", timeout=8000)
            return True, "url_match"
        except Exception:
            current = ""
            try:
                current = page.url or ""
            except Exception:
                pass
            if needle in current:
                return True, "url_match"
            return False, f"terminal_url_not_matched:{needle}"

    if cond_type == "text_present" and (cond_value or expected_text):
        needle = cond_value or expected_text
        try:
            page.get_by_text(needle, exact=False).first.wait_for(state="visible", timeout=8000)
            return True, "text_present"
        except Exception:
            return False, f"terminal_text_not_found:{needle}"

    if expected_element or (cond_type == "element_present" and cond_value):
        needle = expected_element or cond_value
        for selector in (f"[data-testid='{needle}']", f"#{needle}", needle):
            try:
                page.locator(selector).first.wait_for(state="visible", timeout=4000)
                return True, "element_present"
            except Exception:
                pass
        try:
            page.get_by_text(needle, exact=False).first.wait_for(state="visible", timeout=4000)
            return True, "element_present_text"
        except Exception:
            return False, f"terminal_element_not_found:{needle}"

    return False, "missing_terminal_condition"
