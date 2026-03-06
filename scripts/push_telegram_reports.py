#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


ROOT = Path(__file__).resolve().parents[1]
CONTEXT_DIR = ROOT / ".context"
ENV_FILE = CONTEXT_DIR / "telegram.env"
BOT_LOG = CONTEXT_DIR / "telegram_bot.log"
PUSH_LOG = CONTEXT_DIR / "telegram_report_push.log"
DEFAULT_OPS_REPORT = ROOT / "data" / "reports" / "vc_ops_report.md"
DEFAULT_DB = Path(os.environ.get("FUNDLIST_DB", str(ROOT / "data" / "investment_items.db")))
DEFAULT_PROGRAM_DIR = ROOT / "data" / "reports" / "program_reports"
DEFAULT_SUBMISSION_REPORT = ROOT / "data" / "reports" / "submission_targets_report.md"
DEFAULT_SUBMISSION_JSON = ROOT / "data" / "reports" / "submission_targets.json"


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def log_line(text: str) -> None:
    CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
    with PUSH_LOG.open("a", encoding="utf-8") as fp:
        fp.write(f"[{now_utc_iso()}] {text}\n")


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        k = key.strip()
        v = value.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        if k and k not in os.environ:
            os.environ[k] = v


def split_message(text: str, limit: int = 3400) -> List[str]:
    src = text.strip() or "(empty)"
    out: List[str] = []
    while len(src) > limit:
        cut = src.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        out.append(src[:cut].strip())
        src = src[cut:].strip()
    if src:
        out.append(src)
    return out


def parse_int(value: str) -> Optional[int]:
    try:
        return int(value.strip())
    except Exception:  # noqa: BLE001
        return None


def detect_chat_id() -> Optional[int]:
    direct = os.environ.get("TELEGRAM_REPORT_CHAT_ID", "").strip()
    if direct:
        parsed = parse_int(direct)
        if parsed is not None:
            return parsed

    allowed = os.environ.get("TELEGRAM_ALLOWED_CHATS", "").strip()
    if allowed:
        for token in allowed.split(","):
            parsed = parse_int(token)
            if parsed is not None:
                return parsed

    if BOT_LOG.exists():
        pattern = re.compile(r"chat_id=(-?\d+)")
        last: Optional[int] = None
        for line in BOT_LOG.read_text(encoding="utf-8").splitlines()[-500:]:
            m = pattern.search(line)
            if not m:
                continue
            try:
                last = int(m.group(1))
            except Exception:  # noqa: BLE001
                continue
        if last is not None:
            return last
    return None


def program_slug(value: str) -> str:
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", value.strip().lower())
    return slug.strip("_") or "program"


def read_excerpt(path: Path, *, max_lines: int = 80, max_chars: int = 2800) -> str:
    if not path.exists():
        return f"(missing) {path}"
    lines = path.read_text(encoding="utf-8").splitlines()[:max_lines]
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n... (truncated)"
    return text


def _days_mark(days_left: Optional[int]) -> str:
    if days_left is None:
        return "D?"
    if days_left >= 0:
        return f"D-{days_left}"
    return f"D+{abs(days_left)}"


def _bucket_sort_value(bucket: str) -> int:
    return {"today": 0, "overdue": 1, "this_week": 2, "later": 3, "no_deadline": 4}.get(bucket, 9)


