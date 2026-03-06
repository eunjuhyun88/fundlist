from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .fundraising import import_fundraising_files, parse_files_argument
from .store import ensure_parent_dir


TERMINAL_STATUS = {"done", "rejected", "closed"}
RUNNING_STATUS = {"in_progress", "submitted"}


@dataclass
class SubmissionTask:
    task_key: str
    category: str
    org_name: str
    status_raw: str
    status_norm: str
    deadline_date: str
    days_left: Optional[int]
    is_speedrun: int
    is_active: int
    source_file: str
    source_row: int
    website: str
    notes: str
    imported_at: str


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _slugify_text(value: str) -> str:
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", value.strip().lower())
    return slug.strip("_") or "program"


def _task_search_blob(task: SubmissionTask) -> str:
    return _normalize_text(" | ".join([task.org_name, task.notes, task.website, task.category]))


def _tight_text(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z가-힣]+", "", value.strip().lower())


def _matches_program(task: SubmissionTask, program: str) -> bool:
    key = _normalize_text(program)
    key_tight = _tight_text(program)
    if not key and not key_tight:
        return True
    blob = _task_search_blob(task)
    blob_tight = _tight_text(blob)
    if key and key in blob:
        return True
    if key_tight and key_tight in blob_tight:
        return True
    return False


def _days_mark(days_left: Optional[int]) -> str:
    if days_left is None:
        return "D?"
    if days_left >= 0:
        return f"D-{days_left}"
    return f"D+{abs(days_left)}"


def _extract_raw_highlights(raw_json: str) -> List[Tuple[str, str]]:
    if not raw_json.strip():
        return []
    try:
        payload = json.loads(raw_json)
    except Exception:  # noqa: BLE001
        return []
    headers = payload.get("headers") if isinstance(payload, dict) else None
    row = payload.get("row") if isinstance(payload, dict) else None
    if not isinstance(headers, list) or not isinstance(row, list):
        return []

    wanted = [
        "deadline",
        "date",
        "apply",
        "application",
        "form",
        "requirement",
        "document",
        "제출",
        "지원",
        "마감",
        "링크",
        "url",
        "website",
        "note",
        "설명",
    ]

    out: List[Tuple[str, str]] = []
    for i, h in enumerate(headers):
        hv = str(h or "").strip()
        if not hv:
            continue
        vv = str(row[i]).strip() if i < len(row) and row[i] is not None else ""
        if not vv:
            continue
        hn = _normalize_text(hv)
        if any(k in hn for k in wanted):
            out.append((hv, vv[:240]))
        if len(out) >= 8:
            break
    return out


