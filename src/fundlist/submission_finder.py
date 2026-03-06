from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html import unescape
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .store import ensure_parent_dir


DEFAULT_DISCOVERY_QUERIES = [
    '"pitch us" "venture capital"',
    '"submit your pitch" "venture capital"',
    '"deal submission" "venture capital"',
    '"founder form" "venture capital"',
    '"apply" "seed fund"',
    '"apply" "early stage fund"',
    '"startup application" accelerator',
    '"founder application" accelerator',
    '"batch" accelerator apply',
    '"cohort" accelerator apply',
    '"web3 accelerator" apply',
    '"crypto accelerator" apply',
    '"ai accelerator" apply',
    '"pitch deck" "submit" fund',
    '"we review every pitch" vc',
    '"only way to pitch us"',
    '"no warm intro" vc',
    '"open applications" accelerator',
    '"rolling applications" accelerator',
    '"startup program" apply',
    '"venture studio" apply',
    '"angel syndicate" apply',
    '"submit company" vc',
    '"contact" "pitch us" fund',
    '"investor application" startup',
    '"speedrun" apply',
    '"alliance dao" apply',
    '"yc" apply startup',
    '"seedcamp" apply',
    '"techstars" apply',
    '"web3 grants" apply',
    '"ecosystem grants" "apply"',
    '"foundation grant" "application form"',
    '"developer grant" "applications open"',
    '"startup grant" "rolling applications"',
]

SUBMISSION_PHRASES = [
    "pitch us",
    "submit your pitch",
    "submit your company",
    "deal submission",
    "founder application",
    "application form",
    "startup application",
    "we review every pitch",
    "only way to pitch",
    "apply now",
    "apply here",
    "start your application",
    "grant application",
    "apply for grant",
    "submit grant proposal",
]

STRONG_SUBMISSION_PHRASES = {
    "pitch us",
    "submit your pitch",
    "submit your company",
    "deal submission",
    "founder application",
    "application form",
    "startup application",
    "we review every pitch",
    "only way to pitch",
    "grant application",
    "apply for grant",
    "submit grant proposal",
}

FORM_EMBED_MARKERS = [
    "typeform.com",
    "airtable.com",
    "forms.gle",
    "docs.google.com/forms",
    "hubspot.com",
    "jotform.com",
    "submittable.com",
    "fillout.com",
    "tally.so",
    "notionforms",
]

STRONG_PATH_HINTS = ["apply", "pitch", "submit", "application", "grant", "grants"]
DISCOVERY_PATH_HINTS = STRONG_PATH_HINTS + ["founder", "accelerator", "program", "contact"]

FORM_HOST_MARKERS = [
    "typeform.com",
    "airtable.com",
    "forms.gle",
    "docs.google.com",
    "hubspot.com",
    "jotform.com",
    "submittable.com",
    "fillout.com",
    "tally.so",
    "wkf.ms",
]

CLOSED_MARKERS = [
    "applications closed",
    "application closed",
    "not accepting",
    "closed for applications",
    "currently closed",
    "no longer accepting",
    "no longer accepting responses",
    "this form is no longer accepting responses",
    "submissions closed",
    "applications are now closed",
    "더 이상 응답을 받지 않습니다",
    "신청이 마감되었습니다",
]
ROLLING_MARKERS = [
    "rolling basis",
    "rolling applications",
    "year-round",
    "always open",
    "open applications",
]
DEADLINE_MARKERS = [
    "deadline",
    "apply by",
    "applications due",
    "application due",
    "submissions due",
    "submission due",
    "closes on",
    "applications close",
    "cohort starts",
    "batch starts",
    "마감",
    "지원 마감",
    "신청 마감",
]
MONTH_NAME_TO_NUMBER = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
INTRO_ONLY_MARKERS = [
    "warm intro",
    "by referral",
    "through referral",
    "do not accept unsolicited",
    "we do not accept cold",
    "intro only",
]

REQUIREMENT_MARKERS = [
    ("pitch deck", "deck required"),
    ("deck", "deck requested"),
    ("one-pager", "one-pager requested"),
    ("traction", "traction metrics requested"),
    ("tokenomics", "tokenomics requested"),
    ("demo", "demo requested"),
    ("whitepaper", "whitepaper requested"),
]

NON_TARGET_DOMAINS = {
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "x.com",
    "twitter.com",
    "youtube.com",
    "wikipedia.org",
    "reddit.com",
    "glassdoor.com",
    "indeed.com",
    "wellfound.com",
    "angel.co",
    "techcrunch.com",
    "fortune.com",
    "venturebeat.com",
    "coindesk.com",
    "cointelegraph.com",
    "axios.com",
    "prnewswire.com",
    "businesswire.com",
    "crunchbase.com",
    "finsmes.com",
    "en.wikipedia.org",
    "wikipedia.org",
    "youtube.com",
    "youtu.be",
    "substack.com",
    "mirror.xyz",
    "theblock.co",
    "decrypt.co",
    "sifted.eu",
    "ft.com",
    "economist.com",
    "muckrack.com",
    "news.ycombinator.com",
    "hackernews.com",
    "rss.com",
    "podcastindex.org",
    "sec.gov",
    "vcpost.com",
    "gigaom.com",
    "wsj.com",
    "nytimes.com",
    "bloomberg.com",
    "forbes.com",
    "prweb.com",
    "pehub.com",
    "medium.com",
    "businessinsider.com",
}

CONTENT_PATH_MARKERS = (
    "/blog/",
    "/news/",
    "/insight",
    "/insights/",
    "/article",
    "/articles/",
    "/press",
    "/media",
    "/podcast",
    "/events/",
    "/portfolio/",
    "/careers",
    "/jobs",
)

RELEVANCE_MARKERS = [
    "venture capital",
    "startup",
    "founder",
    "accelerator",
    "cohort",
    "portfolio",
    "invest",
    "grant",
    "program",
    "web3",
    "crypto",
    "seed fund",
    "early stage",
    "angel",
    "syndicate",
]

GENERIC_TITLE_MARKERS = [
    "contact us",
    "contact",
    "apply",
    "application",
    "submit",
    "pitch",
    "refer",
    "home",
    "welcome",
]

BAD_LINK_MARKERS = [
    "privacy",
    "terms",
    "career",
    "jobs",
    "linkedin",
    "twitter",
    "facebook",
    "instagram",
    "youtube",
]

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class DiscoverySeed:
    url: str
    org_name_hint: str
    source: str


@dataclass
class ScanFailure:
    seed_url: str
    org_name_hint: str
    seed_source: str
    page_url: str
    stage: str
    error_type: str
    error_message: str


@dataclass
class ScanSiteResult:
    target: Optional["SubmissionTarget"]
    failures: List[ScanFailure]


