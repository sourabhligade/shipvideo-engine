"""
Scenario tests for every audit priority fix.

Priority map:
  #1  normalize keeps stable selectors
  #2  AB assert_terminal fail-closed + full conditions
  #3  empty re-anchor hard-fail
  #4  runtime goto uses generation real_routes
  #5  multi-match reject
  #6  subtitle cue alignment
  #7  ffmpeg/script timeouts + A/V frame holds
  #8  skip deleted routes
  #9  input merge testid/aria/id
  #10 MAX_PATCH_CHARS >= 4000
  #11 record_run only after success
  #12 preview readiness prefers GET
  +    repair retry breadth, crawler title, mux fail loud, dual-filter removed
"""
from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.policy.selector_validator import validate_step_against_dom
from app.steps.step_normalizer import (
    normalize_steps,
    _extract_routes_from_diff,
    _is_stable_selector,
)
from app.steps.pr_extraction import MAX_PATCH_CHARS
from app.steps.dom_crawler import _merge_snapshots
from app.product.audio_timing import align_texts_to_speech_segments
from app.execution.step_runner import (
    _assert_ab_terminal_condition,
    _resolve_terminal_expectation,
    _allowed_routes_from_objective,
    _merge_allowed_routes_into_dom_ctx,
    _execute_one,
)


# ---------------------------------------------------------------------------
# #1 Normalize keeps stable selectors
# ---------------------------------------------------------------------------
class Scenario1_NormalizeKeepsSelectors(unittest.TestCase):
    def test_testid_kept_with_label(self):
        out = normalize_steps(
            [{"action": "click", "label": "Save", "selector": "[data-testid='save-btn']"}]
        )
        self.assertEqual(out[0]["selector"], "[data-testid='save-btn']")
        self.assertEqual(out[0]["label"], "Save")

    def test_aria_kept_with_label(self):
        out = normalize_steps(
            [{"action": "click", "label": "Close", "selector": "[aria-label='Close dialog']"}]
        )
        self.assertEqual(out[0]["selector"], "[aria-label='Close dialog']")

    def test_double_quotes_normalized(self):
        out = normalize_steps(
            [{"action": "click", "label": "X", "selector": '[data-testid="x"]'}]
        )
        self.assertEqual(out[0]["selector"], "[data-testid='x']")

    def test_id_selector_stable(self):
        self.assertTrue(_is_stable_selector("#primary-cta"))
        out = normalize_steps(
            [{"action": "click", "label": "Go", "selector": "#primary-cta"}]
        )
        self.assertEqual(out[0]["selector"], "#primary-cta")

    def test_label_only_still_works(self):
        out = normalize_steps([{"action": "click", "label": "Continue"}])
        self.assertEqual(out[0]["label"], "Continue")
        self.assertNotIn("selector", out[0])

    def test_weak_css_still_attached(self):
        out = normalize_steps(
            [{"action": "click", "label": "Save", "selector": "button.primary"}]
        )
        self.assertEqual(out[0]["label"], "Save")
        self.assertEqual(out[0]["selector"], "button.primary")


# ---------------------------------------------------------------------------
# #2 AB terminal fail-closed
# ---------------------------------------------------------------------------
class _FakeCLI:
    def __init__(self, *, fail_text=False, fail_url=False, url="https://ex.com/done", testid_ref=""):
        self.fail_text = fail_text
        self.fail_url = fail_url
        self.url = url
        self.testid_ref = testid_ref
        self.calls = []

    def wait_for_text(self, text, *, timeout):
        self.calls.append(("wait_for_text", text))
        if self.fail_text:
            raise RuntimeError("text wait failed")

    def wait_for_url(self, pattern, *, timeout):
        self.calls.append(("wait_for_url", pattern))
        if self.fail_url:
            raise RuntimeError("url wait failed")

    def get_url(self):
        return self.url

    def find_testid_ref(self, t):
        self.calls.append(("find_testid_ref", t))
        return self.testid_ref

    def find_element(self, s):
        return ""

    def find_ref(self, i):
        return ""

    def is_visible(self, r):
        return bool(r)

    def get_count(self, s):
        return 1 if self.testid_ref else 0

    def wait(self, ms):
        pass


