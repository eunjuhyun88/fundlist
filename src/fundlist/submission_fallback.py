from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from .store import ensure_parent_dir
from .submission_finder import (
    DiscoverySeed,
    ScanFailure,
    SubmissionStore,
    SubmissionTarget,
    _canonicalize_url,
    _domain_key,
    _looks_actionable_form_url,
    _looks_form_host,
    _normalize_seed_url,
    _render_submission_report,
    _root_domain,
    _rows_to_json,
    _scan_site,
    _search_duckduckgo,
    _seed_identity,
    now_utc_iso,
    sanitize,
)


DEFAULT_FALLBACK_PROVIDER = os.environ.get("VC_FALLBACK_AI_PROVIDER", "auto").strip().lower() or "auto"
DEFAULT_FALLBACK_REPORT = os.environ.get(
    "VC_FALLBACK_REPORT_PATH",
    "",
).strip()
DEFAULT_FALLBACK_JSON = os.environ.get(
    "VC_FALLBACK_JSON_PATH",
    "",
).strip()


@dataclass
class CandidateURL:
    url: str
    title: str
    score: int


def get_hf_token() -> str:
    return (
        os.environ.get("HF_TOKEN", "").strip()
        or os.environ.get("HUGGINGFACE_API_KEY", "").strip()
        or os.environ.get("HUGGINGFACEHUB_API_TOKEN", "").strip()
    )


def get_openrouter_key() -> str:
    return os.environ.get("OPENROUTER_API_KEY", "").strip()


def choose_ai_provider(preferred: str) -> Tuple[str, str]:
    provider = (preferred or "auto").strip().lower()
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    hf_key = get_hf_token()
    openrouter_key = get_openrouter_key()
    if provider == "groq" and groq_key:
        return "groq", os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
    if provider == "gemini" and gemini_key:
        return "gemini", os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
    if provider == "huggingface" and hf_key:
        return "huggingface", os.environ.get("HUGGINGFACE_MODEL", "Qwen/Qwen2.5-7B-Instruct")
    if provider == "openrouter" and openrouter_key:
        return "openrouter", os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    if provider != "auto":
        return "", ""
    if openrouter_key:
        return "openrouter", os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    if gemini_key:
        return "gemini", os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
    if groq_key:
        return "groq", os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
    if hf_key:
        return "huggingface", os.environ.get("HUGGINGFACE_MODEL", "Qwen/Qwen2.5-7B-Instruct")
    return "", ""


