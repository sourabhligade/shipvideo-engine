from __future__ import annotations

from playwright.sync_api import sync_playwright

from app.context.dom_extractor import extract_dom_context
from app.execution.navigation_detector import capture_state, detect_major_change, wait_stable_after_navigation
from app.llm.retry_engine import regenerate_with_feedback, regenerate_single_step_toward_testid
from app.policy.selector_validator import validate_step_against_dom

from app.execution.constants import (
    MAX_STEPS_PER_RUN,
    MAX_RETRIES_PER_STEP,
    MAX_AB_REPLANS_PER_RUN,
    MAX_AB_FLOW_RESTARTS,
    AB_DOMCONTENTLOADED_TIMEOUT_S,
    AB_NETWORKIDLE_TIMEOUT_S,
    AB_VALIDATION_WAIT_TIMEOUT_S,
    AB_SCROLL_RETRY_COUNT,
    AB_SCROLL_RETRY_PX,
    AB_SCROLL_SETTLE_TIMEOUT_S,
    MAX_TESTID_SEARCH_ACTIONS,
)
from app.execution.events import log_event
from app.execution.ab_url import resolve_url
from app.execution.ab_settle import (
    configure_ab_session,
    settle_ab_page,
    wait_for_ab_element_present,
    wait_for_playwright_validation,
    playwright_condition_holds,
)
from app.execution.ab_target import (
    resolve_ab_ref_with_commands,
    scroll_to_find,
    snapshot_element_by_ref,
    passes_preclick_safety_check,
    resolve_ab_click_target,
    ensure_ab_target_actionable,
)
from app.execution.ab_proof import (
    detect_state_change,
    normalize_validation_condition,
    extract_validation_condition,
    contains_ci,
    matches_validation_condition,
    element_names,
    describe_validation_actual,
    evaluate_click_validation,
    validation_from_successful_text_wait,
    next_click_intent,
    snapshot_has_intent,
    planned_url_needles,
    infer_runtime_validation,
    terminal_match_in_snapshot,
)
from app.execution.ab_bind import (
    bind_click_step,
    drop_labels_from_queue,
    looks_like_cta_intent,
    resolve_failed_confirm_proof,
    used_click_labels,
)
from app.execution.ab_recovery import (
    snapshot_to_dom_context,
    deterministic_setup_chip_from_snapshot,
    recover_ab_prerequisite_steps,
    validated_milestone_steps,
    replay_ab_milestones,
)
from app.execution.ab_objective import (
    get_generation_context,
    allowed_routes_from_objective,
    merge_allowed_routes_into_dom_ctx,
    objective_changed_testids,
    objective_start_route,
    objective_contract,
    snapshot_contains_testid,
    first_active_surface,
    make_search_validation,
    append_search_screenshot_result,
    should_use_testid_search,
)
from app.execution.ab_diagnostics import (
    collect_ab_failure_diagnostics,
    attach_ab_failure_diagnostics,
    discard_screenshots,
    step_screenshot_paths,
    approved_frame_paths,
    discard_step_screenshots,
    should_keep_click_screenshots,
)
from app.execution.ab_terminal import (
    resolve_terminal_expectation,
    assert_ab_terminal_condition,
    assert_playwright_terminal_condition,
)
from app.execution.ab_metrics import _build_metrics, _classify_final_outcome
from app.execution.ab_click import (
    _capture_ab_screenshot,
    _is_stale_ref_error,
    _run_ab_click_attempt,
)
from app.execution.ab_testid_search import _run_ab_changed_testid_search
from app.execution.pw_stepwise import _execute_one, run_stepwise
from app.execution.ab_stepwise import run_ab_stepwise

# Facade names locked by existing tests and patches.
_log = log_event
_resolve_url = resolve_url
_configure_ab_session = configure_ab_session
_settle_ab_page = settle_ab_page
_wait_for_ab_element_present = wait_for_ab_element_present
_wait_for_playwright_validation = wait_for_playwright_validation
_playwright_condition_holds = playwright_condition_holds
_resolve_ab_ref_with_commands = resolve_ab_ref_with_commands
_scroll_to_find = scroll_to_find
_snapshot_element_by_ref = snapshot_element_by_ref
_passes_preclick_safety_check = passes_preclick_safety_check
_resolve_ab_click_target = resolve_ab_click_target
_ensure_ab_target_actionable = ensure_ab_target_actionable
_normalize_validation_condition = normalize_validation_condition
_extract_validation_condition = extract_validation_condition
_detect_state_change = detect_state_change
_contains_ci = contains_ci
_matches_validation_condition = matches_validation_condition
_element_names = element_names
_describe_validation_actual = describe_validation_actual
_evaluate_click_validation = evaluate_click_validation
_validation_from_successful_text_wait = validation_from_successful_text_wait
_next_click_intent = next_click_intent
_snapshot_has_intent = snapshot_has_intent
_planned_url_needles = planned_url_needles
_infer_runtime_validation = infer_runtime_validation
_terminal_match_in_snapshot = terminal_match_in_snapshot
_looks_like_cta_intent = looks_like_cta_intent
_ab_snapshot_to_dom_context = snapshot_to_dom_context
_deterministic_setup_chip_from_snapshot = deterministic_setup_chip_from_snapshot
_recover_ab_prerequisite_steps = recover_ab_prerequisite_steps
_validated_milestone_steps = validated_milestone_steps
_replay_ab_milestones = replay_ab_milestones
_get_generation_context = get_generation_context
_allowed_routes_from_objective = allowed_routes_from_objective
_merge_allowed_routes_into_dom_ctx = merge_allowed_routes_into_dom_ctx
_objective_changed_testids = objective_changed_testids
_objective_start_route = objective_start_route
_objective_contract = objective_contract
_snapshot_contains_testid = snapshot_contains_testid
_first_active_surface = first_active_surface
_make_search_validation = make_search_validation
_append_search_screenshot_result = append_search_screenshot_result
_should_use_testid_search = should_use_testid_search
_collect_ab_failure_diagnostics = collect_ab_failure_diagnostics
_attach_ab_failure_diagnostics = attach_ab_failure_diagnostics
_discard_screenshots = discard_screenshots
_step_screenshot_paths = step_screenshot_paths
_approved_frame_paths = approved_frame_paths
_discard_step_screenshots = discard_step_screenshots
_should_keep_click_screenshots = should_keep_click_screenshots
_resolve_terminal_expectation = resolve_terminal_expectation
_assert_ab_terminal_condition = assert_ab_terminal_condition
_assert_playwright_terminal_condition = assert_playwright_terminal_condition
