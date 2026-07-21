# Phased Fix Plan — shipvideo-engine

| Field | Value |
|-------|--------|
| **Date** | 2026-07-21 |
| **Baseline branch** | `fix/issues-24-38-accuracy-reliability` ([PR #39](https://github.com/sourabhligade/shipvideo-engine/pull/39)) |
| **Unit tests on baseline** | 131 passed |
| **Product goal** | MVP **video accuracy** — real UI path, correct captions, aligned audio/video, no false-green demos |

---

## How to use this document

1. **Do phases in order** unless marked parallel-safe.
2. Each phase = **one PR** (small, reviewable, shippable).
3. Every phase has: problem → files → tasks → tests → success criteria → out of scope.
4. Update the **Phase status table** when a PR merges.
5. Re-run the **validation commands** at the bottom after each merge.

---

## Phase status table

| Phase | Name | Priority | Status | Depends on | Est. |
|-------|------|----------|--------|------------|------|
| **0** | Land PR #39 on `main` | Blocker | READY (merge branch) | — | 0.5d |
| **1** | Product A/V duration contract | P0 | DONE (branch) | 0 | 2d |
| **2** | Generation fail-closed | P0 | DONE (branch) | 0 | 1.5d |
| **3** | Honest sendable / `video_usable` | P0 | DONE (branch) | 0 (2 recommended) | 1.5d |
| **4** | Ingress dedupe + job deadlines | P1 | DONE (branch) | 0 | 1d |
| **5** | Capture proof parity (AB ↔ PW) | P1 | DONE (branch) | 3 | 1.5d |
| **6** | Config hygiene | P2 | DONE (branch) | 0 | 0.5d |
| **7** | Automated pipeline audit harness | P1 | DONE (branch) | 1, 2 | 2d |
| **8** | PR full-path fixture E2E | P2 | DONE (branch) | 7 | 3d+ |

**Legend:** `OPEN` | `IN PROGRESS` | `DONE` | `BLOCKED`

---

## Architecture context (two pipelines)

```text
Pipeline A — PR demo
  Webhook → Preview ready → Diff → Trigger → Contract/Routes → DOM crawl
    → Generate → Normalize → Capture ⇄ Repair → Approval → Render → Upload → Comment

Pipeline B — Product (link → video)
  create_job → capture_journey → TTS/audio → slideshow → mux → job done
```

Accuracy work must not fix only A or only B without noting cross-impact.

---

## Baseline already fixed on PR #39 (do not re-implement)

Merge these via **Phase 0** only.

| Issue | Fix summary | Primary files |
|-------|-------------|---------------|
| #24 | `routes` authority = successful crawl snapshots | `dom_crawler.py` |
| #25 | Playwright validates + runs `assert_terminal` | `step_runner.py`, validator |
| #26 | Webhook returns on `skipped` | `webhook.py` |
| #27 | `comment_triggered` unlocks on-demand | `trigger.py`, `webhook.py`, `pipeline.py` |
| #28 | Script LLM `record_spend` + empty script error | `script_generator.py` |
| #29 | AB terminal default fail-closed | `step_runner.py` |
| #30 | Runtime goto uses generation `real_routes` | `selector_validator.py`, runner |
| #31 | Input merge keys include testid/aria/id | `dom_crawler.py` |
| #32 | Empty re-anchor hard-fails | `step_runner.py` |
| #33 | Product SRT cues follow speech segments | `audio_timing.py` / product path |
| #34 | Normalize keeps stable selectors + label | `step_normalizer.py` |
| #35 | Skip deleted/removed pages for routes | `step_normalizer.py` |
| #36 | Live multi-match `count > 1` rejected | `selector_validator.py` |
| #37 | `MAX_PATCH_CHARS = 4000` | `pr_extraction.py` |
| #38 | FFmpeg timeouts | `render.py`, product video/audio |

---

# Phase 0 — Land PR #39 on `main`

### Goal

Put accuracy fixes on `main` so later phases build on one source of truth.

### Problem

`main` still lacks #24–#38. Parallel branches (e.g. PR #23) overlap and will conflict or double-fix.

### Tasks

1. Code-review and merge [PR #39](https://github.com/sourabhligade/shipvideo-engine/pull/39).
2. Close or supersede [PR #23](https://github.com/sourabhligade/shipvideo-engine/pull/23) (issues #18–#22 already closed; logic largely covered by #39).
3. On `main` after merge:
   ```bash
   git checkout main && git pull
   .venv/bin/python -m pytest -q
   ```
4. Tag: `accuracy-baseline-24-38`.

### Success criteria

- [ ] `main` contains `MAX_PATCH_CHARS = 4000`
- [ ] `main` contains `comment_triggered` in `evaluate_trigger`
- [ ] `pytest -q` green on `main`
- [ ] No competing open PR re-fixing the same contracts

### Out of scope

Any new feature work.

---

# Phase 1 — Product A/V duration contract

### Goal

Product videos end with **aligned audio and video**. No mid-narration cut; no huge silent tail without policy.

### Priority

**P0 accuracy** (Pipeline B)

### Evidence

Live audit (`playwright.dev`, 3 steps): silent ~29s, wav ~15s, final mux ~19s via `-shortest`. Captions can be fixed while A/V still feels wrong.

### Problem (current code)

| Location | Behavior |
|----------|----------|
| `app/product/video.py` ~L322 | Mux flag `-shortest` truncates longer stream |
| Slideshow builder | Frame holds not forced to speech segment lengths |
| Job “ok” | Can succeed with mismatched durations |

### Files to change

| File | Change |
|------|--------|
| `app/product/video.py` | Duration contract; remove unsafe `-shortest`; pad/trim deliberately |
| `app/product/audio_timing.py` | Expose per-step hold seconds from speech segments |
| `app/product/journey.py` | Carry duration targets on steps if needed |
| `test_product_av_contract.py` | **Create** — unit tests for hold math + ffmpeg argv |

### Implementation tasks

1. **Write the contract (code comment + test):**
   - Let `audio_dur` = narration length.
   - Let `holds[i]` = max(min_hold, speech_segment[i].duration) (or equal split if no segments).
   - `silent_dur = sum(holds)` must be `>= audio_dur - 50ms`.
   - Final mux duration ≈ `max(silent_dur, audio_dur)` within 250ms.
2. Build slideshow with those holds (do not use a flat 9–10s per frame when speech is shorter/longer).
3. Mux strategy:
   - If `|silent_dur - audio_dur| < 50ms`: copy mux, no `-shortest` required.
   - If video longer: pad audio with silence (`apad`) **or** accept trailing silent video (document choice — prefer pad audio only if product wants continuous speech end).
   - If audio longer: extend last frame hold (preferred) rather than cutting audio.
   - **Never** use `-shortest` when it would cut narration.
4. Fail job if mux returns non-zero (already improved); do not copy silent-only when audio was required.
5. Log `holds`, `audio_dur`, `final_dur` into job JSON.

### Tests

```text
test_holds_cover_speech_segments
test_mux_argv_has_no_shortest_when_durations_differ
test_final_duration_within_tolerance (mock or ffprobe fixture)
```

### Success criteria

- [ ] Re-run:
  ```bash
  .venv/bin/python -c "
  from pathlib import Path
  from app.product.pipeline import run_link_to_video
  print(run_link_to_video('https://playwright.dev', Path('/tmp/sv-p1'), job_id='p1', max_steps=3, use_azure_subtitles=False))
  "
  ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 /tmp/sv-p1/journey_p1.mp4
  ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 /tmp/sv-p1/journey_p1.wav
  ```
- [ ] `|video_dur - audio_dur| < 0.25s` OR last-frame pad policy documented and met
- [ ] SRT cues still non-overlapping (regression check for #33)
- [ ] Unit tests green

### Out of scope

Azure subtitle wording quality; PR-path render ffmpeg (timeouts already Phase 0).

---

# Phase 2 — Generation fail-closed

### Goal

Never ship a **screenshot-only fake demo** when generation fails for a real feature PR.

### Priority

**P0 accuracy** (Pipeline A)

### Problem (current code)

`app/steps/step_generation.py`:

```text
FALLBACK_STEPS = [{"action": "screenshot"}]
```

Returned at multiple sites (~1019, 1101, 1237, 1281, 1285, 1441). Downstream still captures/renders → looks successful, zero feature accuracy.

### Files to change

| File | Change |
|------|--------|
| `app/steps/step_generation.py` | Split hard-fail vs allowed general_demo soft path |
| `app/steps/pipeline.py` | Propagate generation failure; skip capture when hard-fail |
| `app/webhook.py` | Error comment on hard-fail (no misleading video) |
| `app/steps/errors.py` | Optional `GenerationCollapsedError` |
| `test_generation_fail_closed.py` | **Create** |

### Implementation tasks

1. Define outcomes:
   | Outcome | When | Result |
   |---------|------|--------|
   | **Hard fail** | LLM error, empty plan, validation wiped steps, contract required but unmet | `ok=False` / error; **no** video |
   | **Soft allow** | `general_demo=True` and no UI feature files | Single homepage screenshot OK |
2. Replace unrestricted `FALLBACK_STEPS` returns:
   - Hard fail → empty steps + error reason string.
   - Soft allow → explicit `FALLBACK_STEPS` only if `general_demo`.
3. Webhook: if generation hard-fail, `comment_on_pr` with reason; do not call render success path.
4. Metrics: `generation_hard_fail`, `generation_soft_fallback`.
5. Keep logging at each former fallback site with the reason code.

### Tests

```text
test_llm_error_no_screenshot_fallback_when_ui_files
test_general_demo_allows_screenshot_only
test_empty_validated_steps_hard_fail
test_webhook_posts_error_not_video_on_hard_fail (mock)
```

### Success criteria

- [ ] Feature PR path never returns only `[{action: screenshot}]` after generation error
- [ ] User-facing comment explains failure
- [ ] Unit tests lock both modes

### Out of scope

Improving LLM prompts (separate); repair-loop quality (already partially fixed).

---

# Phase 3 — Honest sendable / `video_usable`

### Goal

Green metrics and PR comments only when the video is **actually usable as a demo**.

### Priority

**P0 accuracy / ops**

### Problem (current code)

`app/steps/pipeline.py` ~L231:

```python
run_metrics.video_usable = bool(video_path and video_path.exists() and video_path.stat().st_size > 0)
```

Any non-empty file counts. Terminal failures, zero clicks, re-anchor aborts can still look “usable”.

### Files to change

| File | Change |
|------|--------|
| `app/steps/metrics.py` | Add/clarify `sendable`, proof fields |
| `app/steps/pipeline.py` | Compute sendable; set `video_usable = sendable` |
| Capture summary producers | Emit proof fields consistently |
| `app/github_comment.py` | Branch copy on sendable vs not |
| `test_sendable_gate.py` | **Create** |

### Sendable definition (implement exactly)

```text
sendable =
  video_file_exists
  AND size > 0
  AND duration_sec >= MIN_DURATION (e.g. 2.0)
  AND failure_reason not in HARD_FAIL_REASONS
  AND (
        general_demo
        OR (clicks_succeeded >= 1 OR gotos_succeeded >= 1)
      )
  AND (if plan had assert_terminal → terminal_passed)
```

Tune constants in one place (`app/steps/metrics.py` or config).

### Implementation tasks

1. Implement `compute_sendable(capture_summary, video_path, plan) -> tuple[bool, dict]`.
2. Wire into pipeline metrics + approval.
3. PR comment:
   - sendable → video link + short summary
   - not sendable → explicit “demo not publishable” + reasons
4. JSON metrics always include reason breakdown.

### Tests

```text
test_nonzero_file_alone_not_sendable
test_terminal_fail_not_sendable
test_general_demo_screenshot_can_be_sendable
test_hard_fail_reason_blocks_sendable
```

### Success criteria

- [ ] `video_usable` false for terminal-fail captures with a file present
- [ ] Comment text differs for sendable vs not
- [ ] Tests green

### Out of scope

Changing FFmpeg encode quality settings.

---

# Phase 4 — Ingress dedupe + job deadlines

### Goal

Failed runs can retry; jobs cannot hang forever.

### Priority

**P1 reliability**

### Problem

| Area | Risk |
|------|------|
| `record_run` | On baseline branch, called **after** successful `run_pipeline` (~L407) — verify on `main` after Phase 0; keep this invariant |
| Product jobs | `threading.Thread(..., daemon=True)` in `app/product/jobs.py` with no wall-clock deadline |
| Preview wait | Long waits need clear errors (GET-first already on baseline) |

### Files to change

| File | Change |
|------|--------|
| `app/webhook.py` | Guard: never `record_run` on failure paths; add tests |
| `app/llm_guards.py` | Optional split: attempt vs success records |
| `app/product/jobs.py` | Deadline, status `failed` on timeout, stale recovery |
| `test_ingress_dedupe.py` / `test_job_deadline.py` | **Create** |

### Implementation tasks

1. **Invariant test:** simulate failure before video → second run with same commit **not** blocked by `check_already_ran`.
2. Product job:
   - `JOB_MAX_SECONDS` (config, default 900).
   - Watchdog sets `status=failed`, `error=job_deadline_exceeded`.
3. Document status machine: `queued → running → done|failed`.
4. Preview timeout message includes URL + seconds waited.

### Success criteria

- [ ] Failed webhook run is retriable for same SHA
- [ ] Product job exceeds deadline → terminal failed status within deadline + grace
- [ ] Tests green

### Out of scope

Distributed job queue (Redis, etc.).

---

# Phase 5 — Capture proof parity (AB ↔ Playwright)

### Goal

Approval/metrics use one schema regardless of capture engine.

### Priority

**P1 accuracy**

### Depends on

Phase 3 (sendable fields defined).

### Problem

Sendable/approval may read fields one runner omits → false negatives/positives when switching engines.

### Files to change

| File | Change |
|------|--------|
| `app/execution/step_runner.py` | Normalize both result shapes |
| `app/steps/step_execution.py` | Adapter → `CaptureProof` |
| `app/steps/preflight.py` | Consume shared fields only |
| `test_capture_proof_schema.py` | **Create** |

### Shared `CaptureProof` fields (minimum)

```text
steps_planned: int
steps_succeeded: int
steps_failed: int
clicks_succeeded: int
gotos_succeeded: int
terminal_passed: bool | null
validation_passed: bool | null
failure_reason: str | null
runner: "agent_browser" | "playwright"
```

### Implementation tasks

1. TypedDict or dataclass `CaptureProof`.
2. Both runners return it (wrapper OK).
3. `compute_sendable` accepts only `CaptureProof` + video path.
4. Tests construct AB-like and PW-like raw results → same proof keys.

### Success criteria

- [ ] No approval code path requires runner-specific optional keys without defaults
- [ ] Engine switch does not change sendable solely due to missing keys

### Out of scope

Rewriting agent-browser CLI.

---

# Phase 6 — Config hygiene

### Goal

No dead config; no dual sources of truth for triggers.

### Priority

**P2 cleanup** (parallel-safe after Phase 0)

### Items

| Item | Action |
|------|--------|
| `full_page_debug_screenshots` in `config_types.py` | Wire into capture **or delete** |
| Webhook smart prefilter | Confirm single path via `evaluate_trigger` (baseline comment says owned by analyze_pr — re-verify on main) |
| Deprecated FastAPI `on_event` | Already fixed on baseline; re-scan after merge |

### Files

| File | Change |
|------|--------|
| `app/config_types.py` | Keep or remove debug flag |
| `app/execution/step_runner.py` / capture | Use flag if kept |
| `app/webhook.py` | No duplicate smart filter logic |

### Success criteria

- [ ] `grep -r full_page_debug app/` → definition + use, or zero hits
- [ ] Trigger behavior only from `evaluate_trigger` for smart/on-demand

---

# Phase 7 — Automated pipeline audit harness

### Goal

One command proves accuracy contracts; CI-friendly.

### Priority

**P1** (guards Phases 1–3)

### Deliverables

1. **`scripts/audit_pipeline.py`**
   - Product jobs: 2 URLs (playwright.dev, example.com), Azure off
   - Stage probes: trigger, normalize, deleted routes, multi-match, terminal, re-anchor, MAX_PATCH
   - Writes:
     - `data/audit/probe_results.json`
     - `data/audit/product_job_*.json`
   - Exit code `1` if any **P0** probe fails
2. Optional GitHub Actions workflow `audit.yml` (manual + nightly).
3. Refresh `docs/PIPELINE_AUDIT_EVIDENCE.md` from latest run header (date, branch, pass/fail table).

### P0 probe matrix

| Probe | Expected |
|-------|----------|
| on-demand, no comment | `should_run=false` |
| on-demand + `comment_triggered` | `should_run=true` |
| normalize keeps testid | selector present |
| deleted page in diff | route **not** seeded |
| multi-match count=3 | validation fail |
| empty assert_terminal | not success |
| empty re-anchor | hard fail |
| product multi-step SRT | no full-span cue from 0 overlapping all |
| product A/V (after Phase 1) | duration contract |

### Success criteria

- [ ] `python scripts/audit_pipeline.py` alone is enough for a regression audit
- [ ] P0 failure → non-zero exit

### Out of scope

Full customer PR against private previews (Phase 8).

---

# Phase 8 — PR full-path fixture E2E (stretch)

### Goal

End-to-end PR demo against a **known fixture app** (not customer prod).

### Priority

**P2**

### Approach

1. Minimal static/Next fixture with stable `data-testid`s and 2 routes.
2. Scripted “diff” or local analyze path with staging URL = fixture.
3. Assert: clicks hit testids, terminal URL, `sendable=true`, video duration > min.

### Success criteria

- [ ] Documented command in README/docs
- [ ] Fails CI if fixture demo not sendable

---

## Dependency graph

```text
Phase 0 ──merge──► main
   │
   ├──────────────► Phase 6 (hygiene, anytime)
   │
   ├─► Phase 1 (A/V) ──────────────┐
   ├─► Phase 2 (fail-closed) ──────┼─► Phase 7 (audit harness)
   │                               │
   └─► Phase 3 (sendable) ─► Phase 5 (proof parity)
   │
   └─► Phase 4 (dedupe / deadlines)

Phase 7 ─► Phase 8 (fixture E2E)
```

**Parallel after Phase 0:** 1 ∥ 2 ∥ 4 ∥ 6; then 3; then 5; harness 7 after 1+2.

---

## Residual bug index (track to closure)

| ID | Residual issue | Phase | Severity |
|----|----------------|-------|----------|
| R1 | Mux `-shortest` cuts/desyncs A/V | 1 | P0 |
| R2 | Frame holds not tied to speech | 1 | P0 |
| R3 | `FALLBACK_STEPS` silent screenshot demos | 2 | P0 |
| R4 | `video_usable` size-only | 3 | P0 |
| R5 | False-green PR comments | 3 | P0 |
| R6 | Dedupe blocks retry if mis-ordered | 4 | P1 |
| R7 | Product job no wall-clock timeout | 4 | P1 |
| R8 | AB vs PW proof schema drift | 5 | P1 |
| R9 | `full_page_debug_screenshots` dead config | 6 | P2 |
| R10 | No one-command audit | 7 | P1 |
| R11 | No fixture PR E2E | 8 | P2 |

---

## Per-PR checklist (every phase)

- [ ] Touches only phase files (+ tests)
- [ ] No drive-by refactors
- [ ] Unit tests for new contract
- [ ] `pytest -q` green
- [ ] PR body states accuracy impact in one paragraph
- [ ] Update this doc’s status table to DONE after merge

---

## Global validation commands

```bash
# Full unit suite
.venv/bin/python -m pytest -q

# Product smoke (no Azure)
.venv/bin/python -c "
from pathlib import Path
from app.product.pipeline import run_link_to_video
r = run_link_to_video(
    'https://playwright.dev',
    Path('/tmp/sv-audit/job'),
    job_id='audit',
    max_steps=3,
    use_azure_subtitles=False,
)
print(r)
"

# Durations + captions
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 \
  /tmp/sv-audit/job/journey_audit.mp4
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 \
  /tmp/sv-audit/job/journey_audit.wav 2>/dev/null || true
cat /tmp/sv-audit/job/journey_audit.srt
```

After Phase 7:

```bash
.venv/bin/python scripts/audit_pipeline.py
```

---

## Out of scope for this plan

- Marketing site redesign  
- New TTS / LLM providers  
- Multi-tenant billing beyond existing budget  
- Agent-browser CLI rewrite unrelated to proof/terminal  
- Performance optimization unrelated to correctness  

---

## Document history

| Date | Author | Change |
|------|--------|--------|
| 2026-07-21 | engineering | Initial detailed phased plan from baseline branch residual gaps + prior pipeline audit |
| 2026-07-21 | engineering | Phase 1+2 implemented on branch: A/V holds+mux contract; generation hard-fail |
| 2026-07-21 | engineering | Phase 3 implemented: compute_sendable; video_usable != size-only; PR comment split |
| 2026-07-21 | engineering | Phase 4–5: job deadlines + stale recovery; CaptureProof AB/PW parity |
| 2026-07-21 | engineering | Phase 6–8: full_page_debug wired; audit_pipeline.py; fixture_e2e + demo_app |
