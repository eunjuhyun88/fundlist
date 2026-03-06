from __future__ import annotations

import argparse
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from .store import ensure_parent_dir


TASK_STATES = {
    "not_started",
    "researching",
    "drafting",
    "waiting_assets",
    "ready_to_submit",
    "submitted",
    "follow_up_due",
    "won",
    "rejected",
    "archived",
}
TERMINAL_TASK_STATES = {"won", "rejected", "archived"}
TASK_BUCKETS = {"", "ready", "submitted", "followup", "blocked", "active", "today", "this_week", "closed"}
STATE_TRANSITIONS = {
    "not_started": {"researching", "archived"},
    "researching": {"drafting", "waiting_assets", "ready_to_submit", "archived"},
    "drafting": {"waiting_assets", "ready_to_submit", "archived"},
    "waiting_assets": {"drafting", "ready_to_submit", "archived"},
    "ready_to_submit": {"submitted", "drafting", "waiting_assets", "archived"},
    "submitted": {"follow_up_due", "won", "rejected", "archived"},
    "follow_up_due": {"submitted", "won", "rejected", "archived"},
    "won": {"archived"},
    "rejected": {"archived"},
    "archived": set(),
}


@dataclass
class CandidateMatch:
    row: sqlite3.Row
    match_score: int


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _tight_text(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z가-힣]+", "", (value or "").strip().lower())


def _parse_iso_date(value: str) -> Optional[date]:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except Exception:  # noqa: BLE001
        return None


def _days_until(value: str) -> Optional[int]:
    parsed = _parse_iso_date(value)
    if parsed is None:
        return None
    return (parsed - utc_today()).days


def _validate_state(value: str) -> str:
    state = _normalize_text(value).replace(" ", "_")
    if state not in TASK_STATES:
        raise ValueError(f"invalid submission_state: {value}")
    return state


def _can_transition(current: str, target: str) -> bool:
    if current == target:
        return True
    if target in TERMINAL_TASK_STATES:
        return True
    return target in STATE_TRANSITIONS.get(current, set())


def _target_status_rank(status: str) -> int:
    return {"deadline": 0, "open": 1, "rolling": 2, "unknown": 3, "closed": 4}.get(_normalize_text(status), 5)


def _task_bucket(row: sqlite3.Row) -> str:
    state = _normalize_text(str(row["submission_state"]))
    if state == "ready_to_submit":
        return "ready"
    if state == "submitted":
        return "submitted"
    if state == "follow_up_due":
        return "followup"
    if state == "waiting_assets":
        return "blocked"
    if state in TERMINAL_TASK_STATES:
        return "closed"

    due_date = str(row["due_date"] or "").strip()
    days_left = _days_until(due_date)
    if days_left is not None:
        if days_left <= 1:
            return "today"
        if days_left <= 7:
            return "this_week"
    return "active"


def _task_bucket_matches(row: sqlite3.Row, bucket: str) -> bool:
    if not bucket:
        return True
    return _task_bucket(row) == bucket


def _task_sort_key(row: sqlite3.Row) -> Tuple[int, int, str, str]:
    bucket = _task_bucket(row)
    bucket_rank = {
        "ready": 0,
        "today": 1,
        "this_week": 2,
        "followup": 3,
        "active": 4,
        "blocked": 5,
        "submitted": 6,
        "closed": 7,
    }.get(bucket, 99)
    return (
        bucket_rank,
        -int(row["priority_score"] or 0),
        str(row["due_date"] or "9999-12-31"),
        str(row["org_name"] or "").lower(),
    )


def _task_due_display(row: sqlite3.Row) -> str:
    state = _normalize_text(str(row["submission_state"] or ""))
    if state in {"submitted", "follow_up_due"} and str(row["follow_up_due_at"] or "").strip():
        return str(row["follow_up_due_at"])
    return str(row["due_date"] or row["follow_up_due_at"] or "")


