from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.product.capture import capture_journey_sync
from app.product.video import (
    SHIPVIDEO_AUDIT_MAX_VIDEO_SECONDS,
    render_journey_video,
)

logger = logging.getLogger(__name__)


ProgressCb = Optional[Callable[[str, Dict[str, Any]], None]]


def video_filename(job_id: str) -> str:
    return f"journey_{job_id}.mp4"


def video_path_for_job(job_dir: Path, job_id: str) -> Path:
    return Path(job_dir) / video_filename(job_id)


def _extract_dom_text_headless(url: str) -> str:
    import asyncio

    async def _run() -> str:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1280, "height": 720})
            try:
                nav_t0 = time.monotonic()
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                nav_duration_sec = time.monotonic() - nav_t0
                _nav_log = logger.warning if nav_duration_sec > 10.0 else logger.debug
                _nav_log(
                    "_extract_dom_text_headless: page.goto completed",
                    extra={
                        "operation": "playwright_navigation",
                        "url": url,
                        "duration_sec": round(nav_duration_sec, 3),
                        "slow": nav_duration_sec > 10.0,
                    },
                )
                await page.wait_for_timeout(800)
                text = await page.evaluate(
                    """() => {
                      const root = document.querySelector('article')
                        || document.querySelector('main')
                        || document.body;
                      const t = (root && root.innerText) || '';
                      return t.replace(/\\s+/g, ' ').trim().slice(0, 14000);
                    }"""
                )
            finally:
                await browser.close()
        return str(text or "")

    extract_t0 = time.monotonic()
    result = asyncio.run(_run())
    extract_duration_sec = time.monotonic() - extract_t0
    _ex_log = logger.warning if extract_duration_sec > 30.0 else logger.debug
    _ex_log(
        "_extract_dom_text_headless: completed",
        extra={
            "operation": "extract_dom_text",
            "url": url,
            "duration_sec": round(extract_duration_sec, 3),
            "dom_chars": len(result or ""),
            "slow": extract_duration_sec > 30.0,
        },
    )
    return result


def _apply_subtitle_lines(steps: List[Any], lines: List[str]) -> None:
    if not steps or not lines:
        return
    for i, step in enumerate(steps):
        line = lines[i] if i < len(lines) else lines[-1]
        step.subtitle = line


