"""Chapter markers (WebVTT) and step SRT from journey labels."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

PathLike = Union[str, Path]


def _ts(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms >= 1000:
        s += 1
        ms = 0
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _srt_ts(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms >= 1000:
        s += 1
        ms = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _yt_ts(seconds: float) -> str:
    """YouTube chapter timestamp (M:SS or H:MM:SS)."""
    if seconds < 0:
        seconds = 0.0
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def chapter_entries_from_steps(
    steps: Sequence[Any],
    *,
    hold_seconds: Optional[Sequence[float]] = None,
    default_hold: float = 2.8,
) -> List[Dict[str, Any]]:
    """Build chapter list. Prefer focus/result milestones over every frame."""
    entries: List[Dict[str, Any]] = []
    t = 0.0
    for i, step in enumerate(steps):
        if hold_seconds is not None and i < len(hold_seconds):
            dur = float(hold_seconds[i])
        else:
            dur = float(getattr(step, "duration_sec", 0) or default_hold)
        role = str(getattr(step, "frame_role", "") or "")
        label = str(getattr(step, "label", "") or "").strip()
        title = str(getattr(step, "title", "") or "").strip()
        action = str(getattr(step, "action", "") or "").strip()
        # Chapter on: first step, focus frames, or labeled clicks
        is_chapter = (
            i == 0
            or role == "focus"
            or (action == "click" and label)
            or (i == len(steps) - 1)
        )
        if is_chapter:
            if role == "focus" and label:
                name = f"Click: {label}"
            elif label:
                name = label
            elif title:
                name = title[:60]
            else:
                name = f"Step {i + 1}"
            # Avoid duplicate consecutive chapter titles at same second
            if entries and abs(entries[-1]["start"] - t) < 0.05:
                entries[-1]["title"] = name
            else:
                entries.append({"index": len(entries) + 1, "start": t, "title": name})
        t += max(0.05, dur)
    # Ensure first starts at 0
    if entries:
        entries[0]["start"] = 0.0
    return entries


def write_webvtt_chapters(
    chapters: Sequence[Dict[str, Any]],
    path: PathLike,
    *,
    total_duration: float = 0.0,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["WEBVTT", ""]
    n = len(chapters)
    for i, ch in enumerate(chapters):
        start = float(ch["start"])
        if i + 1 < n:
            end = float(chapters[i + 1]["start"])
        else:
            end = max(start + 1.0, float(total_duration or start + 10.0))
        title = str(ch.get("title") or f"Chapter {i + 1}").replace("\n", " ")
        lines.append(f"{_ts(start)} --> {_ts(end)}")
        lines.append(title)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_youtube_chapters_text(
    chapters: Sequence[Dict[str, Any]],
    path: PathLike,
) -> Path:
    """Plain text block for YouTube description (00:00 Title)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    for i, ch in enumerate(chapters):
        start = float(ch["start"])
        # YouTube requires first chapter at 0:00
        if i == 0:
            start = 0.0
        title = str(ch.get("title") or f"Chapter {i + 1}").replace("\n", " ")
        lines.append(f"{_yt_ts(start)} {title}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