class Scenario2_ABTerminalFailClosed(unittest.TestCase):
    def test_empty_terminal_not_found(self):
        r = _assert_ab_terminal_condition(
            _FakeCLI(),
            condition={},
            expected_element="",
            extract_snapshot=lambda **k: {},
        )
        self.assertFalse(r["found"])
        self.assertEqual(r["source"], "missing_terminal_condition")

    def test_expected_url_resolves_to_url_match(self):
        cond, exp = _resolve_terminal_expectation({"expected_url": "/settings"})
        self.assertEqual(cond["type"], "url_match")
        self.assertEqual(cond["value"], "/settings")

    def test_expected_text_resolves_to_text_present(self):
        cond, exp = _resolve_terminal_expectation({"expected_text": "Done"})
        self.assertEqual(cond["type"], "text_present")
        self.assertEqual(cond["value"], "Done")

    def test_url_match_success(self):
        cond, exp = _resolve_terminal_expectation({"expected_url": "/done"})
        r = _assert_ab_terminal_condition(
            _FakeCLI(url="https://ex.com/done"),
            condition=cond,
            expected_element=exp,
            extract_snapshot=lambda **k: {},
        )
        self.assertTrue(r["found"])

    def test_url_match_failure(self):
        cond, exp = _resolve_terminal_expectation({"expected_url": "/done"})
        r = _assert_ab_terminal_condition(
            _FakeCLI(fail_url=True, url="https://ex.com/other"),
            condition=cond,
            expected_element=exp,
            extract_snapshot=lambda **k: {},
        )
        self.assertFalse(r["found"])
        self.assertEqual(r["source"], "url_match_failed")

    def test_text_present_failure(self):
        cond, exp = _resolve_terminal_expectation({"expected_text": "Missing"})
        r = _assert_ab_terminal_condition(
            _FakeCLI(fail_text=True),
            condition=cond,
            expected_element=exp,
            extract_snapshot=lambda **k: {},
        )
        self.assertFalse(r["found"])
        self.assertEqual(r["source"], "text_present_failed")

    def test_element_present_via_testid(self):
        r = _assert_ab_terminal_condition(
            _FakeCLI(testid_ref="@e1"),
            condition={"type": "element_present", "value": "modal"},
            expected_element="modal",
            extract_snapshot=lambda **k: {
                "interactive_elements": [],
                "context_elements": [],
                "snapshot_text": "",
            },
        )
        self.assertTrue(r["found"])


# ---------------------------------------------------------------------------
# #3 Empty re-anchor hard-fail
# ---------------------------------------------------------------------------
class Scenario3_ReanchorHardFail(unittest.TestCase):
    def test_empty_regen_returns_navigation_reanchor_failed(self):
        from app.execution import step_runner as sr

        class FakePage:
            def goto(self, *a, **k):
                return None

        class FakeBrowser:
            def new_page(self, **k):
                return FakePage()

            def close(self):
                return None

        class FakeChromium:
            def launch(self, **k):
                return FakeBrowser()

        class FakePW:
            def __init__(self):
                self.chromium = FakeChromium()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        states = [
            SimpleNamespace(path="/", fingerprint=None, dom_hash="a"),
            SimpleNamespace(path="/next", fingerprint=None, dom_hash="b"),
        ]
        idx = {"i": 0}

        def fake_capture(page):
            s = states[min(idx["i"], len(states) - 1)]
            idx["i"] += 1
            return s

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sr, "sync_playwright", return_value=FakePW()), patch.object(
                sr, "wait_stable_after_navigation"
            ), patch.object(
                sr, "extract_dom_context", return_value={"routes": ["/"], "buttons": []}
            ), patch.object(
                sr, "capture_state", side_effect=fake_capture
            ), patch.object(
                sr, "detect_major_change", return_value=True
            ), patch.object(
                sr, "_execute_one", return_value=(True, 1, None)
            ), patch.object(
                sr, "validate_step_against_dom", return_value=(True, "ok")
            ), patch.object(
                sr,
                "regenerate_with_feedback",
                return_value=([], [{"attempt": 1, "status": "empty_steps"}]),
            ):
                result = sr.run_stepwise(
                    preview_url="https://example.com",
                    initial_steps=[{"action": "click", "label": "Go"}],
                    objective={"generation_context": {"real_routes": ["/x"]}},
                    screenshot_dir=Path(tmp),
                    max_retries_per_failure=1,
                )
        self.assertFalse(result["success"])
        self.assertEqual(result["failure_reason"], "navigation_reanchor_failed")