def run_link_to_video(
    url: str,
    job_dir: Path,
    *,
    job_id: Optional[str] = None,
    max_steps: int = 10,
    use_azure_subtitles: bool = False,
    on_progress: ProgressCb = None,
    language: str = "en",
    brand: Optional[Any] = None,
    export_sizzle: bool = True,
) -> Dict[str, Any]:
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    if not job_id:
        job_id = job_dir.name

    from app.product.brand_theme import BrandTheme, DEFAULT_BRAND
    if brand is None:
        brand_theme = DEFAULT_BRAND
    elif isinstance(brand, BrandTheme):
        brand_theme = brand
    elif isinstance(brand, dict):
        brand_theme = BrandTheme.from_dict(brand)
    else:
        brand_theme = DEFAULT_BRAND

    def emit(stage: str, **extra: Any) -> None:
        if on_progress:
            on_progress(stage, extra)

    emit("capture_start", url=url, headless=True, job_id=job_id)
    logger.debug(
        "run_link_to_video: capture starting",
        extra={"operation": "capture_journey", "job_id": job_id, "url": url, "max_steps": max_steps},
    )
    capture_t0 = time.monotonic()
    plan = capture_journey_sync(url, job_dir, max_steps=max_steps, headless=True)
    capture_duration_sec = time.monotonic() - capture_t0
    _cap_log = logger.warning if capture_duration_sec > 120.0 else logger.debug
    _cap_log(
        "run_link_to_video: capture_journey completed",
        extra={
            "operation": "capture_journey",
            "job_id": job_id,
            "url": url,
            "duration_sec": round(capture_duration_sec, 3),
            "step_count": len(plan.steps),
            "slow": capture_duration_sec > 120.0,
        },
    )
    emit(
        "capture_done",
        steps=len(plan.steps),
        end_reached=plan.end_reached,
        end_reason=plan.end_reason,
        headless=True,
    )
    logger.debug(
        "run_link_to_video: capture finished",
        extra={
            "operation": "capture_journey",
            "job_id": job_id,
            "url": url,
            "step_count": len(plan.steps),
            "end_reached": plan.end_reached,
            "end_reason": plan.end_reason,
        },
    )

    if not plan.steps:
        logger.warning(
            "run_link_to_video: no steps captured; aborting without video",
            extra={
                "operation": "run_link_to_video",
                "job_id": job_id,
                "url": url,
                "end_reason": plan.end_reason or "no_steps_captured",
            },
        )
        return {
            "ok": False,
            "error": plan.end_reason or "no_steps_captured",
            "steps": [],
            "video_url": None,
            "job_id": job_id,
            "headless": True,
        }

    azure_meta: Dict[str, Any] = {"used": False}
    if use_azure_subtitles:
        emit("azure_subtitles_start", url=url)
        try:
            from app.product.azure_subtitles import generate_subtitles_from_dom

            dom_text = _extract_dom_text_headless(plan.start_url or url)
            step_summaries = [
                {
                    "action": s.action,
                    "title": s.title,
                    "label": s.label,
                    "url": s.url,
                }
                for s in plan.steps
            ]
            llm_t0 = time.monotonic()
            azure_meta = generate_subtitles_from_dom(
                url=plan.start_url or url,
                dom_text=dom_text,
                step_summaries=step_summaries,
                n_lines=len(plan.steps),
            )
            llm_duration_sec = time.monotonic() - llm_t0
            _llm_log = logger.warning if llm_duration_sec > 45.0 else logger.debug
            _llm_log(
                "run_link_to_video: azure subtitle LLM call completed",
                extra={
                    "operation": "azure_subtitle_llm",
                    "job_id": job_id,
                    "url": plan.start_url or url,
                    "duration_sec": round(llm_duration_sec, 3),
                    "dom_chars": len(dom_text),
                    "n_lines": len(plan.steps),
                    "slow": llm_duration_sec > 45.0,
                },
            )
            azure_meta["used"] = True
            azure_meta["dom_chars"] = len(dom_text)
            _apply_subtitle_lines(plan.steps, list(azure_meta.get("lines") or []))
            emit("azure_subtitles_done", lines=azure_meta.get("lines"))
            logger.debug(
                "run_link_to_video: azure subtitles applied",
                extra={
                    "operation": "azure_subtitles",
                    "job_id": job_id,
                    "url": plan.start_url or url,
                    "line_count": len(azure_meta.get("lines") or []),
                    "dom_chars": azure_meta.get("dom_chars"),
                    "prompt_tokens": azure_meta.get("prompt_tokens"),
                    "completion_tokens": azure_meta.get("completion_tokens"),
                    "tokens_used": (
                        (azure_meta.get("prompt_tokens") or 0)
                        + (azure_meta.get("completion_tokens") or 0)
                    )
                    if (
                        azure_meta.get("prompt_tokens") is not None
                        or azure_meta.get("completion_tokens") is not None
                    )
                    else None,
                },
            )
        except Exception as e:
            azure_meta = {
                "used": False,
                "error": f"{type(e).__name__}: {e}",
            }
            emit("azure_subtitles_failed", error=azure_meta["error"])
            logger.warning(
                "run_link_to_video: azure subtitles failed; continuing with default subtitles",
                extra={
                    "operation": "azure_subtitles",
                    "job_id": job_id,
                    "url": plan.start_url or url,
                    "error": azure_meta["error"],
                },
                exc_info=True,
            )

    out_path = video_path_for_job(job_dir, job_id)
    emit("render_start", frames=len(plan.steps), max_video_seconds=SHIPVIDEO_AUDIT_MAX_VIDEO_SECONDS)
    logger.debug(
        "run_link_to_video: render starting",
        extra={
            "operation": "render_journey_video",
            "job_id": job_id,
            "frame_count": len(plan.steps),
            "output_path": str(out_path),
        },
    )
    render_t0 = time.monotonic()
    render_meta = render_journey_video(
        plan.steps,
        out_path,
        work_dir=job_dir,
        job_id=job_id,
        max_total_seconds=SHIPVIDEO_AUDIT_MAX_VIDEO_SECONDS,
        brand=brand_theme,
        language=language,
        export_sizzle=export_sizzle,
        ken_burns=True,
    )
    gif_path = job_dir / f"journey_{job_id}.gif"
    try:
        from app.render_effects.gif_export import export_gif
        frame_paths = []
        for s in plan.steps:
            rp = getattr(s, "render_path", None)
            if rp and Path(rp).exists():
                frame_paths.append(str(rp))
            elif s.screenshot_path and Path(s.screenshot_path).exists():
                frame_paths.append(s.screenshot_path)
        if frame_paths:
            export_gif(frame_paths, gif_path)
            render_meta["gif"] = str(gif_path)
    except Exception as e:
        logger.debug(
            "run_link_to_video: gif export skipped",
            extra={
                "operation": "gif_export",
                "job_id": job_id,
                "error": f"{type(e).__name__}: {e}",
            },
            exc_info=True,
        )
    render_duration_sec = time.monotonic() - render_t0
    _ren_log = logger.warning if render_duration_sec > 120.0 else logger.debug
    _ren_log(
        "run_link_to_video: render_journey_video completed",
        extra={
            "operation": "render_journey_video",
            "job_id": job_id,
            "output_path": str(out_path),
            "duration_sec": round(render_duration_sec, 3),
            "frames": render_meta.get("frames"),
            "slow": render_duration_sec > 120.0,
        },
    )
    emit(
        "render_done",
        video=str(out_path),
        total_duration_sec=render_meta.get("total_duration_sec"),
        headless=True,
    )
    logger.debug(
        "run_link_to_video: render finished",
        extra={
            "operation": "render_journey_video",
            "job_id": job_id,
            "output_path": str(out_path),
            "total_duration_sec": render_meta.get("total_duration_sec"),
            "frames": render_meta.get("frames"),
            "audio_source": render_meta.get("audio_source"),
        },
    )

    out_bytes = out_path.stat().st_size if out_path.exists() else 0
    try:
        import json as _json
        accuracy_path = job_dir / f"journey_{job_id}_accuracy.json"
        accuracy_path.write_text(
            _json.dumps(
                {
                    "proven_clicks": int(getattr(plan, "proven_clicks", 0) or 0),
                    "failed_clicks": int(getattr(plan, "failed_clicks", 0) or 0),
                    "step_count": len(plan.steps),
                    "focus_frames": sum(1 for s in plan.steps if getattr(s, "frame_role", "") == "focus"),
                    "end_reason": plan.end_reason,
                    "accuracy_ok": bool(int(getattr(plan, "proven_clicks", 0) or 0) > 0 or len(plan.steps) <= 1),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass
    logger.debug(
        "run_link_to_video: completed with counts",
        extra={
            "operation": "run_link_to_video",
            "job_id": job_id,
            "url": url,
            "step_count": len(plan.steps),
            "frames": render_meta.get("frames"),
            "cue_count": len(render_meta.get("cues") or []),
            "bytes": out_bytes,
            "total_duration_sec": render_meta.get("total_duration_sec"),
            "audio_source": render_meta.get("audio_source"),
            "azure_used": bool(azure_meta.get("used")),
            "prompt_tokens": azure_meta.get("prompt_tokens"),
            "completion_tokens": azure_meta.get("completion_tokens"),
        },
    )

    step_payload = [
        {
            "index": s.index,
            "action": s.action,
            "url": s.url,
            "title": s.title,
            "label": s.label,
            "subtitle": s.subtitle,
            "screenshot": Path(s.screenshot_path).name if s.screenshot_path else "",
            "frame_role": getattr(s, "frame_role", "result") or "result",
            "proof_status": getattr(s, "proof_status", "") or "",
            "click_bbox": getattr(s, "click_bbox", None),
        }
        for s in plan.steps
    ]

    proven = int(getattr(plan, "proven_clicks", 0) or 0)
    failed = int(getattr(plan, "failed_clicks", 0) or 0)
    # Accuracy gate: multi-step journeys should have at least one proven interaction
    accuracy_ok = proven > 0 or len(plan.steps) <= 1

    return {
        "ok": True,
        "job_id": job_id,
        "start_url": plan.start_url,
        "end_reached": plan.end_reached,
        "end_reason": plan.end_reason,
        "proven_clicks": proven,
        "failed_clicks": failed,
        "accuracy_ok": accuracy_ok,
        "steps": step_payload,
        "video_path": str(out_path),
        "gif_path": render_meta.get("gif"),
        "thumbnail": render_meta.get("thumbnail"),
        "sizzle": render_meta.get("sizzle"),
        "language": render_meta.get("language") or language,
        "brand": render_meta.get("brand") or brand_theme.to_dict(),
        "srt_lang": render_meta.get("srt_lang"),
        "ken_burns": render_meta.get("ken_burns"),
        "chapters": render_meta.get("chapters"),
        "chapters_vtt": render_meta.get("chapters_vtt"),
        "chapters_txt": render_meta.get("chapters_txt"),
        "youtube_description": render_meta.get("youtube_description"),
        "srt_path": render_meta.get("srt"),
        "subtitles_burned": render_meta.get("subtitles_burned"),
        "cues": render_meta.get("cues"),
        "frames": render_meta.get("frames"),
        "seconds_per_frame": render_meta.get("seconds_per_frame"),
        "total_duration_sec": render_meta.get("total_duration_sec"),
        "max_total_seconds": render_meta.get("max_total_seconds"),
        "headless": True,
        "audio_source": render_meta.get("audio_source"),
        "audio_path": render_meta.get("audio_path"),
        "speech_segments": render_meta.get("speech_segments"),
        "silencedetect_command": render_meta.get("silencedetect_command"),
        "silencedetect": render_meta.get("silencedetect"),
        "azure_subtitles": azure_meta,
    }
