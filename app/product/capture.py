from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from app.product.journey import (
    SHIPVIDEO_AUDIT_DWELL_MS,
    SHIPVIDEO_AUDIT_MAX_JOURNEY_STEPS,
    SHIPVIDEO_FOCUS_DWELL_MS,
    JourneyPlan,
    JourneyStep,
    narrate_step,
    page_fingerprint,
    pick_next_targets,
)

logger = logging.getLogger(__name__)


_COLLECT_CANDIDATES_JS = """
() => {
  const out = [];
  const push = (el, role) => {
    const text = (el.innerText || el.textContent || el.getAttribute('aria-label')
      || el.getAttribute('value') || el.getAttribute('placeholder') || '')
      .replace(/\\s+/g, ' ').trim().slice(0, 120);
    const href = el.href || el.getAttribute('href') || '';
    const testid = el.getAttribute('data-testid') || el.getAttribute('data-test-id') || '';
    const aria = el.getAttribute('aria-label') || el.getAttribute('title') || '';
    if (!text && !href && !testid && !aria) return;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return;
    // Prefer on-screen interactive elements
    if (r.bottom < 0 || r.right < 0 || r.top > (window.innerHeight || 800)
        || r.left > (window.innerWidth || 1280)) return;
    out.push({
      text, href, role,
      tag: (el.tagName || '').toLowerCase(),
      testid, aria,
      bbox: { x: r.x, y: r.y, w: r.width, h: r.height },
    });
  };
  document.querySelectorAll('a[href]').forEach(el => push(el, 'link'));
  document.querySelectorAll(
    'button, [role="button"], input[type="submit"], input[type="button"], [data-testid]'
  ).forEach(el => {
    const tag = (el.tagName || '').toLowerCase();
    const role = tag === 'a' ? 'link' : (el.getAttribute('role') || 'button');
    if (tag === 'a' && el.hasAttribute('href')) return; // already collected
    push(el, role === 'link' ? 'link' : 'button');
  });
  return out.slice(0, 80);
}
"""

_HIGHLIGHT_JS = """
(bbox) => {
  const id = '__shipvideo_focus_ring__';
  let el = document.getElementById(id);
  if (!el) {
    el = document.createElement('div');
    el.id = id;
    el.style.cssText = [
      'position:fixed', 'pointer-events:none', 'z-index:2147483646',
      'border:3px solid #409cff', 'border-radius:10px',
      'box-shadow:0 0 0 4px rgba(64,156,255,0.25), 0 0 24px rgba(64,156,255,0.55)',
      'background:rgba(64,156,255,0.08)', 'transition:all 120ms ease',
    ].join(';');
    document.documentElement.appendChild(el);
  }
  if (!bbox) { el.style.display = 'none'; return false; }
  el.style.display = 'block';
  el.style.left = Math.max(0, bbox.x - 4) + 'px';
  el.style.top = Math.max(0, bbox.y - 4) + 'px';
  el.style.width = Math.max(8, bbox.w + 8) + 'px';
  el.style.height = Math.max(8, bbox.h + 8) + 'px';
  return true;
}
"""

_CLEAR_HIGHLIGHT_JS = """
() => {
  const el = document.getElementById('__shipvideo_focus_ring__');
  if (el) el.remove();
}
"""

_BODY_SAMPLE_JS = """
() => {
  const root = document.querySelector('main') || document.querySelector('article') || document.body;
  const t = (root && root.innerText) || '';
  return t.replace(/\\s+/g, ' ').trim().slice(0, 500);
}
"""


async def _safe_title(page) -> str:
    try:
        return (await page.title() or "").strip()[:160]
    except Exception:
        return ""


async def _safe_url(page) -> str:
    try:
        return page.url or ""
    except Exception:
        return ""


async def _body_sample(page) -> str:
    try:
        return str(await page.evaluate(_BODY_SAMPLE_JS) or "")
    except Exception:
        return ""


async def _collect_candidates(page) -> List[Dict[str, Any]]:
    try:
        return await page.evaluate(_COLLECT_CANDIDATES_JS)
    except Exception as e:
        logger.debug(
            "_collect_candidates failed",
            extra={"operation": "collect_candidates", "error": f"{type(e).__name__}: {e}"},
        )
        return []


async def _fingerprint(page) -> str:
    url = await _safe_url(page)
    title = await _safe_title(page)
    body = await _body_sample(page)
    return page_fingerprint(url, title, body)


async def _inject_highlight(page, bbox: Optional[Dict[str, Any]]) -> bool:
    if not bbox:
        return False
    try:
        return bool(await page.evaluate(_HIGHLIGHT_JS, bbox))
    except Exception:
        return False


async def _clear_highlight(page) -> None:
    try:
        await page.evaluate(_CLEAR_HIGHLIGHT_JS)
    except Exception:
        pass


