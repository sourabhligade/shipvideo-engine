from github import Github
import os
from typing import List, Optional, Sequence


def format_not_sendable_comment(
    pr_number: int,
    reasons: Optional[Sequence[str]] = None,
    *,
    video_url: Optional[str] = None,
) -> str:
    """User-facing copy when a video file exists but is not publishable."""
    reason_list: List[str] = [str(r) for r in (reasons or []) if str(r).strip()]
    bullets = "\n".join(f"- `{r}`" for r in reason_list) or "- (no detailed reasons)"
    body = (
        f"**Demo not publishable for PR #{pr_number}**\n\n"
        "A capture/render ran, but the result did **not** pass the sendable gate "
        f"(`video_usable=false`).\n\n"
        f"**Reasons**\n{bullets}\n"
    )
    if video_url:
        body += (
            f"\nInternal artifact (not approved for demo): {video_url}\n"
        )
    return body


def comment_on_pr(
    repo_full_name: str,
    pr_number: int,
    video_url: str = None,
    error_message: str = None,
    extra_note: str = None,
    *,
    sendable: Optional[bool] = None,
    sendable_reasons: Optional[Sequence[str]] = None,
):
    try:
        token = os.getenv("GITHUB_TOKEN")
        if not token:
            raise ValueError("GITHUB_TOKEN not set in .env")
        g = Github(token)
        repo = g.get_repo(repo_full_name)
        pr = repo.get_pull(pr_number)

        if sendable is False:
            comment_text = format_not_sendable_comment(
                pr_number,
                sendable_reasons,
                video_url=video_url,
            )
            if extra_note:
                comment_text += f"\n---\n{extra_note}"
        elif video_url:
            comment_text = f"**Auto-generated demo video for PR #{pr_number}**\n\n{video_url}"
            if extra_note:
                comment_text += f"\n\n---\n{extra_note}"
        elif error_message:
            comment_text = error_message
        else:
            raise ValueError("Either video_url or error_message must be provided")

        pr.create_issue_comment(comment_text)
        if sendable is False:
            print("[webhook] comment posted not-sendable", flush=True)
        elif video_url:
            print(f"[webhook] comment posted video_url={video_url[:60]}...", flush=True)
        else:
            print("[webhook] comment posted error_message", flush=True)

    except Exception as e:
        print(f"[webhook] comment failed: {e}", flush=True)
        raise e
