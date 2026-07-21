from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from app.product.pipeline import run_link_to_video, video_path_for_job


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / "data" / "jobs"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Wall-clock deadline for a single product job (seconds). Env override: JOB_MAX_SECONDS.
DEFAULT_JOB_MAX_SECONDS = 900

_lock = threading.Lock()
_jobs: Dict[str, Dict[str, Any]] = {}


def job_max_seconds() -> int:
    raw = os.getenv("JOB_MAX_SECONDS", "").strip()
    if raw:
        try:
            return max(30, int(raw))
        except ValueError:
            pass
    return DEFAULT_JOB_MAX_SECONDS


def _job_dir(job_id: str) -> Path:
    d = DATA_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _persist(job: Dict[str, Any]) -> None:
    path = _job_dir(job["id"]) / "job.json"
    path.write_text(json.dumps(job, indent=2, default=str), encoding="utf-8")


def _mark_stale_if_needed(job: Dict[str, Any]) -> Dict[str, Any]:
    """If a job is still 'running' past its deadline, mark failed (stale recovery)."""
    if str(job.get("status") or "") != "running":
        return job
    deadline = job.get("deadline_at")
    if deadline is None:
        created = float(job.get("started_at") or job.get("created_at") or 0)
        max_s = int(job.get("max_seconds") or job_max_seconds())
        deadline = created + max_s if created else None
    if deadline is None:
        return job
    if time.time() <= float(deadline):
        return job
    job = dict(job)
    job.update(
        {
            "status": "failed",
            "stage": "failed",
            "error": "job_deadline_exceeded",
            "updated_at": time.time(),
        }
    )
    with _lock:
        if job["id"] in _jobs:
            _jobs[job["id"]].update(job)
    _persist(job)
    return job


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        if job_id in _jobs:
            job = dict(_jobs[job_id])
        else:
            job = None
    if job is None:
        path = DATA_DIR / job_id / "job.json"
        if path.exists():
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
    if job is None:
        return None
    return _mark_stale_if_needed(job)


def list_jobs(limit: int = 20) -> list[Dict[str, Any]]:
    items = []
    for p in sorted(DATA_DIR.glob("*/job.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            job = json.loads(p.read_text(encoding="utf-8"))
            items.append(_mark_stale_if_needed(job))
        except Exception:
            continue
        if len(items) >= limit:
            break
    return items


def create_job(url: str, *, max_steps: int = 10, use_azure_subtitles: bool = True) -> Dict[str, Any]:
    job_id = uuid.uuid4().hex[:12]
    max_seconds = job_max_seconds()
    now = time.time()
    job = {
        "id": job_id,
        "url": url.strip(),
        "status": "queued",
        "stage": "queued",
        "max_steps": max_steps,
        "use_azure_subtitles": use_azure_subtitles,
        "max_seconds": max_seconds,
        "created_at": now,
        "started_at": None,
        "deadline_at": None,
        "updated_at": now,
        "error": None,
        "result": None,
        "log": [],
        "headless": True,
    }
    with _lock:
        _jobs[job_id] = job
    _persist(job)

    thread = threading.Thread(target=_run_job, args=(job_id,), daemon=True)
    thread.start()
    return dict(job)


def _update(job_id: str, **fields: Any) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job.update(fields)
        job["updated_at"] = time.time()
        snap = dict(job)
    _persist(snap)


def _append_log(job_id: str, stage: str, extra: Dict[str, Any]) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job.setdefault("log", []).append({"stage": stage, **extra, "ts": time.time()})
        job["stage"] = stage
        job["updated_at"] = time.time()
        snap = dict(job)
    _persist(snap)


def _run_job(job_id: str) -> None:
    job = get_job(job_id)
    if not job:
        return
    max_seconds = int(job.get("max_seconds") or job_max_seconds())
    started = time.time()
    deadline = started + max_seconds
    _update(
        job_id,
        status="running",
        stage="starting",
        headless=True,
        started_at=started,
        deadline_at=deadline,
        max_seconds=max_seconds,
    )

    def on_progress(stage: str, extra: Dict[str, Any]) -> None:
        # Soft deadline check on progress ticks
        if time.time() > deadline:
            raise TimeoutError(
                f"job_deadline_exceeded after {max_seconds}s "
                f"(job_id={job_id})"
            )
        _append_log(job_id, stage, extra)

    result_box: Dict[str, Any] = {"error": None, "result": None}

    def worker() -> None:
        try:
            result_box["result"] = run_link_to_video(
                job["url"],
                _job_dir(job_id),
                job_id=job_id,
                max_steps=int(job.get("max_steps") or 10),
                use_azure_subtitles=bool(job.get("use_azure_subtitles", True)),
                on_progress=on_progress,
            )
        except Exception as e:
            result_box["error"] = e

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout=max_seconds + 5)

    if t.is_alive():
        _update(
            job_id,
            status="failed",
            stage="failed",
            error=f"job_deadline_exceeded after {max_seconds}s",
        )
        return

    if result_box["error"] is not None:
        e = result_box["error"]
        msg = str(e)
        if "job_deadline_exceeded" in msg or isinstance(e, TimeoutError):
            err = f"job_deadline_exceeded after {max_seconds}s"
        else:
            err = f"{type(e).__name__}: {e}"
        _update(job_id, status="failed", stage="failed", error=err)
        return

    result = result_box["result"] or {}
    if not result.get("ok"):
        _update(
            job_id,
            status="failed",
            stage="failed",
            error=result.get("error") or "unknown",
            result=result,
        )
        return
    result["video_url"] = f"/api/jobs/{job_id}/video"
    result["srt_url"] = f"/api/jobs/{job_id}/srt"
    result["video_path"] = str(video_path_for_job(_job_dir(job_id), job_id))
    _update(job_id, status="done", stage="done", result=result, error=None, headless=True)
