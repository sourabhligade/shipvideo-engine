from github import Github
import os
from typing import Any, Dict, Optional


def _accuracy_note(meta: Optional[Dict[str, Any]]) -> str:
    if not meta:
        return ""
    lines = []
    if "proven_clicks" in meta:
        lines.append(f"- Proven clicks: **{meta.get('proven_clicks', 0)}**")
    if "failed_clicks" in meta:
        lines.append(f"- Skipped no-op clicks: **{meta.get('failed_clicks', 0)}**")
    if "accuracy_ok" in meta:
        lines.append(
            f"- Accuracy gate: **{'pass' if meta.get('accuracy_ok') else 'needs review'}**"
        )
    if meta.get("reasons"):
        reasons = meta.get("reasons") or []
        if reasons:
            lines.append("- Review reasons: " + ", ".join(f"`{r}`" for r in reasons[:8]))
    if not lines:
        return ""
    return "\n".join(["", "### Capture accuracy", *lines])


def comment_on_pr(
    repo_full_name: str,
    pr_number: int,
    video_url: str = None,
    error_message: str = None,
    extra_note: str = None,
    accuracy: Optional[Dict[str, Any]] = None,
):
    try:
        token = os.getenv("GITHUB_TOKEN")
        if not token:
            raise ValueError("GITHUB_TOKEN not set in .env")
        g = Github(token)
        repo = g.get_repo(repo_full_name)
        pr = repo.get_pull(pr_number)

        if video_url:
            comment_text = f"**Auto-generated demo video for PR #{pr_number}**\n\n{video_url}"
            comment_text += _accuracy_note(accuracy)
            if extra_note:
                comment_text += f"\n\n---\n{extra_note}"
        elif error_message:
            comment_text = error_message
            comment_text += _accuracy_note(accuracy)
        else:
            raise ValueError("Either video_url or error_message must be provided")

        pr.create_issue_comment(comment_text)
        if video_url:
            print(f"[webhook] comment posted video_url={video_url[:60]}...", flush=True)
        else:
            print("[webhook] comment posted error_message", flush=True)

    except Exception as e:
        print(f"[webhook] comment failed: {e}", flush=True)
        raise e
