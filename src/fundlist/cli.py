from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

from .api import api_serve_command
from .collector import CollectorConfig, collect_from_sources, parse_csv_list
from .changefeed import changes_list_command, changes_report_command
from .fundraising import (
    DEFAULT_FUNDRAISE_FILES,
    fundraise_import_command,
    fundraise_report_command,
    fundraise_run_command,
)
from .openclaw import openclaw_multi_command
from .submission_finder import (
    submission_export_command,
    submission_list_command,
    submission_report_command,
    submission_scan_command,
)
from .submission_tasks import (
    task_add_note_command,
    task_create_command,
    task_followup_command,
    task_list_command,
    task_ready_command,
    task_submitted_command,
    task_update_command,
    task_view_command,
)
from .store import SQLiteStore
from .vc_ops import (
    ops_list_command,
    ops_program_report_command,
    ops_report_command,
    ops_sync_command,
    ops_watch_command,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = os.environ.get("FUNDLIST_DB", str(PROJECT_ROOT / "data" / "investment_items.db"))
DEFAULT_OPENCLAW_REPO = os.environ.get("OPENCLAW_REPO", "Virtual-Protocol/openclaw-acp")
DEFAULT_SEC_TICKERS = os.environ.get("SEC_TICKERS", "AAPL,MSFT,NVDA,TSLA,AMZN")
DEFAULT_SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT",
    "FundlistAgent/0.1 (educational; contact: sample@example.com)",
)
DEFAULT_FRED_SERIES = os.environ.get("FRED_SERIES", "FEDFUNDS,CPIAUCSL,UNRATE,GDP")
DEFAULT_COIN_IDS = os.environ.get("COIN_IDS", "bitcoin,ethereum,solana")
DEFAULT_FUNDRAISE_FILES_ARG = os.environ.get("FUNDRAISE_FILES", ",".join(DEFAULT_FUNDRAISE_FILES))
DEFAULT_FUNDRAISE_REPORT_PATH = os.environ.get(
    "FUNDRAISE_REPORT_PATH",
    str(PROJECT_ROOT / "data" / "reports" / "fundraising_report.md"),
)
DEFAULT_GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
DEFAULT_AI_PROVIDER = os.environ.get("AI_PROVIDER", "groq").strip().lower() or "groq"
DEFAULT_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
DEFAULT_HUGGINGFACE_MODEL = os.environ.get("HUGGINGFACE_MODEL", "Qwen/Qwen2.5-7B-Instruct")
DEFAULT_OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
DEFAULT_AI_MODEL = os.environ.get(
    "AI_MODEL",
    (
        DEFAULT_GROQ_MODEL
        if DEFAULT_AI_PROVIDER == "groq"
        else DEFAULT_GEMINI_MODEL
        if DEFAULT_AI_PROVIDER == "gemini"
        else DEFAULT_HUGGINGFACE_MODEL
        if DEFAULT_AI_PROVIDER == "huggingface"
        else DEFAULT_OPENROUTER_MODEL
    ),
)
DEFAULT_OPENCLAW_MULTI_REPORT_PATH = os.environ.get(
    "OPENCLAW_MULTI_REPORT_PATH",
    str(PROJECT_ROOT / "data" / "reports" / "openclaw_multi_agent_report.md"),
)
DEFAULT_OPENCLAW_ACP_DIR = os.environ.get("OPENCLAW_ACP_DIR", "")
DEFAULT_OPENCLAW_ACP_CMD = os.environ.get("OPENCLAW_ACP_CMD", "")
DEFAULT_VC_OPS_REPORT_PATH = os.environ.get(
    "VC_OPS_REPORT_PATH",
    str(PROJECT_ROOT / "data" / "reports" / "vc_ops_report.md"),
)
DEFAULT_SUBMISSION_REPORT_PATH = os.environ.get(
    "SUBMISSION_REPORT_PATH",
    str(PROJECT_ROOT / "data" / "reports" / "submission_targets_report.md"),
)
DEFAULT_CHANGES_REPORT_PATH = os.environ.get(
    "CHANGES_REPORT_PATH",
    str(PROJECT_ROOT / "data" / "reports" / "opportunity_changes_report.md"),
)
DEFAULT_VC_PROGRAM_REPORT_PATH = os.environ.get(
    "VC_PROGRAM_REPORT_PATH",
    str(PROJECT_ROOT / "data" / "reports" / "program_reports" / "alliance_dao_submission_report.md"),
)
DEFAULT_API_HOST = os.environ.get("FUNDLIST_API_HOST", "127.0.0.1")
DEFAULT_API_PORT = int(os.environ.get("FUNDLIST_API_PORT", "8787"))


