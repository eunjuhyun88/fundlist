#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
CONTEXT_DIR = ROOT / ".context"
OFFSET_FILE = CONTEXT_DIR / "telegram_offset.txt"
LOG_FILE = CONTEXT_DIR / "telegram_bot.log"
DEFAULT_FUNDRAISE_REPORT = ROOT / "data" / "reports" / "fundraising_report.md"
DEFAULT_OPENCLAW_REPORT = ROOT / "data" / "reports" / "openclaw_multi_agent_report.md"
DEFAULT_VC_OPS_REPORT = ROOT / "data" / "reports" / "vc_ops_report.md"
DEFAULT_SUBMISSION_REPORT = ROOT / "data" / "reports" / "submission_targets_report.md"
DEFAULT_SUBMISSION_JSON = ROOT / "data" / "reports" / "submission_targets.json"
DEFAULT_FALLBACK_REPORT = ROOT / "data" / "reports" / "submission_fallback_report.md"
DEFAULT_FALLBACK_JSON = ROOT / "data" / "reports" / "submission_fallback.json"
DEFAULT_DB = ROOT / "data" / "investment_items.db"
CHAT_HISTORY: Dict[int, Deque[Dict[str, str]]] = {}
FUNDRAISE_CONTEXT_CACHE: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None

NOISE_URL_SNIPPETS = (
    "typeform.com/application-form-builder",
    "typeform.com/explore",
    "airtable.com/solutions/",
    "storage.tally.so/",
    "tally.so/support",
    "refreshmiami.com/submit-an-event",
    "businessjournaldaily.com/submit-your-story",
    "statnews.com/pitch-guidelines",
)

GENERIC_HOSTS = {
    "airtable.com",
    "docs.google.com",
    "forms.gle",
    "tally.so",
    "typeform.com",
    "form.typeform.com",
    "github.com",
}

GENERIC_ORG_NAMES = {
    "airtable",
    "forms",
    "typeform",
    "google forms",
    "google form",
    "tally",
}


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_dirs() -> None:
    CONTEXT_DIR.mkdir(parents=True, exist_ok=True)


def log_line(text: str) -> None:
    ensure_dirs()
    with LOG_FILE.open("a", encoding="utf-8") as fp:
        fp.write(f"[{now_utc_iso()}] {text}\n")


def split_message(text: str, limit: int = 3500) -> List[str]:
    text = text.strip() or "(empty)"
    parts: List[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts


def mask_secrets(text: str) -> str:
    if not text:
        return text
    replacements = [
        os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        os.environ.get("GROQ_API_KEY", ""),
        os.environ.get("GEMINI_API_KEY", ""),
        os.environ.get("HF_TOKEN", ""),
        os.environ.get("HUGGINGFACE_API_KEY", ""),
        os.environ.get("OPENROUTER_API_KEY", ""),
    ]
    out = text
    for token in replacements:
        t = token.strip()
        if t:
            out = out.replace(t, "***")
    return out


class TelegramClient:
    def __init__(self, token: str) -> None:
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"

    def call(self, method: str, payload: Dict[str, Any]) -> Any:
        req = urllib.request.Request(
            f"{self.base_url}/{method}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=70) as resp:
            body = resp.read().decode("utf-8")
        parsed = json.loads(body)
        if not parsed.get("ok"):
            raise RuntimeError(f"telegram api failed: {parsed}")
        return parsed.get("result")

    def send_message(self, chat_id: int, text: str) -> None:
        for chunk in split_message(mask_secrets(text)):
            self.call(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": chunk,
                    "disable_web_page_preview": True,
                },
            )


def load_offset() -> int:
    if OFFSET_FILE.exists():
        raw = OFFSET_FILE.read_text(encoding="utf-8").strip()
        try:
            return int(raw)
        except Exception:  # noqa: BLE001
            return 0
    return 0


def save_offset(offset: int) -> None:
    ensure_dirs()
    OFFSET_FILE.write_text(str(offset), encoding="utf-8")


def parse_allowed_chats() -> set[int]:
    raw = os.environ.get("TELEGRAM_ALLOWED_CHATS", "").strip()
    out: set[int] = set()
    if not raw:
        return out
    for token in raw.split(","):
        item = token.strip()
        if not item:
            continue
        try:
            out.add(int(item))
        except Exception:  # noqa: BLE001
            pass
    return out


def run_local_command(cmd: Sequence[str], timeout_sec: int = 300) -> Tuple[int, str]:
    try:
        proc = subprocess.run(
            list(cmd),
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            timeout=timeout_sec,
        )
        text = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        return proc.returncode, text
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout_sec}s"


