from __future__ import annotations

import json
import time
from typing import Any, Optional

from app.browser.agent_browser_types import ABPageSettleResult, ValidationCondition
from app.config_types import CaptureSettings
from app.execution.constants import (
    AB_DOMCONTENTLOADED_TIMEOUT_S,
    AB_NETWORKIDLE_TIMEOUT_S,
    AB_VALIDATION_WAIT_TIMEOUT_S,
)


def configure_ab_session(cli: Any, capture_settings: CaptureSettings) -> dict:
    cli.set_viewport(
        capture_settings.viewport_width,
        capture_settings.viewport_height,
    )
    return {
        "viewport_width": int(capture_settings.viewport_width),
        "viewport_height": int(capture_settings.viewport_height),
    }


def wait_for_ab_element_present(
    cli: Any,
    expected: str,
    *,
    timeout_s: int = AB_VALIDATION_WAIT_TIMEOUT_S,
) -> bool:
    needle = str(expected or "").strip()
    if not needle:
        return False

    deadline = time.monotonic() + max(int(timeout_s), 1)
    selector_candidates = (f"[data-testid='{needle}']", f"#{needle}")

    wait_fn = getattr(cli, "wait_for_function", None)
    if callable(wait_fn):
        remaining = max(int(deadline - time.monotonic()), 1)
        try:
            wait_fn(
                f"document.body && document.body.innerText.includes({json.dumps(needle)})",
                timeout=remaining,
            )
        except Exception:
            pass

    while time.monotonic() < deadline:
        try:
            ref = cli.find_testid_ref(needle)
            if ref and cli.is_visible(ref):
                return True
        except Exception:
            pass

        for selector in selector_candidates:
            try:
                if cli.get_count(selector) > 0 and cli.is_visible(selector):
                    return True
            except Exception:
                pass

        try:
            semantic_ref = cli.find_ref(needle)
            if semantic_ref and cli.is_visible(semantic_ref):
                return True
        except Exception:
            pass

        try:
            cli.wait(250)
        except Exception:
            pass

    return False


def wait_for_playwright_validation(page: Any, condition: Optional[ValidationCondition]) -> None:
    if condition is None:
        return

    cond_type = str(condition.get("type") or "").strip()
    cond_value = str(condition.get("value") or "").strip()
    if not cond_type or not cond_value:
        return

    if cond_type == "text_present":
        page.get_by_text(cond_value, exact=False).first.wait_for(
            state="visible",
            timeout=8000,
        )
        return

    if cond_type == "url_match":
        page.wait_for_url(f"**{cond_value}**", timeout=8000)
        return

    if cond_type == "element_present":
        selector_candidates = [
            f"[data-testid='{cond_value}']",
            f"#{cond_value}",
        ]
        for selector in selector_candidates:
            try:
                page.locator(selector).first.wait_for(state="visible", timeout=8000)
                return
            except Exception:
                pass
        page.get_by_text(cond_value, exact=False).first.wait_for(
            state="visible",
            timeout=8000,
        )


def playwright_condition_holds(page: Any, condition: Optional[ValidationCondition]) -> bool:
    if condition is None:
        return False
    cond_type = str(condition.get("type") or "").strip()
    cond_value = str(condition.get("value") or "").strip()
    if not cond_type or not cond_value:
        return False
    try:
        if cond_type == "url_match":
            return cond_value.lower() in str(getattr(page, "url", "") or "").lower()
        if cond_type == "text_present":
            return bool(page.get_by_text(cond_value, exact=False).first.is_visible())
        if cond_type == "element_present":
            for selector in (f"[data-testid='{cond_value}']", f"#{cond_value}"):
                locator = page.locator(selector)
                try:
                    if locator.count() > 0 and locator.first.is_visible():
                        return True
                except Exception:
                    continue
            return bool(page.get_by_text(cond_value, exact=False).first.is_visible())
    except Exception:
        return False
    return False


def settle_ab_page(
    cli: Any,
    *,
    validation_condition: Optional[ValidationCondition] = None,
    require_networkidle: bool = True,
) -> ABPageSettleResult:
    """Wait for the bound proof first. Skip networkidle when that proof already passed."""
    settle: ABPageSettleResult = {
        "domcontentloaded": False,
        "networkidle": False,
        "validation_wait": "",
        "fallback_wait_used": False,
        "networkidle_skipped": False,
    }

    try:
        cli.wait_for_load_state(
            "domcontentloaded",
            timeout=AB_DOMCONTENTLOADED_TIMEOUT_S,
        )
        settle["domcontentloaded"] = True
    except Exception:
        pass

    proof_passed = False
    if validation_condition is not None:
        cond_type = validation_condition["type"]
        cond_value = validation_condition["value"]
        try:
            if cond_type == "text_present":
                cli.wait_for_text(cond_value, timeout=AB_VALIDATION_WAIT_TIMEOUT_S)
                settle["validation_wait"] = "text_present"
                proof_passed = True
            elif cond_type == "url_match":
                cli.wait_for_url(cond_value, timeout=AB_VALIDATION_WAIT_TIMEOUT_S)
                settle["validation_wait"] = "url_match"
                proof_passed = True
            elif cond_type == "element_present":
                if wait_for_ab_element_present(
                    cli,
                    cond_value,
                    timeout_s=AB_VALIDATION_WAIT_TIMEOUT_S,
                ):
                    settle["validation_wait"] = "element_present"
                    proof_passed = True
        except Exception as exc:
            if cond_type == "text_present":
                print(
                    f"[step_runner] wait_for_text failed value={cond_value!r} "
                    f"error={type(exc).__name__}: {exc}",
                    flush=True,
                )

    if proof_passed or not require_networkidle:
        settle["networkidle_skipped"] = True
        settle["fallback_wait_used"] = False
        return settle

    try:
        cli.wait_for_load_state(
            "networkidle",
            timeout=AB_NETWORKIDLE_TIMEOUT_S,
        )
        settle["networkidle"] = True
    except Exception:
        pass

    settle["fallback_wait_used"] = not settle["networkidle"]
    return settle