def collect_command(args: argparse.Namespace) -> int:
    selected = [s.strip().lower() for s in args.sources.split(",") if s.strip()]
    valid = {"openclaw", "sec", "fred", "coingecko"}
    unknown = sorted(set(selected) - valid)
    if unknown:
        print(f"[error] unknown sources: {', '.join(unknown)}", file=sys.stderr)
        return 2

    cfg = CollectorConfig(
        openclaw_repo=args.openclaw_repo,
        openclaw_limit=args.openclaw_limit,
        sec_tickers=parse_csv_list(args.sec_tickers),
        sec_limit=args.sec_limit,
        sec_user_agent=args.sec_user_agent,
        fred_series=parse_csv_list(args.fred_series),
        fred_api_key=os.environ.get("FRED_API_KEY"),
        coin_ids=parse_csv_list(args.coin_ids),
        coingecko_limit=args.coingecko_limit,
        coingecko_api_key=os.environ.get("COINGECKO_API_KEY"),
        github_token=os.environ.get("GITHUB_TOKEN"),
    )

    store = SQLiteStore(args.db)
    fetched_total = 0
    all_items = []

    for source in selected:
        try:
            source_items = collect_from_sources([source], cfg).get(source, [])
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] {source} collection failed: {exc}", file=sys.stderr)
            continue
        fetched_total += len(source_items)
        all_items.extend(source_items)
        print(f"[ok] {source} collected: {len(source_items)}")

    inserted = store.insert_items(all_items)
    print(f"[done] total fetched={fetched_total} inserted={inserted} db={args.db}")
    return 0


