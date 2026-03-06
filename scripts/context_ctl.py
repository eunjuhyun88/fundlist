#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path
from typing import List


ROOT = Path(__file__).resolve().parents[1]
CTX_DIR = ROOT / ".context"
SNAPSHOT_DIR = CTX_DIR / "snapshots"
LATEST_PTR = CTX_DIR / "LATEST"
COMPACT_FILE = CTX_DIR / "COMPACT.md"


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def run_cmd(args: List[str]) -> str:
    try:
        out = subprocess.check_output(args, cwd=ROOT, stderr=subprocess.DEVNULL, text=True)
        return out.strip()
    except Exception:  # noqa: BLE001
        return ""


def ensure_dirs() -> None:
    CTX_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9가-힣]+", "-", text.strip()).strip("-")
    return cleaned.lower()[:32] or "state"


def read_text_arg(text: str | None, file_path: str | None) -> str:
    if text:
        return text.strip()
    if file_path:
        return Path(file_path).read_text(encoding="utf-8").strip()
    if not sys.stdin.isatty():
        data = sys.stdin.read().strip()
        if data:
            return data
    return ""


def latest_snapshot_file() -> Path | None:
    ensure_dirs()
    if LATEST_PTR.exists():
        maybe = SNAPSHOT_DIR / LATEST_PTR.read_text(encoding="utf-8").strip()
        if maybe.exists():
            return maybe
    files = sorted(SNAPSHOT_DIR.glob("*.md"))
    return files[-1] if files else None


def extract_bullets(text: str, max_items: int = 8) -> List[str]:
    out: List[str] = []
    for line in text.splitlines():
        item = line.strip()
        if item.startswith("- "):
            out.append(item[2:].strip())
        elif re.match(r"^\d+\.\s+", item):
            out.append(re.sub(r"^\d+\.\s+", "", item).strip())
        if len(out) >= max_items:
            break
    return [x for x in out if x]


def extract_section(text: str, heading: str) -> str:
    lines = text.splitlines()
    out: List[str] = []
    active = False
    heading_marker = f"## {heading}".strip()
    for line in lines:
        if line.strip().startswith("## "):
            if line.strip() == heading_marker:
                active = True
                continue
            if active:
                break
        if active:
            out.append(line)
    return "\n".join(out).strip()


def save_snapshot(args: argparse.Namespace) -> int:
    ensure_dirs()
    ts = utc_now()
    stamp = ts.strftime("%Y%m%d-%H%M%SZ")
    label = slugify(args.label or "manual")
    snapshot_name = f"{stamp}-{label}.md"
    snapshot_path = SNAPSHOT_DIR / snapshot_name

    summary = read_text_arg(args.summary, args.summary_file).replace("\\n", "\n")
    if not summary:
        summary = "No manual summary provided. Capture generated from repository state only."

    branch = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"]) or "(unknown)"
    commit = run_cmd(["git", "rev-parse", "--short", "HEAD"]) or "(none)"
    status = run_cmd(["git", "status", "--short"]) or "(clean or unavailable)"

    bullets = extract_bullets(summary, max_items=10)
    summary_block = "\n".join([f"- {b}" for b in bullets]) if bullets else f"- {summary}"

    content = f"""# State Snapshot: {stamp}

## Meta
- created_at_utc: {ts.isoformat()}
- branch: {branch}
- commit: {commit}
- label: {args.label or "manual"}

## Summary
{summary_block}

## Git Status
```text
{status}
```
"""

    snapshot_path.write_text(content, encoding="utf-8")
    LATEST_PTR.write_text(snapshot_name, encoding="utf-8")
    print(snapshot_path)
    return 0