def _build_program_detail_map(conn: sqlite3.Connection, tasks: Sequence[SubmissionTask]) -> Dict[str, Dict[str, object]]:
    conn.row_factory = sqlite3.Row
    out: Dict[str, Dict[str, object]] = {}
    for t in tasks:
        key = t.task_key
        cur = conn.execute(
            """
            SELECT org_name, contact_name, email, website, status, region, funding, date_text, notes, raw_json, imported_at
            FROM fundraising_records
            WHERE source_file = ? AND source_row = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (t.source_file, t.source_row),
        )
        row = cur.fetchone()
        if not row:
            out[key] = {
                "org_name": t.org_name,
                "contact_name": "",
                "email": "",
                "website": t.website,
                "status": t.status_raw,
                "region": "",
                "funding": "",
                "date_text": "",
                "notes": t.notes,
                "raw_highlights": [],
                "imported_at": t.imported_at,
            }
            continue

        raw_json = str(row["raw_json"] or "")
        out[key] = {
            "org_name": str(row["org_name"] or t.org_name),
            "contact_name": str(row["contact_name"] or ""),
            "email": str(row["email"] or ""),
            "website": str(row["website"] or t.website),
            "status": str(row["status"] or t.status_raw),
            "region": str(row["region"] or ""),
            "funding": str(row["funding"] or ""),
            "date_text": str(row["date_text"] or ""),
            "notes": str(row["notes"] or t.notes),
            "raw_highlights": _extract_raw_highlights(raw_json),
            "imported_at": str(row["imported_at"] or t.imported_at),
        }
    return out


def _is_submission_category(category: str) -> bool:
    c = category.strip().lower()
    if c in {"accelerator_program", "grants_program"}:
        return True
    if not c.startswith("xlsx:"):
        return False
    sheet = c.split(":", 1)[1].strip()
    # Only track sheet names that likely represent applications/submissions.
    keywords = [
        "grant",
        "accelerator",
        "program",
        "apply",
        "application",
        "지원",
        "제출",
        "일정",
    ]
    return any(k in sheet for k in keywords)


def _parse_yyyymmdd(text: str) -> List[date]:
    out: List[date] = []
    pattern = re.compile(r"(20\d{2})[.\-/년 ]+\s*(\d{1,2})[.\-/월 ]+\s*(\d{1,2})")
    for y, m, d in pattern.findall(text):
        try:
            out.append(date(int(y), int(m), int(d)))
        except Exception:  # noqa: BLE001
            continue
    return out


def _parse_mmddyyyy(text: str) -> List[date]:
    out: List[date] = []
    pattern = re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b")
    for m, d, y in pattern.findall(text):
        try:
            out.append(date(int(y), int(m), int(d)))
        except Exception:  # noqa: BLE001
            continue
    return out


def _extract_dates(text: str) -> List[date]:
    seen = set()
    out: List[date] = []
    for dt in _parse_yyyymmdd(text) + _parse_mmddyyyy(text):
        if dt in seen:
            continue
        seen.add(dt)
        out.append(dt)
    return sorted(out)


def _pick_deadline(candidates: Sequence[date], today: date) -> Optional[date]:
    if not candidates:
        return None
    future = [d for d in candidates if d >= today]
    if future:
        return min(future)
    return max(candidates)


def _normalize_status(status_raw: str, notes: str) -> str:
    text = _normalize_text(f"{status_raw} {notes}")
    done_markers = [
        "완료",
        "done",
        "closed",
        "reject",
        "rejected",
        "탈락",
        "불합격",
        "다음기회",
        "passed",
        "fail",
        "expired",
        "timeout",
    ]
    run_markers = [
        "진행중",
        "in progress",
        "ongoing",
        "active",
        "started",
        "지원",
        "applied",
        "submitted",
        "활성",
    ]
    submit_markers = ["submitted", "제출", "지원완료", "apply", "applied"]
    not_started_markers = ["미진행", "대기", "준비", "todo", "to do", "planned", "예정"]

    if any(m in text for m in done_markers):
        return "done"
    if any(m in text for m in submit_markers):
        return "submitted"
    if any(m in text for m in run_markers):
        return "in_progress"
    if any(m in text for m in not_started_markers):
        return "not_started"
    return "unknown"


def _extract_org_name(row: sqlite3.Row) -> str:
    org_name = str(row["org_name"] or "").strip()
    if org_name:
        return org_name

    raw_json = str(row["raw_json"] or "").strip()
    if not raw_json:
        return ""
    try:
        payload = json.loads(raw_json)
    except Exception:  # noqa: BLE001
        return ""

    headers = payload.get("headers") if isinstance(payload, dict) else None
    values = payload.get("row") if isinstance(payload, dict) else None
    if not isinstance(headers, list) or not isinstance(values, list):
        return ""

    mapping: Dict[str, str] = {}
    for i, h in enumerate(headers):
        key = _normalize_text(str(h))
        if not key:
            continue
        mapping[key] = str(values[i]).strip() if i < len(values) and values[i] is not None else ""

    aliases = [
        "program",
        "task name",
        "taskname",
        "fund name",
        "fundname",
        "그랜트 이름",
        "그랜트이름",
        "조직",
        "투자사",
        "name",
    ]
    for alias in aliases:
        a = _normalize_text(alias)
        for key, value in mapping.items():
            if not value:
                continue
            if key == a or a in key:
                return value.strip()
    return ""


def _build_task_key(
    category: str,
    source_file: str,
    source_row: int,
    org_name: str,
    website: str,
) -> str:
    raw = "|".join([category, source_file, str(source_row), org_name, website])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _load_latest_fundraising_rows(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """
        SELECT
          source_file, source_row, category, org_name, status, date_text,
          notes, website, raw_json, imported_at
        FROM fundraising_records
        ORDER BY imported_at DESC, id DESC
        """
    )
    latest: Dict[Tuple[str, int], sqlite3.Row] = {}
    for row in cur.fetchall():
        key = (str(row["source_file"]), int(row["source_row"]))
        if key in latest:
            continue
        latest[key] = row
    return list(latest.values())


def _build_tasks(rows: Sequence[sqlite3.Row], today: date) -> List[SubmissionTask]:
    tasks: List[SubmissionTask] = []
    for row in rows:
        category = str(row["category"] or "").strip().lower()
        if not _is_submission_category(category):
            continue

        org_name = _extract_org_name(row).strip()
        status_raw = str(row["status"] or "").strip()
        notes = str(row["notes"] or "").strip()
        website = str(row["website"] or "").strip()
        date_text = str(row["date_text"] or "").strip()
        source_file = str(row["source_file"] or "").strip()
        source_row = int(row["source_row"] or 0)
        imported_at = str(row["imported_at"] or "")

        status_norm = _normalize_status(status_raw=status_raw, notes=notes)
        joined = " | ".join([status_raw, date_text, notes])
        candidates = _extract_dates(joined)
        deadline = _pick_deadline(candidates, today=today)
        deadline_date = deadline.isoformat() if deadline else ""
        days_left = (deadline - today).days if deadline else None

        speedrun_blob = _normalize_text(f"{org_name} {notes} {website}")
        is_speedrun = 1 if ("speedrun" in speedrun_blob or "스피드런" in speedrun_blob) else 0
        is_active = 0 if status_norm in TERMINAL_STATUS else 1

        task_key = _build_task_key(
            category=category,
            source_file=source_file,
            source_row=source_row,
            org_name=org_name,
            website=website,
        )
        tasks.append(
            SubmissionTask(
                task_key=task_key,
                category=category,
                org_name=org_name or "(unnamed)",
                status_raw=status_raw,
                status_norm=status_norm,
                deadline_date=deadline_date,
                days_left=days_left,
                is_speedrun=is_speedrun,
                is_active=is_active,
                source_file=source_file,
                source_row=source_row,
                website=website,
                notes=notes[:800],
                imported_at=imported_at,
            )
        )

    tasks.sort(
        key=lambda t: (
            t.deadline_date == "",
            t.deadline_date or "9999-12-31",
            t.category,
            t.org_name,
        )
    )
    return tasks


def _ensure_ops_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vc_submission_tasks (
          task_key TEXT PRIMARY KEY,
          category TEXT NOT NULL,
          org_name TEXT NOT NULL,
          status_raw TEXT NOT NULL,
          status_norm TEXT NOT NULL,
          deadline_date TEXT NOT NULL,
          days_left INTEGER,
          is_speedrun INTEGER NOT NULL,
          is_active INTEGER NOT NULL,
          source_file TEXT NOT NULL,
          source_row INTEGER NOT NULL,
          website TEXT NOT NULL,
          notes TEXT NOT NULL,
          imported_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vc_tasks_deadline ON vc_submission_tasks(deadline_date, is_active)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vc_tasks_speedrun ON vc_submission_tasks(is_speedrun, deadline_date)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vc_ops_snapshots (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_at TEXT NOT NULL,
          parsed_count INTEGER NOT NULL,
          inserted_count INTEGER NOT NULL,
          task_count INTEGER NOT NULL,
          active_task_count INTEGER NOT NULL,
          upcoming_count INTEGER NOT NULL,
          overdue_count INTEGER NOT NULL,
          speedrun_started INTEGER NOT NULL,
          payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vc_ops_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          created_at TEXT NOT NULL,
          event_type TEXT NOT NULL,
          severity TEXT NOT NULL,
          title TEXT NOT NULL,
          body TEXT NOT NULL,
          dedupe_key TEXT NOT NULL UNIQUE,
          payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vc_events_created ON vc_ops_events(created_at DESC)"
    )
    conn.commit()


def _replace_tasks(conn: sqlite3.Connection, tasks: Sequence[SubmissionTask]) -> None:
    now = now_utc_iso()
    with conn:
        conn.execute("DELETE FROM vc_submission_tasks")
        conn.executemany(
            """
            INSERT INTO vc_submission_tasks (
              task_key, category, org_name, status_raw, status_norm, deadline_date, days_left,
              is_speedrun, is_active, source_file, source_row, website, notes, imported_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    t.task_key,
                    t.category,
                    t.org_name,
                    t.status_raw,
                    t.status_norm,
                    t.deadline_date,
                    t.days_left,
                    t.is_speedrun,
                    t.is_active,
                    t.source_file,
                    t.source_row,
                    t.website,
                    t.notes,
                    t.imported_at,
                    now,
                )
                for t in tasks
            ],
        )


