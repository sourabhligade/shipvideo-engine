# Accuracy-first improvement plan — shipvideo-engine

## Goal

Make Pipeline A (PR webhook → proof-backed capture → R2 → PR comment) produce videos a PM would send to a customer without embarrassment. Build inside existing `app/`. Keep every existing accuracy test. Do not re-implement PR #39 issues #24–#38 or Phases 0–8 of `docs/PHASED_FIX_PLAN.md` (already DONE). Do not recreate a mock engine. Do not bind port 8080.

The only metric: would a PM send this video.

## What is already true (do not redo)

Live code already has: HMAC webhook, preview wait, `analyze_pr` → manifest-if-confident else LLM plan, generation fail-closed for feature PRs, screenshot-only abort unless changed-testid recovery, Agent Browser stepwise default, rising-edge click validation, distinct outcomes (`success` / `wrong_click` / `click_failed` / `stale_ref` / `stale_ref_unrecovered` / `unvalidated`), one stale-ref retry, one replan, one flow restart, `MAX_STEPS=10`, render-approval + `compute_sendable` (never size-only), `video_usable = sendable`, `record_run` only on sendable, CaptureProof AB/PW parity, product A/V never `-shortest`, job deadline 900s, audit harness, fixture E2E.

Those are the floor. The remaining work is the accuracy ceiling: **the system still guesses the flow when the manifest does not win, and it still cannot prove causal setup steps (select amount before CTA).**

## North-star architecture

One source of truth for *what* to record. Deterministic proof for *how* to record it.

```
explicit intent (/glimpse --route) or shipvideodemo.json flow
        ↓
structured steps with per-click proof + terminal
        ↓
Agent Browser: resolve → preclick → click → wait-for-proof → rising-edge
        ↓
approved frames only → render → sendable gate → comment
```

LLM stays in three bounded jobs: (1) extract candidate facts from a diff when no manifest matches, (2) rank observed browser candidates during recovery, (3) one remaining-step replan toward the same terminal. LLM never invents the journey as the production path and never invents labels that are not in the live snapshot.

## The load-bearing remaining miss

**Recharge: select amount, then CTA.**

Manifest flow 4 already encodes:

```
start / → Settings → ₹2000 → Recharge Now → Proceed
terminal: URL stays /settings AND "Recharge Successful"
```

What still fails on the LLM/diff path:

1. `_extract_targets` pulls visible CTA strings (`Recharge Now`, `Proceed Recharge`) from added JSX. It does not emit the amount chip as a required earlier step unless that chip's label is also on an added interactive line.
2. `_extract_interaction_hints` writes English phrases (`interaction_hint_high:select amount`) from regex. Preflight then looks for that **phrase** inside earlier **click labels**. `"select amount"` does not match `"₹2000"`. So preflight either false-fails a correct ₹2000 plan, or the planner never inserts the chip and the runner clicks a CTA that is not on screen yet.
3. Runtime recovery (`_recover_ab_prerequisite_steps`) asks the LLM to invent a prerequisite from the snapshot. Cap is 1. If the chip is named `₹2000` and the blocked intent is `Proceed Recharge`, recovery can work — tests lock inserting `₹2000` — but it is LLM-gated, not deterministic.
4. Manifest last label is `"Proceed"`; AB tests use intent `"Proceed Recharge"`. Ambiguous or no-match if both exist.
5. Manifest recharge `start` is `/` (home + Settings click) rather than `/settings`. Fine if Settings is unique; wasteful if the PR is settings-only.

Preflight today is **label coverage**, not **causal completeness**. That is the accuracy wall.

## Workstreams (ordered)

Workstreams 0–4 are Pipeline A accuracy. Workstream 5 is measurement. Workstream 6 is hygiene that prevents silent wrongness. Workstream 7 is Pipeline B and SaaS, explicitly later.

---

### Workstream 0 — Close live correctness bugs (1 PR, 0.5–1 day)

These are small and they make later work lie.

**0a. `find_element` does not exist.** `_assert_ab_terminal_condition` calls `cli.find_element(selector)` after testid wait fails. `AgentBrowserCLI` has `find_testid_ref` / `find_role_ref` / `find_label_ref` / `find_ref` only. The `except Exception` swallows `AttributeError`, then falls through to snapshot-substring matching. Terminal can pass on incidental text.