def list_command(args: argparse.Namespace) -> int:
    store = SQLiteStore(args.db)
    rows = store.list_items(limit=args.limit, source=args.source, symbol=args.symbol)
    if not rows:
        print("(no rows)")
        return 0

    for row in rows:
        print(
            " | ".join(
                [
                    f"#{row['id']}",
                    row["published_at"],
                    row["source"],
                    row["category"],
                    row["symbol"],
                    row["title"],
                    row["url"],
                ]
            )
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fundlist",
        description="Collect investment data from multiple sources and persist to SQLite.",
    )
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite database path")
    sub = parser.add_subparsers(dest="cmd", required=True)

    collect = sub.add_parser("collect", help="Collect source data and store it")
    collect.add_argument(
        "--sources",
        default="openclaw,sec,fred,coingecko",
        help="Comma-separated source names: openclaw,sec,fred,coingecko",
    )
    collect.add_argument("--openclaw-repo", default=DEFAULT_OPENCLAW_REPO)
    collect.add_argument("--openclaw-limit", type=int, default=15)
    collect.add_argument("--sec-tickers", default=DEFAULT_SEC_TICKERS)
    collect.add_argument("--sec-limit", type=int, default=4)
    collect.add_argument("--sec-user-agent", default=DEFAULT_SEC_USER_AGENT)
    collect.add_argument("--fred-series", default=DEFAULT_FRED_SERIES)
    collect.add_argument("--coin-ids", default=DEFAULT_COIN_IDS)
    collect.add_argument("--coingecko-limit", type=int, default=50)
    collect.set_defaults(func=collect_command)

    list_cmd = sub.add_parser("list", help="List stored records")
    list_cmd.add_argument("--limit", type=int, default=30)
    list_cmd.add_argument("--source", default=None)
    list_cmd.add_argument("--symbol", default=None)
    list_cmd.set_defaults(func=list_command)

    fundraise_import = sub.add_parser("fundraise-import", help="Import fundraising files into SQLite")
    fundraise_import.add_argument(
        "--files",
        default=DEFAULT_FUNDRAISE_FILES_ARG,
        help="Comma-separated absolute file paths (.csv,.tsv,.xlsx,.pdf)",
    )
    fundraise_import.set_defaults(func=fundraise_import_command)

    fundraise_report = sub.add_parser("fundraise-report", help="Generate fundraising markdown report")
    fundraise_report.add_argument("--output", default=DEFAULT_FUNDRAISE_REPORT_PATH)
    fundraise_report.add_argument("--with-ai", action="store_true", help="Use AI provider for summary section")
    fundraise_report.add_argument(
        "--ai-provider",
        choices=["groq", "gemini", "huggingface", "openrouter"],
        default=DEFAULT_AI_PROVIDER,
        help="AI provider for summary",
    )
    fundraise_report.add_argument("--model", default="", help="AI model (optional; provider default if empty)")
    fundraise_report.set_defaults(func=fundraise_report_command)

    fundraise_run = sub.add_parser("fundraise-run", help="Import fundraising files and generate report")
    fundraise_run.add_argument(
        "--files",
        default=DEFAULT_FUNDRAISE_FILES_ARG,
        help="Comma-separated absolute file paths (.csv,.tsv,.xlsx,.pdf)",
    )
    fundraise_run.add_argument("--output", default=DEFAULT_FUNDRAISE_REPORT_PATH)
    fundraise_run.add_argument("--with-ai", action="store_true", help="Use AI provider for summary section")
    fundraise_run.add_argument(
        "--ai-provider",
        choices=["groq", "gemini", "huggingface", "openrouter"],
        default=DEFAULT_AI_PROVIDER,
        help="AI provider for summary",
    )
    fundraise_run.add_argument("--model", default="", help="AI model (optional; provider default if empty)")
    fundraise_run.set_defaults(func=fundraise_run_command)

    openclaw_multi = sub.add_parser(
        "openclaw-multi",
        help="Run multiple ACP agents in parallel (browse -> job create -> polling -> report)",
    )
    openclaw_multi.add_argument("--query", required=True, help='ACP browse query, e.g. "fundraising outreach"')
    openclaw_multi.add_argument("--max-agents", type=int, default=3)
    openclaw_multi.add_argument(
        "--offering-filter",
        default="",
        help="Optional substring filter for offering name",
    )
    openclaw_multi.add_argument(
        "--requirements-json",
        default="{}",
        help='Base requirements JSON object applied to all jobs, e.g. \'{"task":"fundraising"}\'',
    )
    openclaw_multi.add_argument(
        "--requirements-map",
        default="",
        help="Path to JSON map (offering name or wallet|offering -> requirements object)",
    )
    openclaw_multi.add_argument("--poll-interval", type=int, default=10)
    openclaw_multi.add_argument("--timeout-seconds", type=int, default=600)
    openclaw_multi.add_argument("--output", default=DEFAULT_OPENCLAW_MULTI_REPORT_PATH)
    openclaw_multi.add_argument("--dry-run", action="store_true")
    openclaw_multi.add_argument("--acp-dir", default=DEFAULT_OPENCLAW_ACP_DIR)
    openclaw_multi.add_argument("--acp-cmd", default=DEFAULT_OPENCLAW_ACP_CMD)
    openclaw_multi.set_defaults(func=openclaw_multi_command)

    ops_sync = sub.add_parser(
        "ops-sync",
        help="VC assistant sync: import -> deadline/speedrun analysis -> snapshot/event logging",
    )
    ops_sync.add_argument(
        "--files",
        default=DEFAULT_FUNDRAISE_FILES_ARG,
        help="Comma-separated absolute file paths (.csv,.tsv,.xlsx,.pdf)",
    )
    ops_sync.add_argument("--skip-import", action="store_true", help="Skip file import and use DB only")
    ops_sync.add_argument("--alert-days", type=int, default=14)
    ops_sync.add_argument("--output", default=DEFAULT_VC_OPS_REPORT_PATH)
    ops_sync.add_argument("--no-report", action="store_true", help="Skip markdown report generation")
    ops_sync.set_defaults(func=ops_sync_command)

    ops_report = sub.add_parser(
        "ops-report",
        help="Generate VC ops report (optionally re-import before reporting)",
    )
    ops_report.add_argument(
        "--files",
        default=DEFAULT_FUNDRAISE_FILES_ARG,
        help="Comma-separated absolute file paths (.csv,.tsv,.xlsx,.pdf)",
    )
    ops_report.add_argument("--skip-import", action="store_true", help="Skip file import and use DB only")
    ops_report.add_argument("--alert-days", type=int, default=14)
    ops_report.add_argument("--output", default=DEFAULT_VC_OPS_REPORT_PATH)
    ops_report.set_defaults(func=ops_report_command)

    ops_program_report = sub.add_parser(
        "ops-program-report",
        help="Generate accelerator/grant submission report for a specific program keyword",
    )
    ops_program_report.add_argument(
        "--files",
        default=DEFAULT_FUNDRAISE_FILES_ARG,
        help="Comma-separated absolute file paths (.csv,.tsv,.xlsx,.pdf)",
    )
    ops_program_report.add_argument("--skip-import", action="store_true", help="Skip file import and use DB only")
    ops_program_report.add_argument("--program", required=True, help="Program keyword, e.g. 'alliance dao'")
    ops_program_report.add_argument("--alert-days", type=int, default=21)
    ops_program_report.add_argument("--output", default=DEFAULT_VC_PROGRAM_REPORT_PATH)
    ops_program_report.set_defaults(func=ops_program_report_command)

    ops_list = sub.add_parser("ops-list", help="List active submission tasks by deadline window")
    ops_list.add_argument("--from-days", type=int, default=-365, help="Start of days-left window")
    ops_list.add_argument("--to-days", type=int, default=30, help="End of days-left window")
    ops_list.add_argument("--limit", type=int, default=80)
    ops_list.add_argument("--speedrun-only", action="store_true")
    ops_list.add_argument("--include-no-deadline", action="store_true")
    ops_list.add_argument(
        "--bucket",
        default="",
        choices=["", "today", "overdue", "this_week", "later", "no_deadline"],
        help="Optional deadline bucket filter",
    )
    ops_list.set_defaults(func=ops_list_command)

    ops_watch = sub.add_parser(
        "ops-watch",
        help="Continuously run VC ops sync and print status each cycle",
    )
    ops_watch.add_argument(
        "--files",
        default=DEFAULT_FUNDRAISE_FILES_ARG,
        help="Comma-separated absolute file paths (.csv,.tsv,.xlsx,.pdf)",
    )
    ops_watch.add_argument("--skip-import", action="store_true", help="Skip file import and use DB only")
    ops_watch.add_argument("--alert-days", type=int, default=14)
    ops_watch.add_argument("--output", default=DEFAULT_VC_OPS_REPORT_PATH)
    ops_watch.add_argument("--no-report", action="store_true")
    ops_watch.add_argument("--interval-seconds", type=int, default=900)
    ops_watch.add_argument("--runs", type=int, default=0, help="0 means infinite loop")
    ops_watch.set_defaults(func=ops_watch_command)

    submission_scan = sub.add_parser(
        "submission-scan",
        help="Discover VC/accelerator apply-pitch pages and store normalized targets",
    )
    submission_scan.add_argument(
        "--seed-urls",
        default="",
        help="Comma-separated seed URLs/domains to scan first",
    )
    submission_scan.add_argument(
        "--query",
        action="append",
        default=[],
        help="Discovery query (repeatable). If omitted, built-in query set is used.",
    )
    submission_scan.add_argument("--skip-search", action="store_true", help="Skip web query discovery stage")
    submission_scan.add_argument(
        "--no-fundraise-seeds",
        dest="from_fundraise",
        action="store_false",
        help="Do not include website seeds from fundraising_records table",
    )
    submission_scan.set_defaults(from_fundraise=True)
    submission_scan.add_argument("--fundraise-seed-limit", type=int, default=300)
    submission_scan.add_argument("--max-results-per-query", type=int, default=10)
    submission_scan.add_argument("--max-sites", type=int, default=120)
    submission_scan.add_argument("--max-pages-per-site", type=int, default=6)
    submission_scan.add_argument("--http-timeout", type=int, default=10)
    submission_scan.add_argument("--query-file", default="", help="Path to query template file (one query per line)")
    submission_scan.add_argument("--sector", default="", help="Comma-separated focus sectors (optional)")
    submission_scan.add_argument("--stage", default="", help="Comma-separated focus stages (optional)")
    submission_scan.add_argument("--region", default="", help="Comma-separated focus regions (optional)")
    submission_scan.add_argument("--report-limit", type=int, default=120)
    submission_scan.add_argument("--status-filter", default="")
    submission_scan.add_argument("--org-type-filter", default="")
    submission_scan.add_argument("--min-score", type=int, default=0)
    submission_scan.add_argument("--event-limit", type=int, default=30)
    submission_scan.add_argument("--json-output", default="", help="Optional JSON export path")
    submission_scan.add_argument("--output", default=DEFAULT_SUBMISSION_REPORT_PATH)
    submission_scan.set_defaults(func=submission_scan_command)

    submission_list = sub.add_parser("submission-list", help="List discovered submission targets from DB")
    submission_list.add_argument("--limit", type=int, default=80)
    submission_list.add_argument("--status-filter", default="")
    submission_list.add_argument("--org-type-filter", default="")
    submission_list.add_argument("--min-score", type=int, default=0)
    submission_list.set_defaults(func=submission_list_command)

    submission_report = sub.add_parser("submission-report", help="Generate markdown report from discovered targets")
    submission_report.add_argument("--limit", type=int, default=120)
    submission_report.add_argument("--status-filter", default="")
    submission_report.add_argument("--org-type-filter", default="")
    submission_report.add_argument("--min-score", type=int, default=0)
    submission_report.add_argument("--event-limit", type=int, default=30)
    submission_report.add_argument("--output", default=DEFAULT_SUBMISSION_REPORT_PATH)
    submission_report.set_defaults(func=submission_report_command)

    submission_export = sub.add_parser("submission-export", help="Export discovered submission targets as JSON")
    submission_export.add_argument("--limit", type=int, default=200)
    submission_export.add_argument("--status-filter", default="")
    submission_export.add_argument("--org-type-filter", default="")
    submission_export.add_argument("--min-score", type=int, default=0)
    submission_export.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "data" / "reports" / "submission_targets.json"),
    )
    submission_export.set_defaults(func=submission_export_command)

    changes_list = sub.add_parser("changes-list", help="List structured opportunity changes")
    changes_list.add_argument(
        "--change-type",
        default="",
        choices=["", "new_opportunity", "status_changed", "deadline_changed", "submission_url_changed", "source_url_changed", "reopened"],
    )
    changes_list.add_argument("--since-days", type=int, default=1)
    changes_list.add_argument("--limit", type=int, default=40)
    changes_list.set_defaults(func=changes_list_command)

    changes_report = sub.add_parser("changes-report", help="Generate markdown report from structured opportunity changes")
    changes_report.add_argument(
        "--change-type",
        default="",
        choices=["", "new_opportunity", "status_changed", "deadline_changed", "submission_url_changed", "source_url_changed", "reopened"],
    )
    changes_report.add_argument("--since-days", type=int, default=1)
    changes_report.add_argument("--limit", type=int, default=50)
    changes_report.add_argument("--output", default=DEFAULT_CHANGES_REPORT_PATH)
    changes_report.set_defaults(func=changes_report_command)

    task_create = sub.add_parser("task-create", help="Create a managed submission task from a verified opportunity")
    task_create.add_argument("target", help="Opportunity fingerprint or keyword query")
    task_create.add_argument("--workspace", default="default")
    task_create.add_argument("--owner", default="")
    task_create.add_argument("--due-date", default="")
    task_create.add_argument(
        "--submission-state",
        default="researching",
        choices=sorted(
            [
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
            ]
        ),
    )
    task_create.add_argument("--notes", default="")
    task_create.set_defaults(func=task_create_command)

    task_list = sub.add_parser("task-list", help="List managed submission tasks")
    task_list.add_argument("--workspace", default="default")
    task_list.add_argument("--submission-state", default="")
    task_list.add_argument("--owner", default="")
    task_list.add_argument(
        "--bucket",
        default="",
        choices=["", "ready", "submitted", "followup", "blocked", "active", "today", "this_week", "closed"],
    )
    task_list.add_argument("--limit", type=int, default=40)
    task_list.set_defaults(func=task_list_command)

    task_view = sub.add_parser("task-view", help="Show a single managed submission task")
    task_view.add_argument("task_id", type=int)
    task_view.add_argument("--limit", type=int, default=20, help="Number of recent updates to include")
    task_view.set_defaults(func=task_view_command)

    task_update = sub.add_parser("task-update", help="Update managed submission task fields")
    task_update.add_argument("task_id", type=int)
    task_update.add_argument(
        "--submission-state",
        default=None,
        choices=[
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
        ],
    )
    task_update.add_argument("--owner", default=None)
    task_update.add_argument("--due-date", default=None)
    task_update.add_argument("--notes", default=None)
    task_update.add_argument("--recommended-action", default=None)
    task_update.set_defaults(func=task_update_command)

    task_add_note = sub.add_parser("task-add-note", help="Append an activity note to a managed task")
    task_add_note.add_argument("task_id", type=int)
    task_add_note.add_argument("body", help="Note body")
    task_add_note.set_defaults(func=task_add_note_command)

    task_ready = sub.add_parser("task-ready", help="Mark task as ready_to_submit")
    task_ready.add_argument("task_id", type=int)
    task_ready.add_argument("--note", default="")
    task_ready.set_defaults(func=task_ready_command)

    task_submitted = sub.add_parser("task-submitted", help="Mark task as submitted and set follow-up due")
    task_submitted.add_argument("task_id", type=int)
    task_submitted.add_argument("--submitted-at", default="")
    task_submitted.add_argument("--follow-up-days", type=int, default=14)
    task_submitted.add_argument("--note", default="")
    task_submitted.set_defaults(func=task_submitted_command)

    task_followup = sub.add_parser("task-followup", help="Mark task as follow_up_due")
    task_followup.add_argument("task_id", type=int)
    task_followup.add_argument("--due-date", default="")
    task_followup.add_argument("--note", default="")
    task_followup.set_defaults(func=task_followup_command)

    api_serve = sub.add_parser("api-serve", help="Serve dependency-free Phase 1 HTTP API")
    api_serve.add_argument("--host", default=DEFAULT_API_HOST)
    api_serve.add_argument("--port", type=int, default=DEFAULT_API_PORT)
    api_serve.add_argument("--ops-report", default=DEFAULT_VC_OPS_REPORT_PATH)
    api_serve.add_argument("--submission-report", default=DEFAULT_SUBMISSION_REPORT_PATH)
    api_serve.add_argument(
        "--submission-json",
        default=str(PROJECT_ROOT / "data" / "reports" / "submission_targets.json"),
    )
    api_serve.add_argument("--api-token", default=os.environ.get("FUNDLIST_API_TOKEN", ""))
    api_serve.set_defaults(func=api_serve_command)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