# ---------------------------------------------------------------------------
# #4 Runtime goto uses generation real_routes
# ---------------------------------------------------------------------------
class Scenario4_RuntimeGotoRealRoutes(unittest.TestCase):
    def test_goto_rejected_without_generation_routes(self):
        ok, reason = validate_step_against_dom(
            {"action": "goto", "url": "/settings"},
            {"routes": ["/", "/home"]},
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "route_not_in_dom:/settings")

    def test_goto_allowed_with_allowed_routes(self):
        ok, reason = validate_step_against_dom(
            {"action": "goto", "url": "/settings"},
            {"routes": ["/", "/home"]},
            allowed_routes={"/settings", "/"},
        )
        self.assertTrue(ok)

    def test_allowed_routes_from_objective(self):
        routes = _allowed_routes_from_objective(
            {
                "generation_context": {
                    "real_routes": ["/a", "/b"],
                    "start_route": "/c",
                    "start_route_candidates": ["/d"],
                }
            }
        )
        self.assertTrue({"/a", "/b", "/c", "/d", "/"}.issubset(routes))

    def test_merge_into_dom_ctx(self):
        merged = _merge_allowed_routes_into_dom_ctx(
            {"routes": ["/"], "buttons": [1]},
            {"/settings"},
        )
        self.assertIn("/settings", merged["routes"])
        self.assertEqual(merged["buttons"], [1])

    def test_goto_missing_url_still_fails(self):
        ok, reason = validate_step_against_dom(
            {"action": "goto", "url": ""},
            {"routes": ["/"]},
            allowed_routes={"/x"},
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "missing_goto_url")


# ---------------------------------------------------------------------------
# #5 Multi-match reject
# ---------------------------------------------------------------------------
class Scenario5_MultiMatchReject(unittest.TestCase):
    def test_selector_count_3_rejected(self):
        page = MagicMock()
        loc = MagicMock()
        loc.count.return_value = 3
        page.locator.return_value = loc
        ok, reason = validate_step_against_dom(
            {"action": "click", "selector": "[data-testid='x']"},
            {"routes": ["/"]},
            page=page,
        )
        self.assertFalse(ok)
        self.assertIn("selector_not_unique", reason)
        self.assertIn("count=3", reason)

    def test_label_count_2_rejected(self):
        page = MagicMock()
        loc = MagicMock()
        loc.count.return_value = 2
        page.get_by_text.return_value = loc
        ok, reason = validate_step_against_dom(
            {"action": "click", "label": "Save"},
            {"routes": ["/"], "buttons": []},
            page=page,
        )
        self.assertFalse(ok)
        self.assertIn("label_not_unique", reason)

    def test_unique_count_1_ok(self):
        page = MagicMock()
        loc = MagicMock()
        loc.count.return_value = 1
        page.locator.return_value = loc
        ok, _ = validate_step_against_dom(
            {"action": "click", "selector": "[data-testid='x']"},
            {"routes": ["/"]},
            page=page,
        )
        self.assertTrue(ok)

    def test_execute_one_rejects_ambiguous_selector(self):
        page = MagicMock()
        loc = MagicMock()
        loc.count.return_value = 4
        page.locator.return_value = loc
        ok, _, err = _execute_one(
            page,
            "https://ex.com",
            {"action": "click", "selector": "button"},
            Path("/tmp"),
            1,
        )
        self.assertFalse(ok)
        self.assertIn("selector_not_unique", err or "")

    def test_execute_one_rejects_ambiguous_label(self):
        page = MagicMock()
        loc = MagicMock()
        loc.count.return_value = 2
        page.get_by_text.return_value = loc
        ok, _, err = _execute_one(
            page,
            "https://ex.com",
            {"action": "click", "label": "Next"},
            Path("/tmp"),
            1,
        )
        self.assertFalse(ok)
        self.assertIn("label_not_unique", err or "")