- File: `app/browser/agent_browser_cli.py` add `find_element(selector)` as a thin `find` wrapper, **or** delete the call and use `find_testid_ref` + `find_ref` only.
- File: `app/execution/step_runner.py` `_assert_ab_terminal_condition` — stop snapshot-substring as success for `element_present`. Fail closed if wait + find_* miss.
- Test: extend `test_step_runner_phase1.py` so a missing `find_element` cannot auto-pass.

**0b. `app/steps/errors.py` uses `Any` without importing it.** Latent `NameError` on `ContractIntegrityError` construction. Add `from typing import Any`.

**0c. Comment command split.** `webhook.py` default `/glimpse`, `trigger.py` default `/demo`, config `/glimpse`. One default: `/glimpse`, read only from config, webhook and trigger share it. Tests that inject `/demo` keep injecting; add one test that config `/glimpse` is what webhook parses.

**0d. Preview timeout vs poll.** `project_config.json` `preview_ready_timeout_seconds: 3` and `preview_ready_poll_interval_seconds: 15` means at most one GET then fail. For the demo Vercel app this may be intentional (already live). Raise timeout to ≥ 90s (or set poll ≤ timeout) so a cold preview can become ready. Keep GET-then-HEAD.

**0e. Webhook secret fallback `"secret"`.** Require `GITHUB_WEBHOOK_SECRET`. Invalid if missing. Tests mock env.

**0f. Product bind.** `run_product.sh` `PORT:-8080` and footer copy. Default to `8001`. 8080 stays ITT.

Do not rename `skipComment` in this PR (tests lock the source string). Document in a one-line comment: `true` means post a skip comment.

Success: `pytest -q` green; terminal no longer depends on a missing method.

---

### Workstream 1 — First-class setup steps (the recharge fix)

This is the accuracy PR. One source of truth for “something must be selected before the CTA exists.”

#### 1.1 Contract model

Extend `DemoContract` / extraction — do **not** invent a parallel schema.

Add structured `setup_steps: List[TargetRef]` (or a `kind` on `TargetRef`: `cta | option | toggle | tab | amount`). Minimum viable kinds:

| kind | meaning | example |
| amount | must click a visible amount/option chip before CTA | `₹2000` |
| option | choose plan/tier | `Pro` |
| tab | switch surface | `Billing` |
| toggle | enable a setting | `Auto-renew` |

Keep `targets` as the CTAs that must appear in the plan. `setup_steps` must appear **before** the first CTA they enable.

`extraction_notes` regex hints remain as a fallback detector, but they stop being the preflight matcher.

Files: `app/steps/demo_contract.py`, `app/steps/contract_extraction.py`.

Extraction rules (deterministic, added lines only, same as today):

- Amount: `₹` / `$` / numeric chip in button/role=radio/option/testid containing `amount`.
- Keep existing high-signal regex, but emit a `TargetRef(label=actual UI string)` when the line also has a quoted label or testid. Phrase-only (`"select amount"` with no chip) stays a hint, confidence medium, and **cannot** alone drive a sendable multi-step plan.

Confidence: `high` still requires start_route + targets + terminal. If high-signal setup is detected and no setup `TargetRef` was grounded in a real label/testid, cap confidence at `medium` so `_should_fallback_to_guarded_screenshot` / discovery hunt runs instead of a CTA-only plan.

#### 1.2 Preflight becomes causal

`preflight_gate` today:

```
hint "select amount" must appear as a substring of an earlier click label
```

Change to:

```
every setup TargetRef must be a click (or select/check) **before** the first CTA target
every CTA target must still be present
every click still has url_match | text_present | element_present
last click before terminal still has proof
```

Matching: exact label, testid selector, or (len>4) substring either way — against setup labels, **not** against the English hint phrase.

If only an ungrounded hint remains (no TargetRef): **warn**, do not block a plan that already contains a likely chip (`₹`, currency, role=radio/option). Blocking on the phrase `"select amount"` vs `"₹2000"` is the current false-fail.

Files: `app/steps/preflight.py`. Tests already in `test_step_runner_phase1.py` (`test_preflight_rejects_missing_prerequisite_step_from_contract_hint`) — **update the fixture** so the required setup is a `TargetRef(label="₹2000")` (or keep the hint test as warn-only and add the TargetRef test as the hard fail). Do not delete the spirit of the test.

#### 1.3 Planner synthesizes setup clicks

`_synthesize_click_steps` currently: goto + extraction `click_labels` + terminal inject. It never inserts setup chips.

Change: for each setup TargetRef, insert a click **before** CTA labels, with proof `element_present` = next CTA label (same pattern `flow_to_steps` already uses).

