#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone
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
CHAT_HISTORY: Dict[int, Deque[Dict[str, str]]] = {}


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
    return "명령은 /help, 대화형은 질문 문장을 보내면 됩니다."


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

    if cmd in {"/start", "/help"}:
        client.send_message(
            chat_id,
            "\n".join(
                [
                    "fundlist bot commands:",
                    "/status",
                    "/fundraise",
                    "/fundraise_ai [groq|gemini|huggingface|openrouter]",
                    "/report [fundraise|openclaw]",
                    "/openclaw_dry <query>",
                    "/openclaw_run <query>",
                    "/ops_sync",
                    "/ops_daily [morning|evening]",
                    "/ops_report",
                    "/ops_list [days]",
                    "/ops_today",
                    "/ops_week",
                    "/ops_speedrun",
                    "/ops_program <program-keyword>",
                    "/submit_report <program-keyword>",
                    "/ops_push",
                    "/submission_scan [query]",
                    "/submission_list [limit]",
                    "/submission_report",
                    "/submission_export",
                    "/context_save <summary>",
                    "/context_compact",
                    "/context_restore",
                ]
            ),
        )
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
        run_cmd = fundlist + [
            "submission-scan",
            "--max-sites",
            os.environ.get("VC_SUBMISSION_MAX_SITES", "80"),
            "--max-pages-per-site",
            os.environ.get("VC_SUBMISSION_MAX_PAGES", "6"),
            "--max-results-per-query",
            os.environ.get("VC_SUBMISSION_MAX_RESULTS_PER_QUERY", "10"),
            "--report-limit",
            os.environ.get("VC_SUBMISSION_REPORT_LIMIT", "80"),
            "--output",
            str(DEFAULT_SUBMISSION_REPORT),
        ]
        if arg:
            run_cmd.extend(["--query", arg.strip()])
        code, out = run_local_command(run_cmd, timeout_sec=1200)
        msg = [format_command_result("submission-scan", code, out, max_lines=50), "", read_report(DEFAULT_SUBMISSION_REPORT)]
        client.send_message(chat_id, "\n".join(msg))
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
        msg = [format_command_result("submission-report", code, out, max_lines=30), "", read_report(DEFAULT_SUBMISSION_REPORT)]
        client.send_message(chat_id, "\n".join(msg))
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
        client.send_message(chat_id, "unknown command. use /help")


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