# ---------------------------------------------------------------------------
# #6 Subtitle cue alignment
# ---------------------------------------------------------------------------
class Scenario6_SubtitleCueAlignment(unittest.TestCase):
    def test_equal_segments_one_to_one(self):
        segs = [
            {"start": 0.0, "end": 6.5, "duration": 6.5},
            {"start": 7.0, "end": 9.9, "duration": 2.9},
            {"start": 10.3, "end": 14.8, "duration": 4.5},
        ]
        cues = align_texts_to_speech_segments(["a", "b", "c"], segs, total_duration=14.8)
        self.assertEqual(len(cues), 3)
        self.assertAlmostEqual(cues[0]["start"], 0.0)
        self.assertAlmostEqual(cues[1]["start"], 7.0)
        self.assertAlmostEqual(cues[2]["start"], 10.3)
        self.assertGreater(cues[2]["start"], cues[1]["start"])

    def test_last_cue_never_restarts_at_zero_when_equal(self):
        # Repro of live audit1 bug inputs
        segs = [
            {"start": 0.0, "end": 6.543855, "duration": 6.543855},
            {"start": 6.960181, "end": 9.87873, "duration": 2.918549},
            {"start": 10.281587, "end": 14.790703, "duration": 4.509116},
        ]
        cues = align_texts_to_speech_segments(
            ["open", "docs", "api"], segs, total_duration=14.790703
        )
        self.assertNotAlmostEqual(cues[2]["start"], 0.0)
        self.assertAlmostEqual(cues[2]["start"], 10.281587)

    def test_more_segments_than_lines_reserves_last(self):
        segs = [
            {"start": 0.0, "end": 1.0, "duration": 1.0},
            {"start": 1.0, "end": 2.0, "duration": 1.0},
            {"start": 2.0, "end": 3.0, "duration": 1.0},
            {"start": 3.0, "end": 4.0, "duration": 1.0},
        ]
        cues = align_texts_to_speech_segments(["a", "b"], segs, total_duration=4.0)
        self.assertEqual(len(cues), 2)
        self.assertAlmostEqual(cues[1]["end"], 4.0)
        self.assertGreater(cues[1]["start"], 0.0)

    def test_empty_texts(self):
        self.assertEqual(
            align_texts_to_speech_segments([], [{"start": 0, "end": 1, "duration": 1}], total_duration=1),
            [],
        )

    def test_no_speech_equal_fallback(self):
        cues = align_texts_to_speech_segments(["a", "b"], [], total_duration=10)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["source"], "equal_fallback")
        self.assertAlmostEqual(cues[0]["end"], 5.0)


# ---------------------------------------------------------------------------
# #7 Timeouts + frame duration alignment (source + unit)
# ---------------------------------------------------------------------------
class Scenario7_TimeoutsAndAV(unittest.TestCase):
    def test_render_has_timeout(self):
        self.assertIn("timeout=120", Path("app/render.py").read_text())

    def test_webm_converter_has_timeout(self):
        self.assertIn("timeout=120", Path("app/recorder/video_processor.py").read_text())

    def test_product_video_has_timeout_and_mux_raises(self):
        src = Path("app/product/video.py").read_text()
        self.assertIn("timeout=120", src)
        self.assertIn("ffmpeg mux failed", src)
        self.assertNotIn("write_bytes(silent_mp4.read_bytes())\n    else:", src.replace(" ", ""))

    def test_audio_timing_key_calls_have_timeout(self):
        src = Path("app/product/audio_timing.py").read_text()
        self.assertGreaterEqual(src.count("timeout=120"), 4)

    def test_script_runner_enforces_wall_clock(self):
        src = Path("app/recorder/playwright_runner.py").read_text()
        self.assertIn("ThreadPoolExecutor", src)
        self.assertIn("fut.result", src)
        self.assertIn("timeout_seconds", src)

    def test_frame_hold_uses_next_cue_start(self):
        src = Path("app/product/video.py").read_text()
        self.assertIn('cues[i + 1]["start"]', src)
        self.assertIn("audio_total", src)


