from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse


SHIPVIDEO_AUDIT_MAX_JOURNEY_STEPS = 12
SHIPVIDEO_AUDIT_MAX_CANDIDATES = 40
SHIPVIDEO_AUDIT_DWELL_MS = 900
# Short dwell after injecting a highlight before focus screenshot
SHIPVIDEO_FOCUS_DWELL_MS = 250


@dataclass
class JourneyStep:
    index: int
    action: str
    url: str
    title: str = ""
    label: str = ""
    subtitle: str = ""
    screenshot_path: str = ""
    duration_sec: float = 2.8
    # Click-target geometry in viewport pixels (x, y, w, h)
    click_bbox: Optional[Dict[str, float]] = None
    # proven | url_changed | same_page | unproven | failed
    proof_status: str = ""
    # focus = pre-click highlight frame; result = post-nav; capture = generic
    frame_role: str = "result"
    # Optional path of a zoomed companion frame
    zoom_path: str = ""


@dataclass
class JourneyPlan:
    start_url: str
    steps: List[JourneyStep] = field(default_factory=list)
    end_reached: bool = False
    end_reason: str = ""
    proven_clicks: int = 0
    failed_clicks: int = 0


_CTA_RE = re.compile(
    r"\b(get\s*started|sign\s*up|try\s*(it|free|now)?|start\s*(free|now|trial)?|"
    r"learn\s*more|see\s*(more|demo|how)|explore|continue|next|submit|"
    r"buy\s*now|shop|pricing|features|docs|documentation|product|"
    r"watch\s*demo|request\s*demo|book\s*a?\s*demo|contact|download|"
    r"create|add|new|open|view|settings|dashboard|home)\b",
    re.I,
)

_SKIP_RE = re.compile(
    r"\b(login|log\s*in|sign\s*in|cart|cookie|privacy|terms|careers|"
    r"twitter|linkedin|facebook|instagram|youtube|github\.com/login|"
    r"accept\s*all|reject\s*all|manage\s*cookies)\b",
    re.I,
)


def _same_site(base: str, href: str) -> bool:
    try:
        b = urlparse(base)
        h = urlparse(href)
        if not h.netloc:
            return True
        return h.netloc.lower().replace("www.", "") == b.netloc.lower().replace("www.", "")
    except Exception:
        return False


def _normalize_url(base: str, href: str) -> str:
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return ""
    full = urljoin(base, href)
    parsed = urlparse(full)
    if parsed.scheme not in ("http", "https"):
        return ""
    return full.split("#")[0]


def score_candidate(
    text: str,
    href: str,
    role: str = "",
    *,
    testid: str = "",
    aria: str = "",
    bbox: Optional[Dict[str, float]] = None,
    viewport: tuple[int, int] = (1280, 720),
) -> int:
    blob = f"{text} {href} {role} {testid} {aria}".strip()
    if not blob:
        return 0
    if _SKIP_RE.search(blob):
        return -50
    score = 0
    if _CTA_RE.search(blob):
        score += 40
    if testid:
        score += 25
    if aria:
        score += 8
    if role in ("button", "link"):
        score += 5
    if role == "button":
        score += 3
    words = len(text.split())
    if 1 <= words <= 5:
        score += 10
    elif words <= 10:
        score += 4
    if href and not href.startswith("http"):
        score += 3
    # Prefer mid-viewport targets (hero/CTA) over footer/cookie chrome
    if isinstance(bbox, dict):
        try:
            cx = float(bbox.get("x", 0)) + float(bbox.get("w", 0)) / 2.0
            cy = float(bbox.get("y", 0)) + float(bbox.get("h", 0)) / 2.0
            vw, vh = float(viewport[0]), float(viewport[1])
            if vw > 0 and vh > 0:
                dx = abs(cx - vw / 2.0) / vw
                dy = abs(cy - vh * 0.4) / vh  # slightly above center (hero)
                dist = (dx * dx + dy * dy) ** 0.5
                score += int(max(0, 18 * (1.0 - min(1.0, dist * 1.6))))
                # Deprioritize very bottom chrome
                if cy > vh * 0.88:
                    score -= 20
        except (TypeError, ValueError):
            pass
    return score


def pick_next_targets(
    candidates: List[Dict[str, Any]],
    *,
    current_url: str,
    visited: set[str],
    limit: int = 3,
) -> List[Dict[str, Any]]:
    ranked: List[tuple[int, Dict[str, Any]]] = []
    for c in candidates[:SHIPVIDEO_AUDIT_MAX_CANDIDATES]:
        text = str(c.get("text") or "").strip()
        href = str(c.get("href") or "").strip()
        role = str(c.get("role") or "").strip()
        testid = str(c.get("testid") or "").strip()
        aria = str(c.get("aria") or "").strip()
        full = _normalize_url(current_url, href) if href else ""
        if full and (full in visited or not _same_site(current_url, full)):
            continue
        bbox = c.get("bbox") if isinstance(c.get("bbox"), dict) else None
        sc = score_candidate(
            text, href or full, role, testid=testid, aria=aria, bbox=bbox
        )
        if sc <= 0 and not text and not testid:
            continue
        ranked.append(
            (
                sc,
                {
                    **c,
                    "resolved_url": full,
                    "text": text or aria or testid,
                    "testid": testid,
                    "aria": aria,
                },
            )
        )
    ranked.sort(key=lambda x: x[0], reverse=True)
    out: List[Dict[str, Any]] = []
    seen_keys: set[str] = set()
    for sc, item in ranked:
        key = (
            (item.get("resolved_url") or "")
            + "|"
            + (item.get("text") or "").casefold()
            + "|"
            + (item.get("testid") or "")
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def narrate_step(step: JourneyStep, *, is_first: bool, is_last: bool) -> str:
    title = (step.title or "this page").strip()
    label = (step.label or "").strip()
    role = (step.frame_role or "result").strip()
    if role == "focus" and label:
        return f"We focus on “{label}” — the next control to click."
    if is_first:
        return f"We open the starting link and land on {title}."
    if step.action == "click" and label:
        if is_last:
            return f"We click “{label}” and reach the end of the journey on {title}."
        return f"Next, we click “{label}” and arrive at {title}."
    if step.action == "goto":
        if is_last:
            return f"We navigate to {title} — end of the recorded path."
        return f"We navigate onward to {title}."
    if is_last:
        return f"Journey complete on {title}."
    return f"We capture the state of {title}."


def build_subtitles(steps: List[JourneyStep], seconds_per_frame: float = 2.8) -> List[Dict[str, Any]]:
    cues: List[Dict[str, Any]] = []
    t = 0.0
    n = len(steps)
    for i, step in enumerate(steps):
        dur = float(step.duration_sec or seconds_per_frame)
        text = step.subtitle or narrate_step(
            step, is_first=(i == 0), is_last=(i == n - 1)
        )
        cues.append(
            {
                "index": i + 1,
                "start": t,
                "end": t + dur,
                "text": text,
            }
        )
        t += dur
    return cues


def page_fingerprint(url: str, title: str, body_sample: str = "") -> str:
    """Compact fingerprint for post-click change detection."""
    path = ""
    try:
        path = urlparse(url).path or "/"
    except Exception:
        path = url or ""
    sample = re.sub(r"\s+", " ", (body_sample or "")[:400]).strip().casefold()
    return f"{path}|{(title or '').strip().casefold()}|{sample}"