async def _click_by_text(page, text: str) -> bool:
    if not text:
        return False
    for role in ("button", "link"):
        try:
            loc = page.get_by_role(role, name=text, exact=False)
            if await loc.count() > 0:
                await loc.first.scroll_into_view_if_needed(timeout=2000)
                await loc.first.click(timeout=4000)
                return True
        except Exception:
            pass
    try:
        loc = page.get_by_text(text, exact=False)
        if await loc.count() > 0:
            await loc.first.scroll_into_view_if_needed(timeout=2000)
            await loc.first.click(timeout=4000)
            return True
    except Exception:
        pass
    return False


async def _click_by_testid(page, testid: str) -> bool:
    if not testid:
        return False
    try:
        loc = page.locator(f"[data-testid='{testid}'], [data-test-id='{testid}']")
        if await loc.count() > 0:
            await loc.first.scroll_into_view_if_needed(timeout=2000)
            await loc.first.click(timeout=4000)
            return True
    except Exception:
        return False
    return False


async def _click_by_bbox(page, bbox: Optional[Dict[str, float]]) -> bool:
    if not bbox:
        return False
    try:
        x = float(bbox["x"]) + float(bbox["w"]) / 2.0
        y = float(bbox["y"]) + float(bbox["h"]) / 2.0
        if x < 0 or y < 0:
            return False
        await page.mouse.click(x, y)
        return True
    except Exception:
        return False



async def _screenshot(page, path: Path) -> bool:
    try:
        await page.screenshot(path=str(path), full_page=False)
        return path.exists()
    except Exception as e:
        logger.debug(
            "screenshot failed",
            extra={"operation": "screenshot", "path": str(path), "error": f"{type(e).__name__}: {e}"},
        )
        return False


def _bbox_dict(raw: Any) -> Optional[Dict[str, float]]:
    if not isinstance(raw, dict):
        return None
    try:
        return {
            "x": float(raw.get("x", 0)),
            "y": float(raw.get("y", 0)),
            "w": float(raw.get("w", raw.get("width", 0))),
            "h": float(raw.get("h", raw.get("height", 0))),
        }
    except (TypeError, ValueError):
        return None