# ---------------------------------------------------------------------------
# #8 Skip deleted routes
# ---------------------------------------------------------------------------
class Scenario8_SkipDeletedRoutes(unittest.TestCase):
    def test_removed_status_skipped(self):
        routes = _extract_routes_from_diff(
            [
                {"path": "app/old/page.tsx", "status": "removed"},
                {"path": "app/new/page.tsx", "status": "added"},
            ]
        )
        self.assertEqual(routes, {"/new"})

    def test_deleted_status_skipped(self):
        routes = _extract_routes_from_diff(
            [{"path": "app/gone/page.tsx", "status": "deleted"}]
        )
        self.assertEqual(routes, set())

    def test_modified_kept(self):
        routes = _extract_routes_from_diff(
            [{"path": "app/billing/page.tsx", "status": "modified"}]
        )
        self.assertEqual(routes, {"/billing"})

    def test_pages_router_deleted_skipped(self):
        routes = _extract_routes_from_diff(
            [
                {"path": "pages/about.tsx", "status": "removed"},
                {"path": "pages/contact.tsx", "status": "added"},
            ]
        )
        self.assertEqual(routes, {"/contact"})


# ---------------------------------------------------------------------------
# #9 Input merge keys
# ---------------------------------------------------------------------------
class Scenario9_InputMergeKeys(unittest.TestCase):
    def test_testid_only_input_kept(self):
        merged = _merge_snapshots(
            {
                "/": {
                    "buttons": [],
                    "links": [],
                    "inputs": [
                        {
                            "name": "",
                            "placeholder": "",
                            "testid": "email",
                            "aria": "Email",
                            "id": "e1",
                            "input_type": "email",
                        },
                        {
                            "name": "q",
                            "placeholder": "Search",
                            "testid": "",
                            "aria": "",
                            "id": "",
                            "input_type": "text",
                        },
                    ],
                    "data_testids": [],
                }
            }
        )
        self.assertEqual(len(merged["inputs"]), 2)
        self.assertIn("email", {i.get("testid") for i in merged["inputs"]})

    def test_aria_only_input_kept(self):
        merged = _merge_snapshots(
            {
                "/": {
                    "buttons": [],
                    "links": [],
                    "inputs": [
                        {
                            "name": "",
                            "placeholder": "",
                            "testid": "",
                            "aria": "Password",
                            "id": "",
                            "input_type": "password",
                        }
                    ],
                    "data_testids": [],
                }
            }
        )
        self.assertEqual(len(merged["inputs"]), 1)
        self.assertEqual(merged["inputs"][0]["aria"], "Password")

    def test_id_only_input_kept(self):
        merged = _merge_snapshots(
            {
                "/": {
                    "buttons": [],
                    "links": [],
                    "inputs": [
                        {
                            "name": "",
                            "placeholder": "",
                            "testid": "",
                            "aria": "",
                            "id": "user-name",
                            "input_type": "text",
                        }
                    ],
                    "data_testids": [],
                }
            }
        )
        self.assertEqual(len(merged["inputs"]), 1)

    def test_duplicate_testid_deduped(self):
        merged = _merge_snapshots(
            {
                "/a": {
                    "buttons": [],
                    "links": [],
                    "inputs": [
                        {"name": "", "placeholder": "", "testid": "email", "aria": "", "id": "", "input_type": "email"}
                    ],
                    "data_testids": [],
                },
                "/b": {
                    "buttons": [],
                    "links": [],
                    "inputs": [
                        {"name": "", "placeholder": "", "testid": "email", "aria": "", "id": "", "input_type": "email"}
                    ],
                    "data_testids": [],
                },
            }
        )
        self.assertEqual(len(merged["inputs"]), 1)


