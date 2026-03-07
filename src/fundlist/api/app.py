from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import traceback
import urllib.parse
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import __version__
from ..changefeed import _since_iso
from ..review_queue import list_review_queue
from ..submission_finder import SubmissionStore
from ..submission_tasks import SubmissionTaskStore


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_API_TOKEN = os.environ.get("FUNDLIST_API_TOKEN", "").strip()


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _row_to_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    return {str(key): _jsonable(row[key]) for key in row.keys()}


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _parse_int(value: Any, *, default: int, minimum: Optional[int] = None, maximum: Optional[int] = None) -> int:
    try:
        parsed = int(str(value).strip())
    except Exception:  # noqa: BLE001
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _parse_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    raw = str(value).strip().lower()
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _split_csv(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _normalize_since(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return raw


class FundlistAPIServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: Tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        *,
        db_path: Path,
        ops_report_path: Path,
        submission_report_path: Path,
        submission_json_path: Path,
        api_token: str,
    ) -> None:
        super().__init__(server_address, handler_class)
        self.db_path = db_path
        self.ops_report_path = ops_report_path
        self.submission_report_path = submission_report_path
        self.submission_json_path = submission_json_path
        self.api_token = api_token.strip()


class FundlistAPIHandler(BaseHTTPRequestHandler):
    server: FundlistAPIServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), format % args))

    def _write_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> Dict[str, Any]:
        raw_len = self.headers.get("Content-Length", "").strip()
        if not raw_len:
            return {}
        length = _parse_int(raw_len, default=0, minimum=0)
        if length <= 0:
            return {}
        payload = self.rfile.read(length).decode("utf-8").strip()
        if not payload:
            return {}
        try:
            parsed = json.loads(payload)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"invalid json body: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("json body must be an object")
        return parsed

    def _require_auth(self) -> bool:
        token = self.server.api_token
        if not token:
            return True
        header = self.headers.get("Authorization", "").strip()
        if header == f"Bearer {token}":
            return True
        self._write_json(
            HTTPStatus.UNAUTHORIZED,
            {
                "ok": False,
                "error": "unauthorized",
                "message": "set Authorization: Bearer <token>",
            },
        )
        return False

    def _parse_request(self) -> Tuple[List[str], Dict[str, List[str]]]:
        parsed = urllib.parse.urlsplit(self.path)
        parts = [segment for segment in parsed.path.split("/") if segment]
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)
        return parts, query

    def _submission_store(self) -> SubmissionStore:
        return SubmissionStore(str(self.server.db_path))

    def _task_store(self) -> SubmissionTaskStore:
        return SubmissionTaskStore(str(self.server.db_path))

    def _handle_error(self, exc: Exception, *, status: int = HTTPStatus.BAD_REQUEST) -> None:
        self._write_json(
            status,
            {
                "ok": False,
                "error": exc.__class__.__name__,
                "message": str(exc),
            },
        )

    def do_GET(self) -> None:  # noqa: N802
        if not self._require_auth():
            return
        try:
            parts, query = self._parse_request()
            if parts == ["health"]:
                self._write_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "service": "fundlist-api",
                        "version": __version__,
                        "db_path": str(self.server.db_path),
                        "time": now_utc_iso(),
                    },
                )
                return
            if parts == ["v1", "opportunities"]:
                self._handle_list_opportunities(query)
                return
            if len(parts) == 3 and parts[:2] == ["v1", "opportunities"]:
                self._handle_get_opportunity(parts[2])
                return
            if parts == ["v1", "changes"]:
                self._handle_list_changes(query)
                return
            if parts == ["v1", "review-queue"]:
                self._handle_review_queue(query)
                return
            if parts == ["v1", "tasks"]:
                self._handle_list_tasks(query)
                return
            if parts == ["v1", "briefs", "latest"]:
                self._handle_latest_brief()
                return
            self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._handle_error(exc, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:  # noqa: N802
        if not self._require_auth():
            return
        try:
            parts, _query = self._parse_request()
            body = self._read_json_body()
            if parts == ["v1", "tasks"]:
                self._handle_create_task(body)
                return
            if len(parts) == 4 and parts[:2] == ["v1", "tasks"] and parts[3] == "submitted":
                self._handle_mark_submitted(parts[2], body)
                return
            if parts == ["v1", "scans", "full"]:
                self._handle_scan(body, full=True)
                return
            if parts == ["v1", "scans", "delta"]:
                self._handle_scan(body, full=False)
                return
            if parts == ["v1", "scans", "review-retry"]:
                self._handle_scan(body, full=False, review_targets_only=True)
                return
            if parts == ["v1", "scans", "fallback"]:
                self._handle_fallback_scan(body)
                return
            self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._handle_error(exc)

    def do_PATCH(self) -> None:  # noqa: N802
        if not self._require_auth():
            return
        try:
            parts, _query = self._parse_request()
            body = self._read_json_body()
            if len(parts) == 3 and parts[:2] == ["v1", "opportunities"]:
                self._handle_patch_opportunity(parts[2], body)
                return
            if len(parts) == 3 and parts[:2] == ["v1", "tasks"]:
                self._handle_patch_task(parts[2], body)
                return
            self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._handle_error(exc)

    def _handle_list_opportunities(self, query: Dict[str, List[str]]) -> None:
        store = self._submission_store()
        try:
            status = str(query.get("status", [""])[0]).strip()
            org_type = str(query.get("org_type", [""])[0]).strip()
            min_score = _parse_int(query.get("min_score", [0])[0], default=0, minimum=0)
            limit = _parse_int(query.get("limit", [80])[0], default=80, minimum=1, maximum=500)
            rows = store.list_targets(limit=limit, status=status, org_type=org_type, min_score=min_score)
            self._write_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "count": len(rows),
                    "items": [_row_to_dict(row) for row in rows],
                },
            )
        finally:
            store.close()

    def _handle_get_opportunity(self, fingerprint: str) -> None:
        store = self._submission_store()
        try:
            row = store.get_target(fingerprint)
            if row is None:
                self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "opportunity_not_found"})
                return
            self._write_json(HTTPStatus.OK, {"ok": True, "item": _row_to_dict(row)})
        finally:
            store.close()

    def _handle_patch_opportunity(self, fingerprint: str, body: Dict[str, Any]) -> None:
        score: Optional[int] = None
        if "score" in body and body.get("score") is not None:
            score = _parse_int(body.get("score"), default=0, minimum=0, maximum=1000)
        store = self._submission_store()
        try:
            row = store.override_target(
                fingerprint,
                org_name=body.get("org_name"),
                org_type=body.get("org_type"),
                source_url=body.get("source_url"),
                submission_url=body.get("submission_url"),
                submission_type=body.get("submission_type"),
                status=body.get("status"),
                requirements=body.get("requirements"),
                notes=body.get("notes"),
                evidence=body.get("evidence"),
                deadline_text=body.get("deadline_text"),
                deadline_date=body.get("deadline_date"),
                score=score,
            )
            if row is None:
                self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "opportunity_not_found"})
                return
            self._write_json(HTTPStatus.OK, {"ok": True, "item": _row_to_dict(row)})
        finally:
            store.close()

    def _handle_list_changes(self, query: Dict[str, List[str]]) -> None:
        store = self._submission_store()
        try:
            change_type = str(query.get("change_type", [""])[0]).strip()
            since = _normalize_since(str(query.get("since", [""])[0]))
            since_days = _parse_int(query.get("since_days", [1])[0], default=1, minimum=0, maximum=365)
            limit = _parse_int(query.get("limit", [50])[0], default=50, minimum=1, maximum=500)
            if not since:
                since = _since_iso(since_days)
            rows = store.list_changes(limit=limit, change_type=change_type, since=since)
            self._write_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "count": len(rows),
                    "items": [_row_to_dict(row) for row in rows],
                },
            )
        finally:
            store.close()

    def _handle_review_queue(self, query: Dict[str, List[str]]) -> None:
        store = self._submission_store()
        try:
            limit = _parse_int((query.get("limit") or ["40"])[0], default=40, minimum=1, maximum=200)
            rows = list_review_queue(store, limit=limit)
            self._write_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "items": rows,
                    "count": len(rows),
                },
            )
        finally:
            store.close()

    def _handle_list_tasks(self, query: Dict[str, List[str]]) -> None:
        store = self._task_store()
        try:
            workspace_key = str(query.get("workspace_key", ["default"])[0]).strip() or "default"
            submission_state = str(query.get("submission_state", [""])[0]).strip()
            owner = str(query.get("owner", [""])[0]).strip()
            bucket = str(query.get("bucket", [""])[0]).strip()
            limit = _parse_int(query.get("limit", [40])[0], default=40, minimum=1, maximum=500)
            rows = store.list_tasks(
                workspace_key=workspace_key,
                submission_state=submission_state,
                owner=owner,
                bucket=bucket,
                limit=limit,
            )
            self._write_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "count": len(rows),
                    "items": [_row_to_dict(row) for row in rows],
                },
            )
        finally:
            store.close()

    def _handle_create_task(self, body: Dict[str, Any]) -> None:
        target_query = str(body.get("target") or body.get("target_query") or "").strip()
        if not target_query:
            raise ValueError("target or target_query is required")
        workspace_key = str(body.get("workspace_key") or "default").strip() or "default"
        owner = str(body.get("owner") or "").strip()
        due_date = str(body.get("due_date") or "").strip()
        submission_state = str(body.get("submission_state") or "researching").strip()
        notes = str(body.get("notes") or "").strip()

        store = self._task_store()
        try:
            target_row, candidates, error = store.resolve_target(target_query)
            if target_row is None:
                payload: Dict[str, Any] = {
                    "ok": False,
                    "error": "target_resolution_failed",
                    "message": error,
                }
                if candidates:
                    payload["candidates"] = [_row_to_dict(candidate.row) for candidate in candidates]
                self._write_json(HTTPStatus.BAD_REQUEST, payload)
                return

            task_id, created = store.create_task(
                target=target_row,
                workspace_key=workspace_key,
                owner=owner,
                due_date=due_date,
                submission_state=submission_state,
                notes=notes,
                actor="api",
            )
            row = store.get_task(task_id)
            self._write_json(
                HTTPStatus.CREATED if created else HTTPStatus.OK,
                {
                    "ok": True,
                    "created": created,
                    "task": _row_to_dict(row),
                },
            )
        finally:
            store.close()

    def _handle_patch_task(self, task_id_raw: str, body: Dict[str, Any]) -> None:
        task_id = _parse_int(task_id_raw, default=0, minimum=1)
        if task_id <= 0:
            raise ValueError("valid task id is required")
        store = self._task_store()
        try:
            row = store.update_task(
                task_id,
                submission_state=body.get("submission_state"),
                owner=body.get("owner"),
                due_date=body.get("due_date"),
                notes=body.get("notes"),
                recommended_action=body.get("recommended_action"),
                actor="api",
            )
            self._write_json(HTTPStatus.OK, {"ok": True, "task": _row_to_dict(row)})
        finally:
            store.close()

    def _handle_mark_submitted(self, task_id_raw: str, body: Dict[str, Any]) -> None:
        task_id = _parse_int(task_id_raw, default=0, minimum=1)
        if task_id <= 0:
            raise ValueError("valid task id is required")
        store = self._task_store()
        try:
            row = store.mark_submitted(
                task_id,
                submitted_at=str(body.get("submitted_at") or "").strip(),
                follow_up_days=_parse_int(body.get("follow_up_days"), default=14, minimum=1, maximum=365),
                note=str(body.get("note") or "").strip(),
                actor="api",
            )
            self._write_json(HTTPStatus.OK, {"ok": True, "task": _row_to_dict(row)})
        finally:
            store.close()

    def _handle_latest_brief(self) -> None:
        self._write_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "generated_at": now_utc_iso(),
                "ops_report_path": str(self.server.ops_report_path),
                "submission_report_path": str(self.server.submission_report_path),
                "submission_json_path": str(self.server.submission_json_path),
                "ops_report": _read_text(self.server.ops_report_path),
                "submission_report": _read_text(self.server.submission_report_path),
                "submission_json": _read_text(self.server.submission_json_path),
            },
        )

    def _handle_scan(self, body: Dict[str, Any], *, full: bool, review_targets_only: bool = False) -> None:
        seed_urls = _split_csv(body.get("seed_urls"))
        query_values = _split_csv(body.get("queries") or body.get("query"))
        args = [
            sys.executable,
            str(PROJECT_ROOT / "fundlist.py"),
            "--db",
            str(self.server.db_path),
            "submission-scan",
        ]
        if seed_urls:
            args.extend(["--seed-urls", ",".join(seed_urls)])
        for query in query_values:
            args.extend(["--query", query])
        if review_targets_only or _parse_bool(body.get("review_targets_only"), default=False):
            args.append("--review-targets-only")
        else:
            review_targets = _parse_bool(body.get("review_targets"), default=False)
            if review_targets:
                args.append("--review-targets")
        if _parse_bool(
            body.get("prune_domains"),
            default=(full and not review_targets_only and not _parse_bool(body.get("review_targets"), default=False)),
        ):
            args.append("--prune-domains")
        if _parse_bool(body.get("skip_search"), default=(not full or review_targets_only)):
            args.append("--skip-search")
        if not _parse_bool(body.get("from_fundraise"), default=(not review_targets_only)):
            args.append("--no-fundraise-seeds")
        args.extend(["--review-target-limit", str(_parse_int(body.get("review_target_limit"), default=80, minimum=1, maximum=500))])
        args.extend(["--fundraise-seed-limit", str(_parse_int(body.get("fundraise_seed_limit"), default=300, minimum=0))])
        args.extend(["--max-results-per-query", str(_parse_int(body.get("max_results_per_query"), default=10, minimum=1, maximum=100))])
        args.extend(["--max-sites", str(_parse_int(body.get("max_sites"), default=120 if full else 40, minimum=1, maximum=500))])
        args.extend(["--max-pages-per-site", str(_parse_int(body.get("max_pages_per_site"), default=6 if full else 3, minimum=1, maximum=20))])
        args.extend(["--http-timeout", str(_parse_int(body.get("http_timeout"), default=10, minimum=1, maximum=120))])

        for key in ["query_file", "sector", "stage", "region", "status_filter", "org_type_filter", "json_output", "output"]:
            value = str(body.get(key) or "").strip()
            if value:
                args.extend([f"--{key.replace('_', '-')}", value])
        args.extend(["--report-limit", str(_parse_int(body.get("report_limit"), default=120, minimum=1, maximum=1000))])
        args.extend(["--min-score", str(_parse_int(body.get("min_score"), default=0, minimum=0, maximum=1000))])
        args.extend(["--event-limit", str(_parse_int(body.get("event_limit"), default=30, minimum=0, maximum=1000))])

        proc = subprocess.run(args, capture_output=True, text=True, cwd=str(PROJECT_ROOT))
        payload = {
            "ok": proc.returncode == 0,
            "scan_type": "review_retry" if review_targets_only else ("full" if full else "delta"),
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "command": args[2:],
        }
        self._write_json(HTTPStatus.OK if proc.returncode == 0 else HTTPStatus.BAD_REQUEST, payload)

    def _handle_fallback_scan(self, body: Dict[str, Any]) -> None:
        seed_urls = _split_csv(body.get("seed_urls"))
        args = [
            sys.executable,
            str(PROJECT_ROOT / "fundlist.py"),
            "--db",
            str(self.server.db_path),
            "submission-fallback",
        ]
        if seed_urls:
            args.extend(["--seed-urls", ",".join(seed_urls)])
        for key, default, minimum, maximum in [
            ("limit", 20, 1, 500),
            ("max_results_per_query", 6, 1, 50),
            ("max_candidates", 12, 1, 50),
            ("max_candidate_scan", 4, 1, 20),
            ("max_pages_per_site", 6, 1, 20),
            ("http_timeout", 10, 1, 120),
            ("min_score", 0, 0, 1000),
            ("report_limit", 120, 1, 1000),
            ("event_limit", 30, 0, 1000),
        ]:
            args.extend([f"--{key.replace('_', '-')}", str(_parse_int(body.get(key), default=default, minimum=minimum, maximum=maximum))])
        for key in [
            "ai_provider",
            "model",
            "status_filter",
            "org_type_filter",
            "output",
            "json_output",
            "refresh_submission_report",
            "refresh_submission_json",
        ]:
            value = str(body.get(key) or "").strip()
            if value:
                args.extend([f"--{key.replace('_', '-')}", value])

        proc = subprocess.run(args, capture_output=True, text=True, cwd=str(PROJECT_ROOT))
        payload = {
            "ok": proc.returncode == 0,
            "scan_type": "fallback",
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "command": args[2:],
        }
        self._write_json(HTTPStatus.OK if proc.returncode == 0 else HTTPStatus.BAD_REQUEST, payload)


def api_serve_command(args: argparse.Namespace) -> int:
    host = args.host.strip() or "127.0.0.1"
    port = _parse_int(args.port, default=8787, minimum=1, maximum=65535)
    db_path = Path(args.db).expanduser()
    ops_report_path = Path(args.ops_report).expanduser()
    submission_report_path = Path(args.submission_report).expanduser()
    submission_json_path = Path(args.submission_json).expanduser()
    api_token = str(args.api_token or DEFAULT_API_TOKEN).strip()
    if not api_token and not bool(args.allow_no_auth):
        print("[error] api token required: set FUNDLIST_API_TOKEN or pass --api-token (use --allow-no-auth only for local debugging)", file=sys.stderr)
        return 2

    httpd = FundlistAPIServer(
        (host, port),
        FundlistAPIHandler,
        db_path=db_path,
        ops_report_path=ops_report_path,
        submission_report_path=submission_report_path,
        submission_json_path=submission_json_path,
        api_token=api_token,
    )
    print(f"[fundlist-api] serving on http://{host}:{port} db={db_path}")
    if api_token:
        print("[fundlist-api] bearer auth enabled")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[fundlist-api] stopping")
    finally:
        httpd.server_close()
    return 0