def format_command_result(name: str, code: int, output: str, max_lines: int = 60) -> str:
    lines = [ln for ln in output.splitlines() if ln.strip()]
    if len(lines) > max_lines:
        lines = lines[: max_lines // 2] + ["... (truncated) ..."] + lines[-(max_lines // 2) :]
    body = "\n".join(lines) if lines else "(no output)"
    return f"[{name}] exit={code}\n{body}"


def program_slug(value: str) -> str:
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", value.strip().lower())
    return slug.strip("_") or "program"


def read_report(path: Path) -> str:
    if not path.exists():
        return f"report not found: {path}"
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if len(lines) > 120:
        lines = lines[:120]
        lines.append("... (truncated) ...")
    return "\n".join(lines)


def _normalize_org_key(value: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", str(value or "").strip().lower())


def _canonical_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith("mailto:"):
        return raw.lower()
    try:
        parsed = urllib.parse.urlparse(raw)
    except Exception:  # noqa: BLE001
        return raw.strip()
    scheme = (parsed.scheme or "https").lower()
    host = parsed.netloc.lower()
    path = parsed.path or ""
    if host.startswith("www."):
        host = host[4:]
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return urllib.parse.urlunparse((scheme, host, path, "", parsed.query, ""))


def _root_domain(value: str) -> str:
    url = _canonical_url(value)
    if url.startswith("mailto:"):
        return ""
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:  # noqa: BLE001
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _is_noise_submission_url(value: str) -> bool:
    url = _canonical_url(value)
    if not url:
        return True
    lowered = url.lower()
    if any(snippet in lowered for snippet in NOISE_URL_SNIPPETS):
        return True
    if lowered in {"https://forms.gle", "https://forms.gle/"}:
        return True
    try:
        parsed = urllib.parse.urlparse(lowered)
    except Exception:  # noqa: BLE001
        return False
    if parsed.netloc == "forms.gle" and parsed.path in {"", "/"}:
        return True
    if parsed.path.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")):
        return True
    return False


def _extract_portfolio_examples(raw_json: str) -> str:
    raw = str(raw_json or "").strip()
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(payload, dict):
        return ""
    headers = payload.get("headers")
    row = payload.get("row")
    if not isinstance(headers, list) or not isinstance(row, list):
        return ""
    for idx, header in enumerate(headers):
        key = str(header or "").strip().lower()
        if not key:
            continue
        if any(
            token in key
            for token in (
                "portfolio",
                "투자 프로젝트",
                "portfolio companies",
                "investments",
                "대표 포트폴리오",
            )
        ):
            if idx >= len(row):
                continue
            value = _compact_text(str(row[idx] or ""), limit=140)
            if value in {"-", "미지정", "확인된 투자 내역 없음"}:
                return value
            if value:
                return value
    return ""


def _append_unique(target: List[str], value: str, *, limit: int = 4) -> None:
    item = _compact_text(value, limit=160)
    if item == "-" or not item or item in target:
        return
    if len(target) < limit:
        target.append(item)


def _load_fundraise_context() -> Dict[str, Dict[str, Dict[str, Any]]]:
    global FUNDRAISE_CONTEXT_CACHE
    if FUNDRAISE_CONTEXT_CACHE is not None:
        return FUNDRAISE_CONTEXT_CACHE

    cache: Dict[str, Dict[str, Dict[str, Any]]] = {"by_name": {}, "by_domain": {}}
    if not DEFAULT_DB.exists():
        FUNDRAISE_CONTEXT_CACHE = cache
        return cache

    conn = sqlite3.connect(str(DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT org_name, category, website, region, funding, notes, raw_json
            FROM fundraising_records
            ORDER BY imported_at DESC, id DESC
            """
        ).fetchall()
    finally:
        conn.close()

    for row in rows:
        name_key = _normalize_org_key(str(row["org_name"] or ""))
        domain_key = _root_domain(str(row["website"] or ""))
        portfolio_examples = _extract_portfolio_examples(str(row["raw_json"] or ""))

        def upsert(slot_key: str, slot_value: str) -> None:
            if not slot_value:
                return
            bucket = cache[slot_key].setdefault(
                slot_value,
                {
                    "categories": [],
                    "regions": [],
                    "fundings": [],
                    "notes": [],
                    "websites": [],
                    "portfolio_examples": [],
                },
            )
            _append_unique(bucket["categories"], str(row["category"] or ""))
            _append_unique(bucket["regions"], str(row["region"] or ""))
            _append_unique(bucket["fundings"], str(row["funding"] or ""))
            _append_unique(bucket["notes"], str(row["notes"] or ""), limit=6)
            _append_unique(bucket["websites"], str(row["website"] or ""))
            _append_unique(bucket["portfolio_examples"], portfolio_examples)

        upsert("by_name", name_key)
        upsert("by_domain", domain_key)

    FUNDRAISE_CONTEXT_CACHE = cache
    return cache


def _context_for_item(item: Dict[str, Any]) -> Dict[str, Any]:
    cache = _load_fundraise_context()
    name_key = _normalize_org_key(str(item.get("org_name") or ""))
    domain_key = _root_domain(str(item.get("source_url") or "")) or _root_domain(str(item.get("submission_url") or ""))
    merged: Dict[str, List[str]] = {
        "categories": [],
        "regions": [],
        "fundings": [],
        "notes": [],
        "websites": [],
        "portfolio_examples": [],
    }
    candidate_sources = [cache["by_name"].get(name_key)]
    if domain_key and domain_key not in GENERIC_HOSTS:
        candidate_sources.append(cache["by_domain"].get(domain_key))
    for source in candidate_sources:
        if not source:
            continue
        for key, values in source.items():
            for value in values:
                _append_unique(merged[key], value, limit=6)
    return {
        "category": merged["categories"][0] if merged["categories"] else "",
        "categories": merged["categories"],
        "region": merged["regions"][0] if merged["regions"] else "",
        "regions": merged["regions"],
        "funding": merged["fundings"][0] if merged["fundings"] else "",
        "fundings": merged["fundings"],
        "notes": merged["notes"],
        "website": merged["websites"][0] if merged["websites"] else "",
        "portfolio_examples": merged["portfolio_examples"],
    }


def load_submission_payload() -> Dict[str, Any]:
    if not DEFAULT_SUBMISSION_JSON.exists():
        return {}
    try:
        payload = json.loads(DEFAULT_SUBMISSION_JSON.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _local_time_text(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return "-"
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return raw
    local = parsed.astimezone()
    return local.strftime("%Y-%m-%d %H:%M %Z")


def _compact_text(value: str, *, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return "-"
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _deadline_display(item: Dict[str, Any]) -> str:
    deadline_date = str(item.get("deadline_date") or "").strip()
    deadline_text = _compact_text(str(item.get("deadline_text") or ""), limit=90)
    status = str(item.get("status", "")).strip().lower()
    if deadline_date:
        return deadline_date
    if status == "rolling":
        return "rolling / 고정 마감일 미발견"
    if status == "open":
        return "공식 페이지에 명시된 마감일 없음"
    if deadline_text != "-":
        return deadline_text
    if status == "closed":
        return "closed"
    return "미확인"


def _evidence_display(item: Dict[str, Any]) -> str:
    evidence = str(item.get("evidence") or "").strip().lower()
    bits: List[str] = []
    if "direct-form" in evidence:
        bits.append("direct form")
    if "typeform" in evidence:
        bits.append("typeform")
    if "airtable" in evidence:
        bits.append("airtable")
    if "docs.google.com/forms" in evidence or "forms.gle" in evidence:
        bits.append("google form")
    if "tally.so" in evidence:
        bits.append("tally")
    if "html:form" in evidence:
        bits.append("page has form")
    if "phrase:application form" in evidence:
        bits.append("application form wording")
    if "phrase:pitch us" in evidence:
        bits.append("pitch us wording")
    if not bits:
        return _compact_text(evidence, limit=80)
    deduped = list(dict.fromkeys(bits))
    return ", ".join(deduped)


def _parse_iso_datetime(value: str) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def _parse_deadline_date(value: str) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return None


def _days_left_text(item: Dict[str, Any]) -> str:
    deadline = _parse_deadline_date(str(item.get("deadline_date") or ""))
    if deadline is None:
        return "미확인"
    today = datetime.now().astimezone().date()
    remaining = (deadline.date() - today).days
    if remaining > 0:
        return f"D-{remaining}"
    if remaining == 0:
        return "D-Day"
    return f"D+{abs(remaining)}"


def _extract_money_snippet(values: Sequence[str]) -> str:
    for value in values:
        text = _compact_text(value, limit=120)
        if text == "-":
            continue
        if re.search(r"[$€£₩]\s?\d", text) or re.search(r"\b\d+(\.\d+)?\s?(m|bn|k)\b", text, re.IGNORECASE):
            return text
    return "-"


def _derive_focus(item: Dict[str, Any], context: Dict[str, Any]) -> str:
    corpus_parts = [
        str(item.get("org_type") or ""),
        str(item.get("org_name") or ""),
        str(item.get("notes") or ""),
        str(item.get("source_page_snapshot") or ""),
        str(item.get("requirements") or ""),
        " ".join(context.get("categories", [])),
        " ".join(context.get("regions", [])),
        " ".join(context.get("notes", [])),
    ]
    corpus = " ".join(part for part in corpus_parts if part).lower()
    tags: List[str] = []
    if "ai" in corpus or "genai" in corpus or "llm" in corpus:
        tags.append("AI")
    if any(token in corpus for token in ("web3", "crypto", "blockchain", "defi", "onchain", "zk", "l2")):
        tags.append("Web3/Crypto")
    if "fintech" in corpus:
        tags.append("Fintech")
    if any(token in corpus for token in ("infra", "developer", "devtool", "tooling", "platform", "cloud")):
        tags.append("Infra/Devtools")
    if "accelerator" in corpus:
        tags.append("Accelerator")
    if "grant" in corpus:
        tags.append("Grant")
    if str(item.get("org_type") or "").strip() == "VC":
        tags.append("VC")
    for region_value in context.get("regions", []):
        region_token = str(region_value or "").strip()
        region_lower = region_token.lower()
        if any(token in region_lower for token in ("web2", "web3", "crypto", "ai", "fintech", "infra", "consumer", "enterprise")) and region_token not in tags and len(tags) < 4:
            tags.append(region_token)
    if not tags and context.get("category"):
        tags.append(_compact_text(str(context["category"]), limit=40))
    if not tags:
        tags.append(str(item.get("org_type") or "미확인"))
    deduped = list(dict.fromkeys(tag for tag in tags if tag and tag != "-"))
    return ", ".join(deduped[:4]) if deduped else "미확인"


def _portfolio_display(item: Dict[str, Any], context: Dict[str, Any]) -> str:
    values = list(context.get("portfolio_examples", []))
    note_candidates = list(context.get("notes", []))
    for text in note_candidates:
        compact = _compact_text(text, limit=140)
        if compact in {"-", ""}:
            continue
        if "확인된 투자 내역 없음" in compact:
            return "확인된 투자 내역 없음"
        lowered = compact.lower()
        if "deadline:" in lowered or "title:" in lowered:
            continue
        if any(token in lowered for token in ("portfolio", "invested", "투자")):
            values.append(compact)
    for value in values:
        compact = _compact_text(value, limit=140)
        if compact and compact != "-":
            return compact
    return "미확인"


def _is_stale_action_target(item: Dict[str, Any]) -> bool:
    deadline = _parse_deadline_date(str(item.get("deadline_date") or ""))
    if deadline is not None:
        if (deadline.date() - datetime.now().astimezone().date()).days < -30:
            return True
    evidence = str(item.get("evidence") or "").lower()
    primary_url = _canonical_url(str(item.get("submission_url") or item.get("source_url") or ""))
    if not primary_url:
        return True
    lowered = primary_url.lower()
    if any(token in lowered for token in ("/blog/", "/articles/", "/news/", "/newsroom/")) and not any(
        token in evidence for token in ("direct-form", "typeform", "airtable", "docs.google.com/forms", "tally.so", "phrase:application form", "phrase:pitch us")
    ):
        return True
    return False


def _status_rank(value: str) -> int:
    lowered = str(value or "").strip().lower()
    return {
        "deadline": 4,
        "open": 3,
        "rolling": 2,
        "closed": 1,
    }.get(lowered, 0)


def _normalize_submission_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    normalized = dict(item)
    org_name = str(item.get("org_name") or "").strip()
    if org_name.lower() in GENERIC_ORG_NAMES:
        return None
    submit_url = _canonical_url(str(item.get("submission_url") or ""))
    source_url = _canonical_url(str(item.get("source_url") or ""))
    primary_url = submit_url or source_url
    if _is_noise_submission_url(primary_url):
        return None

    status = str(item.get("status") or "").strip().lower()
    if submit_url.endswith("/closedform") or source_url.endswith("/closedform"):
        status = "closed"

    context = _context_for_item(item)
    checked_at = _parse_iso_datetime(str(item.get("last_checked_at") or ""))
    deadline_date = str(item.get("deadline_date") or "").strip()
    deadline_display = _deadline_display({**item, "status": status, "deadline_date": deadline_date})
    money_display = _extract_money_snippet(
        [
            str(context.get("funding") or ""),
            str(item.get("notes") or ""),
            str(item.get("source_page_snapshot") or ""),
        ]
    )

    normalized.update(
        {
            "status": status,
            "submission_url": submit_url or str(item.get("submission_url") or ""),
            "source_url": source_url or str(item.get("source_url") or ""),
            "checked_at_display": _local_time_text(str(item.get("last_checked_at") or "")),
            "checked_at_dt": checked_at,
            "deadline_display": deadline_display,
            "days_left_display": _days_left_text(item),
            "focus_display": _derive_focus(item, context),
            "capital_display": money_display,
            "portfolio_display": _portfolio_display(item, context),
            "region_display": _compact_text(str(context.get("region") or ""), limit=60),
            "context_category": _compact_text(str(context.get("category") or ""), limit=60),
            "evidence_display": _evidence_display(item),
            "requirements_display": _compact_text(str(item.get("requirements") or ""), limit=120),
            "org_type_display": _compact_text(str(item.get("org_type") or context.get("category") or "미확인"), limit=40),
        }
    )
    return normalized


def load_submission_items() -> List[Dict[str, Any]]:
    payload = load_submission_payload()
    items = payload.get("items", [])
    if not isinstance(items, list):
        return []

    deduped: Dict[str, Dict[str, Any]] = {}
    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        item = _normalize_submission_item(raw_item)
        if not item:
            continue
        dedupe_key = _canonical_url(str(item.get("submission_url") or "")) or _normalize_org_key(str(item.get("org_name") or ""))
        if not dedupe_key:
            continue
        existing = deduped.get(dedupe_key)
        if existing is None:
            deduped[dedupe_key] = item
            continue
        existing_checked = existing.get("checked_at_dt") or datetime.min.replace(tzinfo=timezone.utc)
        item_checked = item.get("checked_at_dt") or datetime.min.replace(tzinfo=timezone.utc)
        existing_key = (_status_rank(existing.get("status")), int(existing.get("score", 0) or 0), existing_checked)
        candidate_key = (_status_rank(item.get("status")), int(item.get("score", 0) or 0), item_checked)
        if candidate_key > existing_key:
            deduped[dedupe_key] = item
    return list(deduped.values())


def _submission_sort_key(item: Dict[str, Any]) -> Tuple[int, str, int, str]:
    status = str(item.get("status", "")).strip().lower()
    deadline = str(item.get("deadline_date") or "").strip()
    score = int(item.get("score", 0) or 0)
    if status == "deadline":
        return (0, deadline or "9999-12-31", -score, str(item.get("org_name", "")).lower())
    if status in {"open", "rolling"}:
        return (1, deadline or "9999-12-31", -score, str(item.get("org_name", "")).lower())
    if status == "closed":
        return (3, deadline or "9999-12-31", -score, str(item.get("org_name", "")).lower())
    return (2, deadline or "9999-12-31", -score, str(item.get("org_name", "")).lower())


def format_submission_subset(title: str, statuses: Sequence[str], *, limit: int = 12) -> str:
    wanted = {s.strip().lower() for s in statuses if s.strip()}
    items = [item for item in load_submission_items() if str(item.get("status", "")).strip().lower() in wanted]
    if wanted & {"open", "rolling", "deadline"}:
        items = [item for item in items if not _is_stale_action_target(item)]
    if not items:
        return f"[{title}]\n- none"

    payload = load_submission_payload()
    generated_at = _local_time_text(str(payload.get("generated_at") or ""))
    ordered = sorted(items, key=_submission_sort_key)[:limit]
    lines = [f"[{title}]", f"- generated: {generated_at}", f"- count: {len(items)}", ""]
    for idx, item in enumerate(ordered, start=1):
        org = str(item.get("org_name", "-")).strip()
        status = str(item.get("status", "-")).strip()
        score = int(item.get("score", 0) or 0)
        submission_type = str(item.get("submission_type", "-")).strip() or "-"
        org_type = str(item.get("org_type_display", "-")).strip() or "-"
        requirements = str(item.get("requirements_display", "-")).strip() or "-"
        checked_at = str(item.get("checked_at_display", "-")).strip() or "-"
        evidence = str(item.get("evidence_display", "-")).strip() or "-"
        official_page = str(item.get("source_url", "-")).strip() or "-"
        submit_url = str(item.get("submission_url", "-")).strip() or "-"
        deadline = str(item.get("deadline_display", "미확인")).strip() or "미확인"
        days_left = str(item.get("days_left_display", "미확인")).strip() or "미확인"
        focus = str(item.get("focus_display", "미확인")).strip() or "미확인"
        capital = str(item.get("capital_display", "미확인")).strip() or "미확인"
        portfolio = str(item.get("portfolio_display", "미확인")).strip() or "미확인"
        region = str(item.get("region_display", "미확인")).strip() or "미확인"
        lines.append(f"{idx}. {org}")
        lines.append(f"   status: {status} | checked: {checked_at}")
        lines.append(f"   deadline: {deadline} | days_left: {days_left}")
        lines.append(f"   focus: {focus} | org_type: {org_type}")
        lines.append(f"   capital/fund_size: {capital} | region/track: {region}")
        lines.append(f"   submission: {submission_type} | score: {score}")
        lines.append(f"   portfolio/examples: {portfolio}")
        lines.append(f"   requirements: {requirements}")
        lines.append(f"   evidence: {evidence}")
        lines.append(f"   official: {official_page}")
        lines.append(f"   apply: {submit_url}")
    return "\n".join(lines)


def _parse_json_output(output: str) -> Optional[Dict[str, Any]]:
    raw = str(output or "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except Exception:  # noqa: BLE001
        return None
    return payload if isinstance(payload, dict) else None


def format_submission_scan_summary(title: str, code: int, output: str, *, full_sweep: bool) -> str:
    payload = _parse_json_output(output)
    if code != 0 or payload is None:
        return format_command_result(title, code, output, max_lines=40)

    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    artifacts = payload.get("artifacts", {}) if isinstance(payload.get("artifacts"), dict) else {}
    lines = [
        f"[{title.upper()}]",
        f"- searched_at: {_local_time_text(str(payload.get('generated_at') or ''))}",
        f"- mode: {'full sweep' if full_sweep else 'targeted scan'}",
        f"- seeds(raw/deduped): {summary.get('raw_seed_count', 0)}/{summary.get('deduped_seed_count', 0)}",
        f"- scanned: {summary.get('scanned', 0)} | found: {summary.get('found', 0)} | changed: {summary.get('changed', 0)}",
        f"- failures: {summary.get('failures', 0)} | resolved_failures: {summary.get('resolved_failures', 0)}",
        f"- report_json: {artifacts.get('json_output_path') or '-'}",
    ]
    return "\n".join(lines)


def format_submission_scan_digest(title: str, code: int, output: str, *, full_sweep: bool) -> str:
    sections = [format_submission_scan_summary(title, code, output, full_sweep=full_sweep)]
    if code == 0:
        sections.append("")
        sections.append(format_submission_subset("TOP DEADLINES", ["deadline"], limit=8))
        sections.append("")
        sections.append(format_submission_subset("TOP OPEN / ROLLING", ["open", "rolling"], limit=8))
    return "\n".join(sections)


def parse_bool_env(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() not in {"0", "false", "no", "off", ""}


def get_hf_token() -> str:
    return (
        os.environ.get("HF_TOKEN", "").strip()
        or os.environ.get("HUGGINGFACE_API_KEY", "").strip()
        or os.environ.get("HUGGINGFACEHUB_API_TOKEN", "").strip()
    )


def get_openrouter_key() -> str:
    return os.environ.get("OPENROUTER_API_KEY", "").strip()


def strip_bot_mention(text: str, bot_username: str) -> str:
    if not bot_username:
        return text.strip()
    lowered = text.lower()
    tag = f"@{bot_username.lower()}"
    if tag not in lowered:
        return text.strip()
    idx = lowered.find(tag)
    out = (text[:idx] + text[idx + len(tag) :]).strip()
    return out


def choose_chat_provider() -> Tuple[str, str]:
    provider = os.environ.get("TELEGRAM_CHAT_AI_PROVIDER", "auto").strip().lower()
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    hf_key = get_hf_token()
    openrouter_key = get_openrouter_key()
    if provider == "groq":
        return "groq", os.environ.get("TELEGRAM_CHAT_GROQ_MODEL", os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"))
    if provider == "gemini":
        return "gemini", os.environ.get("TELEGRAM_CHAT_GEMINI_MODEL", os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"))
    if provider == "huggingface":
        return "huggingface", os.environ.get(
            "TELEGRAM_CHAT_HUGGINGFACE_MODEL",
            os.environ.get("HUGGINGFACE_MODEL", "Qwen/Qwen2.5-7B-Instruct"),
        )
    if provider == "openrouter":
        return "openrouter", os.environ.get(
            "TELEGRAM_CHAT_OPENROUTER_MODEL",
            os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
        )
    if groq_key:
        return "groq", os.environ.get("TELEGRAM_CHAT_GROQ_MODEL", os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"))
    if gemini_key:
        return "gemini", os.environ.get("TELEGRAM_CHAT_GEMINI_MODEL", os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"))
    if openrouter_key:
        return "openrouter", os.environ.get(
            "TELEGRAM_CHAT_OPENROUTER_MODEL",
            os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
        )
    if hf_key:
        return "huggingface", os.environ.get(
            "TELEGRAM_CHAT_HUGGINGFACE_MODEL",
            os.environ.get("HUGGINGFACE_MODEL", "Qwen/Qwen2.5-7B-Instruct"),
        )
    return "", ""


def build_chat_messages(chat_id: int, user_text: str, chat_type: str, bot_username: str) -> List[Dict[str, str]]:
    max_turns = int(os.environ.get("TELEGRAM_CHAT_HISTORY_TURNS", "6"))
    history = CHAT_HISTORY.setdefault(chat_id, deque(maxlen=max_turns * 2))
    system_prompt = os.environ.get(
        "TELEGRAM_CHAT_SYSTEM_PROMPT",
        (
            "너는 VC/fundraising 리서치 보조 에이전트다. "
            "한국어로 짧고 실행 가능한 답변을 준다. "
            "모르는 사실은 추정하지 말고 필요한 확인 항목을 1~3개로 제시한다."
        ),
    ).strip()

    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for item in history:
        role = item.get("role", "")
        content = item.get("content", "").strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    context_header = f"[chat_type={chat_type} bot={bot_username or '-'}]"
    messages.append({"role": "user", "content": f"{context_header}\n{user_text.strip()}"})
    return messages


def call_groq_chat(messages: Sequence[Dict[str, str]], model: str) -> str:
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        return "GROQ_API_KEY가 없어 대화 응답을 생성할 수 없습니다."

    endpoint = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1/chat/completions")
    payload = {
        "model": model,
        "temperature": 0.35,
        "messages": list(messages),
    }

    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=80) as resp:
            body = resp.read().decode("utf-8")
        parsed = json.loads(body)
        text = (
            parsed.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "응답이 비어 있습니다.")
        )
        return str(text).strip() or "응답이 비어 있습니다."
    except Exception as exc:  # noqa: BLE001
        return f"대화 응답 실패(groq): {exc}"


def call_gemini_chat(messages: Sequence[Dict[str, str]], model: str) -> str:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return "GEMINI_API_KEY가 없어 대화 응답을 생성할 수 없습니다."

    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    merged = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "").strip()
        if not content:
            continue
        merged.append(f"{role}: {content}")
    prompt = "\n\n".join(merged)
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": {"temperature": 0.35},
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=80) as resp:
                body = resp.read().decode("utf-8")
            parsed = json.loads(body)
            candidates = parsed.get("candidates", [])
            if not candidates:
                return f"대화 응답 실패(gemini): {parsed}"
            content = candidates[0].get("content", {})
            parts = content.get("parts", []) if isinstance(content, dict) else []
            if parts and isinstance(parts[0], dict) and parts[0].get("text"):
                return str(parts[0]["text"]).strip()
            return "응답이 비어 있습니다."
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 2:
                time.sleep(1.2 * (attempt + 1))
                continue
            return f"대화 응답 실패(gemini): HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001
            return f"대화 응답 실패(gemini): {exc}"
    return "대화 응답 실패(gemini): rate limited"


def call_huggingface_chat(messages: Sequence[Dict[str, str]], model: str) -> str:
    api_key = get_hf_token()
    if not api_key:
        return "HF_TOKEN (or HUGGINGFACE_API_KEY)이 없어 대화 응답을 생성할 수 없습니다."

    endpoint = os.environ.get("HUGGINGFACE_BASE_URL", "https://router.huggingface.co/v1/chat/completions")
    router_payload = {
        "model": model,
        "temperature": 0.35,
        "messages": list(messages),
    }
    try:
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(router_payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=80) as resp:
            body = resp.read().decode("utf-8")
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            choices = parsed.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                content = choices[0].get("message", {}).get("content")
                if content:
                    return str(content).strip()
            if parsed.get("generated_text"):
                return str(parsed["generated_text"]).strip()
            if parsed.get("error"):
                return f"대화 응답 실패(huggingface): {parsed.get('error')}"
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            if parsed[0].get("generated_text"):
                return str(parsed[0]["generated_text"]).strip()
        return "응답이 비어 있습니다."
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            raw = exc.read().decode("utf-8", errors="ignore")
            detail = " ".join(raw.split())[:220]
        except Exception:  # noqa: BLE001
            pass
        return f"대화 응답 실패(huggingface): HTTP {exc.code}{' - ' + detail if detail else ''}"
    except Exception as exc:  # noqa: BLE001
        return f"대화 응답 실패(huggingface): {exc}"


def call_openrouter_chat(messages: Sequence[Dict[str, str]], model: str) -> str:
    api_key = get_openrouter_key()
    if not api_key:
        return "OPENROUTER_API_KEY가 없어 대화 응답을 생성할 수 없습니다."

    endpoint = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions")
    payload = {
        "model": model,
        "temperature": 0.35,
        "messages": list(messages),
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.environ.get("OPENROUTER_HTTP_REFERER", "https://local.fundlist"),
        "X-Title": os.environ.get("OPENROUTER_APP_TITLE", "fundlist"),
    }

    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=80) as resp:
            body = resp.read().decode("utf-8")
        parsed = json.loads(body)
        text = (
            parsed.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "응답이 비어 있습니다.")
        )
        return str(text).strip() or "응답이 비어 있습니다."
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            raw = exc.read().decode("utf-8", errors="ignore")
            detail = " ".join(raw.split())[:220]
        except Exception:  # noqa: BLE001
            pass
        return f"대화 응답 실패(openrouter): HTTP {exc.code}{' - ' + detail if detail else ''}"
    except Exception as exc:  # noqa: BLE001
        return f"대화 응답 실패(openrouter): {exc}"


def chat_provider_has_key(provider: str) -> bool:
    if provider == "groq":
        return bool(os.environ.get("GROQ_API_KEY", "").strip())
    if provider == "gemini":
        return bool(os.environ.get("GEMINI_API_KEY", "").strip())
    if provider == "huggingface":
        return bool(get_hf_token())
    if provider == "openrouter":
        return bool(get_openrouter_key())
    return False


def model_for_provider(provider: str) -> str:
    if provider == "groq":
        return os.environ.get("TELEGRAM_CHAT_GROQ_MODEL", os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"))
    if provider == "gemini":
        return os.environ.get("TELEGRAM_CHAT_GEMINI_MODEL", os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"))
    if provider == "huggingface":
        return os.environ.get(
            "TELEGRAM_CHAT_HUGGINGFACE_MODEL",
            os.environ.get("HUGGINGFACE_MODEL", "Qwen/Qwen2.5-7B-Instruct"),
        )
    if provider == "openrouter":
        return os.environ.get(
            "TELEGRAM_CHAT_OPENROUTER_MODEL",
            os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
        )
    return ""


def call_chat_provider(provider: str, messages: Sequence[Dict[str, str]], model: str) -> str:
    if provider == "gemini":
        return call_gemini_chat(messages, model=model)
    if provider == "huggingface":
        return call_huggingface_chat(messages, model=model)
    if provider == "openrouter":
        return call_openrouter_chat(messages, model=model)
    return call_groq_chat(messages, model=model)


def is_chat_error(provider: str, reply: str) -> bool:
    prefix = f"대화 응답 실패({provider})"
    return str(reply).strip().startswith(prefix)


def is_gemini_rate_limited(reply: str) -> bool:
    txt = str(reply or "")
    return is_chat_error("gemini", txt) and ("HTTP 429" in txt or "rate limited" in txt.lower())


def answer_chat(
    chat_id: int,
    user_text: str,
    chat_type: str,
    bot_username: str,
) -> str:
    provider, model = choose_chat_provider()
    if not provider:
        return "대화형 응답을 위해 `GROQ_API_KEY` 또는 `GEMINI_API_KEY` 또는 `HF_TOKEN` 또는 `OPENROUTER_API_KEY`를 설정해 주세요."

    messages = build_chat_messages(
        chat_id=chat_id,
        user_text=user_text,
        chat_type=chat_type,
        bot_username=bot_username,
    )
    primary_provider = provider
    primary_model = model or model_for_provider(primary_provider)
    reply = call_chat_provider(primary_provider, messages, model=primary_model)
    used_provider = primary_provider
    used_model = primary_model

    if primary_provider == "gemini" and is_gemini_rate_limited(reply):
        fallback_order = ("openrouter", "huggingface", "groq")
        for fallback_provider in fallback_order:
            if not chat_provider_has_key(fallback_provider):
                continue
            fallback_model = model_for_provider(fallback_provider)
            fallback_reply = call_chat_provider(fallback_provider, messages, model=fallback_model)
            if is_chat_error(fallback_provider, fallback_reply):
                log_line(
                    f"chat-fallback failed chat_id={chat_id} from=gemini to={fallback_provider} "
                    f"model={fallback_model} err={mask_secrets(fallback_reply)[:240]}"
                )
                continue
            reply = fallback_reply
            used_provider = fallback_provider
            used_model = fallback_model
            log_line(
                f"chat-fallback success chat_id={chat_id} from=gemini to={fallback_provider} "
                f"model={fallback_model}"
            )
            break

    history = CHAT_HISTORY.setdefault(chat_id, deque(maxlen=int(os.environ.get("TELEGRAM_CHAT_HISTORY_TURNS", "6")) * 2))
    history.append({"role": "user", "content": user_text.strip()[:2000]})
    history.append({"role": "assistant", "content": reply.strip()[:3000]})
    log_line(f"chat-reply chat_id={chat_id} provider={used_provider} model={used_model}")
    return reply


def help_hint(bot_username: str, chat_type: str, require_mention_in_group: bool) -> str:
    if require_mention_in_group and chat_type in {"group", "supergroup"} and bot_username:
        return (
            f"이 그룹에서는 봇명을 붙여주세요.\n"
            f"예: /help@{bot_username}\n"
            f"또는 @{bot_username} 이번주 VC 리드 5개 뽑아줘"
        )
    return "명령은 /help, /commands, /quickstart 중 하나를 쓰면 됩니다."


def parse_review_set_args(raw: str) -> Tuple[str, Dict[str, str], str]:
    text = str(raw or "").strip()
    if not text:
        return "", {}, "usage: /review_set target:<fingerprint> key=value ..."
    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        return "", {}, f"invalid arguments: {exc}"
    if not tokens:
        return "", {}, "usage: /review_set target:<fingerprint> key=value ..."

    ref = tokens[0].strip()
    if not ref:
        return "", {}, "target ref is required"
    fields: Dict[str, str] = {}
    allowed = {
        "org_name",
        "org_type",
        "source_url",
        "submission_url",
        "submission_type",
        "status",
        "requirements",
        "notes",
        "evidence",
        "deadline_text",
        "deadline_date",
        "score",
    }
    for token in tokens[1:]:
        if "=" not in token:
            return "", {}, f"invalid token: {token} (expected key=value)"
        key, value = token.split("=", 1)
        key = key.strip().lower()
        if key not in allowed:
            return "", {}, f"unsupported field: {key}"
        value = value.strip()
        if value.lower() in {"clear", "none", "null"}:
            value = ""
        fields[key] = value
    if not fields:
        return "", {}, "at least one field is required"
    return ref, fields, ""


def build_quickstart_text() -> str:
    return "\n".join(
        [
            "[quickstart]",
            "1. 전체 검증 돌리기",
            "/submission_scan full",
            "",
            "2. 지금 바로 낼 수 있는 것 보기",
            "/apply_open 10",
            "",
            "3. 마감 임박 보기",
            "/apply_deadline 10",
            "",
            "4. 관심 항목 task 만들기",
            "/task_create alliance dao",
            "",
            "5. 제출 직전으로 올리기",
            "/task_ready <task-id>",
            "",
            "6. 제출 완료 처리",
            "/task_submitted <task-id> submitted manually",
            "",
            "7. 최근 변경 보기",
            "/changes_today 20",
            "",
            "8. 실패한 스캔만 다시 돌리기",
            "/scan_failures 20",
            "/retry_failed 50",
            "",
            "9. unknown target 다시 검증하기",
            "/retry_unknown 20",
            "",
            "10. AI fallback으로 실패 복구하기",
            "/retry_failed_ai 20",
            "",
            "11. 불확실 항목 검토하기",
            "/review_queue 20",
            "/review_set target:<fingerprint> status=closed",
            "",
            "더 자세한 설명:",
            "/help ops",
            "/help apply",
            "/help tasks",
            "/help review",
        ]
    )


def build_help_text(
    bot_username: str,
    chat_type: str,
    require_mention_in_group: bool,
    topic: str = "",
) -> str:
    prefix = ""
    if require_mention_in_group and chat_type in {"group", "supergroup"} and bot_username:
        prefix = f"그룹에서는 `@{bot_username}`를 붙여 쓰세요.\n예: `/help@{bot_username} ops`\n\n"

    topic_key = topic.strip().lower()
    if topic_key in {"ops", "daily", "report"}:
        return (
            prefix
            + "\n".join(
                [
                    "[help: ops]",
                    "/ops_sync",
                    "  VC 리스트 import + 우선순위 큐 갱신",
                    "/ops_daily [morning|evening]",
                    "  daily digest 생성/전송",
                    "/ops_report",
                    "  전체 ops 리포트 생성",
                    "/ops_list [days]",
                    "  기본 큐 조회",
                    "/ops_today",
                    "  오늘 처리할 것",
                    "/ops_week",
                    "  이번 주 처리할 것",
                    "/ops_speedrun",
                    "  speedrun/cohort 후보 리포트",
                    "/ops_program <keyword>",
                    "  특정 프로그램 dossier",
                    "/ops_push [morning|evening]",
                    "  현재 채팅으로 digest 푸시",
                ]
            )
        )
    if topic_key in {"apply", "submission"}:
        return (
            prefix
            + "\n".join(
                [
                    "[help: apply]",
                    "/submission_scan [query|full]",
                    "  공식 페이지/제출 링크 검증",
                    "/submission_list [limit]",
                    "  검증된 제출 타깃 목록",
                    "/submission_report",
                    "  submission markdown report 생성",
                    "/submission_export",
                    "  submission JSON export 생성",
                    "/scan_failures [limit]",
                    "  unresolved scan failure 목록",
                    "/retry_failed [limit]",
                    "  실패한 seed만 재시도",
                    "/retry_unknown [limit]",
                    "  unknown/불확실 target만 재검증",
                    "/retry_failed_ai [limit]",
                    "  검색 + AI 후보선택으로 실패 seed 복구 시도",
                    "/review_queue [limit]",
                    "  실패 항목 + 불확실 target 검토 큐",
                    "/review_resolve <failure:id>",
                    "  failure 항목을 수동 resolved 처리",
                    "/review_ignore <failure:id>",
                    "  failure 항목을 ignore 처리",
                    "/review_set <target:fingerprint> key=value ...",
                    "  target 항목 status/url/deadline 수동 보정",
                    "/apply_open [limit]",
                    "  지금 제출 가능한 항목",
                    "/apply_deadline [limit]",
                    "  마감일 있는 항목",
                    "/apply_closed [limit]",
                    "  닫힌 항목",
                    "",
                    "예:",
                    "/submission_scan full",
                    "/apply_open 10",
                ]
            )
        )
    if topic_key in {"task", "tasks"}:
        return (
            prefix
            + "\n".join(
                [
                    "[help: tasks]",
                    "/task_create <query>",
                    "  verified opportunity를 task로 생성",
                    "/task_view <task-id>",
                    "  단일 task 상세 조회",
                    "/task_ready <task-id>",
                    "  ready_to_submit 전환",
                    "/task_submitted <task-id> [note]",
                    "  submitted 처리 + follow-up date 생성",
                    "/tasks_ready [limit]",
                    "  ready_to_submit 목록",
                    "/tasks_followup [limit]",
                    "  follow_up_due 목록",
                    "",
                    "예:",
                    "/task_create alliance dao",
                    "/task_ready 12",
                    "/task_submitted 12 submitted manually",
                ]
            )
        )
    if topic_key in {"changes", "change"}:
        return (
            prefix
            + "\n".join(
                [
                    "[help: changes]",
                    "/changes_today [limit]",
                    "  최근 24시간 변경",
                    "/changes_recent [days]",
                    "  최근 N일 변경",
                    "",
                    "변경 타입:",
                    "- new_opportunity",
                    "- status_changed",
                    "- deadline_changed",
                    "- submission_url_changed",
                    "- reopened",
                ]
            )
        )
    if topic_key in {"review", "triage", "qa"}:
        return (
            prefix
            + "\n".join(
                [
                    "[help: review]",
                    "검수 루틴:",
                    "1. /changes_today 20",
                    "   최근 상태/링크 변경 확인",
                    "2. /apply_open 20",
                    "   실제 신청 가능한 항목 확인",
                    "3. /apply_deadline 20",
                    "   마감일 있는 항목 확인",
                    "4. /scan_failures 20",
                    "   중단/예외 난 seed 확인",
                    "5. /retry_failed 50",
                    "   실패 seed만 다시 스캔",
                    "6. /retry_unknown 20",
                    "   unknown/deadline-missing target 재검증",
                    "7. /retry_failed_ai 20",
                    "   검색 + AI fallback으로 대체 링크 복구",
                    "8. /review_queue 20",
                    "   실패 + unknown 항목 검토",
                    "9. /review_set target:<fingerprint> status=deadline deadline_date=2026-03-25 submission_url=https://alliance.xyz/apply",
                    "   target 항목 수동 보정",
                    "10. /review_ignore failure:123",
                    "   잘못된 failure 항목 숨기기",
                    "11. 이상한 항목은 공식 링크 직접 열어 확인",
                    "12. 맞는 항목은 /task_create <query>",
                    "",
                    "이상 징후 예:",
                    "- closed 인데 open 으로 보임",
                    "- submission_url 이 공식 폼이 아님",
                    "- deadline 이 비었거나 이상함",
                    "- 같은 프로그램이 중복됨",
                    "",
                    "관련 명령:",
                    "/changes_recent 7",
                    "/review_queue 20",
                    "/retry_unknown 20",
                    "/review_set target:<fingerprint> status=open",
                    "/review_ignore failure:123",
                    "/submission_list 30",
                    "/task_view <task-id>",
                ]
            )
        )
    if topic_key in {"context", "memory"}:
        return (
            prefix
            + "\n".join(
                [
                    "[help: context]",
                    "/context_save <summary>",
                    "/context_compact",
                    "/context_restore",
                ]
            )
        )

    return (
        prefix
        + "\n".join(
            [
                "[fundlist help]",
                "",
                "기본:",
                "/status",
                "/help <ops|apply|tasks|changes|review|context>",
                "/commands",
                "/quickstart",
                "",
                "VC ops:",
                "/ops_sync",
                "/ops_daily",
                "/ops_today",
                "/ops_week",
                "/ops_program <keyword>",
                "",
                "submission/apply:",
                "/submission_scan [full|query]",
                "/scan_failures [limit]",
                "/retry_failed [limit]",
                "/retry_unknown [limit]",
                "/retry_failed_ai [limit]",
                "/review_queue [limit]",
                "/review_resolve <failure:id>",
                "/review_ignore <failure:id>",
                "/review_set <target:fingerprint> key=value ...",
                "/apply_open [limit]",
                "/apply_deadline [limit]",
                "/apply_closed [limit]",
                "/changes_today [limit]",
                "",
                "task management:",
                "/task_create <query>",
                "/task_view <task-id>",
                "/task_ready <task-id>",
                "/task_submitted <task-id> [note]",
                "/tasks_ready [limit]",
                "/tasks_followup [limit]",
                "",
                "예:",
                "/help ops",
                "/help apply",
                "/help review",
                "/quickstart",
                "/submission_scan full",
                "/task_create alliance dao",
            ]
        )
    )


def handle_command(
    client: TelegramClient,
    chat_id: int,
    text: str,
    bot_username: str,
    chat_type: str,
    require_mention_in_group: bool,
) -> None:
    raw = text.strip()
    parts = raw.split(maxsplit=1)
    raw_cmd = parts[0].strip()
    command_part = raw_cmd
    target_username = ""
    if "@" in raw_cmd:
        command_part, target_username = raw_cmd.split("@", 1)
        if bot_username and target_username.strip().lower() != bot_username.strip().lower():
            return
    elif require_mention_in_group and chat_type in {"group", "supergroup"}:
        # Avoid command collisions when multiple bots are in one group.
        client.send_message(chat_id, help_hint(bot_username, chat_type, require_mention_in_group))
        return

    cmd = command_part.lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    py = sys.executable or "/usr/bin/python3"
    fundlist = [py, str(ROOT / "fundlist.py")]
    context_ctl = [py, str(ROOT / "scripts" / "context_ctl.py")]

    if cmd in {"/start", "/help", "/commands"}:
        client.send_message(
            chat_id,
            build_help_text(
                bot_username=bot_username,
                chat_type=chat_type,
                require_mention_in_group=require_mention_in_group,
                topic=arg,
            ),
        )
        return

    if cmd == "/quickstart":
        client.send_message(chat_id, build_quickstart_text())
        return

    if cmd == "/status":
        rows_cmd = fundlist + ["list", "--limit", "1"]
        code, out = run_local_command(rows_cmd, timeout_sec=60)
        msg = [
            f"status at {now_utc_iso()}",
            f"fundraise report: {'ok' if DEFAULT_FUNDRAISE_REPORT.exists() else 'missing'}",
            f"openclaw report: {'ok' if DEFAULT_OPENCLAW_REPORT.exists() else 'missing'}",
            format_command_result("latest-market", code, out, max_lines=8),
        ]
        client.send_message(chat_id, "\n\n".join(msg))
        return

    if cmd == "/fundraise":
        report = str(DEFAULT_FUNDRAISE_REPORT)
        run_cmd = fundlist + ["fundraise-run", "--output", report]
        code, out = run_local_command(run_cmd, timeout_sec=600)
        client.send_message(chat_id, format_command_result("fundraise-run", code, out))
        return

    if cmd == "/fundraise_ai":
        provider = (arg or "groq").strip().lower()
        if provider not in {"groq", "gemini", "huggingface", "openrouter"}:
            client.send_message(chat_id, "usage: /fundraise_ai [groq|gemini|huggingface|openrouter]")
            return
        model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
        if provider == "gemini":
            model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
        elif provider == "huggingface":
            model = os.environ.get("HUGGINGFACE_MODEL", "Qwen/Qwen2.5-7B-Instruct")
        elif provider == "openrouter":
            model = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
        report = str(ROOT / "data" / "reports" / f"fundraising_report_{provider}.md")
        run_cmd = fundlist + [
            "fundraise-run",
            "--with-ai",
            "--ai-provider",
            provider,
            "--model",
            model,
            "--output",
            report,
        ]
        code, out = run_local_command(run_cmd, timeout_sec=900)
        client.send_message(chat_id, format_command_result(f"fundraise-run-{provider}", code, out))
        return

    if cmd == "/report":
        target = (arg or "fundraise").strip().lower()
        if target == "openclaw":
            path = DEFAULT_OPENCLAW_REPORT
        else:
            path = DEFAULT_FUNDRAISE_REPORT
        client.send_message(chat_id, read_report(path))
        return

    if cmd == "/openclaw_dry":
        if not arg:
            client.send_message(chat_id, "usage: /openclaw_dry <query>")
            return
        run_cmd = fundlist + [
            "openclaw-multi",
            "--query",
            arg,
            "--dry-run",
            "--max-agents",
            os.environ.get("OPENCLAW_BOT_MAX_AGENTS", "3"),
            "--acp-dir",
            os.environ.get("OPENCLAW_ACP_DIR", ""),
            "--acp-cmd",
            os.environ.get("OPENCLAW_ACP_CMD", ""),
        ]
        code, out = run_local_command(run_cmd, timeout_sec=240)
        client.send_message(chat_id, format_command_result("openclaw-dry", code, out))
        return

    if cmd == "/openclaw_run":
        if not arg:
            client.send_message(chat_id, "usage: /openclaw_run <query>")
            return
        run_cmd = fundlist + [
            "openclaw-multi",
            "--query",
            arg,
            "--max-agents",
            os.environ.get("OPENCLAW_BOT_MAX_AGENTS", "2"),
            "--timeout-seconds",
            os.environ.get("OPENCLAW_BOT_TIMEOUT", "600"),
            "--poll-interval",
            os.environ.get("OPENCLAW_BOT_POLL_INTERVAL", "10"),
            "--output",
            str(DEFAULT_OPENCLAW_REPORT),
            "--acp-dir",
            os.environ.get("OPENCLAW_ACP_DIR", ""),
            "--acp-cmd",
            os.environ.get("OPENCLAW_ACP_CMD", ""),
        ]
        code, out = run_local_command(run_cmd, timeout_sec=1200)
        client.send_message(chat_id, format_command_result("openclaw-run", code, out))
        return

    if cmd == "/ops_sync":
        run_cmd = fundlist + [
            "ops-sync",
            "--alert-days",
            os.environ.get("VC_OPS_ALERT_DAYS", "14"),
            "--output",
            str(DEFAULT_VC_OPS_REPORT),
        ]
        code, out = run_local_command(run_cmd, timeout_sec=900)
        client.send_message(chat_id, format_command_result("ops-sync", code, out))
        return

    if cmd == "/ops_daily":
        mode = "morning"
        if arg and arg.strip().lower() in {"morning", "evening"}:
            mode = arg.strip().lower()
        run_cmd = ["/bin/zsh", str(ROOT / "scripts" / "vc_ops_cron.sh"), mode]
        code, out = run_local_command(run_cmd, timeout_sec=1800)
        client.send_message(chat_id, format_command_result("ops-daily", code, out, max_lines=60))
        return

    if cmd == "/ops_report":
        run_cmd = fundlist + [
            "ops-report",
            "--alert-days",
            os.environ.get("VC_OPS_ALERT_DAYS", "14"),
            "--output",
            str(DEFAULT_VC_OPS_REPORT),
        ]
        code, out = run_local_command(run_cmd, timeout_sec=900)
        msg = [format_command_result("ops-report", code, out), "", read_report(DEFAULT_VC_OPS_REPORT)]
        client.send_message(chat_id, "\n".join(msg))
        return

    if cmd == "/ops_list":
        days = "21"
        if arg:
            trimmed = arg.strip()
            if trimmed.isdigit():
                days = trimmed
        run_cmd = fundlist + [
            "ops-list",
            "--from-days",
            "-365",
            "--to-days",
            days,
            "--limit",
            "30",
        ]
        code, out = run_local_command(run_cmd, timeout_sec=120)
        client.send_message(chat_id, format_command_result("ops-list", code, out, max_lines=50))
        return

    if cmd == "/ops_today":
        run_cmd = fundlist + [
            "ops-list",
            "--bucket",
            "today",
            "--limit",
            "20",
        ]
        code, out = run_local_command(run_cmd, timeout_sec=120)
        client.send_message(chat_id, format_command_result("ops-today", code, out, max_lines=40))
        return

    if cmd == "/ops_week":
        run_cmd = fundlist + [
            "ops-list",
            "--bucket",
            "this_week",
            "--limit",
            "25",
        ]
        code, out = run_local_command(run_cmd, timeout_sec=120)
        client.send_message(chat_id, format_command_result("ops-week", code, out, max_lines=50))
        return

    if cmd == "/ops_speedrun":
        run_cmd = fundlist + [
            "submission-report",
            "--limit",
            os.environ.get("VC_SUBMISSION_REPORT_LIMIT", "60"),
            "--min-score",
            os.environ.get("VC_SUBMISSION_MIN_SCORE", "8"),
            "--output",
            str(DEFAULT_SUBMISSION_REPORT),
        ]
        code, out = run_local_command(run_cmd, timeout_sec=180)
        msg = [format_command_result("ops-speedrun", code, out, max_lines=30), "", read_report(DEFAULT_SUBMISSION_REPORT)]
        client.send_message(chat_id, "\n".join(msg))
        return

    if cmd in {"/submit_report", "/ops_program"}:
        if not arg:
            client.send_message(chat_id, "usage: /ops_program <program-keyword> (예: /ops_program alliance dao)")
            return
        keyword = arg.strip()
        slug = program_slug(keyword)
        report_path = ROOT / "data" / "reports" / "program_reports" / f"{slug}_submission_report.md"
        run_cmd = fundlist + [
            "ops-program-report",
            "--skip-import",
            "--program",
            keyword,
            "--alert-days",
            os.environ.get("VC_OPS_ALERT_DAYS", "21"),
            "--output",
            str(report_path),
        ]
        code, out = run_local_command(run_cmd, timeout_sec=900)
        msg = [format_command_result("ops-program", code, out), "", read_report(report_path)]
        client.send_message(chat_id, "\n".join(msg))
        return

    if cmd == "/ops_push":
        push_mode = "morning"
        if arg and arg.strip().lower() in {"morning", "evening"}:
            push_mode = arg.strip().lower()
        run_cmd = [
            py,
            str(ROOT / "scripts" / "push_telegram_reports.py"),
            "--chat-id",
            str(chat_id),
            "--mode",
            push_mode,
        ]
        code, out = run_local_command(run_cmd, timeout_sec=300)
        client.send_message(chat_id, format_command_result("ops-push", code, out, max_lines=40))
        return

    if cmd == "/submission_scan":
        raw_arg = (arg or "").strip()
        full_sweep = raw_arg.lower() in {"full", "daily", "seed"}
        run_cmd = fundlist + [
            "submission-scan",
            "--max-pages-per-site",
            os.environ.get("VC_SUBMISSION_MAX_PAGES", "6"),
            "--output",
            str(DEFAULT_SUBMISSION_REPORT),
            "--json-output",
            str(DEFAULT_SUBMISSION_JSON),
            "--stdout-format",
            "json",
            "--stdout-fields",
            "summary,artifacts,recent_changes",
        ]
        if full_sweep:
            run_cmd.extend(
                [
                    "--prune-domains",
                    "--skip-search",
                    "--max-sites",
                    os.environ.get("VC_SUBMISSION_MAX_SITES", "500"),
                    "--max-results-per-query",
                    os.environ.get("VC_SUBMISSION_MAX_RESULTS_PER_QUERY", "0"),
                    "--fundraise-seed-limit",
                    os.environ.get("VC_SUBMISSION_FUNDRAISE_SEED_LIMIT", "5000"),
                    "--report-limit",
                    os.environ.get("VC_SUBMISSION_REPORT_LIMIT", "500"),
                ]
            )
            seed_urls = os.environ.get("VC_SUBMISSION_SEED_URLS", "").strip()
            if seed_urls:
                run_cmd.extend(["--seed-urls", seed_urls])
        else:
            run_cmd.extend(
                [
                    "--max-sites",
                    os.environ.get("VC_SUBMISSION_MAX_SITES", "80"),
                    "--max-results-per-query",
                    os.environ.get("VC_SUBMISSION_MAX_RESULTS_PER_QUERY", "10"),
                    "--report-limit",
                    os.environ.get("VC_SUBMISSION_REPORT_LIMIT", "80"),
                ]
            )
            if raw_arg:
                run_cmd.extend(["--query", raw_arg])
        code, out = run_local_command(run_cmd, timeout_sec=1200)
        client.send_message(chat_id, format_submission_scan_digest("submission-scan", code, out, full_sweep=full_sweep))
        return

    if cmd == "/scan_failures":
        limit = "20"
        if arg and arg.strip().isdigit():
            limit = str(max(1, min(100, int(arg.strip()))))
        run_cmd = fundlist + ["scan-failures", "--status", "pending", "--limit", limit]
        code, out = run_local_command(run_cmd, timeout_sec=60)
        client.send_message(chat_id, format_command_result("scan-failures", code, out, max_lines=40))
        return

    if cmd == "/retry_failed":
        limit = os.environ.get("VC_SUBMISSION_FAILURE_LIMIT", "80")
        if arg and arg.strip().isdigit():
            limit = str(max(1, min(300, int(arg.strip()))))
        run_cmd = fundlist + [
            "submission-scan",
            "--resume-failures",
            "--failures-only",
            "--skip-search",
            "--no-fundraise-seeds",
            "--failure-limit",
            limit,
            "--max-sites",
            limit,
            "--max-pages-per-site",
            os.environ.get("VC_SUBMISSION_MAX_PAGES", "6"),
            "--report-limit",
            os.environ.get("VC_SUBMISSION_REPORT_LIMIT", "120"),
            "--output",
            str(DEFAULT_SUBMISSION_REPORT),
            "--json-output",
            str(DEFAULT_SUBMISSION_JSON),
            "--stdout-format",
            "json",
            "--stdout-fields",
            "summary,artifacts,recent_changes",
        ]
        code, out = run_local_command(run_cmd, timeout_sec=1200)
        client.send_message(chat_id, format_submission_scan_digest("retry-failed", code, out, full_sweep=False))
        return

    if cmd == "/retry_unknown":
        limit = os.environ.get("VC_SUBMISSION_REVIEW_LIMIT", "30")
        if arg and arg.strip().isdigit():
            limit = str(max(1, min(200, int(arg.strip()))))
        run_cmd = fundlist + [
            "submission-scan",
            "--review-targets-only",
            "--skip-search",
            "--no-fundraise-seeds",
            "--review-target-limit",
            limit,
            "--max-sites",
            limit,
            "--max-pages-per-site",
            os.environ.get("VC_SUBMISSION_MAX_PAGES", "6"),
            "--report-limit",
            os.environ.get("VC_SUBMISSION_REPORT_LIMIT", "120"),
            "--output",
            str(DEFAULT_SUBMISSION_REPORT),
            "--json-output",
            str(DEFAULT_SUBMISSION_JSON),
            "--stdout-format",
            "json",
            "--stdout-fields",
            "summary,artifacts,recent_changes",
        ]
        code, out = run_local_command(run_cmd, timeout_sec=1200)
        client.send_message(chat_id, format_submission_scan_digest("retry-unknown", code, out, full_sweep=False))
        return

    if cmd == "/retry_failed_ai":
        limit = os.environ.get("VC_SUBMISSION_FAILURE_LIMIT", "20")
        if arg and arg.strip().isdigit():
            limit = str(max(1, min(100, int(arg.strip()))))
        run_cmd = fundlist + [
            "submission-fallback",
            "--limit",
            limit,
            "--output",
            str(DEFAULT_FALLBACK_REPORT),
            "--json-output",
            str(DEFAULT_FALLBACK_JSON),
            "--refresh-submission-report",
            str(DEFAULT_SUBMISSION_REPORT),
            "--refresh-submission-json",
            str(DEFAULT_SUBMISSION_JSON),
        ]
        code, out = run_local_command(run_cmd, timeout_sec=1200)
        sections = [format_command_result("retry-failed-ai", code, out, max_lines=30)]
        if code == 0:
            sections.extend(
                [
                    "",
                    format_submission_subset("TOP DEADLINES", ["deadline"], limit=8),
                    "",
                    format_submission_subset("TOP OPEN / ROLLING", ["open", "rolling"], limit=8),
                ]
            )
        client.send_message(chat_id, "\n".join(sections))
        return

    if cmd == "/review_queue":
        limit = "20"
        if arg and arg.strip().isdigit():
            limit = str(max(1, min(100, int(arg.strip()))))
        run_cmd = fundlist + ["review-queue", "--limit", limit]
        code, out = run_local_command(run_cmd, timeout_sec=60)
        client.send_message(chat_id, format_command_result("review-queue", code, out, max_lines=45))
        return

    if cmd == "/review_set":
        ref, fields, error = parse_review_set_args(arg)
        if error:
            client.send_message(
                chat_id,
                "\n".join(
                    [
                        error,
                        "",
                        "example:",
                        '/review_set target:abc123 status=deadline deadline_date=2026-03-25 submission_url=https://alliance.xyz/apply',
                    ]
                ),
            )
            return
        if not ref.startswith("target:"):
            client.send_message(chat_id, "usage: /review_set target:<fingerprint> key=value ...")
            return
        run_cmd = fundlist + ["target-override", ref]
        for key, value in fields.items():
            if key == "score":
                if not re.fullmatch(r"-?\\d+", value or ""):
                    client.send_message(chat_id, "score must be an integer")
                    return
                run_cmd.extend(["--score", value])
                continue
            run_cmd.extend([f"--{key.replace('_', '-')}", value])
        code, out = run_local_command(run_cmd, timeout_sec=60)
        client.send_message(chat_id, format_command_result("target-override", code, out, max_lines=20))
        return

    if cmd in {"/review_resolve", "/review_ignore"}:
        raw_ref = (arg or "").strip()
        if not raw_ref:
            client.send_message(chat_id, f"usage: {cmd} <failure:id|id|seed-url>")
            return
        subcmd = "scan-failure-resolve" if cmd == "/review_resolve" else "scan-failure-ignore"
        run_cmd = fundlist + [subcmd, raw_ref]
        code, out = run_local_command(run_cmd, timeout_sec=60)
        client.send_message(chat_id, format_command_result(subcmd, code, out, max_lines=20))
        return

    if cmd == "/submission_list":
        limit = "30"
        if arg and arg.strip().isdigit():
            limit = arg.strip()
        run_cmd = fundlist + [
            "submission-list",
            "--limit",
            limit,
            "--min-score",
            os.environ.get("VC_SUBMISSION_MIN_SCORE", "4"),
        ]
        code, out = run_local_command(run_cmd, timeout_sec=120)
        client.send_message(chat_id, format_command_result("submission-list", code, out, max_lines=60))
        return

    if cmd in {"/apply_open", "/apply_deadline", "/apply_closed"}:
        limit = 12
        if arg and arg.strip().isdigit():
            limit = max(1, min(50, int(arg.strip())))
        if cmd == "/apply_open":
            client.send_message(chat_id, format_submission_subset("APPLY OPEN", ["open", "rolling"], limit=limit))
            return
        if cmd == "/apply_deadline":
            client.send_message(chat_id, format_submission_subset("APPLY DEADLINE", ["deadline"], limit=limit))
            return
        client.send_message(chat_id, format_submission_subset("APPLY CLOSED", ["closed"], limit=limit))
        return

    if cmd == "/task_create":
        if not arg:
            client.send_message(chat_id, "usage: /task_create <keyword or fingerprint>")
            return
        run_cmd = fundlist + ["task-create", arg.strip()]
        code, out = run_local_command(run_cmd, timeout_sec=120)
        client.send_message(chat_id, format_command_result("task-create", code, out, max_lines=30))
        return

    if cmd == "/task_view":
        if not arg or not arg.strip().isdigit():
            client.send_message(chat_id, "usage: /task_view <task-id>")
            return
        run_cmd = fundlist + ["task-view", arg.strip()]
        code, out = run_local_command(run_cmd, timeout_sec=60)
        client.send_message(chat_id, format_command_result("task-view", code, out, max_lines=40))
        return

    if cmd == "/task_ready":
        if not arg or not arg.strip().isdigit():
            client.send_message(chat_id, "usage: /task_ready <task-id>")
            return
        run_cmd = fundlist + ["task-ready", arg.strip()]
        code, out = run_local_command(run_cmd, timeout_sec=60)
        client.send_message(chat_id, format_command_result("task-ready", code, out, max_lines=25))
        return

    if cmd == "/task_submitted":
        if not arg:
            client.send_message(chat_id, "usage: /task_submitted <task-id> [note]")
            return
        parts2 = arg.split(maxsplit=1)
        if not parts2[0].isdigit():
            client.send_message(chat_id, "usage: /task_submitted <task-id> [note]")
            return
        run_cmd = fundlist + ["task-submitted", parts2[0]]
        if len(parts2) > 1 and parts2[1].strip():
            run_cmd.extend(["--note", parts2[1].strip()])
        code, out = run_local_command(run_cmd, timeout_sec=60)
        client.send_message(chat_id, format_command_result("task-submitted", code, out, max_lines=25))
        return

    if cmd in {"/tasks_ready", "/tasks_followup"}:
        limit = "20"
        if arg and arg.strip().isdigit():
            limit = str(max(1, min(50, int(arg.strip()))))
        bucket = "ready" if cmd == "/tasks_ready" else "followup"
        run_cmd = fundlist + ["task-list", "--bucket", bucket, "--limit", limit]
        code, out = run_local_command(run_cmd, timeout_sec=60)
        client.send_message(chat_id, format_command_result(cmd.lstrip("/"), code, out, max_lines=35))
        return

    if cmd == "/changes_today":
        limit = "20"
        if arg and arg.strip().isdigit():
            limit = str(max(1, min(50, int(arg.strip()))))
        run_cmd = fundlist + ["changes-list", "--since-days", "1", "--limit", limit]
        code, out = run_local_command(run_cmd, timeout_sec=60)
        client.send_message(chat_id, format_command_result("changes-today", code, out, max_lines=35))
        return

    if cmd == "/changes_recent":
        days = "7"
        if arg and arg.strip().isdigit():
            days = str(max(1, min(30, int(arg.strip()))))
        run_cmd = fundlist + ["changes-list", "--since-days", days, "--limit", "30"]
        code, out = run_local_command(run_cmd, timeout_sec=60)
        client.send_message(chat_id, format_command_result("changes-recent", code, out, max_lines=40))
        return

    if cmd == "/submission_report":
        run_cmd = fundlist + [
            "submission-report",
            "--limit",
            os.environ.get("VC_SUBMISSION_REPORT_LIMIT", "100"),
            "--min-score",
            os.environ.get("VC_SUBMISSION_MIN_SCORE", "4"),
            "--output",
            str(DEFAULT_SUBMISSION_REPORT),
        ]
        code, out = run_local_command(run_cmd, timeout_sec=180)
        sections = [format_command_result("submission-report", code, out, max_lines=20)]
        if code == 0:
            sections.extend(
                [
                    "",
                    format_submission_subset("TOP DEADLINES", ["deadline"], limit=12),
                    "",
                    format_submission_subset("TOP OPEN / ROLLING", ["open", "rolling"], limit=12),
                    "",
                    format_submission_subset("RECENTLY CLOSED", ["closed"], limit=6),
                ]
            )
        client.send_message(chat_id, "\n".join(sections))
        return

    if cmd == "/submission_export":
        run_cmd = fundlist + [
            "submission-export",
            "--limit",
            os.environ.get("VC_SUBMISSION_REPORT_LIMIT", "100"),
            "--min-score",
            os.environ.get("VC_SUBMISSION_MIN_SCORE", "4"),
            "--output",
            str(DEFAULT_SUBMISSION_JSON),
        ]
        code, out = run_local_command(run_cmd, timeout_sec=180)
        preview = read_report(DEFAULT_SUBMISSION_JSON)
        msg = [format_command_result("submission-export", code, out, max_lines=30), "", preview]
        client.send_message(chat_id, "\n".join(msg))
        return

    if cmd == "/context_save":
        summary = arg or "- telegram command snapshot"
        run_cmd = context_ctl + ["save", "--label", "telegram", "--summary", summary]
        code, out = run_local_command(run_cmd, timeout_sec=60)
        client.send_message(chat_id, format_command_result("context-save", code, out))
        return

    if cmd == "/context_compact":
        run_cmd = context_ctl + ["compact"]
        code, out = run_local_command(run_cmd, timeout_sec=60)
        client.send_message(chat_id, format_command_result("context-compact", code, out))
        return

    if cmd == "/context_restore":
        run_cmd = context_ctl + ["restore", "--mode", "compact"]
        code, out = run_local_command(run_cmd, timeout_sec=60)
        client.send_message(chat_id, format_command_result("context-restore", code, out, max_lines=40))
        return

    if chat_type == "private":
        client.send_message(chat_id, "unknown command. use /help, /quickstart, or /help ops")


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("TELEGRAM_BOT_TOKEN is required", file=sys.stderr)
        return 2

    allowed_chats = parse_allowed_chats()
    ensure_dirs()
    client = TelegramClient(token)
    require_mention_in_group = os.environ.get("TELEGRAM_REQUIRE_MENTION_IN_GROUP", "1").strip() != "0"
    chat_mode_enabled = parse_bool_env("TELEGRAM_CHAT_MODE", "1")
    bot_username = ""
    try:
        me = client.call("getMe", {})
        bot_username = str((me or {}).get("username", "")).strip().lower()
    except Exception as exc:  # noqa: BLE001
        log_line(f"getMe failed: {exc}")

    offset = load_offset()
    log_line(
        "telegram bot started "
        f"username={bot_username or '-'} "
        f"require_mention_in_group={int(require_mention_in_group)} "
        f"chat_mode={int(chat_mode_enabled)}"
    )

    while True:
        try:
            updates = client.call(
                "getUpdates",
                {
                    "offset": offset + 1,
                    "timeout": 25,
                    "allowed_updates": ["message", "channel_post", "edited_message", "edited_channel_post"],
                },
            )
        except urllib.error.URLError as exc:
            log_line(f"poll network error: {exc}")
            time.sleep(3)
            continue
        except Exception as exc:  # noqa: BLE001
            log_line(f"poll error: {exc}")
            time.sleep(3)
            continue

        if not isinstance(updates, list):
            time.sleep(1)
            continue

        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                offset = max(offset, update_id)
                save_offset(offset)

            msg = (
                update.get("message")
                or update.get("edited_message")
                or update.get("channel_post")
                or update.get("edited_channel_post")
            )
            if not isinstance(msg, dict):
                continue
            chat = msg.get("chat", {})
            chat_id = chat.get("id")
            chat_type = str(chat.get("type", "")).strip().lower()
            chat_title = str(chat.get("title", "") or chat.get("username", "") or "").strip()
            text = msg.get("text", "")
            if not isinstance(chat_id, int) or not isinstance(text, str):
                continue
            text = text.strip()
            if not text:
                continue

            has_command = text.startswith("/")
            mention_tag = f"@{bot_username}" if bot_username else ""
            is_mention = bool(mention_tag and mention_tag in text.lower())
            if not has_command:
                wants_help = text.lower() in {"help", "도움", "status", "상태"}
                allow_group_without_mention = (
                    chat_type in {"group", "supergroup"} and not require_mention_in_group
                )
                allow_chat = chat_type in {"private", "channel"} or is_mention or allow_group_without_mention
                if chat_mode_enabled and allow_chat:
                    plain = strip_bot_mention(text, bot_username).strip()
                    if not plain:
                        try:
                            client.send_message(
                                chat_id,
                                help_hint(bot_username, chat_type, require_mention_in_group),
                            )
                        except Exception as exc:  # noqa: BLE001
                            log_line(f"hint-send error chat_id={chat_id} err={exc}")
                        continue
                    try:
                        reply = answer_chat(
                            chat_id=chat_id,
                            user_text=plain,
                            chat_type=chat_type,
                            bot_username=bot_username,
                        )
                        client.send_message(chat_id, reply)
                    except Exception as exc:  # noqa: BLE001
                        log_line(f"chat error chat_id={chat_id} err={exc}")
                        try:
                            client.send_message(chat_id, f"대화 응답 실패: {exc}")
                        except Exception:  # noqa: BLE001
                            pass
                    continue

                if chat_type == "private" or is_mention or wants_help:
                    log_line(
                        f"non-command chat_id={chat_id} chat_type={chat_type} title={chat_title} "
                        f"mention={int(is_mention)} wants_help={int(wants_help)}"
                    )
                    if text.lower() in {"status", "상태"}:
                        try:
                            handle_command(
                                client=client,
                                chat_id=chat_id,
                                text="/status",
                                bot_username=bot_username,
                                chat_type=chat_type,
                                require_mention_in_group=require_mention_in_group,
                            )
                        except Exception as exc:  # noqa: BLE001
                            log_line(f"hint-status error chat_id={chat_id} err={exc}")
                    else:
                        try:
                            client.send_message(
                                chat_id,
                                help_hint(bot_username, chat_type, require_mention_in_group),
                            )
                        except Exception as exc:  # noqa: BLE001
                            log_line(f"hint-send error chat_id={chat_id} err={exc}")
                continue

            if allowed_chats and chat_id not in allowed_chats:
                log_line(f"blocked chat_id={chat_id} chat_type={chat_type} title={chat_title}")
                continue

            cmd = text.split(maxsplit=1)[0].split("@")[0].lower()
            log_line(f"command chat_id={chat_id} chat_type={chat_type} title={chat_title} cmd={cmd}")
            try:
                handle_command(
                    client=client,
                    chat_id=chat_id,
                    text=text,
                    bot_username=bot_username,
                    chat_type=chat_type,
                    require_mention_in_group=require_mention_in_group,
                )
            except Exception as exc:  # noqa: BLE001
                log_line(f"command error cmd={cmd} err={exc}")
                try:
                    client.send_message(chat_id, f"command failed: {exc}")
                except Exception:  # noqa: BLE001
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