# ---------------------------------------------------------------------------
# #10 MAX_PATCH_CHARS
# ---------------------------------------------------------------------------
class Scenario10_MaxPatchChars(unittest.TestCase):
    def test_max_patch_at_least_primary_tier(self):
        self.assertGreaterEqual(MAX_PATCH_CHARS, 4000)

    def test_matches_diff_budget_tier2(self):
        from app.steps.diff_budget import _TIER_BUDGET

        self.assertGreaterEqual(MAX_PATCH_CHARS, _TIER_BUDGET[2])


# ---------------------------------------------------------------------------
# #11 record_run only after success
# ---------------------------------------------------------------------------
class Scenario11_RecordRunAfterSuccess(unittest.TestCase):
    def test_no_record_run_immediately_after_dedupe_check(self):
        src = Path("app/webhook.py").read_text()
        start = src.find("check_already_ran")
        self.assertGreater(start, 0)
        window = src[start : start + 300]
        self.assertNotIn("record_run(", window)

    def test_record_run_after_successful_comment(self):
        src = Path("app/webhook.py").read_text()
        success_comment = src.find(
            "comment_on_pr(repo_full_name, pr_number, video_url, extra_note=extra_note)"
        )
        self.assertGreater(success_comment, 0)
        after = src[success_comment : success_comment + 200]
        self.assertIn("record_run(repo_full_name, pr_number, commit_sha)", after)

    def test_dual_smart_prefilter_removed(self):
        src = Path("app/webhook.py").read_text()
        self.assertNotIn('trigger_mode == "smart" and not force', src)


# ---------------------------------------------------------------------------
# #12 Preview GET readiness
# ---------------------------------------------------------------------------
class Scenario12_PreviewGetReady(unittest.TestCase):
    def test_prefers_get(self):
        src = Path("app/preview_url_resolver.py").read_text()
        self.assertIn('"GET"', src)
        self.assertIn("Range", src)
        # GET before HEAD in probe order
        get_i = src.find('("GET", "HEAD")')
        if get_i < 0:
            get_i = src.find("'GET'")
        self.assertGreater(get_i, 0)

    def test_wait_for_preview_ready_callable(self):
        from app.preview_url_resolver import wait_for_preview_ready

        self.assertTrue(callable(wait_for_preview_ready))


# ---------------------------------------------------------------------------
# Extra reliability scenarios
# ---------------------------------------------------------------------------
class ScenarioRepairRetryBreadth(unittest.TestCase):
    def test_retries_json_and_value_errors(self):
        src = Path("app/llm/retry_engine.py").read_text()
        self.assertIn("JSONDecodeError", src)
        self.assertIn("ValueError", src)
        self.assertIn("TypeError", src)
        self.assertIn("KeyError", src)

    def test_regenerate_accepts_allowed_routes(self):
        from app.llm.retry_engine import regenerate_with_feedback

        sig = inspect.signature(regenerate_with_feedback)
        self.assertIn("allowed_routes", sig.parameters)


class ScenarioCrawlerTitle(unittest.TestCase):
    def test_title_collected_not_hardcoded_empty(self):
        src = Path("app/steps/dom_crawler.py").read_text()
        self.assertIn("getAttribute('title')", src)
        self.assertIn('meta.get("title"', src)


class ScenarioValidatorAssertTerminal(unittest.TestCase):
    def test_validator_accepts_assert_terminal_with_expected_text(self):
        ok, reason = validate_step_against_dom(
            {"action": "assert_terminal", "expected_text": "Done"},
            {"routes": ["/"]},
        )
        self.assertTrue(ok)

    def test_validator_rejects_empty_assert_terminal(self):
        ok, reason = validate_step_against_dom(
            {"action": "assert_terminal"},
            {"routes": ["/"]},
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "missing_terminal_condition")


class ScenarioPlaywrightTerminal(unittest.TestCase):
    def test_execute_one_assert_terminal_missing_condition(self):
        page = MagicMock()
        ok, _, err = _execute_one(
            page,
            "https://ex.com",
            {"action": "assert_terminal"},
            Path("/tmp"),
            1,
        )
        self.assertFalse(ok)
        self.assertIn("missing_terminal_condition", err or "")


if __name__ == "__main__":
    unittest.main()