@dataclass
class SubmissionTarget:
    org_name: str
    org_type: str
    domain: str
    source_url: str
    submission_url: str
    submission_type: str
    status: str
    requirements: str
    notes: str
    evidence: str
    source_page_snapshot: str
    deadline_text: str
    deadline_date: str
    score: int

    @property
    def fingerprint(self) -> str:
        norm_url = re.sub(r"^https?://", "", self.submission_url.strip().lower())
        raw = f"{self.domain}|{norm_url}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class SubmissionStore:
    def __init__(self, db_path: str) -> None:
        ensure_parent_dir(db_path)
        self.conn = sqlite3.connect(db_path)
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS submission_targets (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              fingerprint TEXT NOT NULL UNIQUE,
              org_name TEXT NOT NULL,
              org_type TEXT NOT NULL,
              domain TEXT NOT NULL,
              source_url TEXT NOT NULL,
              submission_url TEXT NOT NULL,
              submission_type TEXT NOT NULL,
              status TEXT NOT NULL,
              requirements TEXT NOT NULL,
              notes TEXT NOT NULL,
              evidence TEXT NOT NULL,
              source_page_snapshot TEXT NOT NULL DEFAULT '',
              deadline_text TEXT NOT NULL DEFAULT '',
              deadline_date TEXT NOT NULL DEFAULT '',
              score INTEGER NOT NULL,
              discovered_at TEXT NOT NULL,
              last_checked_at TEXT NOT NULL
            )
            """
        )
        self._ensure_column("submission_targets", "source_page_snapshot", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("submission_targets", "deadline_text", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("submission_targets", "deadline_date", "TEXT NOT NULL DEFAULT ''")

        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS submission_target_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              fingerprint TEXT NOT NULL,
              domain TEXT NOT NULL,
              submission_url TEXT NOT NULL,
              event_type TEXT NOT NULL,
              before_json TEXT NOT NULL,
              after_json TEXT NOT NULL,
              detected_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS opportunity_changes (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              fingerprint TEXT NOT NULL,
              org_name TEXT NOT NULL,
              domain TEXT NOT NULL,
              change_type TEXT NOT NULL,
              old_value TEXT NOT NULL DEFAULT '',
              new_value TEXT NOT NULL DEFAULT '',
              source_url TEXT NOT NULL DEFAULT '',
              submission_url TEXT NOT NULL DEFAULT '',
              detected_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scan_failures (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              seed_url TEXT NOT NULL,
              org_name_hint TEXT NOT NULL DEFAULT '',
              seed_source TEXT NOT NULL DEFAULT '',
              page_url TEXT NOT NULL DEFAULT '',
              stage TEXT NOT NULL,
              error_type TEXT NOT NULL,
              error_message TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'pending',
              retry_count INTEGER NOT NULL DEFAULT 1,
              first_detected_at TEXT NOT NULL,
              last_attempted_at TEXT NOT NULL,
              resolved_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_submission_score ON submission_targets(score DESC, last_checked_at DESC)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_submission_domain ON submission_targets(domain, last_checked_at DESC)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_submission_events_time ON submission_target_events(detected_at DESC)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_opportunity_changes_time ON opportunity_changes(detected_at DESC)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_opportunity_changes_fp ON opportunity_changes(fingerprint, detected_at DESC)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_scan_failures_status ON scan_failures(status, last_attempted_at DESC)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_scan_failures_seed ON scan_failures(seed_url, status, last_attempted_at DESC)"
        )
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        cur = self.conn.execute(f"PRAGMA table_info({table})")
        cols = {str(r[1]) for r in cur.fetchall()}
        if column not in cols:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _table_exists(self, table: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
            (table,),
        )
        return cur.fetchone() is not None

    def _state_from_row(self, row: sqlite3.Row) -> Dict[str, object]:
        return {
            "org_name": row["org_name"],
            "org_type": row["org_type"],
            "source_url": row["source_url"],
            "submission_url": row["submission_url"],
            "submission_type": row["submission_type"],
            "status": row["status"],
            "requirements": row["requirements"],
            "notes": row["notes"],
            "evidence": row["evidence"],
            "source_page_snapshot": row["source_page_snapshot"],
            "deadline_text": row["deadline_text"],
            "deadline_date": row["deadline_date"],
            "score": row["score"],
        }

    def _state_from_target(self, target: SubmissionTarget) -> Dict[str, object]:
        return {
            "org_name": target.org_name,
            "org_type": target.org_type,
            "source_url": target.source_url,
            "submission_url": target.submission_url,
            "submission_type": target.submission_type,
            "status": target.status,
            "requirements": target.requirements,
            "notes": target.notes,
            "evidence": target.evidence,
            "source_page_snapshot": target.source_page_snapshot,
            "deadline_text": target.deadline_text,
            "deadline_date": target.deadline_date,
            "score": target.score,
        }

    def _insert_event(
        self,
        *,
        fingerprint: str,
        domain: str,
        submission_url: str,
        event_type: str,
        before_state: Dict[str, object],
        after_state: Dict[str, object],
        detected_at: str,
        ) -> None:
        self.conn.execute(
            """
            INSERT INTO submission_target_events (
              fingerprint, domain, submission_url, event_type, before_json, after_json, detected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fingerprint,
                domain,
                submission_url,
                event_type,
                json.dumps(before_state, ensure_ascii=False),
                json.dumps(after_state, ensure_ascii=False),
                detected_at,
            ),
        )

    def _insert_structured_change(
        self,
        *,
        fingerprint: str,
        org_name: str,
        domain: str,
        change_type: str,
        old_value: object,
        new_value: object,
        source_url: str,
        submission_url: str,
        detected_at: str,
    ) -> None:
        old_text = "" if old_value is None else str(old_value)
        new_text = "" if new_value is None else str(new_value)
        if old_text == new_text and change_type != "new_opportunity":
            return
        self.conn.execute(
            """
            INSERT INTO opportunity_changes (
              fingerprint, org_name, domain, change_type, old_value, new_value,
              source_url, submission_url, detected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fingerprint,
                org_name,
                domain,
                change_type,
                old_text,
                new_text,
                source_url,
                submission_url,
                detected_at,
            ),
        )

    def _record_structured_changes(
        self,
        *,
        fingerprint: str,
        domain: str,
        old_state: Dict[str, object],
        new_state: Dict[str, object],
        detected_at: str,
    ) -> None:
        org_name = str(new_state.get("org_name") or old_state.get("org_name") or "")
        source_url = str(new_state.get("source_url") or old_state.get("source_url") or "")
        submission_url = str(new_state.get("submission_url") or old_state.get("submission_url") or "")
        if not old_state:
            self._insert_structured_change(
                fingerprint=fingerprint,
                org_name=org_name,
                domain=domain,
                change_type="new_opportunity",
                old_value="",
                new_value=str(new_state.get("status") or ""),
                source_url=source_url,
                submission_url=submission_url,
                detected_at=detected_at,
            )
            return

        old_status = str(old_state.get("status") or "")
        new_status = str(new_state.get("status") or "")
        if old_status != new_status:
            change_type = "status_changed"
            if old_status.strip().lower() == "closed" and new_status.strip().lower() in {"open", "rolling", "deadline"}:
                change_type = "reopened"
            self._insert_structured_change(
                fingerprint=fingerprint,
                org_name=org_name,
                domain=domain,
                change_type=change_type,
                old_value=old_status,
                new_value=new_status,
                source_url=source_url,
                submission_url=submission_url,
                detected_at=detected_at,
            )

        old_deadline = str(old_state.get("deadline_date") or old_state.get("deadline_text") or "")
        new_deadline = str(new_state.get("deadline_date") or new_state.get("deadline_text") or "")
        if old_deadline != new_deadline:
            self._insert_structured_change(
                fingerprint=fingerprint,
                org_name=org_name,
                domain=domain,
                change_type="deadline_changed",
                old_value=old_deadline,
                new_value=new_deadline,
                source_url=source_url,
                submission_url=submission_url,
                detected_at=detected_at,
            )

        old_submission_url = str(old_state.get("submission_url") or "")
        new_submission_url = str(new_state.get("submission_url") or "")
        if old_submission_url != new_submission_url:
            self._insert_structured_change(
                fingerprint=fingerprint,
                org_name=org_name,
                domain=domain,
                change_type="submission_url_changed",
                old_value=old_submission_url,
                new_value=new_submission_url,
                source_url=source_url,
                submission_url=new_submission_url,
                detected_at=detected_at,
            )

        old_source_url = str(old_state.get("source_url") or "")
        new_source_url = str(new_state.get("source_url") or "")
        if old_source_url != new_source_url:
            self._insert_structured_change(
                fingerprint=fingerprint,
                org_name=org_name,
                domain=domain,
                change_type="source_url_changed",
                old_value=old_source_url,
                new_value=new_source_url,
                source_url=new_source_url,
                submission_url=submission_url,
                detected_at=detected_at,
            )

    def _find_existing_target_row(self, target: SubmissionTarget) -> Optional[sqlite3.Row]:
        self.conn.row_factory = sqlite3.Row
        cur = self.conn.execute(
            """
            SELECT id, fingerprint, org_name, org_type, source_url, submission_url, submission_type,
                   status, requirements, notes, evidence, source_page_snapshot, deadline_text, deadline_date,
                   score, domain
            FROM submission_targets
            WHERE fingerprint = ?
            LIMIT 1
            """,
            (target.fingerprint,),
        )
        row = cur.fetchone()
        if row is not None:
            return row
        cur = self.conn.execute(
            """
            SELECT id, fingerprint, org_name, org_type, source_url, submission_url, submission_type,
                   status, requirements, notes, evidence, source_page_snapshot, deadline_text, deadline_date,
                   score, domain
            FROM submission_targets
            WHERE lower(org_name) = lower(?)
              AND (
                domain = ?
                OR lower(source_url) = lower(?)
              )
            ORDER BY last_checked_at DESC, score DESC, id DESC
            LIMIT 1
            """,
            (
                target.org_name,
                target.domain,
                _canonicalize_url(target.source_url),
            ),
        )
        return cur.fetchone()

    def _recommended_action_for_state(self, *, status: str, deadline_date: str) -> str:
        status_low = str(status or "").strip().lower()
        due = str(deadline_date or "").strip()
        if status_low == "deadline":
            return f"review requirements and prepare submission before {due or '-'}"
        if status_low in {"open", "rolling"}:
            return "review requirements, prepare materials, and move to ready_to_submit"
        if status_low == "closed":
            return "do not prepare submission; watch for reopen or next cohort"
        return "reverify opportunity before assigning submission work"

    def _sync_tasks_for_target(
        self,
        *,
        old_fingerprint: str,
        old_state: Dict[str, object],
        target: SubmissionTarget,
        detected_at: str,
    ) -> None:
        if not self._table_exists("submission_tasks"):
            return
        self.conn.row_factory = sqlite3.Row
        cur = self.conn.execute(
            """
            SELECT id, opportunity_fingerprint, submission_state, due_date, priority_score,
                   recommended_action, submitted_at, follow_up_due_at
            FROM submission_tasks
            WHERE opportunity_fingerprint = ?
            ORDER BY id ASC
            """,
            (old_fingerprint,),
        )
        rows = list(cur.fetchall())
        if not rows:
            return

        old_due = str(old_state.get("deadline_date") or old_state.get("deadline_text") or "").strip()
        new_due = str(target.deadline_date or "").strip()
        old_priority = int(old_state.get("score") or 0)
        new_priority = int(target.score or 0)
        old_recommended = self._recommended_action_for_state(
            status=str(old_state.get("status") or ""),
            deadline_date=str(old_state.get("deadline_date") or ""),
        )
        new_recommended = self._recommended_action_for_state(status=target.status, deadline_date=target.deadline_date)

        for row in rows:
            submission_state = str(row["submission_state"] or "").strip().lower()
            current_due = str(row["due_date"] or "").strip()
            current_priority = int(row["priority_score"] or 0)
            current_recommended = str(row["recommended_action"] or "").strip()
            submitted_at = str(row["submitted_at"] or "").strip()
            follow_up_due_at = str(row["follow_up_due_at"] or "").strip()

            synced_due = current_due
            if (
                submission_state not in {"submitted", "follow_up_due", "won", "rejected", "archived"}
                and not submitted_at
                and not follow_up_due_at
                and (not current_due or current_due == old_due)
            ):
                synced_due = new_due

            synced_priority = current_priority
            if current_priority == old_priority:
                synced_priority = new_priority

            synced_recommended = current_recommended
            if not current_recommended or current_recommended == old_recommended:
                synced_recommended = new_recommended

            self.conn.execute(
                """
                UPDATE submission_tasks
                SET opportunity_fingerprint = ?,
                    org_name = ?,
                    domain = ?,
                    official_page = ?,
                    submission_url = ?,
                    opportunity_status = ?,
                    due_date = ?,
                    priority_score = ?,
                    recommended_action = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    target.fingerprint,
                    target.org_name,
                    target.domain,
                    target.source_url,
                    target.submission_url,
                    target.status,
                    synced_due,
                    synced_priority,
                    synced_recommended,
                    detected_at,
                    int(row["id"]),
                ),
            )

            change_bits: List[str] = []
            if old_fingerprint != target.fingerprint:
                change_bits.append("fingerprint relinked")
            if old_state.get("status") != target.status:
                change_bits.append(f"status -> {target.status}")
            if current_due != synced_due:
                change_bits.append(f"due_date -> {synced_due or '-'}")
            if current_priority != synced_priority:
                change_bits.append(f"priority_score -> {synced_priority}")
            if current_recommended != synced_recommended:
                change_bits.append("recommended_action synced")
            old_submission_url = str(old_state.get("submission_url") or "").strip()
            if old_submission_url != target.submission_url:
                change_bits.append("submission_url synced")
            old_source_url = str(old_state.get("source_url") or "").strip()
            if old_source_url != target.source_url:
                change_bits.append("official_page synced")

            if change_bits:
                self.conn.execute(
                    """
                    INSERT INTO submission_task_updates (task_id, event_type, body, actor, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        int(row["id"]),
                        "opportunity_synced",
                        "; ".join(change_bits),
                        "system",
                        detected_at,
                    ),
                )

    def upsert_targets(self, targets: Sequence[SubmissionTarget]) -> int:
        now = now_utc_iso()
        self.conn.row_factory = sqlite3.Row
        upsert_sql = """
            INSERT INTO submission_targets (
              fingerprint, org_name, org_type, domain, source_url, submission_url,
              submission_type, status, requirements, notes, evidence, source_page_snapshot,
              deadline_text, deadline_date, score, discovered_at, last_checked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fingerprint) DO UPDATE SET
              org_name=excluded.org_name,
              org_type=excluded.org_type,
              source_url=excluded.source_url,
              submission_type=excluded.submission_type,
              status=excluded.status,
              requirements=excluded.requirements,
              notes=excluded.notes,
              evidence=excluded.evidence,
              source_page_snapshot=excluded.source_page_snapshot,
              deadline_text=excluded.deadline_text,
              deadline_date=excluded.deadline_date,
              score=excluded.score,
              last_checked_at=excluded.last_checked_at
        """
        before = self.conn.total_changes
        with self.conn:
            for target in targets:
                old_row = self._find_existing_target_row(target)
                old_state = self._state_from_row(old_row) if old_row else {}
                new_state = self._state_from_target(target)
                event_type = "created" if old_row is None else "updated"
                old_fingerprint = str(old_row["fingerprint"] or "") if old_row is not None else ""

                if old_row is None or old_state != new_state:
                    self._insert_event(
                        fingerprint=target.fingerprint,
                        domain=target.domain,
                        submission_url=target.submission_url,
                        event_type=event_type,
                        before_state=old_state,
                        after_state=new_state,
                        detected_at=now,
                    )
                    self._record_structured_changes(
                        fingerprint=target.fingerprint,
                        domain=target.domain,
                        old_state=old_state,
                        new_state=new_state,
                        detected_at=now,
                    )

                self.conn.execute(
                    upsert_sql,
                    (
                        target.fingerprint,
                        target.org_name,
                        target.org_type,
                        target.domain,
                        target.source_url,
                        target.submission_url,
                        target.submission_type,
                        target.status,
                        target.requirements,
                        target.notes,
                        target.evidence,
                        target.source_page_snapshot,
                        target.deadline_text,
                        target.deadline_date,
                        target.score,
                        now,
                        now,
                    ),
                )
                if old_row is not None and old_fingerprint and old_state != new_state:
                    self._sync_tasks_for_target(
                        old_fingerprint=old_fingerprint,
                        old_state=old_state,
                        target=target,
                        detected_at=now,
                    )
                if old_row is not None and old_fingerprint and old_fingerprint != target.fingerprint:
                    self.conn.execute("DELETE FROM submission_targets WHERE fingerprint = ?", (old_fingerprint,))
        return self.conn.total_changes - before

    def list_targets(
        self,
        *,
        limit: int = 80,
        status: str = "",
        org_type: str = "",
        min_score: int = 0,
    ) -> List[sqlite3.Row]:
        self.conn.row_factory = sqlite3.Row
        where: List[str] = ["score >= ?"]
        args: List[object] = [min_score]
        if status:
            where.append("status = ?")
            args.append(status)
        if org_type:
            where.append("org_type = ?")
            args.append(org_type)
        sql = f"""
            SELECT id, org_name, org_type, domain, source_url, submission_url, submission_type,
                   fingerprint, status, requirements, notes, evidence, source_page_snapshot,
                   deadline_text, deadline_date, score,
                   discovered_at, last_checked_at
            FROM submission_targets
            WHERE {' AND '.join(where)}
            ORDER BY score DESC, last_checked_at DESC, id DESC
            LIMIT ?
        """
        args.append(limit)
        cur = self.conn.execute(sql, tuple(args))
        return cur.fetchall()

    def get_target(self, fingerprint: str) -> Optional[sqlite3.Row]:
        self.conn.row_factory = sqlite3.Row
        cur = self.conn.execute(
            """
            SELECT id, org_name, org_type, domain, source_url, submission_url, submission_type,
                   fingerprint, status, requirements, notes, evidence, source_page_snapshot,
                   deadline_text, deadline_date, score,
                   discovered_at, last_checked_at
            FROM submission_targets
            WHERE fingerprint = ?
            LIMIT 1
            """,
            (fingerprint.strip(),),
        )
        return cur.fetchone()

    def resolve_target_ref(self, ref: str) -> Optional[sqlite3.Row]:
        raw_ref = str(ref or "").strip()
        if raw_ref.startswith("target:"):
            raw_ref = raw_ref.split(":", 1)[1].strip()
        if not raw_ref:
            return None

        current = self.get_target(raw_ref)
        if current is not None:
            return current

        if len(raw_ref) < 8:
            return None
        self.conn.row_factory = sqlite3.Row
        cur = self.conn.execute(
            """
            SELECT id, org_name, org_type, domain, source_url, submission_url, submission_type,
                   fingerprint, status, requirements, notes, evidence, source_page_snapshot,
                   deadline_text, deadline_date, score,
                   discovered_at, last_checked_at
            FROM submission_targets
            WHERE fingerprint LIKE ?
            ORDER BY last_checked_at DESC, score DESC
            LIMIT 2
            """,
            (f"{raw_ref}%",),
        )
        rows = cur.fetchall()
        if len(rows) != 1:
            return None
        return rows[0]

    def override_target(
        self,
        ref: str,
        *,
        org_name: str | None = None,
        org_type: str | None = None,
        source_url: str | None = None,
        submission_url: str | None = None,
        submission_type: str | None = None,
        status: str | None = None,
        requirements: str | None = None,
        notes: str | None = None,
        evidence: str | None = None,
        deadline_text: str | None = None,
        deadline_date: str | None = None,
        score: int | None = None,
    ) -> Optional[sqlite3.Row]:
        current = self.resolve_target_ref(ref)
        if current is None:
            return None

        merged_source_url = _canonicalize_url(str(source_url).strip()) if source_url is not None and str(source_url).strip() else str(current["source_url"] or "")
        merged_submission_url = _canonicalize_url(str(submission_url).strip()) if submission_url is not None and str(submission_url).strip() else str(current["submission_url"] or "")
        merged_domain = _domain_key(merged_submission_url or merged_source_url or str(current["source_url"] or ""))
        merged_notes = str(current["notes"] or "")
        if notes is not None:
            merged_notes = sanitize(str(notes), limit=600)
        merged_evidence = str(current["evidence"] or "")
        if evidence is not None:
            merged_evidence = sanitize(str(evidence), limit=600)
        elif "manual-override" not in merged_evidence:
            merged_evidence = sanitize((merged_evidence + " | manual-override").strip(" |"), limit=600)

        target = SubmissionTarget(
            org_name=sanitize(str(org_name if org_name is not None else current["org_name"]), limit=120),
            org_type=sanitize(str(org_type if org_type is not None else current["org_type"]), limit=80),
            domain=merged_domain or str(current["domain"] or ""),
            source_url=merged_source_url or str(current["source_url"] or ""),
            submission_url=merged_submission_url or str(current["submission_url"] or ""),
            submission_type=sanitize(str(submission_type if submission_type is not None else current["submission_type"]), limit=40),
            status=sanitize(str(status if status is not None else current["status"]), limit=40),
            requirements=sanitize(str(requirements if requirements is not None else current["requirements"]), limit=600),
            notes=merged_notes,
            evidence=merged_evidence,
            source_page_snapshot=sanitize(str(current["source_page_snapshot"] or ""), limit=1000),
            deadline_text=sanitize(str(deadline_text if deadline_text is not None else current["deadline_text"]), limit=240),
            deadline_date=sanitize(str(deadline_date if deadline_date is not None else current["deadline_date"]), limit=40),
            score=int(score if score is not None else int(current["score"] or 0)),
        )
        self.upsert_targets([target])
        row = self.get_target(target.fingerprint)
        if row is not None:
            return row
        return self.get_target(str(current["fingerprint"] or ""))

    def list_events(self, *, limit: int = 40) -> List[sqlite3.Row]:
        self.conn.row_factory = sqlite3.Row
        cur = self.conn.execute(
            """
            SELECT id, domain, submission_url, event_type, before_json, after_json, detected_at
            FROM submission_target_events
            ORDER BY detected_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return cur.fetchall()

    def list_changes(
        self,
        *,
        limit: int = 50,
        change_type: str = "",
        since: str = "",
    ) -> List[sqlite3.Row]:
        self.conn.row_factory = sqlite3.Row
        where: List[str] = ["1 = 1"]
        args: List[object] = []
        if change_type:
            where.append("change_type = ?")
            args.append(change_type)
        if since:
            where.append("detected_at >= ?")
            args.append(since)
        sql = f"""
            SELECT id, fingerprint, org_name, domain, change_type, old_value, new_value,
                   source_url, submission_url, detected_at
            FROM opportunity_changes
            WHERE {' AND '.join(where)}
            ORDER BY detected_at DESC, id DESC
            LIMIT ?
        """
        args.append(limit)
        cur = self.conn.execute(sql, tuple(args))
        return cur.fetchall()

    def record_scan_failure(self, failure: ScanFailure, *, detected_at: str) -> None:
        seed_url = _canonicalize_url(failure.seed_url)
        if not seed_url:
            return
        page_url = _canonicalize_url(failure.page_url) if failure.page_url else ""
        error_type = sanitize(failure.error_type, limit=80)
        stage = sanitize(failure.stage, limit=80)
        org_name_hint = sanitize(failure.org_name_hint, limit=180)
        raw_seed_source = str(failure.seed_source or "").strip()
        while raw_seed_source.startswith("failure:"):
            raw_seed_source = raw_seed_source.split(":", 1)[1].strip()
        seed_source = sanitize(raw_seed_source or "scan", limit=120)
        error_message = sanitize(failure.error_message, limit=300)
        with self.conn:
            cur = self.conn.execute(
                """
                SELECT id, retry_count
                FROM scan_failures
                WHERE status = 'pending'
                  AND seed_url = ?
                  AND page_url = ?
                  AND stage = ?
                  AND error_type = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (seed_url, page_url, stage, error_type),
            )
            row = cur.fetchone()
            if row is None:
                self.conn.execute(
                    """
                    INSERT INTO scan_failures (
                      seed_url, org_name_hint, seed_source, page_url, stage, error_type,
                      error_message, status, retry_count, first_detected_at, last_attempted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 1, ?, ?)
                    """,
                    (
                        seed_url,
                        org_name_hint,
                        seed_source,
                        page_url,
                        stage,
                        error_type,
                        error_message,
                        detected_at,
                        detected_at,
                    ),
                )
                return
            self.conn.execute(
                """
                UPDATE scan_failures
                SET org_name_hint = ?,
                    seed_source = ?,
                    error_message = ?,
                    retry_count = ?,
                    last_attempted_at = ?
                WHERE id = ?
                """,
                (
                    org_name_hint,
                    seed_source,
                    error_message,
                    int(row[1] or 0) + 1,
                    detected_at,
                    int(row[0]),
                ),
            )

    def resolve_scan_failures(self, seed_url: str, *, resolved_at: str) -> int:
        normalized = _canonicalize_url(seed_url)
        if not normalized:
            return 0
        before = self.conn.total_changes
        with self.conn:
            self.conn.execute(
                """
                UPDATE scan_failures
                SET status = 'resolved',
                    resolved_at = ?,
                    last_attempted_at = ?
                WHERE status = 'pending'
                  AND seed_url = ?
                """,
                (resolved_at, resolved_at, normalized),
            )
        return self.conn.total_changes - before

    def set_scan_failure_status(self, ref: str, *, status: str, changed_at: str) -> int:
        status_low = str(status or "").strip().lower()
        if status_low not in {"pending", "resolved", "ignored"}:
            return 0
        raw_ref = str(ref or "").strip()
        if raw_ref.startswith("failure:"):
            raw_ref = raw_ref.split(":", 1)[1].strip()
        before = self.conn.total_changes
        with self.conn:
            if raw_ref.isdigit():
                where_sql = "id = ?"
                where_arg: object = int(raw_ref)
            else:
                normalized = _canonicalize_url(raw_ref)
                if not normalized:
                    return 0
                where_sql = "seed_url = ?"
                where_arg = normalized
            self.conn.execute(
                f"""
                UPDATE scan_failures
                SET status = ?,
                    resolved_at = ?,
                    last_attempted_at = ?
                WHERE {where_sql}
                """,
                (
                    status_low,
                    "" if status_low == "pending" else changed_at,
                    changed_at,
                    where_arg,
                ),
            )
        return self.conn.total_changes - before

    def list_scan_failures(
        self,
        *,
        limit: int = 50,
        status: str = "pending",
    ) -> List[sqlite3.Row]:
        self.conn.row_factory = sqlite3.Row
        where: List[str] = ["1 = 1"]
        args: List[object] = []
        status_low = status.strip().lower()
        if status_low in {"pending", "resolved", "ignored"}:
            where.append("status = ?")
            args.append(status_low)
        sql = f"""
            SELECT id, seed_url, org_name_hint, seed_source, page_url, stage, error_type,
                   error_message, status, retry_count, first_detected_at, last_attempted_at, resolved_at
            FROM scan_failures
            WHERE {' AND '.join(where)}
            ORDER BY
              CASE status WHEN 'pending' THEN 0 WHEN 'resolved' THEN 1 ELSE 2 END,
              last_attempted_at DESC,
              id DESC
            LIMIT ?
        """
        args.append(limit)
        cur = self.conn.execute(sql, tuple(args))
        return cur.fetchall()

    def load_pending_failure_seeds(self, *, limit: int = 80) -> List[DiscoverySeed]:
        self.conn.row_factory = sqlite3.Row
        cur = self.conn.execute(
            """
            SELECT seed_url, org_name_hint, seed_source, retry_count, last_attempted_at
            FROM scan_failures
            WHERE status = 'pending'
            ORDER BY retry_count ASC, last_attempted_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        )
        out: List[DiscoverySeed] = []
        seen: set[str] = set()
        for row in cur.fetchall():
            url = _normalize_seed_url(str(row["seed_url"] or ""))
            if not url:
                continue
            key = _seed_identity(url)
            if not key or key in seen:
                continue
            seen.add(key)
            source = str(row["seed_source"] or "").strip() or "scan"
            out.append(
                DiscoverySeed(
                    url=url,
                    org_name_hint=sanitize(str(row["org_name_hint"] or ""), limit=120),
                    source=f"failure:{source}",
                )
            )
        return out

    def load_review_target_seeds(self, *, limit: int = 80) -> List[DiscoverySeed]:
        self.conn.row_factory = sqlite3.Row
        cur = self.conn.execute(
            """
            SELECT fingerprint, org_name, source_url, submission_url, submission_type, status, deadline_text, score, last_checked_at
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
              last_checked_at DESC,
              id DESC
            LIMIT ?
            """,
            (limit,),
        )
        out: List[DiscoverySeed] = []
        seen: set[str] = set()
        for row in cur.fetchall():
            candidate_url = str(row["source_url"] or "").strip() or str(row["submission_url"] or "").strip()
            url = _normalize_seed_url(candidate_url)
            if not url:
                continue
            key = _seed_identity(url)
            if not key or key in seen:
                continue
            seen.add(key)
            fingerprint = str(row["fingerprint"] or "").strip()
            out.append(
                DiscoverySeed(
                    url=url,
                    org_name_hint=sanitize(str(row["org_name"] or ""), limit=120),
                    source=f"review-target:{fingerprint}",
                )
            )
        return out

    def prune_scanned_domains(self, scanned_domains: Sequence[str], keep_fingerprints: Sequence[str]) -> int:
        domains = [d.strip().lower() for d in scanned_domains if d and _is_target_domain(d.strip().lower())]
        if not domains:
            return 0
        domains = list(dict.fromkeys(domains))
        keep = [fp.strip() for fp in keep_fingerprints if fp.strip()]
        keep = list(dict.fromkeys(keep))

        before = self.conn.total_changes
        with self.conn:
            domain_placeholders = ",".join(["?"] * len(domains))
            if keep:
                keep_placeholders = ",".join(["?"] * len(keep))
                sql = (
                    f"DELETE FROM submission_targets "
                    f"WHERE domain IN ({domain_placeholders}) "
                    f"AND fingerprint NOT IN ({keep_placeholders})"
                )
                args: Tuple[object, ...] = tuple(domains) + tuple(keep)
            else:
                sql = f"DELETE FROM submission_targets WHERE domain IN ({domain_placeholders})"
                args = tuple(domains)
            self.conn.execute(sql, args)
        return self.conn.total_changes - before

    def cleanup_noise(self) -> int:
        before = self.conn.total_changes
        with self.conn:
            placeholders = ",".join(["?"] * len(NON_TARGET_DOMAINS))
            self.conn.execute(
                f"DELETE FROM submission_targets WHERE domain IN ({placeholders})",
                tuple(sorted(NON_TARGET_DOMAINS)),
            )
            # Remove weak generic contact pages that do not show explicit pitch/apply intent.
            self.conn.execute(
                """
                DELETE FROM submission_targets
                WHERE lower(submission_url) LIKE '%/contact%'
                  AND evidence NOT LIKE '%phrase:pitch us%'
                  AND evidence NOT LIKE '%phrase:submit your pitch%'
                  AND evidence NOT LIKE '%embed:%'
                  AND evidence NOT LIKE '%email:%'
                  AND evidence NOT LIKE '%policy:intro-only%'
                """
            )
            # Remove weak "embedded form" detections that do not expose a real submission path.
            self.conn.execute(
                """
                DELETE FROM submission_targets
                WHERE evidence LIKE 'embed:%'
                  AND evidence NOT LIKE '%| path:%'
                  AND evidence NOT LIKE '%| phrase:%'
                  AND evidence NOT LIKE '%| email:%'
                  AND lower(submission_url) NOT LIKE '%apply%'
                  AND lower(submission_url) NOT LIKE '%pitch%'
                  AND lower(submission_url) NOT LIKE '%submit%'
                  AND lower(submission_url) NOT LIKE '%grant%'
                  AND lower(submission_url) NOT LIKE '%typeform%'
                  AND lower(submission_url) NOT LIKE '%airtable%'
                  AND lower(submission_url) NOT LIKE '%forms.gle%'
                  AND lower(submission_url) NOT LIKE '%docs.google.com/forms%'
                """
            )
            # Remove generic form-host marketing pages (not actual submission forms).
            self.conn.execute(
                """
                DELETE FROM submission_targets
                WHERE lower(submission_url) LIKE '%airtable.com/solutions/%'
                   OR lower(submission_url) LIKE '%airtable.com/templates/%'
                   OR lower(submission_url) LIKE '%airtable.com/blog/%'
                   OR lower(submission_url) LIKE '%airtable.com/product/%'
                   OR lower(submission_url) LIKE '%airtable.com/internal/page_view%'
                   OR lower(submission_url) LIKE '%typeform.com/templates/%'
                   OR lower(submission_url) LIKE '%typeform.com/application-form-builder%'
                """
            )

            self.conn.row_factory = sqlite3.Row
            rows = self.conn.execute(
                """
                SELECT id, domain, submission_url, score, submission_type, status
                FROM submission_targets
                ORDER BY last_checked_at DESC, id DESC
                """
            ).fetchall()
            ranked_rows = sorted(
                rows,
                key=lambda row: (
                    0
                    if any(
                        tok in str(row["submission_url"]).lower()
                        for tok in [
                            "apply",
                            "pitch",
                            "submit",
                            "grant",
                            "application",
                            "typeform",
                            "airtable",
                            "forms.gle",
                            "docs.google.com/forms",
                            "jotform",
                            "tally.so",
                        ]
                    )
                    else 1,
                    {"form": 0, "email": 1, "intro-only": 2, "unknown": 3}.get(
                        str(row["submission_type"]).strip().lower(), 3
                    ),
                    {"open": 0, "rolling": 1, "deadline": 2, "closed": 3}.get(
                        str(row["status"]).strip().lower(), 4
                    ),
                    -int(row["score"] or 0),
                    -int(row["id"]),
                ),
            )
            keep: set[int] = set()
            seen: set[str] = set()
            root_count: Dict[str, int] = {}
            for row in ranked_rows:
                submission_url = _canonicalize_url(str(row["submission_url"]).strip())
                is_form_target = bool(
                    submission_url and (_looks_actionable_form_url(submission_url) or _looks_form_host(submission_url))
                )
                key = re.sub(r"^https?://", "", str(submission_url or row["submission_url"]).strip().lower())
                if key in seen:
                    continue
                root = "" if is_form_target else _root_domain(str(row["domain"]).strip().lower())
                if root:
                    used = root_count.get(root, 0)
                    if used >= 1:
                        continue
                    root_count[root] = used + 1
                seen.add(key)
                keep.add(int(row["id"]))
            if keep:
                keep_ids = sorted(keep)
                placeholders_keep = ",".join(["?"] * len(keep_ids))
                self.conn.execute(
                    f"DELETE FROM submission_targets WHERE id NOT IN ({placeholders_keep})",
                    tuple(keep_ids),
                )
        return self.conn.total_changes - before


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sanitize(text: str, limit: int = 1200) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())[:limit]


