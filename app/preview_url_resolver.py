from typing import Optional
from app.config import load_config
from observability import pipeline_step


@pipeline_step("preview_lookup")
def get_preview_url(
    pr_number: Optional[int] = None,
    branch: Optional[str] = None,
) -> str:
    config = load_config()
    template = config.get("preview_url_template")

    if not template:
        raise ValueError(
            "preview_url_template not configured in project_config.json. "
            "Set it to your app URL. For PR previews use placeholders: "
            "{pr_number} and/or {branch_slug}"
        )

    url = template
    if pr_number is not None and "{pr_number}" in url:
        url = url.replace("{pr_number}", str(pr_number))
    if branch is not None and "{branch_slug}" in url:
        slug = branch.strip().replace("/", "-")
        url = url.replace("{branch_slug}", slug)

    print(f"[preview] url={url}", flush=True)
    return url


@pipeline_step("preview_ready")
def wait_for_preview_ready(
    url: str,
    timeout_seconds: Optional[int] = None,
    poll_interval_seconds: Optional[int] = None,
) -> bool:
    import time
    import urllib.request
    import urllib.error
    import ssl

    config = load_config()
    timeout = timeout_seconds if timeout_seconds is not None else config.get("preview_ready_timeout_seconds", 300)
    interval = poll_interval_seconds if poll_interval_seconds is not None else config.get("preview_ready_poll_interval_seconds", 15)

    if timeout <= 0:
        print("[preview] skip ready check timeout=0", flush=True)
        return True

    deadline = time.monotonic() + timeout
    next_log = time.monotonic()
    last_err = None

    ssl_context = None
    try:
        import certifi                

        ssl_context = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ssl_context = None

    def _probe(method: str) -> bool:
        req = urllib.request.Request(url, method=method)
        req.add_header("User-Agent", "ShipVideo-Engine/1.0")
        if method == "GET":
            req.add_header("Range", "bytes=0-0")
        open_kwargs = {"timeout": 15}
        if ssl_context is not None:
            open_kwargs["context"] = ssl_context
        with urllib.request.urlopen(req, **open_kwargs) as resp:
            return 200 <= int(getattr(resp, "status", 0) or 0) < 400

    while time.monotonic() < deadline:
        last_err = None
        for method in ("GET", "HEAD"):
            try:
                if _probe(method):
                    print(f"[preview] ready method={method} url={url}", flush=True)
                    return True
            except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
                last_err = e
                continue
        if last_err is not None and time.monotonic() >= next_log:
            print(f"[preview] waiting for ready error={last_err!r}", flush=True)
            next_log = time.monotonic() + 30
        time.sleep(interval)

    print(
        f"[preview] not ready after timeout={timeout}s url={url} "
        f"(polled every {interval}s with GET then HEAD; "
        f"last_error={last_err!r})",
        flush=True,
    )
    return False