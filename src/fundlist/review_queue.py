from __future__ import annotations

import argparse
import sqlite3
from typing import Dict, List

from .submission_finder import SubmissionStore


def _failure_rows(store: SubmissionStore, *, limit: int) -> List[Dict[str, object]]:
    grouped: Dict[str, sqlite3.Row] = {}
    for row in store.list_scan_failures(limit=max(limit * 4, 40), status="pending"):
        key = str(row["seed_url"] or "").strip()
        if not key or key in grouped:
            continue
        grouped[key] = row
        if len(grouped) >= limit:
            break

    out: List[Dict[str, object]] = []
    for row in grouped.values():
        out.append(
            {
                "queue_type": "scan_failure",
                "review_reason": f"scan_{str(row['stage'] or '').strip().lower()}_failed",
                "org_name": str(row["org_name_hint"] or "").strip(),
                "status": "pending_failure",
                "priority_score": 999,
                "official_page": str(row["seed_url"] or "").strip(),
                "submission_url": "",
                "seed_source": str(row["seed_source"] or "").strip(),
                "error_type": str(row["error_type"] or "").strip(),
                "error_message": str(row["error_message"] or "").strip(),
                "last_seen_at": str(row["last_attempted_at"] or "").strip(),
            }
        )
    return out


def _target_rows(store: SubmissionStore, *, limit: int) -> List[Dict[str, object]]:
    store.conn.row_factory = sqlite3.Row
    cur = store.conn.execute(
        """
        SELECT org_name, org_type, domain, source_url, submission_url, submission_type,
               status, deadline_text, deadline_date, score, last_checked_at
        FROM submission_targets
        WHERE submission_type = 'unknown'
           OR status = 'unknown'
           OR (status = 'deadline' AND trim(deadline_date) = '' AND trim(deadline_text) = '')
        ORDER BY
          CASE
            WHEN status = 'unknown' THEN 0
            WHEN submission_type = 'unknown' THEN 1
            ELSE 2
          END,
          score DESC,
          last_checked_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = list(cur.fetchall())
    out: List[Dict[str, object]] = []
    for row in rows:
        reason = "needs_manual_review"
        if str(row["status"] or "").strip().lower() == "unknown":
            reason = "unknown_status"
        elif str(row["submission_type"] or "").strip().lower() == "unknown":
            reason = "unknown_submission_type"
        elif str(row["status"] or "").strip().lower() == "deadline":
            reason = "deadline_missing_date"
        out.append(
            {
                "queue_type": "target_review",
                "review_reason": reason,
                "org_name": str(row["org_name"] or "").strip(),
                "status": str(row["status"] or "").strip(),
                "priority_score": int(row["score"] or 0),
                "official_page": str(row["source_url"] or "").strip(),
                "submission_url": str(row["submission_url"] or "").strip(),
                "seed_source": str(row["org_type"] or "").strip(),
                "error_type": "",
                "error_message": str(row["deadline_text"] or "").strip(),
                "last_seen_at": str(row["last_checked_at"] or "").strip(),
            }
        )
    return out


def list_review_queue(store: SubmissionStore, *, limit: int = 40) -> List[Dict[str, object]]:
    failures = _failure_rows(store, limit=limit)
    targets = _target_rows(store, limit=limit)
    merged = failures + targets
    merged.sort(
        key=lambda row: (
            1 if str(row["queue_type"]) == "scan_failure" else 0,
            int(row["priority_score"] or 0),
            str(row["last_seen_at"] or ""),
            str(row["org_name"] or ""),
        ),
        reverse=True,
    )
    return merged[:limit]


def review_queue_command(args: argparse.Namespace) -> int:
    store = SubmissionStore(args.db)
    rows = list_review_queue(store, limit=args.limit)
    if not rows:
        print("(no review items)")
        return 0

    print(
        "queue_type\treview_reason\torg_name\tstatus\tpriority_score\tofficial_page\tsubmission_url\tseed_source\terror_type\tlast_seen_at\tdetail"
    )
    for row in rows:
        print(
            "\t".join(
                [
                    str(row["queue_type"]),
                    str(row["review_reason"]),
                    str(row["org_name"]),
                    str(row["status"]),
                    str(row["priority_score"]),
                    str(row["official_page"]),
                    str(row["submission_url"]),
                    str(row["seed_source"]),
                    str(row["error_type"]),
                    str(row["last_seen_at"]),
                    str(row["error_message"]),
                ]
            )
        )
    return 0
