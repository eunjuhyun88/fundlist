from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

from .store import ensure_parent_dir
from .submission_finder import SubmissionStore


CHANGE_TYPES = {
    "",
    "new_opportunity",
    "status_changed",
    "deadline_changed",
    "submission_url_changed",
    "source_url_changed",
    "reopened",
}


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _since_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=max(0, days))).replace(microsecond=0).isoformat()


def _format_change_line(idx: int, row) -> str:
    old_value = str(row["old_value"] or "-").strip() or "-"
    new_value = str(row["new_value"] or "-").strip() or "-"
    url = str(row["submission_url"] or row["source_url"] or "-").strip() or "-"
    return (
        f"{idx}. {row['org_name']} | {row['change_type']} | "
        f"{old_value} -> {new_value} | {url} | {row['detected_at']}"
    )


def _render_changes_report(rows, *, title: str = "Opportunity Changes") -> str:
    lines: List[str] = [f"# {title}", "", f"_Generated at: {now_utc_iso()}_", ""]
    if not rows:
        lines.append("(no changes)")
        return "\n".join(lines).rstrip() + "\n"

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- total_changes: {len(rows)}")
    counts = {}
    for row in rows:
        key = str(row["change_type"])
        counts[key] = counts.get(key, 0) + 1
    lines.append("- by_type: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    lines.append("")
    lines.append("## Recent Changes")
    lines.append("")
    for idx, row in enumerate(rows, start=1):
        lines.append(f"### {idx}. {row['org_name']}")
        lines.append(f"- change_type: {row['change_type']}")
        lines.append(f"- old_value: {row['old_value'] or '-'}")
        lines.append(f"- new_value: {row['new_value'] or '-'}")
        lines.append(f"- source_url: {row['source_url'] or '-'}")
        lines.append(f"- submission_url: {row['submission_url'] or '-'}")
        lines.append(f"- detected_at: {row['detected_at']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def changes_list_command(args: argparse.Namespace) -> int:
    change_type = (args.change_type or "").strip().lower()
    if change_type not in CHANGE_TYPES:
        print(f"[error] invalid change_type: {args.change_type}")
        return 2
    store = SubmissionStore(args.db)
    since = _since_iso(args.since_days) if args.since_days >= 0 else ""
    rows = store.list_changes(limit=args.limit, change_type=change_type, since=since)
    if not rows:
        print("(no changes)")
        return 0
    print("id\tchange_type\torg_name\told_value\tnew_value\tdetected_at\tsubmission_url")
    for row in rows:
        print(
            "\t".join(
                [
                    str(row["id"]),
                    str(row["change_type"]),
                    str(row["org_name"]),
                    str(row["old_value"] or ""),
                    str(row["new_value"] or ""),
                    str(row["detected_at"]),
                    str(row["submission_url"] or row["source_url"] or ""),
                ]
            )
        )
    return 0


def changes_report_command(args: argparse.Namespace) -> int:
    change_type = (args.change_type or "").strip().lower()
    if change_type not in CHANGE_TYPES:
        print(f"[error] invalid change_type: {args.change_type}")
        return 2
    store = SubmissionStore(args.db)
    since = _since_iso(args.since_days) if args.since_days >= 0 else ""
    rows = store.list_changes(limit=args.limit, change_type=change_type, since=since)
    output = Path(args.output).expanduser()
    ensure_parent_dir(str(output))
    title = "Opportunity Changes"
    if change_type:
        title = f"Opportunity Changes ({change_type})"
    output.write_text(_render_changes_report(rows, title=title), encoding="utf-8")
    print(f"[done] changes={len(rows)} report={output}")
    return 0