def compact_snapshots(args: argparse.Namespace) -> int:
    ensure_dirs()
    files = sorted(SNAPSHOT_DIR.glob("*.md"))
    if not files:
        print("[warn] no snapshots to compact", file=sys.stderr)
        return 1

    selected = files[-max(1, args.max_snapshots) :]

    summary_items: List[str] = []
    status_items: List[str] = []
    for path in selected:
        text = path.read_text(encoding="utf-8")
        summary_section = (extract_section(text, "Summary") or text).replace("\\n", "\n")
        summary_items.extend(extract_bullets(summary_section, max_items=10))
        git_section = extract_section(text, "Git Status")
        for line in git_section.splitlines():
            line_strip = line.strip()
            if (
                line_strip.startswith("M ")
                or line_strip.startswith("A ")
                or line_strip.startswith("D ")
                or line_strip.startswith("?? ")
                or line_strip.startswith("R ")
            ):
                status_items.append(line_strip)

    def dedupe_keep_order(items: List[str], max_items: int) -> List[str]:
        seen = set()
        out = []
        for item in items:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
            if len(out) >= max_items:
                break
        return out

    summary_final = dedupe_keep_order(summary_items, max_items=args.max_bullets)
    status_final = dedupe_keep_order(status_items, max_items=args.max_files)

    compact_lines = [
        "# Compact Context",
        "",
        f"- generated_at_utc: {utc_now().isoformat()}",
        f"- source_snapshots: {len(selected)}",
        "",
        "## Core State",
    ]
    if summary_final:
        compact_lines.extend([f"- {item}" for item in summary_final])
    else:
        compact_lines.append("- (no summary bullets found)")

    compact_lines.extend(["", "## Changed Files (Recent)"])
    if status_final:
        compact_lines.extend([f"- {item}" for item in status_final])
    else:
        compact_lines.append("- (no changed files captured)")

    compact_lines.extend(
        [
            "",
            "## Restore Hint",
            "- Use latest snapshot for full detail.",
            "- If user says '복구해줘', ask whether context restore or file restore first.",
            "",
        ]
    )

    COMPACT_FILE.write_text("\n".join(compact_lines), encoding="utf-8")
    print(COMPACT_FILE)
    return 0


def restore_context(args: argparse.Namespace) -> int:
    ensure_dirs()
    if args.mode == "compact":
        path = COMPACT_FILE
    elif args.mode == "latest":
        path = latest_snapshot_file()
        if path is None:
            print("[warn] no snapshot found", file=sys.stderr)
            return 1
    else:
        target = args.id.strip()
        candidates = sorted(SNAPSHOT_DIR.glob(f"*{target}*.md"))
        if not candidates:
            print(f"[warn] snapshot not found for id={target}", file=sys.stderr)
            return 1
        path = candidates[-1]

    if path is None or not path.exists():
        print("[warn] restore source missing", file=sys.stderr)
        return 1

    if args.path_only:
        print(path)
    else:
        print(path.read_text(encoding="utf-8"))
    return 0


def list_snapshots(_: argparse.Namespace) -> int:
    ensure_dirs()
    files = sorted(SNAPSHOT_DIR.glob("*.md"))
    if not files:
        print("(no snapshots)")
        return 0
    for path in files:
        print(path.name)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Context state control: save, compact, restore.")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_save = sub.add_parser("save", help="Save current state snapshot")
    p_save.add_argument("--label", default="manual", help="short label for snapshot file name")
    p_save.add_argument("--summary", default=None, help="summary text")
    p_save.add_argument("--summary-file", default=None, help="path to summary markdown/text")
    p_save.set_defaults(func=save_snapshot)

    p_compact = sub.add_parser("compact", help="Build compact summary from recent snapshots")
    p_compact.add_argument("--max-snapshots", type=int, default=6)
    p_compact.add_argument("--max-bullets", type=int, default=20)
    p_compact.add_argument("--max-files", type=int, default=20)
    p_compact.set_defaults(func=compact_snapshots)

    p_restore = sub.add_parser("restore", help="Restore and print saved context")
    p_restore.add_argument("--mode", choices=["compact", "latest", "id"], default="compact")
    p_restore.add_argument("--id", default="", help="substring id when mode=id")
    p_restore.add_argument("--path-only", action="store_true", help="print path only")
    p_restore.set_defaults(func=restore_context)

    p_list = sub.add_parser("list", help="List snapshot files")
    p_list.set_defaults(func=list_snapshots)
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