def _candidate_match_score(row: sqlite3.Row, query: str) -> int:
    q = _normalize_text(query)
    q_tight = _tight_text(query)
    org = _normalize_text(str(row["org_name"] or ""))
    domain = _normalize_text(str(row["domain"] or ""))
    source_url = _normalize_text(str(row["source_url"] or ""))
    submission_url = _normalize_text(str(row["submission_url"] or ""))
    fingerprint = str(row["fingerprint"] or "").strip().lower()

    score = 0
    if q and q == org:
        score += 140
    elif q and q in org:
        score += 90
    if q and q in domain:
        score += 70
    if q and (q in source_url or q in submission_url):
        score += 60
    if q_tight:
        if q_tight == _tight_text(org):
            score += 120
        elif q_tight and q_tight in _tight_text(org):
            score += 75
        if q_tight in _tight_text(domain):
            score += 50
        if q_tight in _tight_text(source_url) or q_tight in _tight_text(submission_url):
            score += 45
        if q_tight == _tight_text(fingerprint):
            score += 250

    tokens = [tok for tok in re.split(r"\s+", q) if tok]
    blob = " | ".join([org, domain, source_url, submission_url])
    token_hits = sum(1 for tok in tokens if tok in blob)
    score += token_hits * 8
    score += min(20, int(row["score"] or 0))
    score += max(0, 10 - _target_status_rank(str(row["status"] or "")) * 2)
    return score


def _summarize_candidate(row: sqlite3.Row, *, include_fingerprint: bool = True) -> str:
    bits = [
        str(row["org_name"]),
        f"status={row['status']}",
        f"deadline={row['deadline_date'] or '-'}",
        f"score={row['score']}",
    ]
    if include_fingerprint:
        bits.append(f"fp={str(row['fingerprint'])[:12]}")
    bits.append(str(row["submission_url"] or row["source_url"] or "-"))
    return " | ".join(bits)