LLM planning prompt already says “include an explicit earlier step” for hints. That is insufficient. After LLM returns, a **deterministic pass** walks setup_steps and inserts any missing chip click before the first CTA. Then preflight. Then one regenerate if still missing. Then hard-fail (already the fail-closed path).

Files: `app/steps/step_generation.py` (`_synthesize_click_steps`, post-LLM repair, `_inject_sequential_click_validations`).

Sequential proof for recharge should be:

```
click Settings        → element_present ₹2000
click ₹2000           → element_present Recharge Now
click Recharge Now    → element_present Proceed   (or Proceed Recharge)
click Proceed         → text_present Recharge Successful
assert_terminal       → text_present Recharge Successful
```

This is already what `flow_to_steps` does for manifest string steps. LLM path must emit the same shape.

#### 1.4 Manifest hardening

`shipvideodemo.json` flow 4:

- Convert string steps to structured `{action, label, success_condition}` like creator-journey / trial flows.
- Align last CTA with the live demo label. If the app shows `Proceed Recharge`, store that. If it shows `Proceed`, store that. One string.
- Keep start `/` if the demo home has Settings; add `start_route` alias `/settings` in scoring so a settings-only PR still selects this flow (`changed_start_route` already +6).
- Optional: `success_condition` on ₹2000 is `element_present: Recharge Now` (CTA appears after selection). That is the causal proof.

`get_manifest_flow` already runs first in `analyze_pr`. Do not weaken the `best_score >= 6` and lead-by-2 rule. Add a test that a PR title/diff touching settings + recharge tokens selects flow 4 and **does not** call `generate_steps_from_diff`.

Files: `shipvideodemo.json`, `app/manifest/runner.py` (only if scoring needs a recharge/settings token bonus — prefer tests over score inflation).

#### 1.5 Deterministic AB recovery for amount chips

Today recovery is `regenerate_with_feedback` (LLM). Add a **first** deterministic pass in `_recover_ab_prerequisite_steps`:

If blocked intent looks like a CTA (`recharge`, `proceed`, `submit`, `pay`, `confirm`) and snapshot has an unmatched option/radio/button whose name matches `₹|$|€|\d{2,}` or role `radio|option|tab`, insert that click before the blocked step. No LLM. Mark `_ab_recovery_attempted`. Proof: `element_present` of blocked intent.

Keep LLM regen as the second try, still `max_attempts=1` total replans per run.

Files: `app/execution/step_runner.py`. Tests: extend `test_recover_ab_prerequisite_steps_inserts_recovery_before_retry` with a snapshot containing `₹2000` and **no LLM mock call**.

Success for workstream 1: a recharge-shaped plan is either manifest-selected or synthesized with ₹2000 before Recharge Now; preflight fails CTA-only plans; runner inserts the chip from snapshot without LLM when the chip is visible; sendable video shows amount selected then CTA then success modal.

---

### Workstream 2 — Agent Browser as evidence engine

The wrapper is still click/screenshot. Product law in `mdfiles/BROAGENTBOWSER.MD`: before snapshot → click → wait for explicit proof → after snapshot → snapshot diff → diagnostics on failure. Do not add Stagehand. Do not use `agent-browser chat`.

Add to `AgentBrowserCLI` (thin subprocess wrappers, same style as existing methods):

| method | CLI | use |
| `wait_for_function(js)` | `wait --fn` | proof `document.body.innerText.includes("Recharge Successful")` |
| `get_box(ref)` | `get box` | geometry fallback instead of snapshot order |
| `diff_snapshot()` | `diff snapshot` | store on step result after every click |
| `annotated_screenshot(path)` | `screenshot --annotate` | failure bundle |
| `fill(ref, text)` / `select` / `check` | native | setup that is not a click |

Wire into `_run_ab_click_attempt` and `_assert_ab_terminal_condition`:

- Proof wait uses `wait --text` / `wait --url` / `wait --fn` **before** snapshot substring.
- Snapshot substring remains diagnostic, not success.
- On proof failure: annotated screenshot + snapshot diff + existing console/errors/network (already collected).
- Position fallback: if `select_ref` is no_match, use `get_box` + role, not list order.

`fill`/`select`/`check` matter once setup kinds include toggles; for recharge, click on the chip is enough.

Files: `app/browser/agent_browser_cli.py`, `app/browser/agent_browser_types.py`, `app/execution/step_runner.py`.

Tests: unit the wrapper command argv (mock subprocess); terminal fail-closed when wait --fn fails; failure diagnostics include annotate/diff keys.

