from __future__ import annotations

import logging
import subprocess
import time
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from app.product.journey import JourneyStep, build_subtitles
from app.product.audio_timing import prepare_audio_and_cues
from app.render_effects.annotate import draw_click_highlight, zoom_crop_toward_bbox
from app.render_effects.progress import draw_click_ripple, draw_progress_bar
from app.render_effects.brand import stamp_brand
from app.render_effects.chapters import (
    chapter_entries_from_steps,
    write_webvtt_chapters,
    write_youtube_chapters_text,
)
from app.render_effects.youtube_desc import write_youtube_description
from app.render_effects.thumbnail import make_thumbnail
from app.render_effects.title_card import make_title_card
from app.render_effects.sizzle import export_sizzle_mp4
from app.render_effects.i18n_captions import (
    normalize_lang,
    translate_lines,
    write_translated_srt,
)
from app.product.brand_theme import BrandTheme, DEFAULT_BRAND

logger = logging.getLogger(__name__)


SHIPVIDEO_AUDIT_FRAME_SECONDS = 2.8
SHIPVIDEO_AUDIT_VIEWPORT = (1280, 720)
SHIPVIDEO_AUDIT_MAX_VIDEO_SECONDS = 60.0


def allocate_frame_durations(
    n_frames: int,
    *,
    default_seconds: float = SHIPVIDEO_AUDIT_FRAME_SECONDS,
    max_total_seconds: float = SHIPVIDEO_AUDIT_MAX_VIDEO_SECONDS,
) -> float:
    """Seconds per frame so total duration never exceeds max_total_seconds."""
    if n_frames <= 0:
        return default_seconds
    per = float(default_seconds)
    total = per * n_frames
    if total > max_total_seconds:
        per = max_total_seconds / float(n_frames)
    # Keep a tiny minimum so ffmpeg still produces a valid clip
    return max(0.05, per)