class SubmissionTaskStore:
    def __init__(self, db_path: str) -> None:
        ensure_parent_dir(db_path)
        self.conn = sqlite3.connect(db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS submission_tasks (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              workspace_key TEXT NOT NULL DEFAULT 'default',
              opportunity_fingerprint TEXT NOT NULL,
              org_name TEXT NOT NULL,
              program_name TEXT NOT NULL DEFAULT '',
              domain TEXT NOT NULL DEFAULT '',
              official_page TEXT NOT NULL DEFAULT '',
              submission_url TEXT NOT NULL DEFAULT '',
              opportunity_status TEXT NOT NULL DEFAULT '',
              submission_state TEXT NOT NULL,
              owner TEXT NOT NULL DEFAULT '',
              due_date TEXT NOT NULL DEFAULT '',
              priority_score INTEGER NOT NULL DEFAULT 0,
              fit_score INTEGER NOT NULL DEFAULT 0,
              recommended_action TEXT NOT NULL DEFAULT '',
              notes TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              submitted_at TEXT NOT NULL DEFAULT '',
              follow_up_due_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS submission_task_updates (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              task_id INTEGER NOT NULL,
              event_type TEXT NOT NULL,
              body TEXT NOT NULL,
              actor TEXT NOT NULL DEFAULT 'system',
              created_at TEXT NOT NULL,
              FOREIGN KEY(task_id) REFERENCES submission_tasks(id) ON DELETE CASCADE
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_submission_tasks_state ON submission_tasks(workspace_key, submission_state, updated_at DESC)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_submission_tasks_due ON submission_tasks(workspace_key, due_date, priority_score DESC)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_submission_tasks_fp ON submission_tasks(opportunity_fingerprint, updated_at DESC)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_submission_task_updates_time ON submission_task_updates(task_id, created_at DESC)"
        )
        self.conn.commit()

    def _table_exists(self, table: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table,),
        )
        return cur.fetchone() is not None

    def get_target_by_fingerprint(self, fingerprint: str) -> Optional[sqlite3.Row]:
        if not self._table_exists("submission_targets"):
            return None
        self.conn.row_factory = sqlite3.Row
        cur = self.conn.execute(
            """
            SELECT fingerprint, org_name, org_type, domain, source_url, submission_url,
                   submission_type, status, requirements, notes, deadline_text, deadline_date, score
            FROM submission_targets
            WHERE fingerprint = ?
            LIMIT 1
            """,
            (fingerprint.strip(),),
        )
        return cur.fetchone()

    def search_targets(self, query: str, *, limit: int = 8) -> List[CandidateMatch]:
        if not self._table_exists("submission_targets"):
            return []
        q = _normalize_text(query)
        q_like = f"%{q}%"
        q_tight = _tight_text(query)
        self.conn.row_factory = sqlite3.Row
        cur = self.conn.execute(
            """
            SELECT fingerprint, org_name, org_type, domain, source_url, submission_url,
                   submission_type, status, requirements, notes, deadline_text, deadline_date, score
            FROM submission_targets
            WHERE lower(org_name) LIKE ?
               OR lower(domain) LIKE ?
               OR lower(source_url) LIKE ?
               OR lower(submission_url) LIKE ?
            ORDER BY score DESC, last_checked_at DESC, id DESC
            LIMIT 40
            """,
            (q_like, q_like, q_like, q_like),
        )
        rows = list(cur.fetchall())
        if not rows and q_tight:
            cur = self.conn.execute(
                """
                SELECT fingerprint, org_name, org_type, domain, source_url, submission_url,
                       submission_type, status, requirements, notes, deadline_text, deadline_date, score
                FROM submission_targets
                ORDER BY score DESC, last_checked_at DESC, id DESC
                LIMIT 120
                """
            )
            rows = list(cur.fetchall())

        matched = [CandidateMatch(row=row, match_score=_candidate_match_score(row, query)) for row in rows]
        matched = [item for item in matched if item.match_score > 0]
        matched.sort(
            key=lambda item: (
                -item.match_score,
                _target_status_rank(str(item.row["status"] or "")),
                -int(item.row["score"] or 0),
                str(item.row["org_name"] or "").lower(),
            )
        )
        return matched[:limit]

    def resolve_target(self, query: str) -> Tuple[Optional[sqlite3.Row], List[CandidateMatch], str]:
        query = query.strip()
        if not query:
            return None, [], "target query is required"
        if re.fullmatch(r"[0-9a-fA-F]{64}", query):
            row = self.get_target_by_fingerprint(query)
            if row is None:
                return None, [], f"no submission target for fingerprint={query}"
            return row, [CandidateMatch(row=row, match_score=999)], ""

        candidates = self.search_targets(query)
        if not candidates:
            return None, [], f"no submission target matched query={query!r}"

        top = candidates[0]
        if len(candidates) == 1:
            return top.row, candidates, ""

        second = candidates[1]
        top_org = _normalize_text(str(top.row["org_name"] or ""))
        second_org = _normalize_text(str(second.row["org_name"] or ""))
        if top_org == second_org:
            return top.row, candidates, ""
        if top.match_score >= second.match_score + 12:
            return top.row, candidates, ""
        if _normalize_text(query) == top_org:
            return top.row, candidates, ""
        return None, candidates, f"ambiguous query={query!r}; rerun with fingerprint"

    def find_active_task_for_fingerprint(self, fingerprint: str, *, workspace_key: str = "default") -> Optional[sqlite3.Row]:
        self.conn.row_factory = sqlite3.Row
        cur = self.conn.execute(
            """
            SELECT id, opportunity_fingerprint, org_name, submission_state, updated_at
            FROM submission_tasks
            WHERE workspace_key = ?
              AND opportunity_fingerprint = ?
              AND submission_state NOT IN ('won', 'rejected', 'archived')
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (workspace_key, fingerprint),
        )
        return cur.fetchone()

    def create_task(
        self,
        *,
        target: sqlite3.Row,
        workspace_key: str = "default",
        owner: str = "",
        due_date: str = "",
        submission_state: str = "researching",
        notes: str = "",
        actor: str = "cli",
    ) -> Tuple[int, bool]:
        state = _validate_state(submission_state)
        fingerprint = str(target["fingerprint"])
        existing = self.find_active_task_for_fingerprint(fingerprint, workspace_key=workspace_key)
        if existing is not None:
            return int(existing["id"]), False

        now = now_utc_iso()
        due = (due_date or str(target["deadline_date"] or "")).strip()
        recommended_action = self._recommended_action_for_target(target)
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO submission_tasks (
                  workspace_key, opportunity_fingerprint, org_name, program_name, domain, official_page,
                  submission_url, opportunity_status, submission_state, owner, due_date,
                  priority_score, fit_score, recommended_action, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace_key,
                    fingerprint,
                    str(target["org_name"] or ""),
                    "",
                    str(target["domain"] or ""),
                    str(target["source_url"] or ""),
                    str(target["submission_url"] or ""),
                    str(target["status"] or ""),
                    state,
                    owner.strip(),
                    due,
                    int(target["score"] or 0),
                    0,
                    recommended_action,
                    notes.strip(),
                    now,
                    now,
                ),
            )
            task_id = int(cur.lastrowid)
            self._add_update(
                task_id=task_id,
                event_type="created",
                body=f"task created from opportunity {str(target['org_name'])}",
                actor=actor,
            )
            if notes.strip():
                self._add_update(task_id=task_id, event_type="note", body=notes.strip(), actor=actor)
        return task_id, True

    def _recommended_action_for_target(self, target: sqlite3.Row) -> str:
        status = _normalize_text(str(target["status"] or ""))
        if status == "deadline":
            due = str(target["deadline_date"] or "-")
            return f"review requirements and prepare submission before {due}"
        if status in {"open", "rolling"}:
            return "review requirements, prepare materials, and move to ready_to_submit"
        if status == "closed":
            return "do not prepare submission; watch for reopen or next cohort"
        return "reverify opportunity before assigning submission work"

    def _add_update(self, *, task_id: int, event_type: str, body: str, actor: str) -> None:
        self.conn.execute(
            """
            INSERT INTO submission_task_updates (task_id, event_type, body, actor, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (task_id, event_type, body.strip(), actor.strip() or "system", now_utc_iso()),
        )

    def get_task(self, task_id: int) -> Optional[sqlite3.Row]:
        self.conn.row_factory = sqlite3.Row
        cur = self.conn.execute(
            """
            SELECT id, workspace_key, opportunity_fingerprint, org_name, program_name, domain,
                   official_page, submission_url, opportunity_status, submission_state, owner,
                   due_date, priority_score, fit_score, recommended_action, notes, created_at,
                   updated_at, submitted_at, follow_up_due_at
            FROM submission_tasks
            WHERE id = ?
            LIMIT 1
            """,
            (task_id,),
        )
        return cur.fetchone()

    def list_task_updates(self, task_id: int, *, limit: int = 20) -> List[sqlite3.Row]:
        self.conn.row_factory = sqlite3.Row
        cur = self.conn.execute(
            """
            SELECT id, event_type, body, actor, created_at
            FROM submission_task_updates
            WHERE task_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (task_id, limit),
        )
        return list(cur.fetchall())

    def list_tasks(
        self,
        *,
        workspace_key: str = "default",
        submission_state: str = "",
        owner: str = "",
        bucket: str = "",
        limit: int = 40,
    ) -> List[sqlite3.Row]:
        self.conn.row_factory = sqlite3.Row
        where = ["workspace_key = ?"]
        args: List[object] = [workspace_key]
        if submission_state:
            where.append("submission_state = ?")
            args.append(_validate_state(submission_state))
        if owner:
            where.append("owner = ?")
            args.append(owner.strip())

        cur = self.conn.execute(
            f"""
            SELECT id, workspace_key, opportunity_fingerprint, org_name, program_name, domain,
                   official_page, submission_url, opportunity_status, submission_state, owner,
                   due_date, priority_score, fit_score, recommended_action, notes, created_at,
                   updated_at, submitted_at, follow_up_due_at
            FROM submission_tasks
            WHERE {' AND '.join(where)}
            ORDER BY updated_at DESC, id DESC
            """,
            tuple(args),
        )
        rows = list(cur.fetchall())
        rows = [row for row in rows if _task_bucket_matches(row, bucket)]
        rows.sort(key=_task_sort_key)
        return rows[:limit]

    def update_task(
        self,
        task_id: int,
        *,
        submission_state: Optional[str] = None,
        owner: Optional[str] = None,
        due_date: Optional[str] = None,
        notes: Optional[str] = None,
        recommended_action: Optional[str] = None,
        actor: str = "cli",
    ) -> sqlite3.Row:
        row = self.get_task(task_id)
        if row is None:
            raise ValueError(f"task not found: {task_id}")

        current_state = str(row["submission_state"])
        new_state = current_state
        if submission_state is not None:
            new_state = _validate_state(submission_state)
            if not _can_transition(current_state, new_state):
                raise ValueError(f"invalid transition: {current_state} -> {new_state}")

        new_owner = str(row["owner"] or "") if owner is None else owner.strip()
        new_due = str(row["due_date"] or "") if due_date is None else due_date.strip()
        new_notes = str(row["notes"] or "") if notes is None else notes.strip()
        new_recommended_action = (
            str(row["recommended_action"] or "") if recommended_action is None else recommended_action.strip()
        )

        now = now_utc_iso()
        with self.conn:
            self.conn.execute(
                """
                UPDATE submission_tasks
                SET submission_state = ?, owner = ?, due_date = ?, notes = ?, recommended_action = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_state, new_owner, new_due, new_notes, new_recommended_action, now, task_id),
            )
            changes: List[str] = []
            if new_state != current_state:
                changes.append(f"state {current_state} -> {new_state}")
            if owner is not None and new_owner != str(row["owner"] or ""):
                changes.append(f"owner -> {new_owner or '-'}")
            if due_date is not None and new_due != str(row["due_date"] or ""):
                changes.append(f"due_date -> {new_due or '-'}")
            if notes is not None and new_notes != str(row["notes"] or ""):
                changes.append("notes updated")
            if recommended_action is not None and new_recommended_action != str(row["recommended_action"] or ""):
                changes.append("recommended_action updated")
            if changes:
                self._add_update(task_id=task_id, event_type="updated", body="; ".join(changes), actor=actor)
        updated = self.get_task(task_id)
        if updated is None:
            raise ValueError(f"task disappeared after update: {task_id}")
        return updated

    def add_note(self, task_id: int, *, body: str, actor: str = "cli") -> sqlite3.Row:
        row = self.get_task(task_id)
        if row is None:
            raise ValueError(f"task not found: {task_id}")
        if not body.strip():
            raise ValueError("note body is required")
        with self.conn:
            self._add_update(task_id=task_id, event_type="note", body=body.strip(), actor=actor)
            self.conn.execute(
                "UPDATE submission_tasks SET updated_at = ? WHERE id = ?",
                (now_utc_iso(), task_id),
            )
        updated = self.get_task(task_id)
        if updated is None:
            raise ValueError(f"task disappeared after note: {task_id}")
        return updated

    def mark_ready(self, task_id: int, *, actor: str = "cli") -> sqlite3.Row:
        return self.update_task(task_id, submission_state="ready_to_submit", actor=actor)

    def mark_submitted(
        self,
        task_id: int,
        *,
        submitted_at: str = "",
        follow_up_days: int = 14,
        note: str = "",
        actor: str = "cli",
    ) -> sqlite3.Row:
        row = self.get_task(task_id)
        if row is None:
            raise ValueError(f"task not found: {task_id}")
        if not _can_transition(str(row["submission_state"]), "submitted"):
            raise ValueError(f"invalid transition: {row['submission_state']} -> submitted")

        submitted_value = (submitted_at or now_utc_iso())
        due_base = _parse_iso_date(submitted_value)
        follow_up_due = ""
        if due_base is not None:
            follow_up_due = (due_base + timedelta(days=max(1, follow_up_days))).isoformat()

        now = now_utc_iso()
        with self.conn:
            self.conn.execute(
                """
                UPDATE submission_tasks
                SET submission_state = ?, submitted_at = ?, follow_up_due_at = ?, updated_at = ?
                WHERE id = ?
                """,
                ("submitted", submitted_value, follow_up_due, now, task_id),
            )
            self._add_update(
                task_id=task_id,
                event_type="submitted",
                body=f"submitted_at={submitted_value}; follow_up_due_at={follow_up_due or '-'}",
                actor=actor,
            )
            if note.strip():
                self._add_update(task_id=task_id, event_type="note", body=note.strip(), actor=actor)
        updated = self.get_task(task_id)
        if updated is None:
            raise ValueError(f"task disappeared after submitted: {task_id}")
        return updated

    def mark_followup(self, task_id: int, *, due_date: str = "", note: str = "", actor: str = "cli") -> sqlite3.Row:
        row = self.get_task(task_id)
        if row is None:
            raise ValueError(f"task not found: {task_id}")
        current_state = str(row["submission_state"])
        if not _can_transition(current_state, "follow_up_due"):
            raise ValueError(f"invalid transition: {current_state} -> follow_up_due")
        follow_up_due = due_date.strip() or str(row["follow_up_due_at"] or "") or str(row["due_date"] or "")
        now = now_utc_iso()
        with self.conn:
            self.conn.execute(
                """
                UPDATE submission_tasks
                SET submission_state = ?, follow_up_due_at = ?, updated_at = ?
                WHERE id = ?
                """,
                ("follow_up_due", follow_up_due, now, task_id),
            )
            self._add_update(
                task_id=task_id,
                event_type="follow_up_due",
                body=f"follow_up_due_at={follow_up_due or '-'}",
                actor=actor,
            )
            if note.strip():
                self._add_update(task_id=task_id, event_type="note", body=note.strip(), actor=actor)
        updated = self.get_task(task_id)
        if updated is None:
            raise ValueError(f"task disappeared after follow_up_due: {task_id}")
        return updated


def _print_ambiguous_candidates(candidates: Sequence[CandidateMatch]) -> None:
    print("[ambiguous] multiple submission targets matched")
    for idx, candidate in enumerate(candidates[:6], start=1):
        print(f"{idx}. {_summarize_candidate(candidate.row)}")


def task_create_command(args: argparse.Namespace) -> int:
    store = SubmissionTaskStore(args.db)
    target_row, candidates, error = store.resolve_target(args.target)
    if target_row is None:
        if candidates:
            _print_ambiguous_candidates(candidates)
        print(f"[error] {error}")
        return 2

    try:
        task_id, created = store.create_task(
            target=target_row,
            workspace_key=args.workspace,
            owner=args.owner,
            due_date=args.due_date,
            submission_state=args.submission_state,
            notes=args.notes,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[error] task create failed: {exc}")
        return 2

    task = store.get_task(task_id)
    if task is None:
        print(f"[error] task missing after create: {task_id}")
        return 2
    label = "created" if created else "exists"
    print(
        f"[{label}] task_id={task_id} | state={task['submission_state']} | "
        f"org={task['org_name']} | due={_task_due_display(task) or '-'} | "
        f"url={task['submission_url'] or task['official_page'] or '-'}"
    )
    return 0


def task_list_command(args: argparse.Namespace) -> int:
    bucket = args.bucket.strip().lower()
    if bucket not in TASK_BUCKETS:
        print(f"[error] invalid bucket: {args.bucket}")
        return 2
    store = SubmissionTaskStore(args.db)
    try:
        rows = store.list_tasks(
            workspace_key=args.workspace,
            submission_state=args.submission_state,
            owner=args.owner,
            bucket=bucket,
            limit=args.limit,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[error] task list failed: {exc}")
        return 2

    if not rows:
        print("(no tasks)")
        return 0

    print("id\tstate\tbucket\tdue\tpriority\towner\torg\topportunity_status\tsubmission_url")
    for row in rows:
        print(
            "\t".join(
                [
                    str(row["id"]),
                    str(row["submission_state"]),
                    _task_bucket(row),
                    _task_due_display(row),
                    str(row["priority_score"] or 0),
                    str(row["owner"] or ""),
                    str(row["org_name"] or ""),
                    str(row["opportunity_status"] or ""),
                    str(row["submission_url"] or row["official_page"] or ""),
                ]
            )
        )
    return 0


def task_view_command(args: argparse.Namespace) -> int:
    store = SubmissionTaskStore(args.db)
    row = store.get_task(args.task_id)
    if row is None:
        print(f"[error] task not found: {args.task_id}")
        return 2
    print(f"id: {row['id']}")
    print(f"workspace: {row['workspace_key']}")
    print(f"state: {row['submission_state']}")
    print(f"bucket: {_task_bucket(row)}")
    print(f"org_name: {row['org_name']}")
    print(f"domain: {row['domain']}")
    print(f"opportunity_status: {row['opportunity_status']}")
    print(f"due_date: {row['due_date'] or '-'}")
    print(f"submitted_at: {row['submitted_at'] or '-'}")
    print(f"follow_up_due_at: {row['follow_up_due_at'] or '-'}")
    print(f"priority_score: {row['priority_score']}")
    print(f"fit_score: {row['fit_score']}")
    print(f"owner: {row['owner'] or '-'}")
    print(f"official_page: {row['official_page'] or '-'}")
    print(f"submission_url: {row['submission_url'] or '-'}")
    print(f"recommended_action: {row['recommended_action'] or '-'}")
    print(f"notes: {row['notes'] or '-'}")
    print(f"created_at: {row['created_at']}")
    print(f"updated_at: {row['updated_at']}")
    updates = store.list_task_updates(args.task_id, limit=args.limit)
    if updates:
        print("")
        print("updates:")
        for item in updates:
            print(f"- {item['created_at']} | {item['event_type']} | {item['actor']} | {item['body']}")
    return 0


def task_update_command(args: argparse.Namespace) -> int:
    if args.submission_state is None and args.owner is None and args.due_date is None and args.notes is None and args.recommended_action is None:
        print("[error] at least one field must be provided")
        return 2
    store = SubmissionTaskStore(args.db)
    try:
        row = store.update_task(
            args.task_id,
            submission_state=args.submission_state,
            owner=args.owner,
            due_date=args.due_date,
            notes=args.notes,
            recommended_action=args.recommended_action,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[error] task update failed: {exc}")
        return 2
    print(
        f"[updated] task_id={row['id']} | state={row['submission_state']} | "
        f"owner={row['owner'] or '-'} | due={row['due_date'] or '-'}"
    )
    return 0


def task_add_note_command(args: argparse.Namespace) -> int:
    store = SubmissionTaskStore(args.db)
    try:
        row = store.add_note(args.task_id, body=args.body)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] task note failed: {exc}")
        return 2
    print(f"[noted] task_id={row['id']} | updated_at={row['updated_at']}")
    return 0


def task_ready_command(args: argparse.Namespace) -> int:
    store = SubmissionTaskStore(args.db)
    try:
        row = store.mark_ready(args.task_id)
        if args.note:
            row = store.add_note(args.task_id, body=args.note)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] task ready failed: {exc}")
        return 2
    print(f"[ready] task_id={row['id']} | state={row['submission_state']} | org={row['org_name']}")
    return 0


def task_submitted_command(args: argparse.Namespace) -> int:
    store = SubmissionTaskStore(args.db)
    try:
        row = store.mark_submitted(
            args.task_id,
            submitted_at=args.submitted_at,
            follow_up_days=args.follow_up_days,
            note=args.note,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[error] task submitted failed: {exc}")
        return 2
    print(
        f"[submitted] task_id={row['id']} | submitted_at={row['submitted_at'] or '-'} | "
        f"follow_up_due_at={row['follow_up_due_at'] or '-'}"
    )
    return 0


def task_followup_command(args: argparse.Namespace) -> int:
    store = SubmissionTaskStore(args.db)
    try:
        row = store.mark_followup(args.task_id, due_date=args.due_date, note=args.note)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] task followup failed: {exc}")
        return 2
    print(f"[followup] task_id={row['id']} | follow_up_due_at={row['follow_up_due_at'] or '-'}")
    return 0