def _load_ops_rows(db_path: Path) -> List[sqlite3.Row]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT
              org_name, deadline_bucket, deadline_date, days_left, status_norm, is_speedrun,
              priority_score, priority_reason, submission_url, website, fit_tags
            FROM vc_submission_tasks
            WHERE is_active = 1
            ORDER BY
              CASE deadline_bucket
                WHEN 'today' THEN 0
                WHEN 'overdue' THEN 1
                WHEN 'this_week' THEN 2
                WHEN 'later' THEN 3
                ELSE 4
              END,
              priority_score DESC,
              deadline_date ASC,
              org_name ASC
            LIMIT 240
            """
        ).fetchall()
    except Exception:  # noqa: BLE001
        rows = []
    finally:
        conn.close()
    return rows


def _load_recent_events(db_path: Path, limit: int = 6) -> List[sqlite3.Row]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT created_at, event_type, title, body
            FROM vc_ops_events
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    except Exception:  # noqa: BLE001
        rows = []
    finally:
        conn.close()
    return rows


def _format_task_line(idx: int, row: sqlite3.Row) -> str:
    url = str(row["submission_url"] or row["website"] or "").strip()
    reason = str(row["priority_reason"] or "").strip()
    return (
        f"{idx}. {row['org_name']} | {_days_mark(row['days_left'])} | "
        f"score={row['priority_score']} | {row['status_norm']} | "
        f"{reason or '-'} | {url or '-'}"
    )


def build_ops_digest(db_path: Path, report_path: Path, *, mode: str = "morning") -> str:
    rows = _load_ops_rows(db_path)
    submission_json = ROOT / "data" / "reports" / "submission_targets.json"
    submission_items = _sort_submission_items(load_submission_items(submission_json))
    apply_now_items = [
        item
        for item in submission_items
        if str(item.get("status", "")).lower() in {"open", "rolling"} and int(item.get("score", 0) or 0) >= 9
    ]
    speedrun_items = [item for item in submission_items if _is_speedrun_submission(item)]

    if not rows and not submission_items:
        if not report_path.exists():
            return f"(missing) {report_path}"
        return "[VC OPS]\n" + read_excerpt(report_path, max_lines=50, max_chars=2200)

    by_bucket: Dict[str, List[sqlite3.Row]] = {}
    for row in rows:
        bucket = str(row["deadline_bucket"] or "no_deadline")
        by_bucket.setdefault(bucket, []).append(row)
    speedrun_rows = [row for row in rows if int(row["is_speedrun"] or 0)]

    title = "[VC OPS EVENING]" if mode == "evening" else "[VC OPS MORNING]"
    out = [
        title,
        f"- generated: {now_utc_iso()}",
        f"- active outreach rows: {len(rows)}",
        f"- apply targets: {len(submission_items)}",
    ]

    if mode == "evening":
        out.extend(["", "today changes:"])
        recent_events = _load_recent_events(db_path, limit=6)
        if recent_events:
            for event in recent_events:
                out.append(f"- {event['title']} | {str(event['body'])[:140]}")
        else:
            out.append("- none")

        out.extend(["", "tomorrow top 3:"])
        tomorrow_candidates = by_bucket.get("today", []) + by_bucket.get("overdue", [])
        if tomorrow_candidates:
            for idx, row in enumerate(tomorrow_candidates[:3], start=1):
                out.append(_format_task_line(idx, row))
        else:
            out.append("- none")

        out.extend(["", "apply now:"])
        if apply_now_items:
            for idx, item in enumerate(apply_now_items[:5], start=1):
                out.append(_format_submission_item(idx, item))
        else:
            out.append("- none")

        out.extend(["", "watchlist later:"])
        for idx, row in enumerate(by_bucket.get("this_week", [])[:5], start=1):
            out.append(_format_task_line(idx, row))
        if not by_bucket.get("this_week"):
            out.append("- none")
        return "\n".join(out)

    sections = [
        ("today", by_bucket.get("today", []) + by_bucket.get("overdue", []), 5),
        ("this week", by_bucket.get("this_week", []), 6),
        ("apply now", apply_now_items, 6),
        ("new speedrun / cohort", speedrun_items if speedrun_items else speedrun_rows, 5),
        ("no deadline / outreach", by_bucket.get("no_deadline", []), 6),
    ]
    for title_text, section_rows, limit in sections:
        out.extend(["", f"{title_text}:"])
        if section_rows:
            first = section_rows[0]
            if isinstance(first, sqlite3.Row):
                for idx, row in enumerate(section_rows[:limit], start=1):
                    out.append(_format_task_line(idx, row))
            else:
                for idx, item in enumerate(section_rows[:limit], start=1):
                    out.append(_format_submission_item(idx, item))
        else:
            out.append("- none")
    return "\n".join(out)


def parse_submission_digest(report_path: Path, json_path: Path, *, top_n: int = 8) -> str:
    if json_path.exists():
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            items = payload.get("items", [])
            if isinstance(items, list):
                filtered: List[Dict[str, object]] = [x for x in items if isinstance(x, dict)]
                filtered.sort(key=lambda x: int(x.get("score", 0)), reverse=True)
                top = filtered[:top_n]
                by_status: Dict[str, int] = {}
                by_type: Dict[str, int] = {}
                for it in filtered:
                    st = str(it.get("status", "unknown"))
                    tp = str(it.get("org_type", "Unknown"))
                    by_status[st] = by_status.get(st, 0) + 1
                    by_type[tp] = by_type.get(tp, 0) + 1
                out = ["[SUBMISSION DIGEST]"]
                out.append(f"- total: {len(filtered)}")
                out.append(
                    "- status: "
                    + ", ".join([f"{k}={v}" for k, v in sorted(by_status.items(), key=lambda kv: kv[0])])
                )
                out.append(
                    "- org_type: "
                    + ", ".join([f"{k}={v}" for k, v in sorted(by_type.items(), key=lambda kv: kv[0])])
                )
                out.append("")
                out.append("top targets:")
                for idx, it in enumerate(top, start=1):
                    out.append(
                        f"{idx}. [{it.get('status')}] [{it.get('org_type')}] score={it.get('score')} "
                        f"{it.get('org_name')} | {it.get('submission_type')}"
                    )
                    out.append(f"   {it.get('submission_url')}")
                return "\n".join(out)
        except Exception:  # noqa: BLE001
            pass

    # fallback
    return "[SUBMISSION DIGEST]\n" + read_excerpt(report_path, max_lines=40, max_chars=2000)


def load_submission_items(json_path: Path) -> List[Dict[str, object]]:
    if not json_path.exists():
        return []
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    items = payload.get("items", [])
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _submission_status_rank(status: str) -> int:
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


def _sort_submission_items(items: List[Dict[str, object]]) -> List[Dict[str, object]]:
    return sorted(
        items,
        key=lambda item: (
            _submission_status_rank(str(item.get("status", ""))),
            -int(item.get("score", 0) or 0),
            str(item.get("org_name", "")).lower(),
        ),
    )


def _is_speedrun_submission(item: Dict[str, object]) -> bool:
    blob = " ".join(
        [
            str(item.get("org_name", "")),
            str(item.get("org_type", "")),
            str(item.get("notes", "")),
            str(item.get("evidence", "")),
            str(item.get("source_page_snapshot", ""))[:400],
        ]
    ).lower()
    markers = ["speedrun", "cohort", "batch", "accelerator", "apply now", "demo day"]
    return any(marker in blob for marker in markers)


def _format_submission_item(idx: int, item: Dict[str, object]) -> str:
    return (
        f"{idx}. {item.get('org_name')} | {item.get('status')} | "
        f"score={item.get('score')} | {item.get('org_type')} | {item.get('submission_url')}"
    )


def parse_program_digest(path: Path, *, max_lines: int = 26) -> str:
    if not path.exists():
        return f"(missing) {path}"
    lines = path.read_text(encoding="utf-8").splitlines()
    kept: List[str] = []

    # Global summary lines
    for ln in lines:
        if ln.startswith("# Accelerator Submission Report"):
            kept.append(ln)
        if ln.startswith("- Generated") or ln.startswith("- Program filter") or ln.startswith("- Matched tasks"):
            kept.append(ln)
        if ln.startswith("- Alert window"):
            kept.append(ln)

    # Submission dossier essentials
    dossier_keys = ("- Status:", "- Deadline:", "- Apply URL:", "- Contact:", "- Email:")
    for ln in lines:
        if ln.startswith(dossier_keys):
            kept.append(ln)

    # Priority queue (top 3)
    for i, ln in enumerate(lines):
        if ln.strip() == "## Priority Queue":
            kept.append("## Priority Queue")
            count = 0
            for row in lines[i + 1 :]:
                if row.startswith("## "):
                    break
                if row.startswith("- "):
                    kept.append(row)
                    count += 1
                    if count >= 3:
                        break
            break

    # dedupe while preserving order
    uniq: List[str] = []
    seen = set()
    for ln in kept:
        k = ln.strip()
        if not k or k in seen:
            continue
        seen.add(k)
        uniq.append(ln)

    text = "\n".join(uniq[:max_lines]).strip()
    return text or "(no content)"


def telegram_call(token: str, method: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read().decode("utf-8")
    parsed = json.loads(body)
    if not parsed.get("ok"):
        raise RuntimeError(f"telegram api failed: {parsed}")
    return parsed


def send_message(token: str, chat_id: int, text: str) -> int:
    sent = 0
    for chunk in split_message(text):
        telegram_call(
            token,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            },
        )
        sent += 1
    return sent


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Push VC reports to Telegram channel/chat")
    p.add_argument("--chat-id", default="", help="Override telegram chat id")
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--ops-report", default=str(DEFAULT_OPS_REPORT))
    p.add_argument("--programs", default=os.environ.get("VC_OPS_PROGRAMS", "alliance dao"))
    p.add_argument("--program-dir", default=str(DEFAULT_PROGRAM_DIR))
    p.add_argument("--submission-report", default=str(DEFAULT_SUBMISSION_REPORT))
    p.add_argument("--submission-json", default=str(DEFAULT_SUBMISSION_JSON))
    p.add_argument("--submission-top-n", type=int, default=int(os.environ.get("VC_SUBMISSION_TOP_N", "8")))
    p.add_argument("--mode", choices=["morning", "evening"], default=os.environ.get("VC_OPS_PUSH_MODE", "morning"))
    p.add_argument("--dry-run", action="store_true")
    return p


def main() -> int:
    load_env_file(ENV_FILE)
    args = build_parser().parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token and not args.dry_run:
        print("TELEGRAM_BOT_TOKEN missing", file=sys.stderr)
        log_line("skip: TELEGRAM_BOT_TOKEN missing")
        return 2

    chat_id: Optional[int] = None
    if args.chat_id.strip():
        chat_id = parse_int(args.chat_id)
    if chat_id is None:
        chat_id = detect_chat_id()
    if chat_id is None and not args.dry_run:
        print("telegram chat id not found (set TELEGRAM_REPORT_CHAT_ID)", file=sys.stderr)
        log_line("skip: chat id not found")
        return 2

    programs = [p.strip() for p in args.programs.split(",") if p.strip()]
    db_path = Path(args.db).expanduser()
    ops_path = Path(args.ops_report).expanduser()
    program_dir = Path(args.program_dir).expanduser()
    submission_path = Path(args.submission_report).expanduser()
    submission_json = Path(args.submission_json).expanduser()
    sections: List[str] = [build_ops_digest(db_path, ops_path, mode=args.mode)]

    if os.environ.get("VC_OPS_INCLUDE_SUBMISSION_REPORT", "0").strip() != "0":
        sections.append(parse_submission_digest(submission_path, submission_json, top_n=args.submission_top_n))

    for program in programs[:10]:
        slug = program_slug(program)
        path = program_dir / f"{slug}_submission_report.md"
        if not path.exists():
            continue
        excerpt = parse_program_digest(path, max_lines=26)
        sections.append(f"[PROGRAM: {program}]\n{excerpt}")

    if args.dry_run:
        print("\n\n---\n\n".join(sections))
        log_line(f"dry-run chat_id={chat_id or 0} sections={len(sections)}")
        return 0

    total_chunks = 0
    for sec in sections:
        total_chunks += send_message(token, chat_id, sec)

    log_line(f"sent chat_id={chat_id} sections={len(sections)} chunks={total_chunks}")
    print(f"[done] pushed telegram reports chat_id={chat_id} sections={len(sections)} chunks={total_chunks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
