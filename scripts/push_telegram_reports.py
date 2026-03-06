#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
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


def parse_ops_digest(path: Path) -> str:
    if not path.exists():
        return f"(missing) {path}"
    lines = path.read_text(encoding="utf-8").splitlines()
    wanted_prefixes = [
        "- Generated (UTC):",
        "- Total tracked tasks:",
        "- Active tasks:",
        "- Deadline alert window:",
    ]
    out: List[str] = ["[OPS DIGEST]"]
    for pref in wanted_prefixes:
        for ln in lines:
            if ln.startswith(pref):
                out.append(ln)
                break

    speedrun_rows = [ln for ln in lines if ln.startswith("- Speedrun ") or ln.startswith("- [accelerator_program]")]
    if speedrun_rows:
        out.append("")
        out.append("speedrun:")
        out.extend(speedrun_rows[:3])

    alert_rows: List[str] = []
    for i, ln in enumerate(lines):
        if ln.strip() == "## Deadline Alerts":
            for row in lines[i + 1 :]:
                if row.startswith("## "):
                    break
                if row.startswith("- "):
                    alert_rows.append(row)
            break
    if alert_rows:
        out.append("")
        out.append("deadline alerts:")
        out.extend(alert_rows[:5])
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
    p.add_argument("--ops-report", default=str(DEFAULT_OPS_REPORT))
    p.add_argument("--programs", default=os.environ.get("VC_OPS_PROGRAMS", "alliance dao"))
    p.add_argument("--program-dir", default=str(DEFAULT_PROGRAM_DIR))
    p.add_argument("--submission-report", default=str(DEFAULT_SUBMISSION_REPORT))
    p.add_argument("--submission-json", default=str(DEFAULT_SUBMISSION_JSON))
    p.add_argument("--submission-top-n", type=int, default=int(os.environ.get("VC_SUBMISSION_TOP_N", "8")))
    p.add_argument("--dry-run", action="store_true")
    return p


def main() -> int:
    load_env_file(ENV_FILE)
    args = build_parser().parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("TELEGRAM_BOT_TOKEN missing", file=sys.stderr)
        log_line("skip: TELEGRAM_BOT_TOKEN missing")
        return 2

    chat_id: Optional[int] = None
    if args.chat_id.strip():
        chat_id = parse_int(args.chat_id)
    if chat_id is None:
        chat_id = detect_chat_id()
    if chat_id is None:
        print("telegram chat id not found (set TELEGRAM_REPORT_CHAT_ID)", file=sys.stderr)
        log_line("skip: chat id not found")
        return 2

    programs = [p.strip() for p in args.programs.split(",") if p.strip()]
    ops_path = Path(args.ops_report).expanduser()
    program_dir = Path(args.program_dir).expanduser()
    submission_path = Path(args.submission_report).expanduser()
    submission_json = Path(args.submission_json).expanduser()

    header = (
        f"[VC Auto Report] {now_utc_iso()}\n"
        f"- chat_id: {chat_id}\n"
        f"- programs: {', '.join(programs) if programs else '-'}"
    )

    sections: List[str] = [header, parse_ops_digest(ops_path)]

    if os.environ.get("VC_OPS_INCLUDE_SUBMISSION_REPORT", "1").strip() != "0":
        sections.append(parse_submission_digest(submission_path, submission_json, top_n=args.submission_top_n))

    for program in programs[:10]:
        slug = program_slug(program)
        path = program_dir / f"{slug}_submission_report.md"
        excerpt = parse_program_digest(path, max_lines=26)
        sections.append(f"[PROGRAM: {program}]\n{excerpt}")

    if args.dry_run:
        print("\n\n---\n\n".join(sections))
        log_line(f"dry-run chat_id={chat_id} sections={len(sections)}")
        return 0

    total_chunks = 0
    for sec in sections:
        total_chunks += send_message(token, chat_id, sec)

    log_line(f"sent chat_id={chat_id} sections={len(sections)} chunks={total_chunks}")
    print(f"[done] pushed telegram reports chat_id={chat_id} sections={len(sections)} chunks={total_chunks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
