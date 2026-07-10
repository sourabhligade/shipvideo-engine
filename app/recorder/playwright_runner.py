from __future__ import annotations

import json
import logging
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)


_CURSOR_RIPPLE_JS = r"""
(function () {
  if (document.__shipvideoOverlay) return;
  document.__shipvideoOverlay = true;

  var dot = document.createElement('div');
  dot.style.cssText = [
    'position:fixed', 'width:14px', 'height:14px', 'border-radius:50%',
    'background:rgba(220,50,50,0.9)', 'pointer-events:none',
    'z-index:2147483647', 'transform:translate(-50%,-50%)',
    'transition:left 0.04s,top 0.04s', 'box-shadow:0 0 0 3px rgba(220,50,50,0.35)'
  ].join(';');
  document.body.appendChild(dot);

  document.addEventListener('mousemove', function (e) {
    dot.style.left = e.clientX + 'px';
    dot.style.top = e.clientY + 'px';
  }, true);

  var style = document.createElement('style');
  style.textContent = (
    '@keyframes sv-ripple{' +
    '0%{transform:translate(-50%,-50%) scale(1);opacity:0.8}' +
    '100%{transform:translate(-50%,-50%) scale(2.8);opacity:0}}'
  );
  document.head.appendChild(style);

  document.addEventListener('click', function (e) {
    var r = document.createElement('div');
    r.style.cssText = [
      'position:fixed', 'width:36px', 'height:36px', 'border-radius:50%',
      'border:2px solid rgba(220,50,50,0.7)', 'pointer-events:none',
      'z-index:2147483646',
      'left:' + e.clientX + 'px', 'top:' + e.clientY + 'px',
      'animation:sv-ripple 0.38s ease-out forwards'
    ].join(';');
    document.body.appendChild(r);
    setTimeout(function () { r.parentNode && r.parentNode.removeChild(r); }, 420);
  }, true);
}());
"""


def _log(event: str, payload: Dict[str, Any]) -> None:
    print(json.dumps({"event": event, **payload}, ensure_ascii=False), flush=True)


