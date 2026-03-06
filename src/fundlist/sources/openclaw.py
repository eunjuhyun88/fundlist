from __future__ import annotations

from typing import List, Optional

from ..http import http_get_json
from ..models import Item, to_item


def collect_openclaw_github(
    repo: str,
    per_type: int = 20,
    github_token: Optional[str] = None,
) -> List[Item]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "fundlist-agent",
    }
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    owner_repo = repo.strip()
    base = f"https://api.github.com/repos/{owner_repo}"
    out: List[Item] = []

    releases = http_get_json(f"{base}/releases?per_page={per_type}", headers=headers)
    if isinstance(releases, list):
        for release in releases:
            out.append(
                to_item(
                    source="github_openclaw",
                    category="release",
                    symbol=owner_repo,
                    title=release.get("name") or release.get("tag_name") or "Release",
                    url=release.get("html_url") or f"https://github.com/{owner_repo}/releases",
                    published_at=release.get("published_at") or release.get("created_at"),
                    payload=release,
                )
            )

    issues = http_get_json(
        f"{base}/issues?state=all&sort=updated&direction=desc&per_page={per_type}",
        headers=headers,
    )
    if isinstance(issues, list):
        for issue in issues:
            if "pull_request" in issue:
                continue
            out.append(
                to_item(
                    source="github_openclaw",
                    category="issue",
                    symbol=owner_repo,
                    title=issue.get("title", "Issue"),
                    url=issue.get("html_url") or f"https://github.com/{owner_repo}/issues",
                    published_at=issue.get("updated_at") or issue.get("created_at"),
                    payload=issue,
                )
            )

    commits = http_get_json(f"{base}/commits?per_page={per_type}", headers=headers)
    if isinstance(commits, list):
        for commit_data in commits:
            commit = commit_data.get("commit", {})
            author = commit.get("author", {}) if isinstance(commit, dict) else {}
            title = commit.get("message", "Commit").splitlines()[0]
            out.append(
                to_item(
                    source="github_openclaw",
                    category="commit",
                    symbol=owner_repo,
                    title=title,
                    url=commit_data.get("html_url") or f"https://github.com/{owner_repo}/commits",
                    published_at=author.get("date"),
                    payload=commit_data,
                )
            )

    return out

