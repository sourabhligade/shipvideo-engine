"""YouTube description with chapter timestamps."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from app.render_effects.chapters import _yt_ts, chapter_entries_from_steps, write_youtube_chapters_text

PathLike = Union[str, Path]


def build_youtube_description(
    *,
    title: str = "Product demo",
    steps: Optional[Sequence[Any]] = None,
    chapters: Optional[Sequence[Dict[str, Any]]] = None,
    hold_seconds: Optional[Sequence[float]] = None,
    start_url: str = "",
    proven_clicks: int = 0,
    extra_lines: Optional[Sequence[str]] = None,
) -> str:
    title = (title or "Product demo").strip()
    lines: List[str] = [
        title,
        "",
        "Auto-generated product walkthrough — real UI capture with proof-gated clicks.",
    ]
    if start_url:
        lines.extend(["", f"Start URL: {start_url}"])
    if proven_clicks:
        lines.extend(["", f"Proven interactions: {proven_clicks}"])

    ch = list(chapters or [])
    if not ch and steps is not None:
        ch = chapter_entries_from_steps(steps, hold_seconds=hold_seconds)

    if ch:
        lines.extend(["", "Chapters:"])
        for i, c in enumerate(ch):
            start = 0.0 if i == 0 else float(c["start"])
            lines.append(f"{_yt_ts(start)} {c.get('title') or f'Chapter {i + 1}'}")

    lines.extend(
        [
            "",
            "Generated with proof-gated browser automation by ShipVideo.",
        ]
    )
    if extra_lines:
        lines.extend(["", *extra_lines])
    return "\n".join(lines) + "\n"


def write_youtube_description(
    path: PathLike,
    **kwargs: Any,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = build_youtube_description(**kwargs)
    path.write_text(text, encoding="utf-8")
    # Also drop chapters-only sidecar for easy paste
    ch = kwargs.get("chapters")
    steps = kwargs.get("steps")
    if ch:
        write_youtube_chapters_text(ch, path.with_suffix(".chapters.txt"))
    elif steps is not None:
        entries = chapter_entries_from_steps(
            steps, hold_seconds=kwargs.get("hold_seconds")
        )
        write_youtube_chapters_text(entries, path.with_suffix(".chapters.txt"))
    return path