Out of scope here: batch mode, traces, HAR, dual-mode promotion. Experiment logger stays; `candidate_count` is still 0 — fill it from `select_ref` candidates while touching the click path (small, same PR).

---

### Workstream 3 — Proof strictness leftovers

Most of SENDABLE_VIDEO_PLAN Phases 2 and 5 are live. Remaining:

**3a. Terminal snapshot-text is too weak.** After 0a/2, `element_present` / `text_present` succeed only via wait/find. `_terminal_match_in_snapshot` becomes last-resort **fail evidence**, not pass.

**3b. Failed terminal must not keep the last click frames if that click did not validate.** `_should_keep_click_screenshots` already keeps only `outcome=="success"`. Confirm `_approved_frame_paths` drops terminal-fail tails. Add a test if missing.

**3c. `unvalidated` must never be `ok`.** Live runner already rewrites unrecovered unvalidated to `wrong_click`. Keep that. Do not restore focus.md’s “log unvalidated and continue.”

**3d. Discovery / screenshot-only.** `run_pipeline` already aborts screenshot-only unless `changed_testids`. Keep. Add an explicit `failure_reason=discovery_placeholder` so comments are honest.

**3e. script_first.** It always falls through and is not proof-backed. Leave the code; do not spend accuracy budget on it. Default stays `VIDEO_PIPELINE=stepwise`.

Files: `app/execution/step_runner.py`, `app/steps/pipeline.py` (reason string only).

---

### Workstream 4 — Intent as flow selector

`/glimpse --route /settings` already sets `start_route` and scores the manifest +10. Missing: free-text intent.

Support:

```
/glimpse recharge
/glimpse show the recharge flow
```

Map to manifest flow by the same `_score_flow` using comment body as `pr_title` tokens. If `best_score >= 6` and unique, use that flow and skip LLM. If ambiguous, comment “which flow?” with the top 3 names and **do not capture**. If none, existing LLM/discovery path.

Do **not** let the comment generate a new multi-step flow.

Files: `app/webhook.py` `_parse_glimpse_command` (keep remainder tokens as `intent_text`), `app/manifest/runner.py` (`pr_title` already tokenized — pass intent there), `app/steps/pipeline.py` `analyze_pr`.

Tests: comment ` /glimpse recharge` selects flow 4; ` /glimpse` with two equally scoring flows returns skipped + clarification (new path — today it would LLM).

This is how developer intent becomes a selector, which `mainproblems.md` requires.

---

### Workstream 5 — Repeatability (19/20)

Without this, we cannot know if 1–4 worked.

**5a. Replay the fixture** (`scripts/fixture_e2e.py` + `fixtures/demo_app`): 20 runs, save-settings → Saved. Exit 1 if < 19 pass. `BROWSER_BACKEND=playwright` is fine for the fixture (already). Add an AB mode run if agent-browser is installed.

**5b. Replay recharge against the live demo catalog** (`https://shipvideo-demo.vercel.app` or a local copy of the demo app). 20 runs of manifest flow 4. Track success_rate, wrong_click_count, unvalidated, proof failures, latency — the experiment_logger already has this shape. Wire `test_case_id` through `run_capture` (already supported).

**5c. Persist proven locators** only after 19/20: write back to a `locator_history` sidecar (testid/ref/role that worked), **not** by rewriting `shipvideodemo.json` automatically on a single success. Manual or gated update.

Files: `scripts/replay_flow.py` (new), `app/browser/experiment_logger.py` (consume, do not redesign), `scripts/fixture_e2e.py`.

Do not touch promotion_allowed defaults except as the logger already computes them.

Success: one command proves recharge + fixture at ≥19/20. That is the exit criterion for “execution is reliable.”

---

### Workstream 6 — Hygiene that is not accuracy (same week if cheap, else after 5)

- `cleanup_r2.py` shebang is garbage. Fix to `#!/usr/bin/env python3`.
- `errors.py` import (in WS0).
- `run_product.sh` port (in WS0).
- Per-run screenshot dirs: `render.py` always writes `app/screenshots/out.mp4`. Concurrent PRs clobber. Parameterize `output_path` with `run_id` from `new_run_metrics`. This is correctness under concurrency, not SaaS.
- Protect `/out.mp4` and `/budget-status` or delete them from the webhook app. mvp-roadmap is right; this is a 20-line PR.
- Doppler/env: keep `doppler.yaml`. Do not commit `.env`.