async def capture_journey(
    start_url: str,
    work_dir: Path,
    *,
    max_steps: int = SHIPVIDEO_AUDIT_MAX_JOURNEY_STEPS,
    viewport: tuple[int, int] = (1280, 720),
    headless: bool = True,
    capture_focus_frames: bool = True,
) -> JourneyPlan:
    from playwright.async_api import async_playwright

    headless = True
    work_dir = Path(work_dir)
    frames_dir = work_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    url = start_url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    plan = JourneyPlan(start_url=url)
    visited: set[str] = set()
    plan.headless = True  # type: ignore[attr-defined]
    step_i = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        print("[product.capture] playwright headless=True", flush=True)
        context = await browser.new_context(
            viewport={"width": viewport[0], "height": viewport[1]},
            ignore_https_errors=True,
        )
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(SHIPVIDEO_AUDIT_DWELL_MS)
        except Exception as e:
            plan.end_reason = f"failed_to_open: {type(e).__name__}: {e}"
            await browser.close()
            return plan

        while step_i < max_steps:
            current = await _safe_url(page)
            title = await _safe_title(page)
            visited.add(current.split("#")[0])

            shot_path = frames_dir / f"step_{step_i:03d}.png"
            await _screenshot(page, shot_path)

            pending_label = getattr(plan, "_pending_label", "") or ""
            pending_bbox = getattr(plan, "_pending_bbox", None)
            pending_proof = getattr(plan, "_pending_proof", "") or ""
            pending_action = "click" if pending_label else ("goto" if step_i == 0 else "capture")
            if pending_label:
                plan._pending_label = ""  # type: ignore[attr-defined]
                plan._pending_bbox = None  # type: ignore[attr-defined]
                plan._pending_proof = ""  # type: ignore[attr-defined]

            step = JourneyStep(
                index=step_i,
                action=pending_action if step_i > 0 else "goto",
                url=current,
                title=title or urlparse(current).path or current,
                label=pending_label,
                screenshot_path=str(shot_path) if shot_path.exists() else "",
                click_bbox=pending_bbox if isinstance(pending_bbox, dict) else None,
                proof_status=pending_proof,
                frame_role="result",
            )
            plan.steps.append(step)
            step_i += 1

            if step_i >= max_steps:
                plan.end_reached = True
                plan.end_reason = "max_steps"
                break

            candidates = await _collect_candidates(page)
            targets = pick_next_targets(
                candidates, current_url=current, visited=visited, limit=6
            )
            advanced = False
            for target in targets:
                text = str(target.get("text") or "").strip()
                testid = str(target.get("testid") or "").strip()
                resolved = str(target.get("resolved_url") or "").strip()
                bbox = _bbox_dict(target.get("bbox"))

                # Pre-click focus frame: highlight target then screenshot
                # Reserve one slot for the post-click result frame.
                if capture_focus_frames and step_i + 1 < max_steps:
                    await _inject_highlight(page, bbox)
                    await page.wait_for_timeout(SHIPVIDEO_FOCUS_DWELL_MS)
                    focus_path = frames_dir / f"step_{step_i:03d}_focus.png"
                    if await _screenshot(page, focus_path):
                        # Also burn a durable annotate ring for render resilience
                        try:
                            from app.render_effects.annotate import draw_click_highlight

                            ann_path = frames_dir / f"step_{step_i:03d}_focus_ann.png"
                            draw_click_highlight(
                                focus_path,
                                ann_path,
                                bbox=bbox,
                                label=text or testid,
                                step_index=step_i + 1,
                                total_steps=max_steps,
                                proof_badge="",
                            )
                            focus_shot = ann_path if ann_path.exists() else focus_path
                        except Exception:
                            focus_shot = focus_path

                        focus_step = JourneyStep(
                            index=step_i,
                            action="click",
                            url=current,
                            title=title or urlparse(current).path or current,
                            label=text or testid,
                            screenshot_path=str(focus_shot),
                            click_bbox=bbox,
                            proof_status="",
                            frame_role="focus",
                        )
                        plan.steps.append(focus_step)
                        step_i += 1
                    await _clear_highlight(page)

                before_fp = await _fingerprint(page)
                before_url = await _safe_url(page)
                # Clean pre-click shot (no highlight ring) for visual proof after click
                before_shot = None
                tmp_before = frames_dir / f"_before_{step_i:03d}.png"
                if await _screenshot(page, tmp_before):
                    before_shot = str(tmp_before)

                clicked = False
                if testid:
                    clicked = await _click_by_testid(page, testid)
                if not clicked and text:
                    clicked = await _click_by_text(page, text)
                if not clicked and bbox:
                    clicked = await _click_by_bbox(page, bbox)
                if not clicked and resolved:
                    try:
                        await page.goto(
                            resolved, wait_until="domcontentloaded", timeout=30000
                        )
                        clicked = True
                        text = text or resolved
                    except Exception:
                        clicked = False

                if not clicked:
                    # remove focus frame if click failed so video stays accurate
                    if plan.steps and plan.steps[-1].frame_role == "focus":
                        dead = plan.steps.pop()
                        step_i = max(step_i - 1, len(plan.steps))
                        try:
                            Path(dead.screenshot_path).unlink(missing_ok=True)
                        except Exception:
                            pass
                    if before_shot:
                        try:
                            Path(before_shot).unlink(missing_ok=True)
                        except Exception:
                            pass
                    continue

                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass
                await page.wait_for_timeout(SHIPVIDEO_AUDIT_DWELL_MS)

                after_fp = await _fingerprint(page)
                after_url = await _safe_url(page)
                proof = "unproven"
                if after_url.split("#")[0] != before_url.split("#")[0]:
                    proof = "url_changed"
                elif after_fp != before_fp:
                    proof = "same_page"
                else:
                    # Visual proof: compare live page to pre-click frame if available
                    # (post-click screenshot is taken next loop; use a quick probe shot)
                    probe = frames_dir / f"_probe_{step_i:03d}.png"
                    if before_shot and await _screenshot(page, probe):
                        try:
                            from app.frame_dedup import frames_are_duplicates
                            if not frames_are_duplicates(before_shot, probe):
                                proof = "same_page"
                        except Exception:
                            pass
                        try:
                            probe.unlink(missing_ok=True)
                        except Exception:
                            pass
                    if proof == "unproven":
                        proof = "unproven"

                if before_shot:
                    try:
                        Path(before_shot).unlink(missing_ok=True)
                    except Exception:
                        pass

                if proof == "unproven":
                    plan.failed_clicks += 1
                    # Drop focus frame — no visible change means bad click for demos
                    if plan.steps and plan.steps[-1].frame_role == "focus":
                        dead = plan.steps.pop()
                        step_i = max(step_i - 1, len(plan.steps))
                    logger.debug(
                        "capture: click produced no visible change; trying next target",
                        extra={
                            "operation": "click_proof",
                            "label": text or testid,
                            "proof": proof,
                            "url": after_url,
                        },
                    )
                    continue

                plan.proven_clicks += 1
                plan._pending_label = text or testid  # type: ignore[attr-defined]
                plan._pending_bbox = bbox  # type: ignore[attr-defined]
                plan._pending_proof = proof  # type: ignore[attr-defined]
                # stamp proof on the focus frame we just recorded
                for s in reversed(plan.steps):
                    if s.frame_role == "focus" and s.label == (text or testid):
                        s.proof_status = proof
                        break
                advanced = True
                break

            if not advanced:
                plan.end_reached = True
                plan.end_reason = "no_more_targets"
                break

        n = len(plan.steps)
        for i, step in enumerate(plan.steps):
            step.subtitle = narrate_step(
                step, is_first=(i == 0), is_last=(i == n - 1)
            )
            step.index = i

        await context.close()
        await browser.close()

    if plan.steps and not plan.end_reason:
        plan.end_reached = True
        plan.end_reason = "completed"
    return plan


def capture_journey_sync(
    start_url: str,
    work_dir: Path,
    **kwargs: Any,
) -> JourneyPlan:
    return asyncio.run(capture_journey(start_url, work_dir, **kwargs))