def _canonicalize_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = "https:" + raw
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw):
        raw = "https://" + raw
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in {"http", "https"}:
        return ""
    netloc = parsed.netloc.strip().lower()
    if not netloc:
        return ""
    if netloc.endswith(":80") and parsed.scheme == "http":
        netloc = netloc[:-3]
    if netloc.endswith(":443") and parsed.scheme == "https":
        netloc = netloc[:-4]
    path = re.sub(r"//+", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urllib.parse.urlunsplit((parsed.scheme, netloc, path, "", ""))


def _safe_urljoin(base_url: str, raw_url: str) -> str:
    try:
        return urllib.parse.urljoin(base_url, unescape((raw_url or "").strip()))
    except Exception:  # noqa: BLE001
        return ""


def _domain_key(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _is_target_domain(domain: str) -> bool:
    if not domain:
        return False
    if domain in NON_TARGET_DOMAINS:
        return False
    return not any(domain.endswith("." + d) for d in NON_TARGET_DOMAINS)


def _has_submission_hint(text: str) -> bool:
    t = (text or "").lower()
    return any(h in t for h in ["apply", "pitch", "submit", "application", "grant", "founder", "cohort"])


def _is_content_like_url(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    low = f"{parsed.netloc.lower()}{parsed.path.lower()}"
    if _has_submission_hint(low):
        return False
    return any(marker in parsed.path.lower() for marker in CONTENT_PATH_MARKERS)


def _normalize_seed_url(raw_url: str) -> str:
    url = _canonicalize_url(raw_url)
    if not url:
        return ""
    domain = _domain_key(url)
    if not _is_target_domain(domain):
        return ""
    if _looks_actionable_form_url(url) or _looks_form_host(url):
        return url

    parsed = urllib.parse.urlsplit(url)
    path_low = parsed.path.lower()
    has_submission_path = any(h in path_low for h in DISCOVERY_PATH_HINTS)
    if path_low and path_low != "/" and (not has_submission_path or _is_content_like_url(url)):
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))
    return url


def _normalize_fundraise_seed_url(raw_url: str, *, category: str, status: str, notes: str) -> str:
    canonical = _canonicalize_url(raw_url)
    if not canonical:
        return ""
    domain = _domain_key(canonical)
    if not _is_target_domain(domain):
        return ""

    path_low = urllib.parse.urlsplit(canonical).path.lower()
    context = f"{category} {status} {notes} {path_low}".lower()
    preserve_exact = (
        any(token in context for token in ["accelerator", "grant", "cohort", "batch", "apply", "application", "submit"])
        or _looks_actionable_form_url(canonical)
        or _looks_form_host(canonical)
    )
    if preserve_exact:
        return canonical
    return _normalize_seed_url(canonical)


def _seed_identity(url: str) -> str:
    canonical = _canonicalize_url(url)
    if not canonical:
        return ""
    if _looks_actionable_form_url(canonical) or _looks_form_host(canonical):
        return canonical
    return _domain_key(canonical)


def _same_domain(a: str, b: str) -> bool:
    return _domain_key(a) == _domain_key(b)


def _root_domain(domain: str) -> str:
    host = (domain or "").strip().lower()
    if not host:
        return ""
    parts = [p for p in host.split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    common_second_level_tlds = {"co.uk", "org.uk", "gov.uk", "co.jp", "com.au", "co.kr"}
    tail = ".".join(parts[-2:])
    if tail in common_second_level_tlds and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _same_org_family(a: str, b: str) -> bool:
    da = _root_domain(_domain_key(a))
    db = _root_domain(_domain_key(b))
    return bool(da and db and da == db)


def _title_from_html(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    return sanitize(unescape(re.sub(r"<[^>]+>", " ", m.group(1))), limit=240)


def _domain_to_org_name(domain: str) -> str:
    base = (domain or "").split(".")[0].strip().lower()
    if not base:
        return "Unknown"
    base = base.replace("-", " ").replace("_", " ")
    base = re.sub(r"(accelerator|ventures|capital|foundation|labs|studio|partners|fund|dao|vc)$", r" \1", base)
    base = re.sub(r"\s+", " ", base).strip()
    name = base.title()
    name = re.sub(r"\bVc\b", "VC", name)
    name = re.sub(r"\bDao\b", "DAO", name)
    name = re.sub(r"\bAi\b", "AI", name)
    return sanitize(name, 120)


def _normalize_org_name(name: str, domain: str) -> str:
    cleaned = (name or "")
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"[→↗↘↖↙]+", " ", cleaned)
    cleaned = sanitize(cleaned, 120)
    cleaned = cleaned.strip(" -|:")
    if cleaned.lower() in {"accelerator", "program", "founders", "contact us", "apply", "home"}:
        cleaned = ""
    if any(
        marker in cleaned.lower()
        for marker in ["announcing ", "press release", "newsroom", "latest news", "welcome to"]
    ):
        cleaned = ""
    brand = re.sub(r"[^a-z0-9]", "", (domain or "").split(".")[0].lower())
    cleaned_alnum = re.sub(r"[^a-z0-9]", "", cleaned.lower())
    if brand and brand not in cleaned_alnum and any(k in cleaned.lower() for k in ["world", "investor", "home"]):
        cleaned = ""
    if not cleaned:
        return _domain_to_org_name(domain)
    return cleaned


def _infer_org_name(domain: str, title: str, hint: str) -> str:
    hint_clean = sanitize(re.sub(r"https?://\S+", "", (hint or "").replace("→", " ")), 180)
    hint_low = hint_clean.lower()
    if hint_clean and not any(tok in hint_low for tok in GENERIC_TITLE_MARKERS):
        return hint_clean

    if title:
        parts = re.split(r"\s*[|\-:\u2013\u2014]\s*", title)
        for part in parts:
            chunk = sanitize(part, 120)
            low = chunk.lower()
            if not chunk:
                continue
            if any(tok in low for tok in ["apply", "pitch", "submit", "application", "founder"]):
                continue
            if any(tok in low for tok in GENERIC_TITLE_MARKERS):
                continue
            if len(chunk) < 3:
                continue
            return chunk

    return _domain_to_org_name(domain)


def _fetch_html(url: str, timeout: int = 10) -> Tuple[str, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        final_url = resp.geturl()
        ctype = (resp.headers.get("Content-Type") or "").lower()
        body = resp.read()
    if "text/html" not in ctype and b"<html" not in body[:1000].lower():
        return "", _canonicalize_url(final_url) or _canonicalize_url(url)
    return body.decode("utf-8", errors="ignore"), (_canonicalize_url(final_url) or _canonicalize_url(url))


def _strip_tags(html_fragment: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html_fragment, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return sanitize(unescape(text), limit=12000)


def _extract_links(base_url: str, html: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    pattern = re.compile(
        r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for href, label_html in pattern.findall(html):
        label = sanitize(_strip_tags(label_html), limit=180)
        joined = _safe_urljoin(base_url, href)
        if not joined:
            continue
        url = _canonicalize_url(joined)
        if not url:
            continue
        out.append((url, label))
    return out


def _extract_embed_form_urls(base_url: str, html: str) -> List[str]:
    out: List[str] = []
    patterns = [
        r"https?://[^\s\"'<>]+",
        r"//[^\s\"'<>]+",
    ]
    for pat in patterns:
        for raw in re.findall(pat, html, flags=re.IGNORECASE):
            joined = _safe_urljoin(base_url, raw)
            if not joined:
                continue
            candidate = _canonicalize_url(joined)
            if not candidate:
                continue
            if _looks_actionable_form_url(candidate):
                out.append(candidate)
    return list(dict.fromkeys(out))


def _looks_submission_link(url: str, text: str) -> bool:
    merged = f"{url} {text}".lower()
    return any(h in merged for h in DISCOVERY_PATH_HINTS)


def _looks_form_host(url: str) -> bool:
    domain = _domain_key(url)
    if not domain:
        return False
    return any(marker in domain for marker in FORM_HOST_MARKERS)


def _looks_actionable_form_url(url: str) -> bool:
    domain = _domain_key(url)
    path = urllib.parse.urlsplit(url).path.lower()
    if not domain:
        return False
    if "airtable.com" in domain:
        if any(path.startswith(prefix) for prefix in ["/solutions", "/templates", "/blog", "/product", "/universe"]):
            return False
        return "/form" in path or "/shr" in path or "/pag" in path or bool(re.search(r"/app[a-z0-9]+/", path))
    if "typeform.com" in domain:
        return "/to/" in path or "/form/" in path
    if "docs.google.com" in domain:
        return "/forms/" in path and "/edit" not in path and "/viewanalytics" not in path
    if "forms.gle" in domain:
        return True
    if "tally.so" in domain:
        return path.startswith("/r/") or path.startswith("/forms/") or len(path.strip("/")) >= 4
    if any(host in domain for host in ["jotform.com", "submittable.com", "fillout.com", "hubspot.com", "wkf.ms"]):
        return len(path.strip("/")) > 0
    return _looks_form_host(url)


def _score_submission_link(current_url: str, link_url: str, link_text: str) -> int:
    merged = f"{link_url} {link_text}".lower()
    path_low = urllib.parse.urlsplit(link_url).path.lower()
    score = 0

    strong_hint = any(h in merged for h in STRONG_PATH_HINTS)
    discovery_hint = any(h in merged for h in DISCOVERY_PATH_HINTS)

    if strong_hint:
        score += 4
    elif discovery_hint:
        score += 1
    if any(phrase in merged for phrase in SUBMISSION_PHRASES):
        score += 4
    if _looks_actionable_form_url(link_url):
        score += 5
    elif _looks_form_host(link_url):
        score -= 2
    if _same_org_family(current_url, link_url):
        score += 2
    if _same_domain(current_url, link_url):
        score += 1
    if any(m in merged for m in BAD_LINK_MARKERS):
        score -= 6
    if "refer" in path_low and not any(k in merged for k in ["apply", "pitch", "submit", "application"]):
        score -= 4
    if "contact" in path_low and not any(k in merged for k in ["pitch", "apply", "submit"]):
        score -= 2
    return score


def _resolve_final_url(url: str, timeout: int = 10) -> str:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.8"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final_url = resp.geturl()
        return _canonicalize_url(final_url) or _canonicalize_url(url)
    except Exception:  # noqa: BLE001
        return _canonicalize_url(url)


def _pick_best_submission_url(current_url: str, html: str) -> Tuple[str, str, int]:
    links = _extract_links(current_url, html)
    for form_url in _extract_embed_form_urls(current_url, html):
        links.append((form_url, "embedded form"))
    best_url = _canonicalize_url(current_url)
    best_score = 0
    best_note = ""

    for link_url, link_text in links:
        if not link_url:
            continue
        score = _score_submission_link(current_url, link_url, link_text)
        if score < 5:
            continue
        if score > best_score:
            best_score = score
            best_url = link_url
            best_note = sanitize(f"best-link:{link_text or '-'}", limit=120)
    picked = best_url or _canonicalize_url(current_url) or ""
    current_domain = _domain_key(current_url)
    picked_domain = _domain_key(picked)
    needs_resolve = (
        "/out/" in urllib.parse.urlsplit(picked).path.lower()
        or "/redirect" in picked.lower()
        or (picked_domain and current_domain and picked_domain != current_domain)
    )
    if picked and needs_resolve:
        picked = _resolve_final_url(picked)
    return picked, best_note, best_score


def _safe_iso_date(year: int, month: int, day: int) -> str:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def _choose_best_deadline(candidates: Sequence[Tuple[str, str]], *, today: date) -> Tuple[str, str]:
    if not candidates:
        return "", ""

    future: List[Tuple[date, str, str]] = []
    past: List[Tuple[date, str, str]] = []
    for iso_value, snippet in candidates:
        try:
            parsed = date.fromisoformat(iso_value)
        except ValueError:
            continue
        target = future if parsed >= today else past
        target.append((parsed, iso_value, snippet))

    if future:
        future.sort(key=lambda item: item[0])
        _, iso_value, snippet = future[0]
        return sanitize(snippet, limit=180), iso_value
    if past:
        past.sort(key=lambda item: item[0], reverse=True)
        _, iso_value, snippet = past[0]
        return sanitize(snippet, limit=180), iso_value
    return "", ""


def _extract_deadline(page_text: str, *, today: Optional[date] = None) -> Tuple[str, str]:
    if not page_text:
        return "", ""

    today = today or datetime.now(timezone.utc).date()
    text = sanitize(page_text, limit=12000)
    text_low = text.lower()

    snippets: List[str] = []
    for marker in DEADLINE_MARKERS:
        start = 0
        while True:
            idx = text_low.find(marker, start)
            if idx < 0:
                break
            snippets.append(text[max(0, idx - 80) : min(len(text), idx + 180)])
            start = idx + len(marker)
    if not snippets:
        snippets = [text]

    candidates: List[Tuple[str, str]] = []

    def add_candidate(year: int, month: int, day: int, snippet: str) -> None:
        iso_value = _safe_iso_date(year, month, day)
        if iso_value:
            candidates.append((iso_value, snippet))

    def inferred_year(month: int, day: int, explicit_year: str) -> int:
        if explicit_year:
            return int(explicit_year)
        current_year = today.year
        current_value = _safe_iso_date(current_year, month, day)
        if current_value:
            try:
                parsed = date.fromisoformat(current_value)
                if parsed < today and (today - parsed).days > 45:
                    return current_year + 1
            except ValueError:
                pass
        return current_year

    for snippet in snippets:
        for match in re.finditer(r"\b(20\d{2})[./-](\d{1,2})[./-](\d{1,2})\b", snippet):
            add_candidate(int(match.group(1)), int(match.group(2)), int(match.group(3)), snippet)

        for match in re.finditer(r"\b(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일?\b", snippet):
            add_candidate(int(match.group(1)), int(match.group(2)), int(match.group(3)), snippet)

        for match in re.finditer(
            r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
            r"sep(?:tember)?|sept(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})(?:,?\s*(20\d{2}))?\b",
            snippet,
            flags=re.IGNORECASE,
        ):
            month = MONTH_NAME_TO_NUMBER[match.group(1).lower()]
            day = int(match.group(2))
            year = inferred_year(month, day, match.group(3) or "")
            add_candidate(year, month, day, snippet)

        for match in re.finditer(
            r"\b(\d{1,2})\s+"
            r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
            r"sep(?:tember)?|sept(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?:,?\s*(20\d{2}))?\b",
            snippet,
            flags=re.IGNORECASE,
        ):
            month = MONTH_NAME_TO_NUMBER[match.group(2).lower()]
            day = int(match.group(1))
            year = inferred_year(month, day, match.group(3) or "")
            add_candidate(year, month, day, snippet)

    return _choose_best_deadline(candidates, today=today)


def _classify_status(page_text: str, *, deadline_text: str = "") -> str:
    text = page_text.lower()
    if any(marker in text for marker in CLOSED_MARKERS):
        return "closed"
    if any(marker in text for marker in ROLLING_MARKERS):
        return "rolling"
    if deadline_text or any(marker in text for marker in DEADLINE_MARKERS):
        return "deadline"
    return "open"


def _classify_org_type(page_text: str) -> str:
    text = page_text.lower()
    accel_score = sum(1 for k in ["accelerator", "cohort", "batch", "program"] if k in text)
    vc_score = sum(1 for k in ["venture capital", "vc", "fund", "invest"] if k in text)
    syndicate_score = sum(1 for k in ["syndicate", "angel list", "angel syndicate"] if k in text)
    grant_score = sum(1 for k in ["grant", "grants", "foundation", "ecosystem fund"] if k in text)

    if grant_score > max(accel_score, vc_score, syndicate_score) and grant_score > 0:
        return "Grant"
    if accel_score > max(vc_score, syndicate_score) and accel_score > 0:
        return "Accelerator"
    if syndicate_score > vc_score and syndicate_score > 0:
        return "Angel syndicate"
    if vc_score > 0:
        return "VC"
    return "Unknown"


def _detect_requirements(page_text: str) -> str:
    text = page_text.lower()
    hits = [label for needle, label in REQUIREMENT_MARKERS if needle in text]
    uniq = list(dict.fromkeys(hits))
    return ", ".join(uniq[:4])


def _embed_marker_has_submission_context(html_low: str, marker: str) -> bool:
    pos = html_low.find(marker)
    while pos >= 0:
        snippet = html_low[max(0, pos - 280) : pos + 280]
        if any(k in snippet for k in ["apply", "pitch", "submit", "application", "grant"]):
            return True
        pos = html_low.find(marker, pos + len(marker))
    return False


def _extract_pitch_emails(page_text: str) -> List[str]:
    emails = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", page_text)
    out: List[str] = []
    for email in emails:
        e = email.lower()
        if any(tok in e for tok in ["pitch", "deal", "founder", "invest", "team"]):
            out.append(e)
    return list(dict.fromkeys(out))[:4]


def _build_snapshot(text: str, text_low: str, phrase_hits: Sequence[str], path_hits: Sequence[str]) -> str:
    anchor = ""
    if phrase_hits:
        anchor = phrase_hits[0]
    elif path_hits:
        anchor = path_hits[0]

    if anchor:
        idx = text_low.find(anchor)
        if idx >= 0:
            start = max(0, idx - 120)
            end = min(len(text), idx + 220)
            return sanitize(text[start:end], limit=360)
    return sanitize(text[:320], limit=360)


def _evaluate_page(url: str, source_url: str, html: str, org_hint: str) -> Optional[SubmissionTarget]:
    html_low = html.lower()
    text = _strip_tags(html)
    text_low = text.lower()
    parsed = urllib.parse.urlsplit(url)
    path_low = parsed.path.lower()

    domain = _domain_key(url)
    if not _is_target_domain(domain):
        return None

    path_hits = [hint for hint in STRONG_PATH_HINTS if hint in path_low]
    phrase_hits = [p for p in SUBMISSION_PHRASES if p in text_low]
    strong_phrase_hits = [p for p in phrase_hits if p in STRONG_SUBMISSION_PHRASES]
    has_form = "<form" in html_low
    embed_hits = [m for m in FORM_EMBED_MARKERS if m in html_low and _embed_marker_has_submission_context(html_low, m)]
    intro_only = any(m in text_low for m in INTRO_ONLY_MARKERS)
    pitch_emails = _extract_pitch_emails(text)
    direct_form_url = _looks_actionable_form_url(url)
    direct_form_host = _looks_form_host(url)

    # Avoid generic contact pages unless they clearly indicate pitch submission.
    if "contact" in path_low:
        contact_strong_phrases = {"pitch us", "submit your pitch", "deal submission", "founder application"}
        has_contact_phrase = any(p in contact_strong_phrases for p in phrase_hits)
        if not (has_contact_phrase or embed_hits or pitch_emails or intro_only):
            return None

    score = 0
    evidence: List[str] = []

    if path_hits:
        score += min(4, len(path_hits) + 1)
        evidence.append(f"path:{','.join(path_hits[:3])}")
    if phrase_hits:
        weak_hits = max(0, len(phrase_hits) - len(strong_phrase_hits))
        score += min(6, len(strong_phrase_hits) * 2 + weak_hits)
        evidence.append(f"phrase:{(strong_phrase_hits[0] if strong_phrase_hits else phrase_hits[0])}")
    if has_form:
        score += 4
        evidence.append("html:form")
    if embed_hits:
        score += 4
        evidence.append(f"embed:{embed_hits[0]}")
    if direct_form_url:
        score += 6
        evidence.append("url:direct-form")
    elif direct_form_host:
        score += 2
        evidence.append("url:form-host")
    if intro_only:
        score += 1
        evidence.append("policy:intro-only")
    if pitch_emails:
        score += 3
        evidence.append(f"email:{pitch_emails[0]}")

    if score < 4:
        return None

    intent_signal = bool(path_hits or phrase_hits or embed_hits or pitch_emails or intro_only or direct_form_url)
    if not intent_signal:
        return None

    relevance = sum(1 for marker in RELEVANCE_MARKERS if marker in text_low)
    if (
        relevance == 0
        and not direct_form_url
        and not direct_form_host
        and not any(k in org_hint.lower() for k in ["capital", "ventures", "accelerator", "fund", "grant"])
    ):
        return None

    submission_type = "unknown"
    if has_form or embed_hits or direct_form_url:
        submission_type = "form"
    elif pitch_emails:
        submission_type = "email"
    elif intro_only:
        submission_type = "intro-only"

    deadline_text, deadline_date = _extract_deadline(text)
    status = _classify_status(text, deadline_text=deadline_text)
    requirements = _detect_requirements(text)
    title = _title_from_html(html)
    org_name = _normalize_org_name(_infer_org_name(domain=domain, title=title, hint=org_hint), domain)
    org_type = _classify_org_type(text)

    notes_parts: List[str] = []
    if status == "deadline":
        notes_parts.append("deadline-like wording detected")
    if deadline_text:
        notes_parts.append(f"deadline: {deadline_text}")
    if intro_only:
        notes_parts.append("warm-intro/referral wording detected")
    if pitch_emails:
        notes_parts.append(f"pitch email: {pitch_emails[0]}")
    if title:
        notes_parts.append(f"title: {title}")

    if direct_form_url:
        submission_url = _canonicalize_url(url)
        link_note = "direct-form"
        link_score = 10
    else:
        submission_url, link_note, link_score = _pick_best_submission_url(url, html)
    if not submission_url:
        return None
    submission_url_low = submission_url.lower()
    if org_type == "VC":
        org_name_low = org_name.lower()
        if "grant" in org_name_low or "grant" in submission_url_low:
            org_type = "Grant"
        elif any(k in submission_url_low for k in ["accelerator", "cohort", "batch", "program"]):
            org_type = "Accelerator"
    has_submission_url_signal = (
        _looks_actionable_form_url(submission_url)
        or any(
            token in submission_url_low
            for token in ["apply", "pitch", "submit", "grant", "application", "/form", "typeform", "airtable"]
        )
    )
    if link_score > 0:
        score += min(6, link_score)
    if link_note:
        evidence.append(link_note)
        if submission_url != _canonicalize_url(url):
            notes_parts.append("submission link selected from page anchors")

    if submission_type == "unknown":
        note_low = link_note.lower()
        if (
            _looks_actionable_form_url(submission_url)
            or "/apply" in submission_url.lower()
            or "/pitch" in submission_url.lower()
            or "/submit" in submission_url.lower()
            or link_score >= 8
            or any(k in note_low for k in ["apply", "pitch", "submit", "application"])
        ):
            submission_type = "form"

    # Reject weak article/news URLs unless they expose explicit submission mechanics.
    if not has_submission_url_signal and not pitch_emails and not intro_only:
        has_explicit_submission_page = bool(strong_phrase_hits and (has_form or embed_hits))
        if not has_explicit_submission_page:
            return None

    snapshot = _build_snapshot(text=text, text_low=text_low, phrase_hits=phrase_hits, path_hits=path_hits)

    return SubmissionTarget(
        org_name=org_name,
        org_type=org_type,
        domain=domain,
        source_url=source_url,
        submission_url=submission_url,
        submission_type=submission_type,
        status=status,
        requirements=requirements,
        notes=sanitize("; ".join(notes_parts), limit=500),
        evidence=sanitize(" | ".join(evidence), limit=500),
        source_page_snapshot=snapshot,
        deadline_text=deadline_text,
        deadline_date=deadline_date,
        score=score,
    )


def _candidate_rank(target: SubmissionTarget) -> Tuple[int, int, int, int, int]:
    submission_rank = {"form": 0, "email": 1, "intro-only": 2, "unknown": 3}.get(target.submission_type, 3)
    status_rank = {"open": 0, "rolling": 1, "deadline": 2, "closed": 3}.get(target.status, 4)
    url_low = target.submission_url.lower()
    url_rank = 1
    if any(k in url_low for k in ["apply", "pitch", "submit", "form", "typeform", "airtable", "forms.gle"]):
        url_rank = 0
    return (submission_rank, status_rank, url_rank, -target.score, len(target.submission_url))


def _scan_failure(seed: DiscoverySeed, *, page_url: str, stage: str, exc: Exception) -> ScanFailure:
    return ScanFailure(
        seed_url=seed.url,
        org_name_hint=seed.org_name_hint,
        seed_source=seed.source,
        page_url=page_url,
        stage=stage,
        error_type=exc.__class__.__name__,
        error_message=str(exc),
    )


def _scan_site(seed: DiscoverySeed, max_pages: int = 6, http_timeout: int = 10) -> ScanSiteResult:
    root = _canonicalize_url(seed.url)
    if not root:
        return ScanSiteResult(target=None, failures=[])

    root_home = urllib.parse.urlunsplit((urllib.parse.urlsplit(root).scheme, urllib.parse.urlsplit(root).netloc, "/", "", ""))
    queue: deque[str] = deque([root])
    if root_home != root:
        queue.append(root_home)

    visited: set[str] = set()
    best: Optional[SubmissionTarget] = None
    failures: List[ScanFailure] = []

    while queue and len(visited) < max_pages:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)

        try:
            html, resolved_current = _fetch_html(current, timeout=http_timeout)
        except Exception as exc:  # noqa: BLE001
            failures.append(_scan_failure(seed, page_url=current, stage="fetch", exc=exc))
            continue
        if not html:
            continue
        page_url = resolved_current or current
        if page_url not in visited:
            visited.add(page_url)

        try:
            candidate = _evaluate_page(url=page_url, source_url=page_url, html=html, org_hint=seed.org_name_hint)
        except Exception as exc:  # noqa: BLE001
            failures.append(_scan_failure(seed, page_url=page_url, stage="evaluate", exc=exc))
            candidate = None
        if candidate and (best is None or _candidate_rank(candidate) < _candidate_rank(best)):
            best = candidate

        try:
            links = _extract_links(page_url, html)
        except Exception as exc:  # noqa: BLE001
            failures.append(_scan_failure(seed, page_url=page_url, stage="extract_links", exc=exc))
            links = []
        for link_url, link_text in links:
            if link_url in visited:
                continue
            if not _looks_submission_link(link_url, link_text):
                continue
            if _same_domain(root, link_url) or _same_org_family(root, link_url):
                queue.append(link_url)

    return ScanSiteResult(target=best, failures=failures)


def _decode_ddg_url(url: str) -> str:
    clean = unescape(url.strip())
    if clean.startswith("//"):
        clean = "https:" + clean
    if "duckduckgo.com/l/?" not in clean:
        return clean
    parsed = urllib.parse.urlsplit(clean)
    q = urllib.parse.parse_qs(parsed.query)
    uddg = q.get("uddg", [""])[0]
    return urllib.parse.unquote(uddg) if uddg else clean


def _search_duckduckgo(query: str, max_results: int = 8) -> List[Tuple[str, str]]:
    url = "https://duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=25) as resp:
        html = resp.read().decode("utf-8", errors="ignore")

    out: List[Tuple[str, str]] = []
    pattern = re.compile(
        r"<a[^>]+class=\"[^\"]*result__a[^\"]*\"[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for href, label_html in pattern.findall(html):
        target = _canonicalize_url(_decode_ddg_url(href))
        if not target:
            continue
        label = sanitize(_strip_tags(label_html), limit=160)
        out.append((target, label))
        if len(out) >= max_results:
            break
    return out


def _load_submission_target_seeds(db_path: str, limit: int = 240) -> List[DiscoverySeed]:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            SELECT submission_url, source_url, org_name, score, status
            FROM submission_targets
            WHERE submission_url <> '' OR source_url <> ''
            ORDER BY score DESC, last_checked_at DESC, id DESC
            LIMIT ?
            """,
            (max(200, limit * 3),),
        )
    except sqlite3.OperationalError:
        conn.close()
        return []

    ranked_rows: List[Tuple[int, str, str]] = []
    for submission_url, source_url, org_name, score, status in cur.fetchall():
        preferred_url = str(submission_url or "").strip() or str(source_url or "").strip()
        url = _normalize_seed_url(preferred_url)
        if not url:
            continue
        ranked = int(score or 0)
        status_low = str(status or "").strip().lower()
        if status_low in {"open", "rolling"}:
            ranked += 4
        elif status_low == "deadline":
            ranked += 2
        ranked_rows.append((ranked, url, str(org_name or "")))

    ranked_rows.sort(key=lambda x: x[0], reverse=True)
    out: List[DiscoverySeed] = []
    seen_keys: set[str] = set()
    for _, url, org_name in ranked_rows:
        key = _seed_identity(url)
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(
            DiscoverySeed(
                url=url,
                org_name_hint=sanitize(org_name, limit=120),
                source="submission-db",
            )
        )
        if len(out) >= limit:
            break
    conn.close()
    return out


def _load_fundraise_seeds(db_path: str, limit: int = 300) -> List[DiscoverySeed]:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            SELECT website, org_name, category, status, notes
            FROM fundraising_records
            WHERE website <> ''
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1200, limit * 6),),
        )
    except sqlite3.OperationalError:
        conn.close()
        return []

    def rank_seed(website: str, org_name: str, category: str, status: str, notes: str) -> int:
        w = (website or "").lower()
        o = (org_name or "").lower()
        c = (category or "").lower()
        s = (status or "").lower()
        n = (notes or "").lower()
        score = 0
        if any(k in w for k in ["apply", "pitch", "submit", "grant", "accelerator", "form", "founder"]):
            score += 8
        if any(k in w for k in ["vc", "venture", "capital", "fund"]):
            score += 4
        if any(k in o for k in ["vc", "venture", "capital", "fund", "accelerator", "grant", "foundation", "dao"]):
            score += 4
        if "accelerator_program" in c or "grants_program" in c:
            score += 7
        elif any(k in c for k in ["web3_vc", "web2_vc", "xlsx:web3 vc", "xlsx:web2 vc"]):
            score += 5
        elif "vc_contact" in c:
            score += 2
        if any(k in s for k in ["진행", "open", "rolling", "활성"]):
            score += 2
        if any(k in s for k in ["closed", "마감"]):
            score -= 2
        if any(k in n for k in ["apply", "pitch", "submit", "deadline", "rolling"]):
            score += 2
        if _is_content_like_url(w):
            score -= 5
        return score

    ranked_rows: List[Tuple[int, str, str, str]] = []
    for website, org_name, category, status, notes in cur.fetchall():
        website_str = str(website or "")
        category_str = str(category or "")
        status_str = str(status or "")
        notes_str = str(notes or "")
        seed_url = _normalize_fundraise_seed_url(
            website_str,
            category=category_str,
            status=status_str,
            notes=notes_str,
        )
        if not seed_url:
            continue
        ranked_rows.append(
            (
                rank_seed(
                    website_str,
                    str(org_name or ""),
                    category_str,
                    status_str,
                    notes_str,
                ),
                seed_url,
                str(org_name or ""),
                category_str,
            )
        )

    best_by_identity: Dict[str, Tuple[int, str, str, str]] = {}
    for row in ranked_rows:
        key = _seed_identity(row[1])
        if not key:
            continue
        prev = best_by_identity.get(key)
        if prev is None or row[0] > prev[0]:
            best_by_identity[key] = row

    ranked_rows = sorted(best_by_identity.values(), key=lambda x: x[0], reverse=True)
    seen_keys: set[str] = set()
    out: List[DiscoverySeed] = []
    for _, url, org_name, category in ranked_rows:
        key = _seed_identity(url)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(
            DiscoverySeed(
                url=url,
                org_name_hint=sanitize(org_name, limit=120),
                source=f"fundraise-db:{sanitize(category, limit=80)}",
            )
        )
        if len(out) >= limit:
            break
    conn.close()
    return out


def _dedupe_seeds(seeds: Iterable[DiscoverySeed], *, max_sites: int) -> List[DiscoverySeed]:
    out: List[DiscoverySeed] = []
    seen: set[str] = set()
    for seed in seeds:
        domain = _domain_key(seed.url)
        key = _seed_identity(seed.url)
        if not domain or not key or key in seen:
            continue
        if not _is_target_domain(domain):
            continue
        seen.add(key)
        out.append(seed)
        if len(out) >= max_sites:
            break
    return out


def _load_query_file(path: str) -> List[str]:
    p = Path(path).expanduser()
    if not p.exists():
        return []
    out: List[str] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def _parse_terms(raw: str) -> List[str]:
    terms = [t.strip() for t in raw.split(",") if t.strip()]
    return list(dict.fromkeys(terms))


def _extend_queries_with_focus(queries: List[str], *, sectors: Sequence[str], stages: Sequence[str], regions: Sequence[str]) -> List[str]:
    out = list(queries)
    templates = [
        '"{term}" "pitch us" "venture capital"',
        '"{term}" "submit your pitch" vc',
        '"{term}" accelerator apply',
        '"{term}" "startup program" apply',
    ]
    for term in list(sectors) + list(stages) + list(regions):
        for t in templates:
            out.append(t.format(term=term))
    deduped: List[str] = []
    seen: set[str] = set()
    for q in out:
        k = q.strip().lower()
        if not k or k in seen:
            continue
        seen.add(k)
        deduped.append(q)
    return deduped


def _rows_to_json(rows: Sequence[sqlite3.Row]) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    for row in rows:
        out.append(
            {
                "fingerprint": row["fingerprint"],
                "org_name": row["org_name"],
                "org_type": row["org_type"],
                "domain": row["domain"],
                "source_url": row["source_url"],
                "submission_url": row["submission_url"],
                "submission_type": row["submission_type"],
                "status": row["status"],
                "requirements": row["requirements"],
                "notes": row["notes"],
                "source_page_snapshot": row["source_page_snapshot"],
                "deadline_text": row["deadline_text"],
                "deadline_date": row["deadline_date"],
                "evidence": row["evidence"],
                "score": row["score"],
                "last_checked_at": row["last_checked_at"],
            }
        )
    return out


def _priority_for_row(row: sqlite3.Row) -> str:
    status = str(row["status"]).strip().lower()
    submission_type = str(row["submission_type"]).strip().lower()
    score = int(row["score"] or 0)

    if status in {"open", "rolling"} and submission_type == "form" and score >= 12:
        return "P0"
    if status in {"open", "rolling"} and score >= 9:
        return "P1"
    if status == "deadline" and score >= 8:
        return "P2"
    return "P3"


def _status_rank(status: str) -> int:
    s = (status or "").strip().lower()
    if s == "open":
        return 0
    if s == "rolling":
        return 1
    if s == "deadline":
        return 2
    if s == "closed":
        return 3
    return 4


def _priority_rank(priority: str) -> int:
    p = (priority or "").strip().upper()
    if p == "P0":
        return 0
    if p == "P1":
        return 1
    if p == "P2":
        return 2
    return 3


def _sort_rows_for_report(rows: Sequence[sqlite3.Row]) -> List[sqlite3.Row]:
    return sorted(
        rows,
        key=lambda r: (
            _priority_rank(_priority_for_row(r)),
            _status_rank(str(r["status"])),
            -int(r["score"] or 0),
            str(r["org_name"]).lower(),
        ),
    )


def _render_submission_report(rows: Sequence[sqlite3.Row], events: Sequence[sqlite3.Row]) -> str:
    lines: List[str] = []
    ordered_rows = _sort_rows_for_report(rows)

    lines.append("# VC Submission Targets")
    lines.append("")
    lines.append(f"_Generated at: {now_utc_iso()}_")
    lines.append("")

    total_targets = len(ordered_rows)
    open_count = sum(1 for r in ordered_rows if str(r["status"]).lower() == "open")
    rolling_count = sum(1 for r in ordered_rows if str(r["status"]).lower() == "rolling")
    deadline_count = sum(1 for r in ordered_rows if str(r["status"]).lower() == "deadline")
    closed_count = sum(1 for r in ordered_rows if str(r["status"]).lower() == "closed")
    high_priority_count = sum(1 for r in ordered_rows if _priority_for_row(r) in {"P0", "P1"})

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- total_targets: {total_targets}")
    lines.append(f"- high_priority(P0/P1): {high_priority_count}")
    lines.append(f"- status_breakdown: open={open_count}, rolling={rolling_count}, deadline={deadline_count}, closed={closed_count}")
    lines.append(f"- recent_events: {len(events)}")
    lines.append("")

    if ordered_rows:
        lines.append("## Quick Table")
        lines.append("")
        lines.append("| # | Priority | Organization | Org Type | Submission | Status | Deadline | Score | Link |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for idx, row in enumerate(ordered_rows, start=1):
            priority = _priority_for_row(row)
            org_name = sanitize(str(row["org_name"]), limit=90).replace("|", "/")
            org_type = str(row["org_type"]).replace("|", "/")
            submission_type = str(row["submission_type"]).replace("|", "/")
            status = str(row["status"]).replace("|", "/")
            deadline = str(row["deadline_date"] or row["deadline_text"] or "-").replace("|", "/")
            score = int(row["score"] or 0)
            url = str(row["submission_url"])
            link = f"[open]({url})"
            lines.append(
                f"| {idx} | {priority} | {org_name} | {org_type} | {submission_type} | {status} | {deadline} | {score} | {link} |"
            )
        lines.append("")

        top_open = [r for r in ordered_rows if str(r["status"]).lower() in {"open", "rolling"}][:5]
        if top_open:
            lines.append("## Immediate Action Queue")
            lines.append("")
            for idx, row in enumerate(top_open, start=1):
                priority = _priority_for_row(row)
                org_name = sanitize(str(row["org_name"]), limit=100)
                requirements = sanitize(str(row["requirements"] or "-"), limit=140)
                deadline = str(row["deadline_date"] or row["deadline_text"] or "-")
                lines.append(
                    f"{idx}. [{priority}] {org_name} | status={row['status']} | deadline={deadline} | {row['submission_url']} | requirements={requirements}"
                )
            lines.append("")

    if events:
        lines.append("## Recent Changes")
        lines.append("")
        for e in events:
            lines.append(f"- {e['detected_at']} | {e['event_type']} | {e['domain']} | {e['submission_url']}")
        lines.append("")

    lines.append("## Detailed Targets")
    lines.append("")
    for idx, row in enumerate(ordered_rows, start=1):
        lines.append(f"### {idx}. {row['org_name']} ({row['org_type']})")
        lines.append(f"- priority: {_priority_for_row(row)}")
        lines.append(f"- domain: {row['domain']}")
        lines.append(f"- official_page: {row['source_url']}")
        lines.append(f"- submission_url: {row['submission_url']}")
        lines.append(f"- submission_type: {row['submission_type']}")
        lines.append(f"- status: {row['status']}")
        if row["deadline_date"] or row["deadline_text"]:
            lines.append(f"- deadline: {row['deadline_date'] or row['deadline_text']}")
        lines.append(f"- score: {row['score']}")
        if row["requirements"]:
            lines.append(f"- requirements: {row['requirements']}")
        if row["notes"]:
            lines.append(f"- notes: {row['notes']}")
        if row["evidence"]:
            lines.append(f"- evidence: {row['evidence']}")
        if row["source_page_snapshot"]:
            lines.append(f"- source_page_snapshot: {sanitize(row['source_page_snapshot'], limit=260)}")
        lines.append(f"- last_checked_at: {row['last_checked_at']}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def submission_scan_command(args: argparse.Namespace) -> int:
    store = SubmissionStore(args.db)

    manual_seeds = [s.strip() for s in (args.seed_urls or "").split(",") if s.strip()]
    seeds: List[DiscoverySeed] = []
    only_retry_mode = bool(args.failures_only or args.review_targets_only)
    if not only_retry_mode:
        seeds.extend(DiscoverySeed(url=_normalize_seed_url(u), org_name_hint="", source="manual") for u in manual_seeds)
        # Re-check previously known submission targets first, then expand to fundraising DB websites.
        seeds.extend(_load_submission_target_seeds(args.db, limit=max(120, min(args.max_sites * 3, 360))))
        if args.from_fundraise:
            seeds.extend(_load_fundraise_seeds(args.db, limit=args.fundraise_seed_limit))

    resumed_failure_seeds: List[DiscoverySeed] = []
    if args.resume_failures or args.failures_only:
        resumed_failure_seeds = store.load_pending_failure_seeds(limit=args.failure_limit)
        seeds = resumed_failure_seeds + seeds

    resumed_review_target_seeds: List[DiscoverySeed] = []
    if args.review_targets or args.review_targets_only:
        resumed_review_target_seeds = store.load_review_target_seeds(limit=args.review_target_limit)
        seeds = resumed_review_target_seeds + seeds

    queries: List[str] = list(args.query or [])
    if args.query_file:
        queries.extend(_load_query_file(args.query_file))
    if not queries and not args.skip_search and not only_retry_mode:
        queries = list(DEFAULT_DISCOVERY_QUERIES)

    sectors = _parse_terms(args.sector)
    stages = _parse_terms(args.stage)
    regions = _parse_terms(args.region)
    queries = _extend_queries_with_focus(queries, sectors=sectors, stages=stages, regions=regions)

    if not args.skip_search and not only_retry_mode:
        for query in queries:
            try:
                hits = _search_duckduckgo(query, max_results=args.max_results_per_query)
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] search failed query={query!r}: {exc}")
                continue
            for url, title in hits:
                seeds.append(DiscoverySeed(url=_normalize_seed_url(url), org_name_hint=title, source=f"search:{query}"))

    deduped = _dedupe_seeds(seeds, max_sites=args.max_sites)
    source_counts_raw: Dict[str, int] = {}
    for seed in seeds:
        if not seed.url:
            continue
        key = seed.source.split(":", 1)[0]
        source_counts_raw[key] = source_counts_raw.get(key, 0) + 1

    source_counts_deduped: Dict[str, int] = {}
    for seed in deduped:
        key = seed.source.split(":", 1)[0]
        source_counts_deduped[key] = source_counts_deduped.get(key, 0) + 1

    raw_desc = ", ".join(f"{k}={v}" for k, v in sorted(source_counts_raw.items())) or "none"
    deduped_desc = ", ".join(f"{k}={v}" for k, v in sorted(source_counts_deduped.items())) or "none"
    print(f"[info] seeds={len(deduped)} (raw={len(seeds)})")
    if resumed_failure_seeds:
        print(f"[info] resumed_failures={len(resumed_failure_seeds)}")
    if resumed_review_target_seeds:
        print(f"[info] resumed_review_targets={len(resumed_review_target_seeds)}")
    print(f"[info] seed_sources(raw)={raw_desc}")
    print(f"[info] seed_sources(deduped)={deduped_desc}")
    preview_groups: Dict[str, List[DiscoverySeed]] = {}
    for seed in deduped:
        key = seed.source.split(":", 1)[0]
        preview_groups.setdefault(key, []).append(seed)
    for key in sorted(preview_groups):
        for seed in preview_groups[key][:6]:
            print(
                f"[seed:{key}] src={seed.source} org={seed.org_name_hint or '-'} "
                f"url={seed.url}"
            )

    found: List[SubmissionTarget] = []
    prunable_domains: List[str] = []
    failure_rows = 0
    resolved_failures = 0
    for idx, seed in enumerate(deduped, start=1):
        if not seed.url:
            continue
        domain = _domain_key(seed.url)
        result = _scan_site(seed, max_pages=args.max_pages_per_site, http_timeout=args.http_timeout)
        attempted_at = now_utc_iso()
        if result.failures:
            for failure in result.failures:
                store.record_scan_failure(failure, detected_at=attempted_at)
            failure_rows += len(result.failures)
            print(
                f"[warn] {idx}/{len(deduped)} {seed.org_name_hint or domain or seed.url} | "
                f"failures={len(result.failures)}"
            )
        target = result.target
        if not target:
            if not result.failures:
                prunable_domains.append(domain)
                resolved_failures += store.resolve_scan_failures(seed.url, resolved_at=attempted_at)
            continue
        found.append(target)
        prunable_domains.append(domain)
        resolved_failures += store.resolve_scan_failures(seed.url, resolved_at=attempted_at)
        print(
            f"[hit] {idx}/{len(deduped)} {target.org_name} | {target.submission_type} | "
            f"{target.status} | deadline={target.deadline_date or '-'} | score={target.score} | "
            f"{target.submission_url}"
        )

    changed = store.upsert_targets(found)
    pruned = store.prune_scanned_domains(prunable_domains, [t.fingerprint for t in found])
    cleaned = store.cleanup_noise()
    rows = store.list_targets(
        limit=args.report_limit,
        status=args.status_filter,
        org_type=args.org_type_filter,
        min_score=args.min_score,
    )
    events = store.list_events(limit=args.event_limit)

    output = Path(args.output).expanduser()
    ensure_parent_dir(str(output))
    output.write_text(_render_submission_report(rows, events), encoding="utf-8")

    if args.json_output:
        json_path = Path(args.json_output).expanduser()
        ensure_parent_dir(str(json_path))
        payload = {
            "generated_at": now_utc_iso(),
            "filters": {
                "status": args.status_filter,
                "org_type": args.org_type_filter,
                "min_score": args.min_score,
            },
            "items": _rows_to_json(rows),
        }
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[done] json={json_path} rows={len(rows)}")

    print(
        f"[done] scanned={len(deduped)} found={len(found)} changed={changed} pruned={pruned} cleaned={cleaned} "
        f"failures={failure_rows} resolved_failures={resolved_failures} "
        f"report={output}"
    )
    return 0


def submission_list_command(args: argparse.Namespace) -> int:
    store = SubmissionStore(args.db)
    rows = store.list_targets(
        limit=args.limit,
        status=args.status_filter,
        org_type=args.org_type_filter,
        min_score=args.min_score,
    )
    if not rows:
        print("(no submission targets)")
        return 0

    ordered_rows = _sort_rows_for_report(rows)
    print("priority\torg_name\torg_type\tsubmission_type\tstatus\tdeadline\tscore\tofficial_page\tsubmission_url")
    for row in ordered_rows:
        print(
            "\t".join(
                [
                    _priority_for_row(row),
                    str(row["org_name"]),
                    str(row["org_type"]),
                    str(row["submission_type"]),
                    str(row["status"]),
                    str(row["deadline_date"] or row["deadline_text"]),
                    str(row["score"]),
                    str(row["source_url"]),
                    str(row["submission_url"]),
                ]
            )
        )
    return 0


def submission_report_command(args: argparse.Namespace) -> int:
    store = SubmissionStore(args.db)
    rows = store.list_targets(
        limit=args.limit,
        status=args.status_filter,
        org_type=args.org_type_filter,
        min_score=args.min_score,
    )
    events = store.list_events(limit=args.event_limit)
    output = Path(args.output).expanduser()
    ensure_parent_dir(str(output))
    output.write_text(_render_submission_report(rows, events), encoding="utf-8")
    print(f"[done] report={output} rows={len(rows)} events={len(events)}")
    return 0


def submission_export_command(args: argparse.Namespace) -> int:
    store = SubmissionStore(args.db)
    rows = store.list_targets(
        limit=args.limit,
        status=args.status_filter,
        org_type=args.org_type_filter,
        min_score=args.min_score,
    )
    output = Path(args.output).expanduser()
    ensure_parent_dir(str(output))
    payload = {
        "generated_at": now_utc_iso(),
        "schema": {
            "org_name": "string",
            "org_type": "VC|Accelerator|Grant|Angel syndicate|Unknown",
            "source_url": "string(url)",
            "submission_url": "string(url)",
            "submission_type": "form|email|intro-only|unknown",
            "status": "open|rolling|deadline|closed",
            "deadline_text": "string",
            "deadline_date": "string(yyyy-mm-dd)",
            "requirements": "string",
            "notes": "string",
            "source_page_snapshot": "string",
            "evidence": "string",
            "score": "number",
            "last_checked_at": "string(iso8601)",
        },
        "items": _rows_to_json(rows),
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] export={output} rows={len(rows)}")
    return 0


def scan_failures_command(args: argparse.Namespace) -> int:
    store = SubmissionStore(args.db)
    rows = store.list_scan_failures(limit=args.limit, status=args.status)
    if not rows:
        print("(no scan failures)")
        return 0

    print(
        "id\tstatus\tretry_count\tseed_source\torg_name\tstage\terror_type\tseed_url\tpage_url\tlast_attempted_at\terror_message"
    )
    for row in rows:
        print(
            "\t".join(
                [
                    str(row["id"]),
                    str(row["status"]),
                    str(row["retry_count"]),
                    str(row["seed_source"]),
                    str(row["org_name_hint"]),
                    str(row["stage"]),
                    str(row["error_type"]),
                    str(row["seed_url"]),
                    str(row["page_url"] or ""),
                    str(row["last_attempted_at"]),
                    str(row["error_message"]),
                ]
            )
    )
    return 0


def scan_failure_resolve_command(args: argparse.Namespace) -> int:
    store = SubmissionStore(args.db)
    changed = store.set_scan_failure_status(args.ref, status="resolved", changed_at=now_utc_iso())
    print(f"[done] resolved={changed} ref={args.ref}")
    return 0


def scan_failure_ignore_command(args: argparse.Namespace) -> int:
    store = SubmissionStore(args.db)
    changed = store.set_scan_failure_status(args.ref, status="ignored", changed_at=now_utc_iso())
    print(f"[done] ignored={changed} ref={args.ref}")
    return 0


def target_override_command(args: argparse.Namespace) -> int:
    store = SubmissionStore(args.db)
    row = store.override_target(
        args.ref,
        org_name=args.org_name,
        org_type=args.org_type,
        source_url=args.source_url,
        submission_url=args.submission_url,
        submission_type=args.submission_type,
        status=args.status,
        requirements=args.requirements,
        notes=args.notes,
        evidence=args.evidence,
        deadline_text=args.deadline_text,
        deadline_date=args.deadline_date,
        score=args.score,
    )
    if row is None:
        print(f"[error] target not found: {args.ref}")
        return 2
    print(
        "\t".join(
            [
                str(row["fingerprint"]),
                str(row["org_name"]),
                str(row["status"]),
                str(row["submission_type"]),
                str(row["deadline_date"] or row["deadline_text"]),
                str(row["submission_url"]),
            ]
        )
    )
    return 0
