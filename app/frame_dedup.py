"""Frame deduplication for demo screenshots.

Demo videos must keep frames that show real UI state changes, including small
localized ones (button enablement, toast, badge, focus ring). True duplicates
(identical captures / static re-shots) should still be dropped.

A mean-only similarity threshold is unsafe: a 100x40 button on a 1366x900
viewport is ~0.3% of pixels, so mean abs-diff stays tiny even when that
region is a meaningful product change. This module requires *global* and
*local* agreement before treating two frames as duplicates.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple, Union

from PIL import Image, ImageChops, ImageStat

PathLike = Union[str, Path]

# Comparison is done on a fixed thumbnail so cost is stable across viewports.
COMPARE_SIZE: Tuple[int, int] = (320, 180)
BLOCK: int = 16

# Mean abs-diff (0-255) after resize. Loose alone; tightened by local signals.
MEAN_ABS_DIFF_MAX: float = 2.0
# Max mean abs-diff inside any BLOCK×BLOCK tile. Catches localized UI edits.
MAX_BLOCK_ABS_DIFF_MAX: float = 12.0
# Fraction of pixels whose |diff| exceeds PIXEL_CHANGE_THRESHOLD.
CHANGED_PIXEL_FRACTION_MAX: float = 0.004
PIXEL_CHANGE_THRESHOLD: int = 18


@dataclass(frozen=True)
class FrameDiffStats:
    mean_abs_diff: float
    max_block_abs_diff: float
    changed_pixel_fraction: float

    @property
    def is_duplicate(self) -> bool:
        """True only when global *and* local signals all say "no real change"."""
        return (
            self.mean_abs_diff <= MEAN_ABS_DIFF_MAX
            and self.max_block_abs_diff <= MAX_BLOCK_ABS_DIFF_MAX
            and self.changed_pixel_fraction <= CHANGED_PIXEL_FRACTION_MAX
        )


def _load_gray(path: PathLike, size: Tuple[int, int] = COMPARE_SIZE) -> Image.Image:
    img = Image.open(path).convert("L")
    if img.size != size:
        img = img.resize(size, Image.Resampling.BILINEAR)
    return img


def compute_frame_diff(
    path_a: PathLike,
    path_b: PathLike,
    *,
    size: Tuple[int, int] = COMPARE_SIZE,
    block: int = BLOCK,
    pixel_change_threshold: int = PIXEL_CHANGE_THRESHOLD,
) -> FrameDiffStats:
    """Compute multi-signal visual difference between two frames."""
    a = _load_gray(path_a, size=size)
    b = _load_gray(path_b, size=size)
    if a.size != b.size:
        b = b.resize(a.size, Image.Resampling.BILINEAR)

    diff = ImageChops.difference(a, b)
    stat = ImageStat.Stat(diff)
    mean_abs = float(stat.mean[0])

    w, h = diff.size
    px = diff.load()
    changed = 0
    total = w * h
    max_block = 0.0
    thr = int(pixel_change_threshold)

    for y0 in range(0, h, block):
        for x0 in range(0, w, block):
            x1 = min(x0 + block, w)
            y1 = min(y0 + block, h)
            block_sum = 0
            n = 0
            for y in range(y0, y1):
                for x in range(x0, x1):
                    v = int(px[x, y])
                    block_sum += v
                    n += 1
                    if v > thr:
                        changed += 1
            if n:
                max_block = max(max_block, block_sum / n)

    fraction = (changed / total) if total else 0.0
    return FrameDiffStats(
        mean_abs_diff=mean_abs,
        max_block_abs_diff=max_block,
        changed_pixel_fraction=fraction,
    )


def frames_are_duplicates(
    path_a: PathLike,
    path_b: PathLike,
    *,
    mean_abs_diff_max: float = MEAN_ABS_DIFF_MAX,
    max_block_abs_diff_max: float = MAX_BLOCK_ABS_DIFF_MAX,
    changed_pixel_fraction_max: float = CHANGED_PIXEL_FRACTION_MAX,
    pixel_change_threshold: int = PIXEL_CHANGE_THRESHOLD,
) -> bool:
    """Return True if frames should be treated as the same UI state."""
    stats = compute_frame_diff(
        path_a,
        path_b,
        pixel_change_threshold=pixel_change_threshold,
    )
    return (
        stats.mean_abs_diff <= mean_abs_diff_max
        and stats.max_block_abs_diff <= max_block_abs_diff_max
        and stats.changed_pixel_fraction <= changed_pixel_fraction_max
    )


def mean_only_are_duplicates(
    path_a: PathLike,
    path_b: PathLike,
    *,
    mean_abs_diff_max: float = MEAN_ABS_DIFF_MAX,
) -> bool:
    """Legacy mean-only check — false-negatives small localized UI changes.

    Kept for regression tests that prove multi-signal gating is required.
    """
    stats = compute_frame_diff(path_a, path_b)
    return stats.mean_abs_diff <= mean_abs_diff_max


def dedupe_frames(
    paths: Sequence[PathLike],
    *,
    consecutive_only: bool = True,
) -> List[str]:
    """Drop true-duplicate frames while keeping small UI-change transitions.

    When ``consecutive_only`` is True (default), a frame is dropped only if it
    is a duplicate of the immediately previous *kept* frame. Non-consecutive
    mode drops a frame if it matches any earlier kept frame.
    """
    kept: List[str] = []
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            continue
        key = str(path)
        if not kept:
            kept.append(key)
            continue
        compare_against: Iterable[str]
        if consecutive_only:
            compare_against = (kept[-1],)
        else:
            compare_against = kept
        is_dup = any(frames_are_duplicates(prev, key) for prev in compare_against)
        if not is_dup:
            kept.append(key)
    return kept