def _upsert_event(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    severity: str,
    title: str,
    body: str,
    dedupe_key: str,
    payload: Dict[str, object],
) -> int:
    before = conn.total_changes
    with conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO vc_ops_events (
              created_at, event_type, severity, title, body, dedupe_key, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_utc_iso(),
                event_type,
                severity,
                title,
                body,
                dedupe_key,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
    return conn.total_changes - before


def _to_snapshot_payload(tasks: Sequence[SubmissionTask], alert_days: int) -> Dict[str, object]:
    upcoming = [
        t
        for t in tasks
        if t.is_active and t.days_left is not None and t.days_left >= 0 and t.days_left <= alert_days
    ]
    overdue = [t for t in tasks if t.is_active and t.days_left is not None and t.days_left < 0]
    speedrun = [t for t in tasks if t.is_speedrun]
    speedrun_started = any(t.status_norm in RUNNING_STATUS.union(TERMINAL_STATUS) for t in speedrun)

    by_category: Dict[str, int] = {}
    for t in tasks:
        by_category[t.category] = by_category.get(t.category, 0) + 1

    nearest = []
    for t in tasks:
        if t.deadline_date and t.is_active:
            nearest.append(
                {
                    "org_name": t.org_name,
                    "category": t.category,
                    "deadline_date": t.deadline_date,
                    "days_left": t.days_left,
                    "status_norm": t.status_norm,
                    "is_speedrun": t.is_speedrun,
                }
            )
        if len(nearest) >= 20:
            break

    return {
        "task_count": len(tasks),
        "active_task_count": sum(1 for t in tasks if t.is_active),
        "upcoming_count": len(upcoming),
        "overdue_count": len(overdue),
        "speedrun_started": bool(speedrun_started),
        "speedrun_task_count": len(speedrun),
        "by_category": by_category,
        "nearest_deadlines": nearest,
    }


def _insert_snapshot(
    conn: sqlite3.Connection,
    *,
    parsed_count: int,
    inserted_count: int,
    payload: Dict[str, object],
) -> int:
    with conn:
        cur = conn.execute(
            """
            INSERT INTO vc_ops_snapshots (
              run_at, parsed_count, inserted_count, task_count, active_task_count,
              upcoming_count, overdue_count, speedrun_started, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_utc_iso(),
                parsed_count,
                inserted_count,
                int(payload.get("task_count", 0)),
                int(payload.get("active_task_count", 0)),
                int(payload.get("upcoming_count", 0)),
                int(payload.get("overdue_count", 0)),
                1 if bool(payload.get("speedrun_started")) else 0,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
    return int(cur.lastrowid)


def _emit_events(conn: sqlite3.Connection, tasks: Sequence[SubmissionTask], alert_days: int) -> int:
    added = 0
    speedrun = [t for t in tasks if t.is_speedrun]
    speedrun_started = any(t.status_norm in RUNNING_STATUS.union(TERMINAL_STATUS) for t in speedrun)
    if speedrun:
        if speedrun_started:
            added += _upsert_event(
                conn,
                event_type="speedrun_status",
                severity="info",
                title="Speedrun 상태: 시작됨",
                body="Speedrun 관련 트랙이 진행/제출 또는 종료 상태로 확인되었습니다.",
                dedupe_key="speedrun:started",
                payload={"speedrun_tasks": len(speedrun)},
            )
        else:
            added += _upsert_event(
                conn,
                event_type="speedrun_status",
                severity="warning",
                title="Speedrun 상태: 미시작",
                body="Speedrun 항목이 있으나 진행 상태가 감지되지 않았습니다.",
                dedupe_key=f"speedrun:not_started:{utc_today().isoformat()}",
                payload={"speedrun_tasks": len(speedrun)},
            )

    for t in tasks:
        if not t.is_active or t.days_left is None:
            continue
        if t.days_left < 0:
            added += _upsert_event(
                conn,
                event_type="deadline",
                severity="high",
                title=f"마감 지남: {t.org_name}",
                body=f"{t.category} / deadline={t.deadline_date} / {abs(t.days_left)}일 지남",
                dedupe_key=f"deadline:overdue:{t.task_key}:{t.deadline_date}",
                payload={
                    "task_key": t.task_key,
                    "org_name": t.org_name,
                    "deadline_date": t.deadline_date,
                    "days_left": t.days_left,
                },
            )
        elif t.days_left <= alert_days:
            added += _upsert_event(
                conn,
                event_type="deadline",
                severity="warning",
                title=f"마감 임박: {t.org_name}",
                body=f"{t.category} / deadline={t.deadline_date} / D-{t.days_left}",
                dedupe_key=f"deadline:soon:{t.task_key}:{t.deadline_date}",
                payload={
                    "task_key": t.task_key,
                    "org_name": t.org_name,
                    "deadline_date": t.deadline_date,
                    "days_left": t.days_left,
                },
            )
    return added


def _render_ops_report(
    output_path: str,
    *,
    parsed_count: int,
    inserted_count: int,
    alert_days: int,
    snapshot_payload: Dict[str, object],
    tasks: Sequence[SubmissionTask],
) -> str:
    out = Path(output_path).expanduser()
    ensure_parent_dir(str(out))

    upcoming = [
        t
        for t in tasks
        if t.is_active and t.days_left is not None and t.days_left >= 0 and t.days_left <= alert_days
    ]
    overdue = [t for t in tasks if t.is_active and t.days_left is not None and t.days_left < 0]
    no_deadline = [t for t in tasks if t.is_active and not t.deadline_date][:20]
    speedrun_tasks = [t for t in tasks if t.is_speedrun]
    speedrun_started = bool(snapshot_payload.get("speedrun_started"))

    lines = [
        "# VC Ops Assistant Report",
        "",
        f"- Generated (UTC): {now_utc_iso()}",
        f"- Parsed rows (this run): {parsed_count}",
        f"- Inserted rows (this run): {inserted_count}",
        f"- Total tracked tasks: {snapshot_payload.get('task_count', 0)}",
        f"- Active tasks: {snapshot_payload.get('active_task_count', 0)}",
        f"- Deadline alert window: {alert_days} days",
        "",
        "## Speedrun Check",
        f"- Speedrun detected: {'yes' if speedrun_tasks else 'no'}",
        f"- Speedrun started: {'yes' if speedrun_started else 'no'}",
    ]
    if speedrun_tasks:
        for t in speedrun_tasks[:10]:
            lines.append(
                f"- [{t.category}] {t.org_name} | status={t.status_norm} | deadline={t.deadline_date or '-'}"
            )

    lines.extend(["", "## Deadline Alerts"])
    if upcoming:
        for t in upcoming[:30]:
            lines.append(
                f"- D-{t.days_left}: [{t.category}] {t.org_name} | status={t.status_norm} | deadline={t.deadline_date}"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Overdue"])
    if overdue:
        for t in overdue[:30]:
            lines.append(
                f"- +{abs(t.days_left or 0)}d: [{t.category}] {t.org_name} | status={t.status_norm} | deadline={t.deadline_date}"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Queue By Date"])
    queue = [t for t in tasks if t.is_active and t.deadline_date][:40]
    if queue:
        for t in queue:
            d_mark = f"D-{t.days_left}" if (t.days_left is not None and t.days_left >= 0) else f"D+{abs(t.days_left or 0)}"
            lines.append(
                f"- {t.deadline_date}: [{t.category}] {t.org_name} | {d_mark} | status={t.status_norm}"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Active Without Deadline"])
    if no_deadline:
        for t in no_deadline:
            lines.append(f"- [{t.category}] {t.org_name} | status={t.status_norm} | site={t.website or '-'}")
    else:
        lines.append("- none")

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(out)


def _render_program_report(
    output_path: str,
    *,
    program: str,
    alert_days: int,
    tasks: Sequence[SubmissionTask],
    details_by_key: Dict[str, Dict[str, object]],
) -> str:
    out = Path(output_path).expanduser()
    ensure_parent_dir(str(out))

    lines = [
        "# Accelerator Submission Report",
        "",
        f"- Generated (UTC): {now_utc_iso()}",
        f"- Program filter: {program}",
        f"- Matched tasks: {len(tasks)}",
        f"- Alert window: {alert_days} days",
    ]

    if not tasks:
        lines.extend(
            [
                "",
                "## Result",
                "- No matched active submission tasks found.",
                "- Try broader keyword (e.g. alliance, dao, accelerator).",
            ]
        )
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(out)

    upcoming = [t for t in tasks if t.days_left is not None and 0 <= t.days_left <= alert_days]
    overdue = [t for t in tasks if t.days_left is not None and t.days_left < 0]

    lines.extend(
        [
            "",
            "## Snapshot",
            f"- Upcoming in window: {len(upcoming)}",
            f"- Overdue: {len(overdue)}",
        ]
    )

    lines.extend(["", "## Priority Queue"])
    for t in tasks[:20]:
        lines.append(
            f"- {_days_mark(t.days_left)} | deadline={t.deadline_date or '-'} | [{t.category}] {t.org_name} | status={t.status_norm}"
        )

    lines.extend(["", "## Submission Dossier"])
    for idx, t in enumerate(tasks[:15], start=1):
        d = details_by_key.get(t.task_key, {})
        lines.extend(
            [
                "",
                f"### {idx}. {t.org_name}",
                f"- Status: {t.status_norm} (raw: {t.status_raw or '-'})",
                f"- Deadline: {t.deadline_date or '-'} ({_days_mark(t.days_left)})",
                f"- Apply URL: {d.get('website') or t.website or '-'}",
                f"- Contact: {d.get('contact_name') or '-'}",
                f"- Email: {d.get('email') or '-'}",
                f"- Region: {d.get('region') or '-'}",
                f"- Funding: {d.get('funding') or '-'}",
                f"- Date text: {d.get('date_text') or '-'}",
                f"- Notes: {(d.get('notes') or t.notes or '-')[:260]}",
                f"- Source: {t.source_file}:{t.source_row}",
            ]
        )
        highlights = d.get('raw_highlights') or []
        if isinstance(highlights, list) and highlights:
            lines.append("- Raw fields:")
            for k, v in highlights[:6]:
                lines.append(f"  - {k}: {v}")

    lines.extend(
        [
            "",
            "## Submission Checklist (Template)",
            "- 최신 피치덱 v1 (문제/솔루션/시장/팀/트랙션/로드맵)",
            "- 1페이지 요약(one-pager) + 제품 데모 링크",
            "- 핵심 지표(MAU/리텐션/매출/온체인지표) 최근 4주 업데이트",
            "- 팀 소개(핵심 2~3인) + 왜 지금 이 문제인지",
            "- 프로그램별 질문지 초안(예상 Q/A 10개)",
            "- 제출 직전 QA(링크, 오탈자, 연락처, 마감시각 UTC/로컬 재확인)",
        ]
    )

    lines.extend(["", "## Immediate Next Actions"])
    top = tasks[0]
    lines.append(f"- Top priority: {top.org_name} ({_days_mark(top.days_left)}, deadline={top.deadline_date or '-'})")
    lines.append("- Today: 제출 URL/필수 문항/첨부 요구사항 점검 후 체크리스트 확정")
    lines.append("- Next 24h: 피치덱/원페이지 최신화 및 필수 KPI 수치 동기화")
    lines.append("- Next 48h: 모의 제출 1회 후 최종 제출")

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(out)


def _query_tasks_for_list(
    conn: sqlite3.Connection,
    *,
    from_days: int,
    to_days: int,
    limit: int,
    speedrun_only: bool,
    include_no_deadline: bool,
) -> List[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    where = ["is_active = 1"]
    args: List[object] = []
    if speedrun_only:
        where.append("is_speedrun = 1")

    if include_no_deadline:
        where.append(
            "(deadline_date = '' OR (days_left IS NOT NULL AND days_left >= ? AND days_left <= ?))"
        )
        args.extend([from_days, to_days])
    else:
        where.append("deadline_date <> ''")
        where.append("days_left IS NOT NULL")
        where.append("days_left >= ?")
        where.append("days_left <= ?")
        args.extend([from_days, to_days])

    sql = f"""
        SELECT
          category, org_name, status_norm, deadline_date, days_left,
          is_speedrun, website, source_file, source_row
        FROM vc_submission_tasks
        WHERE {" AND ".join(where)}
        ORDER BY
          CASE WHEN deadline_date = '' THEN 1 ELSE 0 END,
          deadline_date ASC,
          org_name ASC
        LIMIT ?
    """
    args.append(limit)
    return conn.execute(sql, tuple(args)).fetchall()


def run_ops_sync(
    *,
    db_path: str,
    files: Sequence[str],
    skip_import: bool,
    alert_days: int,
    report_output: str,
    write_report: bool,
) -> Dict[str, object]:
    parsed_count = 0
    inserted_count = 0
    if not skip_import:
        parsed_count, inserted_count = import_fundraising_files(db_path, files)

    conn = sqlite3.connect(db_path)
    _ensure_ops_schema(conn)
    rows = _load_latest_fundraising_rows(conn)
    tasks = _build_tasks(rows=rows, today=utc_today())
    _replace_tasks(conn, tasks)
    payload = _to_snapshot_payload(tasks=tasks, alert_days=alert_days)
    snapshot_id = _insert_snapshot(
        conn,
        parsed_count=parsed_count,
        inserted_count=inserted_count,
        payload=payload,
    )
    event_added = _emit_events(conn, tasks=tasks, alert_days=alert_days)
    conn.commit()
    conn.close()

    report_path = ""
    if write_report:
        report_path = _render_ops_report(
            report_output,
            parsed_count=parsed_count,
            inserted_count=inserted_count,
            alert_days=alert_days,
            snapshot_payload=payload,
            tasks=tasks,
        )

    return {
        "snapshot_id": snapshot_id,
        "parsed_count": parsed_count,
        "inserted_count": inserted_count,
        "event_added": event_added,
        "payload": payload,
        "report_path": report_path,
        "tasks": tasks,
    }


def ops_sync_command(args: argparse.Namespace) -> int:
    files = parse_files_argument(args.files)
    result = run_ops_sync(
        db_path=args.db,
        files=files,
        skip_import=args.skip_import,
        alert_days=args.alert_days,
        report_output=args.output,
        write_report=(not args.no_report),
    )
    payload = result["payload"]
    print(
        "[ops-sync] "
        f"snapshot={result['snapshot_id']} "
        f"parsed={result['parsed_count']} inserted={result['inserted_count']} "
        f"tasks={payload['task_count']} active={payload['active_task_count']} "
        f"upcoming={payload['upcoming_count']} overdue={payload['overdue_count']} "
        f"speedrun_started={int(bool(payload['speedrun_started']))} "
        f"events_added={result['event_added']}"
    )
    if result["report_path"]:
        print(f"[done] ops report written: {result['report_path']}")
    return 0


def ops_report_command(args: argparse.Namespace) -> int:
    files = parse_files_argument(args.files)
    result = run_ops_sync(
        db_path=args.db,
        files=files,
        skip_import=args.skip_import,
        alert_days=args.alert_days,
        report_output=args.output,
        write_report=True,
    )
    print(
        f"[done] ops report written: {result['report_path']} "
        f"(tasks={result['payload']['task_count']} upcoming={result['payload']['upcoming_count']})"
    )
    return 0


def ops_program_report_command(args: argparse.Namespace) -> int:
    files = parse_files_argument(args.files)
    result = run_ops_sync(
        db_path=args.db,
        files=files,
        skip_import=args.skip_import,
        alert_days=args.alert_days,
        report_output=args.output,
        write_report=False,
    )

    all_tasks = list(result.get("tasks", []))
    matched = [t for t in all_tasks if t.is_active and _matches_program(t, args.program)]
    matched.sort(
        key=lambda t: (
            t.deadline_date == "",
            t.deadline_date or "9999-12-31",
            t.org_name,
        )
    )

    conn = sqlite3.connect(args.db)
    _ensure_ops_schema(conn)
    details_by_key = _build_program_detail_map(conn, matched)
    conn.close()

    output = args.output
    if not output.strip():
        slug = _slugify_text(args.program)
        output = str(Path(args.db).resolve().parents[0] / "reports" / "program_reports" / f"{slug}_submission_report.md")

    path = _render_program_report(
        output,
        program=args.program,
        alert_days=args.alert_days,
        tasks=matched,
        details_by_key=details_by_key,
    )
    print(
        f"[done] program report written: {path} "
        f"(program={args.program} matched={len(matched)} parsed={result['parsed_count']} inserted={result['inserted_count']})"
    )
    return 0


def ops_list_command(args: argparse.Namespace) -> int:
    conn = sqlite3.connect(args.db)
    _ensure_ops_schema(conn)
    rows = _query_tasks_for_list(
        conn,
        from_days=args.from_days,
        to_days=args.to_days,
        limit=args.limit,
        speedrun_only=args.speedrun_only,
        include_no_deadline=args.include_no_deadline,
    )
    conn.close()
    if not rows:
        print("(no tasks)")
        return 0

    for row in rows:
        days_left = row["days_left"]
        if days_left is None:
            d_mark = "D?"
        elif int(days_left) >= 0:
            d_mark = f"D-{int(days_left)}"
        else:
            d_mark = f"D+{abs(int(days_left))}"
        print(
            " | ".join(
                [
                    row["deadline_date"] or "-",
                    d_mark,
                    row["category"],
                    row["org_name"],
                    row["status_norm"],
                    "speedrun" if int(row["is_speedrun"] or 0) else "-",
                    row["website"] or "-",
                ]
            )
        )
    return 0


def ops_watch_command(args: argparse.Namespace) -> int:
    files = parse_files_argument(args.files)
    runs = int(args.runs)
    interval = int(args.interval_seconds)
    idx = 0
    while True:
        idx += 1
        result = run_ops_sync(
            db_path=args.db,
            files=files,
            skip_import=args.skip_import,
            alert_days=args.alert_days,
            report_output=args.output,
            write_report=(not args.no_report),
        )
        payload = result["payload"]
        print(
            f"[ops-watch #{idx}] run_at={now_utc_iso()} "
            f"parsed={result['parsed_count']} inserted={result['inserted_count']} "
            f"active={payload['active_task_count']} upcoming={payload['upcoming_count']} "
            f"overdue={payload['overdue_count']} speedrun_started={int(bool(payload['speedrun_started']))}"
        )
        if runs > 0 and idx >= runs:
            break
        time.sleep(max(3, interval))
    return 0