def run_script(
    *,
    script: str,
    base_url: str,
    output_dir: Path,
    timeout_seconds: int = 120,
) -> Dict[str, Any]:
    run_t0 = time.monotonic()
    output_dir.mkdir(parents=True, exist_ok=True)
    video_dir = output_dir / "video_tmp"
    video_dir.mkdir(parents=True, exist_ok=True)

    ns: Dict[str, Any] = {}
    try:
        exec(compile(script, "<generated_demo>", "exec"), ns)
    except SyntaxError as e:
        _log("script_runner.syntax_error", {"error": str(e), "base_url": base_url})
        logger.error(
            "run_script: generated demo script has syntax error",
            extra={
                "operation": "compile_script",
                "base_url": base_url,
                "output_dir": str(output_dir),
                "error": str(e),
                "lineno": getattr(e, "lineno", None),
            },
            exc_info=True,
        )
        return {"success": False, "webm_path": None, "error": f"syntax_error: {e}"}

    run_demo = ns.get("run_demo")
    if not callable(run_demo):
        logger.error(
            "run_script: script did not define run_demo(page, context)",
            extra={
                "operation": "compile_script",
                "base_url": base_url,
                "output_dir": str(output_dir),
                "ns_keys": sorted(str(k) for k in ns.keys() if not str(k).startswith("__"))[:30],
            },
        )
        return {
            "success": False,
            "webm_path": None,
            "error": "script did not define run_demo(page, context)",
        }


    video_path: Optional[str] = None
    error_str: Optional[str] = None
    success = False

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            context = browser.new_context(
                viewport={"width": 1366, "height": 900},
                record_video_dir=str(video_dir),
                record_video_size={"width": 1366, "height": 900},
            )
            page = context.new_page()
            page.add_init_script(_CURSOR_RIPPLE_JS)


            ns["base_url"] = base_url
            ns["output_dir"] = str(output_dir)
            ns["page"] = page
            ns["context"] = context

            try:
                nav_t0 = time.monotonic()
                page.goto(base_url, wait_until="domcontentloaded", timeout=15000)
                nav_duration_sec = time.monotonic() - nav_t0
                _nav_log = logger.warning if nav_duration_sec > 10.0 else logger.debug
                _nav_log(
                    "run_script: page.goto completed",
                    extra={
                        "operation": "playwright_navigation",
                        "base_url": base_url,
                        "output_dir": str(output_dir),
                        "duration_sec": round(nav_duration_sec, 3),
                        "slow": nav_duration_sec > 10.0,
                    },
                )
                _log("script_runner.started", {"base_url": base_url, "nav_duration_sec": round(nav_duration_sec, 3)})

                demo_t0 = time.monotonic()
                run_demo(page, context)
                demo_duration_sec = time.monotonic() - demo_t0
                _demo_log = logger.warning if demo_duration_sec > 90.0 else logger.debug
                _demo_log(
                    "run_script: run_demo completed",
                    extra={
                        "operation": "run_demo",
                        "base_url": base_url,
                        "output_dir": str(output_dir),
                        "duration_sec": round(demo_duration_sec, 3),
                        "slow": demo_duration_sec > 90.0,
                    },
                )

                success = True
                _log("script_runner.completed", {"success": True, "demo_duration_sec": round(demo_duration_sec, 3)})
            except Exception as e:
                error_str = f"{type(e).__name__}: {e}"
                _log(
                    "script_runner.execution_error",
                    {"error": error_str, "base_url": base_url, "traceback": traceback.format_exc()},
                )
                logger.error(
                    "run_script: demo execution failed",
                    extra={
                        "operation": "run_demo",
                        "base_url": base_url,
                        "output_dir": str(output_dir),
                        "error": error_str,
                    },
                    exc_info=True,
                )
            finally:

                try:
                    video_path = page.video.path()
                except Exception as e:
                    logger.debug(
                        "run_script: page.video.path() unavailable",
                        extra={
                            "operation": "video_path_resolve",
                            "base_url": base_url,
                            "output_dir": str(output_dir),
                            "error": f"{type(e).__name__}: {e}",
                        },
                        exc_info=True,
                    )
                context.close()
                browser.close()

    except Exception as e:
        error_str = f"playwright_setup_error: {type(e).__name__}: {e}"
        _log(
            "script_runner.setup_error",
            {"error": error_str, "base_url": base_url, "traceback": traceback.format_exc()},
        )
        logger.error(
            "run_script: playwright setup failed",
            extra={
                "operation": "playwright_setup",
                "base_url": base_url,
                "output_dir": str(output_dir),
                "error": error_str,
            },
            exc_info=True,
        )
        return {"success": False, "webm_path": None, "error": error_str}


    run_duration_sec = time.monotonic() - run_t0

    if video_path and Path(video_path).exists():
        _log("script_runner.video_ready", {"path": video_path, "success": success})
        _done_log = logger.warning if run_duration_sec > 120.0 else logger.debug
        _done_log(
            "run_script: finished with video path",
            extra={
                "operation": "run_script",
                "base_url": base_url,
                "output_dir": str(output_dir),
                "webm_path": video_path,
                "success": success,
                "duration_sec": round(run_duration_sec, 3),
                "bytes": Path(video_path).stat().st_size,
                "slow": run_duration_sec > 120.0,
            },
        )
        if success:
            return {"success": True, "webm_path": video_path, "error": None}

        return {"success": False, "webm_path": video_path, "error": error_str}

    webm_files = sorted(video_dir.glob("*.webm"), key=lambda p: p.stat().st_size, reverse=True)
    if webm_files:
        video_path = str(webm_files[0])
        _log("script_runner.video_found_in_dir", {"path": video_path})
        logger.warning(
            "run_script: primary video path missing; using largest webm in video_dir",
            extra={
                "operation": "video_path_fallback",
                "base_url": base_url,
                "output_dir": str(output_dir),
                "fallback_path": video_path,
                "webm_count": len(webm_files),
                "bytes": webm_files[0].stat().st_size,
                "success": success,
                "duration_sec": round(run_duration_sec, 3),
            },
        )
        if success:
            return {"success": True, "webm_path": video_path, "error": None}
        return {"success": False, "webm_path": video_path, "error": error_str}

    logger.error(
        "run_script: no video produced",
        extra={
            "operation": "run_script",
            "base_url": base_url,
            "output_dir": str(output_dir),
            "error": error_str or "no_video_produced",
            "duration_sec": round(run_duration_sec, 3),
        },
    )
    return {
        "success": False,
        "webm_path": None,
        "error": error_str or "no_video_produced",
    }