Skip: Postgres, GitHub App, Redis queue, signed URLs, Next.js dashboard. That is SaaS v1 (`mdfiles/mvp-roadmap.md`) and it does not change whether the video shows the right clicks.

---

### Workstream 7 — Pipeline B and narration (later)

Product URL path has no proof. Heuristic CTA crawl (`pick_next_targets`, `_CTA_RE`) will wander. A/V contract is already done. Do **not** spend MVP budget here until Pipeline A recharge is 19/20.

When we do: reuse AB stepwise + manifest-less “follow unique CTAs with rising-edge url_match” and fail closed if no unique next target. Keep TTS/mux as they are.

Narration quality, Azure subtitle wording, video styling: ignore.

---

## Suggested PR sequence

| PR | Title | Depends | Files |
| 1 | Fix terminal find_element + typing + bind/secret/comment defaults | — | `agent_browser_cli.py`, `step_runner.py`, `errors.py`, `webhook.py`, `trigger.py`, `run_product.sh`, `project_config.json`, tests |
| 2 | Structured setup TargetRefs + causal preflight | 1 | `demo_contract.py`, `contract_extraction.py`, `preflight.py`, `test_step_runner_phase1.py` |
| 3 | Synthesize setup clicks in generation; sequential proof | 2 | `step_generation.py`, tests |
| 4 | Manifest recharge structured + /glimpse intent selector | 2 | `shipvideodemo.json`, `manifest/runner.py`, `webhook.py`, `pipeline.py` |
| 5 | Deterministic amount-chip recovery in AB | 2 | `step_runner.py`, tests |
| 6 | AB wait --fn / diff / annotate / get_box; terminal fail-closed | 1 | `agent_browser_cli.py`, `step_runner.py` |
| 7 | Replay harness 20× fixture + recharge | 3,4,5 | `scripts/replay_flow.py`, fixture e2e |
| 8 | Per-run render output + lock down /out.mp4 | 1 | `render.py`, `pipeline.py`, `webhook.py` |

PRs 2–5 are the product. 6 makes failures diagnosable. 7 is the gate to call it done. 1 and 8 stop the engine lying to itself.

Parallel after PR 1: 2 then 3; 4 and 5 after 2; 6 anytime after 1; 7 last.

## Tests to add (do not weaken existing)

- Preflight: plan `[Recharge Now]` only → fail; plan `[₹2000, Recharge Now]` → pass even if notes still say `select amount`.
- Generation: extraction with setup ₹2000 + CTA → synthesized order chip then CTA, each with proof.
- Manifest: settings/recharge tokens → flow 4, `generate_steps_from_diff` not called.
- Recovery: snapshot has ₹2000, blocked `Recharge Now` → insert chip, zero LLM calls.
- Terminal: no `find_element` attribute, wait miss → `found=False`.
- Webhook: missing `GITHUB_WEBHOOK_SECRET` → reject.
- Replay: fixture 20 runs in CI (or documented local command).

Keep `test_phase2`, `test_phase3`, `test_phase4_5`, audit probes. `scripts/audit_pipeline.py` gets one new P0 probe: causal setup present.

## Explicit non-goals until 19/20 recharge

- New browser framework / FakeBrowser / Stagehand / `agent-browser chat`
- Dual-mode promotion, benchmark harness redesign
- SaaS auth, GitHub App, Postgres, Redis, signed R2
- Narration, TTS engines, video styling
- Making Pipeline B proof-backed
- Rewriting `step_generation.py` prompts as the primary fix (synthesis + preflight beat prompt tweaks)
- Re-implementing #24–#38

## Key decisions

1. **Manifest + structured setup beats better prompts.** The recharge miss is a missing step type, not a missing sentence in the planner.
2. **Preflight matches UI labels, not English hint phrases.** `"select amount"` vs `"₹2000"` is why coverage ≠ causality.
3. **Deterministic chip recovery before LLM regen.** Snapshot already has the amount. Asking the model is how we get wrong_click videos.
4. **Agent Browser stays the capture engine.** Extend the wrapper; do not replace it.
5. **Measurement is a workstream, not a later idea.** 19/20 on recharge is the definition of done for this plan.
6. **SaaS and product-URL accuracy wait.** They do not change sendable PR videos.

## How we will know it worked

Run 10 real PRs on the demo app (focus.md). Score each video `usable` | `usable with edits` | `unusable`. Recharge PRs must be `usable`. Fixture + recharge replay ≥ 19/20. Sendable comments only when amount selection is visible in the video before the CTA.