def _call_groq_chat(messages: Sequence[Dict[str, str]], model: str) -> str:
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        return ""
    endpoint = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1/chat/completions")
    payload = {"model": model, "temperature": 0.1, "messages": list(messages)}
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=80) as resp:
        body = resp.read().decode("utf-8")
    parsed = json.loads(body)
    return str(parsed.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()


def _call_gemini_chat(messages: Sequence[Dict[str, str]], model: str) -> str:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return ""
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    prompt = "\n\n".join(
        f"{str(message.get('role', 'user')).strip()}: {str(message.get('content', '')).strip()}"
        for message in messages
        if str(message.get("content", "")).strip()
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1},
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
                return ""
            content = candidates[0].get("content", {})
            parts = content.get("parts", []) if isinstance(content, dict) else []
            if parts and isinstance(parts[0], dict) and parts[0].get("text"):
                return str(parts[0]["text"]).strip()
            return ""
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 2:
                time.sleep(1.0 * (attempt + 1))
                continue
            return ""
    return ""


def _call_huggingface_chat(messages: Sequence[Dict[str, str]], model: str) -> str:
    api_key = get_hf_token()
    if not api_key:
        return ""
    endpoint = os.environ.get("HUGGINGFACE_BASE_URL", "https://router.huggingface.co/v1/chat/completions")
    payload = {"model": model, "temperature": 0.1, "messages": list(messages)}
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
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
    return ""


def _call_openrouter_chat(messages: Sequence[Dict[str, str]], model: str) -> str:
    api_key = get_openrouter_key()
    if not api_key:
        return ""
    endpoint = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions")
    payload = {"model": model, "temperature": 0.1, "messages": list(messages)}
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.environ.get("OPENROUTER_HTTP_REFERER", "https://local.fundlist"),
            "X-Title": os.environ.get("OPENROUTER_APP_TITLE", "fundlist"),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=80) as resp:
        body = resp.read().decode("utf-8")
    parsed = json.loads(body)
    return str(parsed.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()


def call_ai_chat(provider: str, model: str, messages: Sequence[Dict[str, str]]) -> str:
    try:
        if provider == "gemini":
            return _call_gemini_chat(messages, model=model)
        if provider == "huggingface":
            return _call_huggingface_chat(messages, model=model)
        if provider == "openrouter":
            return _call_openrouter_chat(messages, model=model)
        if provider == "groq":
            return _call_groq_chat(messages, model=model)
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _extract_json_object(text: str) -> Dict[str, object]:
    raw = (text or "").strip()
    if not raw:
        return {}
    for candidate in [raw] + re.findall(r"\{.*\}", raw, flags=re.DOTALL):
        try:
            parsed = json.loads(candidate)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _org_tokens(seed: DiscoverySeed) -> List[str]:
    base = seed.org_name_hint or _domain_key(seed.url).split(".")[0]
    tokens = re.findall(r"[a-zA-Z0-9]+", base.lower())
    return [token for token in tokens if len(token) >= 3]


def _candidate_score(seed: DiscoverySeed, url: str, title: str) -> int:
    score = 0
    normalized = _canonicalize_url(url)
    if not normalized:
        return -999
    if normalized == _canonicalize_url(seed.url):
        score += 12
    if _looks_actionable_form_url(normalized):
        score += 20
    elif _looks_form_host(normalized):
        score += 8
    merged = f"{normalized} {title}".lower()
    if any(token in merged for token in ["apply", "pitch", "submit", "application", "grant", "accelerator", "cohort"]):
        score += 8
    seed_root = _root_domain(_domain_key(seed.url))
    candidate_root = _root_domain(_domain_key(normalized))
    if seed_root and candidate_root and seed_root == candidate_root:
        score += 10
    elif candidate_root and seed_root and candidate_root != seed_root and not _looks_form_host(normalized):
        score -= 10
    for token in _org_tokens(seed):
        if token in merged:
            score += 3
    if not any(token in merged for token in _org_tokens(seed)) and candidate_root != seed_root and not _looks_form_host(normalized):
        score -= 6
    if any(token in merged for token in ["blog", "news", "press", "linkedin", "crunchbase"]):
        score -= 6
    return score


def _build_search_queries(seed: DiscoverySeed) -> List[str]:
    name = sanitize(seed.org_name_hint or "", limit=120)
    domain = _domain_key(seed.url)
    brand = domain.split(".")[0] if domain else ""
    base_terms = [term for term in [name, brand] if term]
    queries: List[str] = []
    for term in base_terms or [seed.url]:
        queries.extend(
            [
                f'"{term}" apply',
                f'"{term}" "application form"',
                f'"{term}" accelerator apply',
                f'"{term}" grant apply',
                f'"{term}" "pitch us"',
            ]
        )
    if domain:
        queries.extend(
            [
                f"site:{domain} apply",
                f"site:{domain} pitch",
                f"site:{domain} grant",
                f"site:{domain} accelerator",
            ]
        )
    deduped: List[str] = []
    seen: set[str] = set()
    for query in queries:
        key = query.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(query)
    return deduped[:6]


def _collect_candidates(seed: DiscoverySeed, *, max_results_per_query: int, max_candidates: int) -> List[CandidateURL]:
    scored: Dict[str, CandidateURL] = {}
    original = _normalize_seed_url(seed.url)
    if original:
        scored[original] = CandidateURL(url=original, title="original seed", score=_candidate_score(seed, original, "original seed"))
    canonical_seed = _canonicalize_url(seed.url)
    if canonical_seed:
        seed_root_url = f"https://{_domain_key(canonical_seed)}/"
        normalized_root = _normalize_seed_url(seed_root_url)
        if normalized_root and normalized_root not in scored:
            scored[normalized_root] = CandidateURL(
                url=normalized_root,
                title="seed root",
                score=_candidate_score(seed, normalized_root, "seed root"),
            )

    for query in _build_search_queries(seed):
        try:
            hits = _search_duckduckgo(query, max_results=max_results_per_query)
        except Exception:  # noqa: BLE001
            continue
        for url, title in hits:
            normalized = _normalize_seed_url(url)
            if not normalized:
                continue
            candidate = CandidateURL(url=normalized, title=sanitize(title, limit=160), score=_candidate_score(seed, normalized, title))
            prev = scored.get(normalized)
            if prev is None or candidate.score > prev.score:
                scored[normalized] = candidate
    ordered = sorted(scored.values(), key=lambda item: (item.score, item.url), reverse=True)
    filtered = [item for item in ordered if item.score >= 0 or item.url == original]
    return (filtered or ordered)[:max_candidates]


def _select_candidate_urls(
    seed: DiscoverySeed,
    candidates: Sequence[CandidateURL],
    *,
    ai_provider: str,
    ai_model: str,
    max_urls: int,
) -> Tuple[List[str], str, str]:
    if not candidates:
        return [], "none", "no candidate URLs"
    provider, model = choose_ai_provider(ai_provider)
    if provider and ai_model:
        model = ai_model
    if not provider:
        ordered = [candidate.url for candidate in candidates[:max_urls]]
        return ordered, "heuristic", "no AI provider configured; using heuristic score"

    prompt = {
        "org_name_hint": seed.org_name_hint,
        "original_seed_url": seed.url,
        "goal": "choose up to 3 URLs that are most likely the official application page or official landing page leading to the application",
        "rules": [
            "prefer official organization domains",
            "prefer direct form URLs if clearly actionable",
            "avoid news, blog, directory, and social pages",
            "return only URLs from the provided candidates",
        ],
        "candidates": [{"url": candidate.url, "title": candidate.title, "score": candidate.score} for candidate in candidates[:12]],
        "json_schema": {"best_urls": ["url1", "url2"], "reason": "short text"},
    }
    messages = [
        {"role": "system", "content": "Return strict JSON only. Do not add markdown."},
        {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
    ]
    reply = call_ai_chat(provider, model, messages)
    parsed = _extract_json_object(reply)
    best_urls = parsed.get("best_urls", [])
    allowed = {candidate.url for candidate in candidates}
    if isinstance(best_urls, list):
        filtered = []
        seen: set[str] = set()
        for item in best_urls:
            normalized = _canonicalize_url(str(item or "").strip())
            if not normalized or normalized not in allowed or normalized in seen:
                continue
            seen.add(normalized)
            filtered.append(normalized)
            if len(filtered) >= max_urls:
                break
        if filtered:
            return filtered, f"ai:{provider}", sanitize(str(parsed.get("reason") or ""), limit=240)

    ordered = [candidate.url for candidate in candidates[:max_urls]]
    return ordered, f"heuristic_after_ai:{provider}", "AI reply unusable; using heuristic ranking"


def _target_rank(target: SubmissionTarget) -> Tuple[int, int, int]:
    status_bonus = {"open": 4, "rolling": 3, "deadline": 2, "closed": 1}.get(target.status.strip().lower(), 0)
    type_bonus = {"form": 3, "email": 2, "intro-only": 1, "unknown": 0}.get(target.submission_type.strip().lower(), 0)
    return (status_bonus, type_bonus, int(target.score or 0))


def _render_fallback_report(results: Sequence[Dict[str, object]]) -> str:
    lines: List[str] = ["# Submission Fallback Retry Report", "", f"_Generated at: {now_utc_iso()}_", ""]
    recovered = sum(1 for item in results if item.get("recovered"))
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- total_items: {len(results)}")
    lines.append(f"- recovered: {recovered}")
    lines.append(f"- unresolved: {len(results) - recovered}")
    lines.append("")
    for idx, item in enumerate(results, start=1):
        lines.append(f"## {idx}. {item.get('org_name') or item.get('seed_url')}")
        lines.append(f"- seed_url: {item.get('seed_url')}")
        lines.append(f"- selection_mode: {item.get('selection_mode')}")
        if item.get("selection_reason"):
            lines.append(f"- selection_reason: {item.get('selection_reason')}")
        lines.append(f"- recovered: {'yes' if item.get('recovered') else 'no'}")
        lines.append(f"- attempted_candidates: {len(item.get('candidate_urls') or [])}")
        if item.get("target"):
            target = item["target"]
            lines.append(f"- submission_url: {target.get('submission_url')}")
            lines.append(f"- status: {target.get('status')}")
            lines.append(f"- deadline: {target.get('deadline_date') or target.get('deadline_text') or '-'}")
            lines.append(f"- score: {target.get('score')}")
        if item.get("candidate_urls"):
            lines.append("- candidate_urls:")
            for url in item["candidate_urls"]:
                lines.append(f"  - {url}")
        if item.get("errors"):
            lines.append("- errors:")
            for error in item["errors"][:6]:
                lines.append(f"  - {error}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def submission_fallback_command(args: argparse.Namespace) -> int:
    store = SubmissionStore(args.db)
    if args.seed_urls:
        seeds = [
            DiscoverySeed(url=_normalize_seed_url(raw), org_name_hint="", source="manual-fallback")
            for raw in str(args.seed_urls or "").split(",")
            if raw.strip()
        ]
    else:
        seeds = []
        seen: set[str] = set()
        for row in store.list_scan_failures(limit=max(args.limit * 6, 120), status="pending"):
            seed_source = str(row["seed_source"] or "").strip().lower()
            if seed_source.startswith("fallback:"):
                continue
            normalized = _normalize_seed_url(str(row["seed_url"] or ""))
            if not normalized:
                continue
            key = _seed_identity(normalized)
            if not key or key in seen:
                continue
            seen.add(key)
            seeds.append(
                DiscoverySeed(
                    url=normalized,
                    org_name_hint=sanitize(str(row["org_name_hint"] or ""), limit=120),
                    source=f"failure:{seed_source or 'scan'}",
                )
            )
            if len(seeds) >= args.limit:
                break

    seeds = [seed for seed in seeds if seed.url]
    seeds = seeds[: args.limit]
    print(f"[info] fallback_seeds={len(seeds)}")

    recovered_targets: List[SubmissionTarget] = []
    results: List[Dict[str, object]] = []
    changed = 0
    resolved_failures = 0
    recorded_failures = 0

    for idx, seed in enumerate(seeds, start=1):
        candidates = _collect_candidates(
            seed,
            max_results_per_query=args.max_results_per_query,
            max_candidates=args.max_candidates,
        )
        selected_urls, selection_mode, selection_reason = _select_candidate_urls(
            seed,
            candidates,
            ai_provider=args.ai_provider,
            ai_model=args.model,
            max_urls=args.max_candidate_scan,
        )
        best_target: SubmissionTarget | None = None
        error_messages: List[str] = []
        tried_urls: List[str] = []
        attempted_at = now_utc_iso()

        for url in selected_urls:
            tried_urls.append(url)
            result = _scan_site(
                DiscoverySeed(url=url, org_name_hint=seed.org_name_hint, source=f"fallback:{selection_mode}"),
                max_pages=args.max_pages_per_site,
                http_timeout=args.http_timeout,
            )
            for failure in result.failures:
                store.record_scan_failure(
                    ScanFailure(
                        seed_url=seed.url,
                        org_name_hint=seed.org_name_hint or failure.org_name_hint,
                        seed_source=f"fallback:{selection_mode}",
                        page_url=failure.page_url,
                        stage=failure.stage,
                        error_type=failure.error_type,
                        error_message=f"{failure.error_message} [candidate={url}]",
                    ),
                    detected_at=attempted_at,
                )
                recorded_failures += 1
                error_messages.append(f"{failure.stage}:{failure.error_type}:{failure.error_message}")
            if result.target and (best_target is None or _target_rank(result.target) > _target_rank(best_target)):
                best_target = result.target

        if best_target is not None:
            recovered_targets.append(best_target)
            resolved_failures += store.resolve_scan_failures(seed.url, resolved_at=attempted_at)
            print(
                f"[recovered] {idx}/{len(seeds)} {best_target.org_name} | "
                f"{best_target.status} | {best_target.submission_type} | {best_target.submission_url}"
            )
        else:
            print(f"[unresolved] {idx}/{len(seeds)} {seed.org_name_hint or seed.url} | candidates={len(selected_urls)}")

        results.append(
            {
                "seed_url": seed.url,
                "org_name": seed.org_name_hint,
                "selection_mode": selection_mode,
                "selection_reason": selection_reason,
                "candidate_urls": tried_urls,
                "recovered": best_target is not None,
                "target": None
                if best_target is None
                else {
                    "org_name": best_target.org_name,
                    "submission_url": best_target.submission_url,
                    "status": best_target.status,
                    "submission_type": best_target.submission_type,
                    "deadline_text": best_target.deadline_text,
                    "deadline_date": best_target.deadline_date,
                    "score": best_target.score,
                },
                "errors": error_messages,
            }
        )

    if recovered_targets:
        changed = store.upsert_targets(recovered_targets)

    output = Path(args.output).expanduser()
    ensure_parent_dir(str(output))
    output.write_text(_render_fallback_report(results), encoding="utf-8")

    if args.json_output:
        json_path = Path(args.json_output).expanduser()
        ensure_parent_dir(str(json_path))
        payload = {
            "generated_at": now_utc_iso(),
            "items": results,
            "recovered_targets": [
                {
                    "org_name": target.org_name,
                    "source_url": target.source_url,
                    "submission_url": target.submission_url,
                    "status": target.status,
                    "submission_type": target.submission_type,
                    "deadline_text": target.deadline_text,
                    "deadline_date": target.deadline_date,
                    "score": target.score,
                }
                for target in recovered_targets
            ],
        }
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[done] fallback_json={json_path} rows={len(results)}")

    if args.refresh_submission_report:
        rows = store.list_targets(limit=args.report_limit, status=args.status_filter, org_type=args.org_type_filter, min_score=args.min_score)
        events = store.list_events(limit=args.event_limit)
        report_path = Path(args.refresh_submission_report).expanduser()
        ensure_parent_dir(str(report_path))
        report_path.write_text(_render_submission_report(rows, events), encoding="utf-8")
        if args.refresh_submission_json:
            json_path = Path(args.refresh_submission_json).expanduser()
            ensure_parent_dir(str(json_path))
            payload = {
                "generated_at": now_utc_iso(),
                "filters": {"status": args.status_filter, "org_type": args.org_type_filter, "min_score": args.min_score},
                "items": _rows_to_json(rows),
            }
            json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"[done] fallback_scanned={len(seeds)} recovered={len(recovered_targets)} changed={changed} "
        f"resolved_failures={resolved_failures} recorded_failures={recorded_failures} report={output}"
    )
    return 0