def _burn_caption_on_image(image_path: Path, caption: str, out_path: Path) -> Path:
    """Burn subtitle text onto a PNG using Pillow (works without libass/drawtext)."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(image_path).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    w, h = img.size
    font_path_used = "default"
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 28)
        font_path_used = "/System/Library/Fonts/Supplemental/Arial.ttf"
    except Exception as e:
        logger.debug(
            "_burn_caption_on_image: Arial font unavailable, trying DejaVu",
            extra={
                "operation": "load_caption_font",
                "frame_path": str(image_path),
                "font_path": "/System/Library/Fonts/Supplemental/Arial.ttf",
                "error": f"{type(e).__name__}: {e}",
            },
            exc_info=True,
        )
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
            font_path_used = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        except Exception as e2:
            logger.debug(
                "_burn_caption_on_image: truetype fonts unavailable, using PIL default",
                extra={
                    "operation": "load_caption_font",
                    "frame_path": str(image_path),
                    "font_path": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                    "error": f"{type(e2).__name__}: {e2}",
                },
                exc_info=True,
            )
            font = ImageFont.load_default()
            font_path_used = "PIL.ImageFont.load_default"

    lines = textwrap.wrap((caption or "").strip(), width=56) or [""]
    line_heights = []
    max_line_w = 0
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        lw = bbox[2] - bbox[0]
        lh = bbox[3] - bbox[1]
        line_heights.append(lh)
        max_line_w = max(max_line_w, lw)
    block_h = sum(line_heights) + 8 * (len(lines) - 1)
    pad_x, pad_y = 22, 14
    box_w = min(w - 40, max_line_w + pad_x * 2)
    box_h = block_h + pad_y * 2
    box_x = (w - box_w) // 2
    box_y = h - box_h - 36
    draw.rounded_rectangle(
        (box_x, box_y, box_x + box_w, box_y + box_h),
        radius=12,
        fill=(0, 0, 0, 170),
    )
    y = box_y + pad_y
    for line, lh in zip(lines, line_heights):
        bbox = draw.textbbox((0, 0), line, font=font)
        lw = bbox[2] - bbox[0]
        x = box_x + (box_w - lw) // 2
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        y += lh + 8
    composed = Image.alpha_composite(img, overlay).convert("RGB")
    out_path = Path(out_path)
    composed.save(out_path, format="PNG")
    logger.debug(
        "_burn_caption_on_image: caption burned",
        extra={
            "operation": "burn_caption",
            "frame_path": str(image_path),
            "out_path": str(out_path),
            "font": font_path_used,
        },
    )
    return out_path


def _ts(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def write_srt(cues: Sequence[Dict[str, Any]], path: Path) -> Path:
    path = Path(path)
    lines: List[str] = []
    for cue in cues:
        lines.append(str(cue["index"]))
        lines.append(f"{_ts(float(cue['start']))} --> {_ts(float(cue['end']))}")
        text = str(cue.get("text") or "").replace("\n", " ").strip()
        lines.append(text)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_ass(cues: Sequence[Dict[str, Any]], path: Path) -> Path:
    """Simple ASS with readable bottom-center style for burn-in."""
    path = Path(path)

    def ass_ts(seconds: float) -> str:
        if seconds < 0:
            seconds = 0.0
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        cs = int(round((seconds - int(seconds)) * 100))
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,36,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,40,40,48,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: List[str] = []
    for cue in cues:
        text = str(cue.get("text") or "").replace("\n", " ").replace("{", "(").replace("}", ")")
        events.append(
            f"Dialogue: 0,{ass_ts(float(cue['start']))},{ass_ts(float(cue['end']))},"
            f"Default,,0,0,0,,{text}"
        )
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return path



def prepare_demo_frames(
    steps: List[JourneyStep],
    work_dir: Path,
    *,
    apply_zoom: bool = True,
    zoom_factor: float = 1.28,
    theme: Optional[BrandTheme] = None,
) -> List[Path]:
    """Produce accuracy-oriented frames: click rings, labels, mild zoom on focus."""
    theme = theme or DEFAULT_BRAND
    out_dir = Path(work_dir) / "demo_frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    prepared: List[Path] = []
    total = len([s for s in steps if s.screenshot_path and Path(s.screenshot_path).exists()])
    idx = 0
    for step in steps:
        if not step.screenshot_path or not Path(step.screenshot_path).exists():
            continue
        idx += 1
        src = Path(step.screenshot_path)
        role = (step.frame_role or "result").strip()
        label = (step.label or "").strip()
        badge = (step.proof_status or "").strip()
        bbox = step.click_bbox if isinstance(step.click_bbox, dict) else None

        # Focus frames already often have DOM highlight + annotate; still ensure badge/label
        ann_path = out_dir / f"frame_{idx:03d}_{role}.png"
        try:
            draw_click_highlight(
                src,
                ann_path,
                bbox=bbox if role == "focus" else None,
                label=label if role == "focus" else (label if step.action == "click" else ""),
                step_index=idx,
                total_steps=total,
                proof_badge=badge if role == "result" and badge else "",
            )
            frame_path = ann_path
        except Exception as e:
            logger.debug(
                "prepare_demo_frames: annotate failed; using source",
                extra={
                    "operation": "prepare_demo_frames",
                    "frame_index": idx,
                    "error": f"{type(e).__name__}: {e}",
                },
                exc_info=True,
            )
            frame_path = src

        if apply_zoom and role == "focus" and bbox:
            zoom_path = out_dir / f"frame_{idx:03d}_zoom.png"
            try:
                zoom_crop_toward_bbox(frame_path, zoom_path, bbox, zoom=zoom_factor)
                step.zoom_path = str(zoom_path)
                frame_path = zoom_path
            except Exception as e:
                logger.debug(
                    "prepare_demo_frames: zoom failed",
                    extra={
                        "operation": "zoom_crop",
                        "frame_index": idx,
                        "error": f"{type(e).__name__}: {e}",
                    },
                    exc_info=True,
                )

        # Screencast-style click ripple on focus frames
        if role == "focus" and bbox:
            ripple_path = out_dir / f"frame_{idx:03d}_ripple.png"
            try:
                draw_click_ripple(frame_path, ripple_path, bbox)
                frame_path = ripple_path
            except Exception as e:
                logger.debug(
                    "prepare_demo_frames: ripple failed",
                    extra={"operation": "click_ripple", "frame_index": idx, "error": f"{type(e).__name__}: {e}"},
                    exc_info=True,
                )

        # Journey progress bar (competitor standard: always show where you are)
        prog_path = out_dir / f"frame_{idx:03d}_prog.png"
        try:
            draw_progress_bar(frame_path, prog_path, step_index=idx, total_steps=total, theme=theme)
            frame_path = prog_path
        except Exception as e:
            logger.debug(
                "prepare_demo_frames: progress bar failed",
                extra={"operation": "progress_bar", "frame_index": idx, "error": f"{type(e).__name__}: {e}"},
                exc_info=True,
            )

        # Subtle brand stamp
        brand_path = out_dir / f"frame_{idx:03d}_brand.png"
        try:
            stamp_brand(frame_path, brand_path, text=theme.name, theme=theme)
            frame_path = brand_path
        except Exception as e:
            logger.debug(
                "prepare_demo_frames: brand stamp failed",
                extra={"operation": "brand_stamp", "frame_index": idx, "error": f"{type(e).__name__}: {e}"},
                exc_info=True,
            )

        prepared.append(frame_path)
    return prepared


def render_journey_video(
    steps: List[JourneyStep],
    output_mp4: Path,
    *,
    work_dir: Optional[Path] = None,
    seconds_per_frame: float = SHIPVIDEO_AUDIT_FRAME_SECONDS,
    max_total_seconds: float = SHIPVIDEO_AUDIT_MAX_VIDEO_SECONDS,
    width: int = SHIPVIDEO_AUDIT_VIEWPORT[0],
    height: int = SHIPVIDEO_AUDIT_VIEWPORT[1],
    burn_subtitles: bool = True,
    job_id: Optional[str] = None,
    brand: Optional[BrandTheme] = None,
    language: str = "en",
    export_sizzle: bool = True,
    ken_burns: bool = True,
) -> Dict[str, Any]:
    render_t0 = time.monotonic()
    output_mp4 = Path(output_mp4)
    work_dir = Path(work_dir or output_mp4.parent)
    work_dir.mkdir(parents=True, exist_ok=True)
    brand = brand or DEFAULT_BRAND
    language = normalize_lang(language)

    missing_shots: List[str] = []
    source_frames = []
    for s in steps:
        if s.screenshot_path and Path(s.screenshot_path).exists():
            source_frames.append(Path(s.screenshot_path))
        elif s.screenshot_path:
            missing_shots.append(str(s.screenshot_path))
        else:
            missing_shots.append(f"step_index={getattr(s, 'index', '?')}:no_path")
    if missing_shots:
        logger.debug(
            "render_journey_video: skipping steps without screenshot files",
            extra={
                "operation": "render_journey_video",
                "job_id": job_id,
                "missing_count": len(missing_shots),
                "missing_frames": missing_shots[:20],
            },
        )
    if not source_frames:
        logger.error(
            "render_journey_video: no screenshot frames to render",
            extra={
                "operation": "render_journey_video",
                "job_id": job_id,
                "step_count": len(steps),
                "missing_frames": missing_shots[:20],
            },
        )
        raise FileNotFoundError("No screenshot frames to render")

    stem = f"journey_{job_id}" if job_id else output_mp4.stem
    steps_with_shots = [s for s in steps if s.screenshot_path and Path(s.screenshot_path).exists()]

    # Accuracy pass: click rings, step labels, mild zoom on focus frames
    prep_t0 = time.monotonic()
    try:
        prepared = prepare_demo_frames(steps_with_shots, work_dir, apply_zoom=True, theme=brand)
        if prepared and len(prepared) == len(steps_with_shots):
            source_frames = prepared
            # Keep original capture paths on steps for API/timeline; use prepared only for render.
            for s, p in zip(steps_with_shots, prepared):
                if not getattr(s, "render_path", None):
                    try:
                        s.render_path = str(p)  # type: ignore[attr-defined]
                    except Exception:
                        pass
            # Drop only true-duplicate consecutive result frames; never drop focus frames
            keep_steps = []
            keep_frames = []
            prev_path = None
            for s, p in zip(steps_with_shots, prepared):
                role = (getattr(s, "frame_role", "") or "")
                if role != "focus" and prev_path is not None:
                    try:
                        from app.frame_dedup import frames_are_duplicates
                        if frames_are_duplicates(prev_path, p):
                            continue
                    except Exception:
                        pass
                keep_steps.append(s)
                keep_frames.append(p)
                prev_path = p
            if keep_frames and len(keep_frames) < len(prepared):
                logger.debug(
                    "render_journey_video: dropped duplicate result frames",
                    extra={
                        "operation": "frame_dedup",
                        "job_id": job_id,
                        "frames_before": len(prepared),
                        "frames_after": len(keep_frames),
                    },
                )
                steps_with_shots = keep_steps
                source_frames = keep_frames
                prepared = keep_frames
        prep_sec = time.monotonic() - prep_t0
        logger.debug(
            "render_journey_video: prepare_demo_frames completed",
            extra={
                "operation": "prepare_demo_frames",
                "job_id": job_id,
                "frame_count": len(prepared) if prepared else 0,
                "duration_sec": round(prep_sec, 3),
            },
        )
    except Exception as e:
        logger.warning(
            "render_journey_video: prepare_demo_frames failed; using raw screenshots",
            extra={
                "operation": "prepare_demo_frames",
                "job_id": job_id,
                "error": f"{type(e).__name__}: {e}",
            },
            exc_info=True,
        )

    # Focus frames get slightly shorter default holds so the click feels snappy
    for s in steps_with_shots:
        if (s.frame_role or "") == "focus" and not s.duration_sec:
            s.duration_sec = max(1.2, SHIPVIDEO_AUDIT_FRAME_SECONDS * 0.65)

    narrations = [
        (s.subtitle or "").strip() or f"Step {i + 1}"
        for i, s in enumerate(steps_with_shots)
    ]
    if language != "en":
        try:
            narrations = translate_lines(narrations, language)
            for s, line in zip(steps_with_shots, narrations):
                s.subtitle = line
        except Exception as e:
            logger.debug(
                "render_journey_video: i18n translate failed; keeping English",
                extra={
                    "operation": "i18n_captions",
                    "job_id": job_id,
                    "language": language,
                    "error": f"{type(e).__name__}: {e}",
                },
                exc_info=True,
            )

    # Waveform-based timing: silencedetect on captured audio, else TTS + silencedetect.
    audio_t0 = time.monotonic()
    audio_pack = prepare_audio_and_cues(
        narrations,
        work_dir,
        stem=stem,
        existing_media=None,
        max_total_seconds=max_total_seconds,
    )
    audio_duration_sec = time.monotonic() - audio_t0
    _audio_log = logger.warning if audio_duration_sec > 45.0 else logger.debug
    _audio_log(
        "render_journey_video: prepare_audio_and_cues completed",
        extra={
            "operation": "prepare_audio_and_cues",
            "job_id": job_id,
            "duration_sec": round(audio_duration_sec, 3),
            "audio_source": audio_pack.get("audio_source"),
            "slow": audio_duration_sec > 45.0,
        },
    )
    cues = list(audio_pack.get("cues") or [])
    if not cues:
        # ultimate fallback: equal chunks within 60s
        logger.warning(
            "render_journey_video: empty cues; using equal-chunk timing fallback",
            extra={
                "operation": "cue_timing_fallback",
                "job_id": job_id,
                "frame_count": len(source_frames),
                "audio_source": audio_pack.get("audio_source"),
            },
        )
        seconds_per_frame = allocate_frame_durations(
            len(source_frames),
            default_seconds=seconds_per_frame,
            max_total_seconds=max_total_seconds,
        )
        for step in steps:
            step.duration_sec = seconds_per_frame
        cues = build_subtitles(steps, seconds_per_frame=seconds_per_frame)

    # Drive frame hold times from cue spans (speech-aligned), then enforce 60s cap.
    # Focus frames stay snappy; result frames get a slight hold so UI is readable.
    frame_durations: List[float] = []
    for i, step in enumerate(steps_with_shots):
        if i < len(cues):
            dur = max(0.05, float(cues[i]["end"]) - float(cues[i]["start"]))
        else:
            dur = seconds_per_frame
        role = (getattr(step, "frame_role", "") or "").strip()
        if role == "focus":
            dur = max(0.8, min(dur, dur * 0.75))
        elif role == "result" and getattr(step, "proof_status", ""):
            dur = max(dur, min(dur * 1.08, seconds_per_frame * 1.2))
        frame_durations.append(dur)
        step.duration_sec = dur

    total_duration = sum(frame_durations)
    if total_duration > max_total_seconds and total_duration > 0:
        scale = max_total_seconds / total_duration
        frame_durations = [max(0.05, d * scale) for d in frame_durations]
        for i, step in enumerate(steps_with_shots):
            step.duration_sec = frame_durations[i]
        # rescale cues
        for cue in cues:
            cue["start"] = float(cue["start"]) * scale
            cue["end"] = float(cue["end"]) * scale
        total_duration = sum(frame_durations)
        logger.debug(
            "render_journey_video: scaled durations to max total",
            extra={
                "operation": "duration_cap",
                "job_id": job_id,
                "scale": scale,
                "total_duration_sec": total_duration,
                "max_total_seconds": max_total_seconds,
            },
        )

    seconds_per_frame = (
        total_duration / len(frame_durations) if frame_durations else seconds_per_frame
    )

    srt_path = work_dir / f"{stem}.srt"
    ass_path = work_dir / f"{stem}.ass"
    write_srt(cues, srt_path)
    write_ass(cues, ass_path)
    srt_lang_path = None
    if language != "en":
        try:
            srt_lang_path = work_dir / f"{stem}.{language}.srt"
            write_translated_srt(cues, srt_lang_path, language)
        except Exception as e:
            logger.debug(
                "render_journey_video: translated srt failed",
                extra={
                    "operation": "i18n_srt",
                    "job_id": job_id,
                    "language": language,
                    "error": f"{type(e).__name__}: {e}",
                },
                exc_info=True,
            )

    # Persist silence analysis for debugging / demos
    import json
    (work_dir / f"{stem}_silencedetect.json").write_text(
        json.dumps(
            {
                "command": audio_pack.get("silencedetect", {}).get("command"),
                "command_str": audio_pack.get("silencedetect", {}).get("command_str"),
                "speech_segments": audio_pack.get("speech_segments"),
                "silence_regions": audio_pack.get("silencedetect", {}).get("silence_regions"),
                "audio_source": audio_pack.get("audio_source"),
                "audio_path": audio_pack.get("audio_path"),
                "audio_duration_sec": audio_pack.get("audio_duration_sec"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    captioned_dir = work_dir / "captioned"
    captioned_dir.mkdir(parents=True, exist_ok=True)
    frames: List[Path] = []
    subtitles_burned = False
    burn_failures = 0
    burn_t0 = time.monotonic()
    if burn_subtitles:
        for i, step in enumerate(steps_with_shots):
            caption = step.subtitle or (cues[i]["text"] if i < len(cues) else "")
            out_img = captioned_dir / f"cap_{i:03d}.png"
            # Prefer polished render frame (brand/progress/zoom) when available
            src_frame = (
                source_frames[i]
                if i < len(source_frames)
                else Path(getattr(step, "render_path", None) or step.screenshot_path)
            )
            try:
                _burn_caption_on_image(Path(src_frame), caption, out_img)
                frames.append(out_img)
                subtitles_burned = True
            except Exception as e:
                burn_failures += 1
                logger.warning(
                    "render_journey_video: caption burn failed; using original frame",
                    extra={
                        "operation": "burn_caption",
                        "job_id": job_id,
                        "frame_index": i,
                        "frame_path": str(src_frame),
                        "error": f"{type(e).__name__}: {e}",
                    },
                    exc_info=True,
                )
                frames.append(Path(src_frame))
        if burn_failures:
            logger.warning(
                "render_journey_video: caption burn degraded",
                extra={
                    "operation": "burn_caption",
                    "job_id": job_id,
                    "burn_failures": burn_failures,
                    "frame_count": len(frames),
                },
            )
    else:
        frames = list(source_frames)
    burn_duration_sec = time.monotonic() - burn_t0
    if burn_subtitles:
        _burn_log = logger.warning if burn_duration_sec > 15.0 else logger.debug
        _burn_log(
            "render_journey_video: caption burn pass completed",
            extra={
                "operation": "burn_caption_batch",
                "job_id": job_id,
                "duration_sec": round(burn_duration_sec, 3),
                "frame_count": len(frames),
                "burn_failures": burn_failures,
                "slow": burn_duration_sec > 15.0,
            },
        )

    if not frames:
        frames = list(source_frames)
    while len(frame_durations) < len(frames):
        frame_durations.append(frame_durations[-1] if frame_durations else 2.8)
    frame_durations = frame_durations[: len(frames)]

    # Parallel flags for Ken Burns (focus content only; intro/outro added later)
    frame_is_focus: List[bool] = [
        (getattr(s, "frame_role", "") or "") == "focus"
        for s in steps_with_shots[: len(frames)]
    ]
    while len(frame_is_focus) < len(frames):
        frame_is_focus.append(False)

    scale_pad = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    )

    intro_title = (
        (steps_with_shots[0].title or steps_with_shots[0].label or "Product journey").strip()[:70]
        if steps_with_shots else "Product journey"
    )
    # Intro / outro title cards (Arcade/Storylane-style polish)
    cards_dir = work_dir / "cards"
    cards_dir.mkdir(parents=True, exist_ok=True)
    intro_path = cards_dir / "intro.png"
    outro_path = cards_dir / "outro.png"
    try:
        from app.render_effects.i18n_captions import translate_phrase
        intro_sub = translate_phrase("Proof-gated walkthrough", language)
        outro_title = translate_phrase("Thanks for watching", language)
        outro_sub = translate_phrase("Generated by ShipVideo", language)
        make_title_card(
            intro_path,
            title=intro_title,
            subtitle=intro_sub,
            footer=brand.name,
            size=(width, height),
            style="intro",
            theme=brand,
        )
        make_title_card(
            outro_path,
            title=outro_title,
            subtitle=outro_sub,
            footer=brand.name,
            size=(width, height),
            style="outro",
            theme=brand,
        )
        intro_dur = 1.4
        outro_dur = 1.2
        # Fit intro/outro under remaining budget when possible
        budget = max_total_seconds - sum(frame_durations)
        if budget < intro_dur + outro_dur and sum(frame_durations) > 0:
            scale = max(0.5, (sum(frame_durations) - 0.1) / (sum(frame_durations) + intro_dur + outro_dur))
            frame_durations = [max(0.05, d * scale) for d in frame_durations]
            for i, step in enumerate(steps_with_shots):
                if i < len(frame_durations):
                    step.duration_sec = frame_durations[i]
        if intro_path.exists():
            frames = [intro_path] + list(frames)
            frame_durations = [intro_dur] + list(frame_durations)
            frame_is_focus = [False] + list(frame_is_focus)
        if outro_path.exists():
            frames = list(frames) + [outro_path]
            frame_durations = list(frame_durations) + [outro_dur]
            frame_is_focus = list(frame_is_focus) + [False]
        total_duration = sum(frame_durations)
    except Exception as e:
        logger.debug(
            "render_journey_video: title cards skipped",
            extra={
                "operation": "title_cards",
                "job_id": job_id,
                "error": f"{type(e).__name__}: {e}",
            },
            exc_info=True,
        )

    silent_mp4 = work_dir / f"{stem}_silent.mp4"
    if len(frames) == 1:
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-loop", "1", "-t", str(frame_durations[0]), "-i", str(frames[0]),
            "-vf", scale_pad,
            "-r", "30",
            "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.0",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-an",
            str(silent_mp4),
        ]
    else:
        input_args: List[str] = []
        for frame, dur in zip(frames, frame_durations):
            input_args.extend(["-loop", "1", "-t", str(dur), "-i", str(frame)])
        filter_parts: List[str] = []
        for i in range(len(frames)):
            dur = float(frame_durations[i])
            use_kb = bool(ken_burns and i < len(frame_is_focus) and frame_is_focus[i] and dur >= 0.8)
            if use_kb:
                frames_n = max(8, int(round(dur * 30)))
                # Slow zoom-in toward center (focus already pre-cropped when possible)
                zp = (
                    f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
                    f"crop={width * 2}:{height * 2},"
                    f"zoompan=z='min(1.0+0.0012*on,1.12)':d={frames_n}:"
                    f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                    f"s={width}x{height}:fps=30,"
                    f"format=yuv420p"
                )
                filter_parts.append(f"[{i}:v]{zp}[v{i}]")
            else:
                filter_parts.append(f"[{i}:v]{scale_pad},format=yuv420p[v{i}]")
        filter_chains = "".join(p + ";" for p in filter_parts)
        concat_inputs = "".join(f"[v{i}]" for i in range(len(frames)))
        concat_filter = (
            f"{filter_chains}{concat_inputs}concat=n={len(frames)}:v=1:a=0,format=yuv420p"
        )
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            *input_args,
            "-filter_complex", concat_filter,
            "-r", "30",
            "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.0",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(silent_mp4),
        ]

    slideshow_t0 = time.monotonic()
    result = subprocess.run(cmd, capture_output=True, text=True)
    slideshow_duration_sec = time.monotonic() - slideshow_t0
    if result.returncode != 0:
        logger.error(
            "render_journey_video: ffmpeg slideshow failed",
            extra={
                "operation": "ffmpeg_slideshow",
                "job_id": job_id,
                "frame_count": len(frames),
                "silent_mp4": str(silent_mp4),
                "returncode": result.returncode,
                "duration_sec": round(slideshow_duration_sec, 3),
                "stderr_tail": (result.stderr or "")[-2000:],
            },
        )
        raise RuntimeError(f"ffmpeg slideshow failed: {result.stderr or result.stdout}")
    _ss_log = logger.warning if slideshow_duration_sec > 60.0 else logger.debug
    _ss_log(
        "render_journey_video: ffmpeg slideshow completed",
        extra={
            "operation": "ffmpeg_slideshow",
            "job_id": job_id,
            "frame_count": len(frames),
            "silent_mp4": str(silent_mp4),
            "duration_sec": round(slideshow_duration_sec, 3),
            "slow": slideshow_duration_sec > 60.0,
        },
    )

    # Mux narration audio when present
    audio_path = audio_pack.get("audio_path")
    if audio_path and Path(audio_path).exists():
        mux_t0 = time.monotonic()
        mux = subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(silent_mp4),
                "-i", str(audio_path),
                "-c:v", "copy",
                "-c:a", "aac",
                "-shortest",
                "-movflags", "+faststart",
                str(output_mp4),
            ],
            capture_output=True,
            text=True,
        )
        mux_duration_sec = time.monotonic() - mux_t0
        if mux.returncode == 0:
            _mux_log = logger.warning if mux_duration_sec > 30.0 else logger.debug
            _mux_log(
                "render_journey_video: audio mux completed",
                extra={
                    "operation": "ffmpeg_mux_audio",
                    "job_id": job_id,
                    "audio_path": str(audio_path),
                    "output_path": str(output_mp4),
                    "duration_sec": round(mux_duration_sec, 3),
                    "slow": mux_duration_sec > 30.0,
                },
            )
        if mux.returncode != 0:
            logger.warning(
                "render_journey_video: audio mux failed; shipping silent video",
                extra={
                    "operation": "ffmpeg_mux_audio",
                    "job_id": job_id,
                    "audio_path": str(audio_path),
                    "silent_mp4": str(silent_mp4),
                    "output_path": str(output_mp4),
                    "returncode": mux.returncode,
                    "stderr_tail": (mux.stderr or "")[-2000:],
                },
            )
            output_mp4.write_bytes(silent_mp4.read_bytes())
    else:
        logger.debug(
            "render_journey_video: no audio to mux; writing silent video",
            extra={
                "operation": "ffmpeg_mux_audio",
                "job_id": job_id,
                "audio_path": audio_path,
                "output_path": str(output_mp4),
            },
        )
        output_mp4.write_bytes(silent_mp4.read_bytes())

    render_duration_sec = time.monotonic() - render_t0
    out_bytes = output_mp4.stat().st_size if output_mp4.exists() else 0
    _rlog = logger.warning if render_duration_sec > 120.0 else logger.debug
    _rlog(
        "render_journey_video: completed",
        extra={
            "operation": "render_journey_video",
            "job_id": job_id,
            "output_path": str(output_mp4),
            "duration_sec": round(render_duration_sec, 3),
            "frame_count": len(frames),
            "frames_kept": len(frames),
            "source_frame_count": len(source_frames),
            "steps_with_shots": len(steps_with_shots),
            "missing_shot_count": len(missing_shots),
            "cue_count": len(cues),
            "burn_failures": burn_failures if burn_subtitles else 0,
            "subtitles_burned": subtitles_burned,
            "bytes": out_bytes,
            "total_duration_sec": total_duration,
            "slow": render_duration_sec > 120.0,
        },
    )

    # Packaging: chapters, YouTube description, thumbnail (share-ready like Arcade)
    chapters_meta: List[Any] = []
    chapters_vtt = work_dir / f"{stem}.chapters.vtt"
    chapters_txt = work_dir / f"{stem}.chapters.txt"
    yt_desc_path = work_dir / f"{stem}.youtube.txt"
    thumb_path = work_dir / f"{stem}_thumb.jpg"
    try:
        holds = list(frame_durations)
        # Chapters from content steps only (exclude intro/outro cards)
        chapters_meta = chapter_entries_from_steps(
            steps_with_shots,
            hold_seconds=[
                float(getattr(s, "duration_sec", 0) or seconds_per_frame)
                for s in steps_with_shots
            ],
        )
        # Offset chapter starts if intro card was prepended
        intro_offset = 0.0
        if frames and frames[0].name == "intro.png":
            intro_offset = float(frame_durations[0]) if frame_durations else 1.4
            for ch in chapters_meta:
                ch["start"] = float(ch["start"]) + intro_offset
            if chapters_meta:
                # Keep first chapter labelable; YouTube wants 0:00 first line separately
                pass
        write_webvtt_chapters(
            chapters_meta,
            chapters_vtt,
            total_duration=total_duration,
        )
        write_youtube_chapters_text(chapters_meta, chapters_txt)
        write_youtube_description(
            yt_desc_path,
            title=intro_title,
            steps=steps_with_shots,
            chapters=chapters_meta,
            proven_clicks=sum(
                1
                for s in steps_with_shots
                if (getattr(s, "proof_status", "") or "")
                in ("url_changed", "same_page", "proven")
            ),
        )
        # Thumbnail from first focus frame else first result
        thumb_src = None
        for s in steps_with_shots:
            if (getattr(s, "frame_role", "") or "") == "focus" and s.screenshot_path:
                thumb_src = s.screenshot_path
                break
        if not thumb_src and steps_with_shots:
            thumb_src = steps_with_shots[0].screenshot_path
        if thumb_src and Path(thumb_src).exists():
            make_thumbnail(
                thumb_src,
                thumb_path,
                title=intro_title,
                subtitle="Auto-generated walkthrough",
                size=(width, height),
                theme=brand,
            )
    except Exception as e:
        logger.debug(
            "render_journey_video: packaging artifacts failed",
            extra={
                "operation": "packaging",
                "job_id": job_id,
                "error": f"{type(e).__name__}: {e}",
            },
            exc_info=True,
        )

    sizzle_path = work_dir / f"{stem}_sizzle.mp4"
    if export_sizzle:
        try:
            content_frames = []
            content_durs = []
            content_bboxes = []
            for i, s in enumerate(steps_with_shots):
                frame = None
                rp = getattr(s, "render_path", None)
                if rp and Path(rp).exists():
                    frame = Path(rp)
                elif i < len(source_frames) and Path(source_frames[i]).exists():
                    frame = Path(source_frames[i])
                elif s.screenshot_path and Path(s.screenshot_path).exists():
                    frame = Path(s.screenshot_path)
                if frame is None:
                    continue
                content_frames.append(frame)
                content_durs.append(float(getattr(s, "duration_sec", 0) or seconds_per_frame))
                content_bboxes.append(s.click_bbox if isinstance(s.click_bbox, dict) else None)
            if content_frames:
                export_sizzle_mp4(
                    content_frames,
                    sizzle_path,
                    durations=content_durs,
                    bboxes=content_bboxes,
                    work_dir=work_dir / "sizzle_work",
                    max_seconds=15.0,
                )
        except Exception as e:
            logger.debug(
                "render_journey_video: sizzle export failed",
                extra={
                    "operation": "sizzle_export",
                    "job_id": job_id,
                    "error": f"{type(e).__name__}: {e}",
                },
                exc_info=True,
            )

    return {
        "video": str(output_mp4),
        "srt": str(srt_path),
        "ass": str(ass_path),
        "srt_lang": str(srt_lang_path) if srt_lang_path and Path(srt_lang_path).exists() else None,
        "language": language,
        "brand": brand.to_dict(),
        "frames": len(frames),
        "subtitles_burned": subtitles_burned,
        "cues": cues,
        "seconds_per_frame": seconds_per_frame,
        "total_duration_sec": total_duration,
        "max_total_seconds": max_total_seconds,
        "headless": True,
        "audio_source": audio_pack.get("audio_source"),
        "audio_path": audio_pack.get("audio_path"),
        "speech_segments": audio_pack.get("speech_segments"),
        "silencedetect_command": audio_pack.get("silencedetect", {}).get("command_str"),
        "silencedetect": audio_pack.get("silencedetect"),
        "chapters": chapters_meta,
        "chapters_vtt": str(chapters_vtt) if chapters_vtt.exists() else None,
        "chapters_txt": str(chapters_txt) if chapters_txt.exists() else None,
        "youtube_description": str(yt_desc_path) if yt_desc_path.exists() else None,
        "thumbnail": str(thumb_path) if thumb_path.exists() else None,
        "sizzle": str(sizzle_path) if sizzle_path.exists() else None,
        "ken_burns": bool(ken_burns),
    }
