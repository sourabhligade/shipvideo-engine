from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from observability import record_agent_browser_diagnostics


def collect_ab_failure_diagnostics(cli: Any) -> Dict[str, Any]:
    console_messages = cli.console_messages()
    page_errors = cli.page_errors()
    network_requests = cli.network_requests()
    network_error_count = 0
    for request in network_requests:
        status = request.get("status")
        error_text = str(request.get("error") or "").strip()
        if isinstance(status, int) and status >= 400:
            network_error_count += 1
        elif error_text:
            network_error_count += 1
    diagnostics = {
        "console_messages": console_messages[:20],
        "page_errors": page_errors[:20],
        "network_request_count": len(network_requests),
        "network_error_count": network_error_count,
        "network_requests_preview": network_requests[:20],
    }
    record_agent_browser_diagnostics(
        console_count=len(console_messages),
        page_error_count=len(page_errors),
        network_request_count=len(network_requests),
        network_error_count=network_error_count,
    )
    return diagnostics


def _evidence_bundle(
    cli: Any,
    step_result: Dict[str, Any],
    *,
    screenshot_dir: Path | None = None,
) -> Dict[str, Any]:
    bundle: Dict[str, Any] = {
        "annotated_screenshot": "",
        "snapshot_diff": {},
    }
    out_dir = screenshot_dir
    if out_dir is None:
        raw = str(
            step_result.get("before_screenshot")
            or step_result.get("after_screenshot")
            or step_result.get("screenshot_path")
            or ""
        ).strip()
        if raw:
            out_dir = Path(raw).parent
    annotated = getattr(cli, "annotated_screenshot", None)
    if callable(annotated) and out_dir is not None:
        path = Path(out_dir) / f"fail_annotated_{step_result.get('index', 0)}.png"
        try:
            annotated(path)
            bundle["annotated_screenshot"] = str(path)
        except Exception as exc:
            bundle["annotated_screenshot_error"] = f"{type(exc).__name__}: {exc}"
    diff_snapshot = getattr(cli, "diff_snapshot", None)
    if callable(diff_snapshot):
        try:
            bundle["snapshot_diff"] = diff_snapshot() or {}
        except Exception as exc:
            bundle["snapshot_diff_error"] = f"{type(exc).__name__}: {exc}"
    return bundle


def attach_ab_failure_diagnostics(
    cli: Any,
    step_result: Dict[str, Any],
    *,
    screenshot_dir: Path | None = None,
) -> None:
    diagnostics = collect_ab_failure_diagnostics(cli)
    diagnostics.update(_evidence_bundle(cli, step_result, screenshot_dir=screenshot_dir))
    step_result["diagnostics"] = diagnostics


def discard_screenshots(paths: List[Path]) -> None:
    for path in paths:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass


def step_screenshot_paths(step_result: Dict[str, Any]) -> List[Path]:
    paths: List[Path] = []
    for key in ("screenshot_path", "before_screenshot", "after_screenshot"):
        raw = str(step_result.get(key) or "").strip()
        if raw:
            paths.append(Path(raw))
    return paths


def approved_frame_paths(results: List[Dict[str, Any]]) -> List[str]:
    approved: List[str] = []
    seen: set[str] = set()
    for idx, result in enumerate(results):
        if str(result.get("status") or "") != "ok":
            continue
        outcome = str(result.get("outcome") or "")
        step = result.get("step") or {}
        action = str(step.get("action") or "")
        keep = False
        if action == "screenshot":
            previous = results[idx - 1] if idx > 0 else {}
            previous_step = previous.get("step") or {}
            previous_action = str(previous_step.get("action") or "")
            previous_ok = str(previous.get("status") or "") == "ok"
            previous_success = str(previous.get("outcome") or "") == "success"
            keep = (
                outcome == "success"
                and previous_ok
                and previous_success
                and previous_action in {"goto", "click"}
            )
        elif action == "assert_terminal":
            keep = bool(result.get("terminal_condition_reached"))
        if not keep:
            continue
        for path in step_screenshot_paths(result):
            raw = str(path)
            if raw and raw not in seen and Path(raw).exists():
                seen.add(raw)
                approved.append(raw)
    return approved


def discard_step_screenshots(step_result: Dict[str, Any]) -> None:
    discard_screenshots(step_screenshot_paths(step_result))
    step_result["before_screenshot"] = ""
    step_result["after_screenshot"] = ""


def should_keep_click_screenshots(step_result: Dict[str, Any]) -> bool:
    outcome = str(step_result.get("outcome") or "")
    return outcome == "success"
