from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .store import ensure_parent_dir


TERMINAL_PHASES = {"COMPLETED", "REJECTED", "EXPIRED", "TIMEOUT", "ERROR"}


@dataclass
class ACPRunner:
    base_cmd: List[str]
    cwd: Optional[str]


@dataclass
class CandidateJob:
    agent_name: str
    wallet: str
    offering_name: str
    offering_schema: Dict[str, Any]
    metrics: Dict[str, Any]
    requirements: Dict[str, Any]


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_float(v: Any, fallback: float = 0.0) -> float:
    try:
        if v is None:
            return fallback
        return float(v)
    except Exception:  # noqa: BLE001
        return fallback


def _safe_int(v: Any, fallback: int = 0) -> int:
    try:
        if v is None:
            return fallback
        return int(v)
    except Exception:  # noqa: BLE001
        return fallback


def _extract_json_from_text(text: str) -> Any:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("empty command output")
    # acp --json outputs a single JSON line in normal cases.
    for line in reversed(lines):
        try:
            return json.loads(line)
        except Exception:  # noqa: BLE001
            continue
    # fallback: try whole text
    return json.loads(text.strip())


def resolve_acp_runner(acp_cmd: str, acp_dir: str) -> ACPRunner:
    if acp_cmd.strip():
        return ACPRunner(base_cmd=shlex.split(acp_cmd), cwd=acp_dir or None)

    acp_bin = shutil.which("acp")
    if acp_bin:
        return ACPRunner(base_cmd=[acp_bin], cwd=acp_dir or None)

    if acp_dir.strip():
        d = Path(acp_dir).expanduser()
        if not (d / "bin" / "acp.ts").exists():
            raise RuntimeError(f"acp.ts not found in OPENCLAW_ACP_DIR: {d}")
        tsx_local = d / "node_modules" / ".bin" / "tsx"
        if tsx_local.exists():
            return ACPRunner(base_cmd=[str(tsx_local), "bin/acp.ts"], cwd=str(d))
        if shutil.which("npx"):
            return ACPRunner(base_cmd=["npx", "tsx", "bin/acp.ts"], cwd=str(d))
        raise RuntimeError("Cannot resolve ACP CLI. Install Node/npm or set OPENCLAW_ACP_CMD=acp")

    raise RuntimeError(
        "Cannot resolve ACP CLI. Set OPENCLAW_ACP_CMD or install acp, "
        "or set OPENCLAW_ACP_DIR to openclaw-acp repo path."
    )


def run_acp_json(runner: ACPRunner, args: Sequence[str]) -> Any:
    cmd = runner.base_cmd + list(args) + ["--json"]
    proc = subprocess.run(
        cmd,
        cwd=runner.cwd,
        text=True,
        capture_output=True,
    )
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if proc.returncode != 0:
        err_msg = stderr or stdout or f"command failed: {' '.join(cmd)}"
        try:
            parsed = _extract_json_from_text(err_msg)
            if isinstance(parsed, dict) and parsed.get("error"):
                err_msg = str(parsed["error"])
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(err_msg)
    if not stdout:
        return {}
    return _extract_json_from_text(stdout)


def _get_agents_from_browse_result(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            return [x for x in payload["data"] if isinstance(x, dict)]
        if isinstance(payload.get("agents"), list):
            return [x for x in payload["agents"] if isinstance(x, dict)]
    return []


def _get_agent_jobs(agent: Dict[str, Any]) -> List[Dict[str, Any]]:
    jobs = agent.get("jobs")
    if isinstance(jobs, list):
        return [j for j in jobs if isinstance(j, dict)]
    offerings = agent.get("jobOfferings")
    if isinstance(offerings, list):
        return [j for j in offerings if isinstance(j, dict)]
    return []


def _build_defaults_from_schema(schema: Any) -> Any:
    if not isinstance(schema, dict):
        return {}
    if "enum" in schema and isinstance(schema["enum"], list) and schema["enum"]:
        return schema["enum"][0]

    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), t[0] if t else None)

    if t == "string":
        return schema.get("default", "sample")
    if t == "number":
        return schema.get("default", 1)
    if t == "integer":
        return schema.get("default", 1)
    if t == "boolean":
        return schema.get("default", True)
    if t == "array":
        return schema.get("default", [])
    if t == "object" or "properties" in schema:
        props = schema.get("properties", {}) if isinstance(schema.get("properties"), dict) else {}
        required = schema.get("required", []) if isinstance(schema.get("required"), list) else []
        out: Dict[str, Any] = {}
        for key, val in props.items():
            if required and key not in required:
                continue
            out[key] = _build_defaults_from_schema(val)
        return out
    return schema.get("default", {})


def _merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    out.update(override)
    return out


def _pick_best_offering(
    jobs: List[Dict[str, Any]],
    offering_filter: str,
) -> Optional[Dict[str, Any]]:
    if not jobs:
        return None
    if offering_filter.strip():
        key = offering_filter.strip().lower()
        filtered = [j for j in jobs if key in str(j.get("name", "")).lower()]
        if filtered:
            jobs = filtered
    # prefer fixed-price/low friction offerings first
    jobs_sorted = sorted(
        jobs,
        key=lambda j: (
            0 if str((j.get("priceV2") or {}).get("type", j.get("priceType", ""))).lower() == "fixed" else 1,
            _safe_float((j.get("priceV2") or {}).get("value", j.get("price", 0)), fallback=0.0),
        ),
    )
    return jobs_sorted[0]


def select_candidates(
    agents: List[Dict[str, Any]],
    max_agents: int,
    offering_filter: str,
    base_requirements: Dict[str, Any],
    requirements_map: Dict[str, Any],
) -> List[CandidateJob]:
    scored: List[Tuple[float, CandidateJob]] = []
    for agent in agents:
        wallet = str(agent.get("walletAddress") or agent.get("agentWalletAddress") or "").strip()
        if not wallet:
            continue
        agent_name = str(agent.get("name", "")).strip() or wallet
        metrics = agent.get("metrics") if isinstance(agent.get("metrics"), dict) else {}
        jobs = _get_agent_jobs(agent)
        picked = _pick_best_offering(jobs, offering_filter=offering_filter)
        if not picked:
            continue

        offering_name = str(picked.get("name", "")).strip()
        if not offering_name:
            continue
        schema = picked.get("requirement") if isinstance(picked.get("requirement"), dict) else {}
        auto_req = _build_defaults_from_schema(schema)
        if not isinstance(auto_req, dict):
            auto_req = {}

        specific = {}
        key_wallet = f"{wallet}|{offering_name}"
        if isinstance(requirements_map.get(key_wallet), dict):
            specific = requirements_map[key_wallet]
        elif isinstance(requirements_map.get(offering_name), dict):
            specific = requirements_map[offering_name]

        req = _merge_dict(auto_req, base_requirements)
        req = _merge_dict(req, specific)

        success_rate = _safe_float(metrics.get("successRate"), fallback=0.0)
        job_count = _safe_int(metrics.get("successfulJobCount"), fallback=0)
        online = 1 if bool(metrics.get("isOnline")) else 0
        score = online * 10000 + success_rate * 100 + job_count

        scored.append(
            (
                score,
                CandidateJob(
                    agent_name=agent_name,
                    wallet=wallet,
                    offering_name=offering_name,
                    offering_schema=schema,
                    metrics=metrics,
                    requirements=req,
                ),
            )
        )

    scored.sort(key=lambda x: x[0], reverse=True)
    out = [item for _, item in scored]

    # keep unique wallets
    unique: List[CandidateJob] = []
    seen_wallets = set()
    for c in out:
        if c.wallet in seen_wallets:
            continue
        seen_wallets.add(c.wallet)
        unique.append(c)
        if len(unique) >= max_agents:
            break
    return unique


def _extract_job_id(payload: Any) -> Optional[int]:
    if isinstance(payload, dict):
        if isinstance(payload.get("jobId"), int):
            return payload["jobId"]
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("jobId"), int):
            return data["jobId"]
    return None


def _normalize_status_payload(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {"phase": "ERROR", "deliverable": None, "raw": payload, "memoHistory": []}
    phase = str(payload.get("phase") or payload.get("jobPhase") or "").upper()
    if not phase and isinstance(payload.get("data"), dict):
        phase = str(payload["data"].get("phase", "")).upper()
    deliverable = payload.get("deliverable")
    memo_history = payload.get("memoHistory") or payload.get("memos") or []
    return {
        "phase": phase or "UNKNOWN",
        "deliverable": deliverable,
        "memoHistory": memo_history if isinstance(memo_history, list) else [],
        "raw": payload,
    }


def _load_requirements_map(path: str) -> Dict[str, Any]:
    if not path.strip():
        return {}
    p = Path(path).expanduser()
    if not p.exists():
        raise RuntimeError(f"requirements map file not found: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("requirements map JSON must be an object")
    return data


def _write_openclaw_report(
    output_path: str,
    query: str,
    candidates: List[CandidateJob],
    jobs: List[Dict[str, Any]],
    raw_json_path: str,
) -> str:
    out = Path(output_path).expanduser()
    ensure_parent_dir(str(out))

    done = [j for j in jobs if j.get("phase") in TERMINAL_PHASES]
    completed = [j for j in jobs if j.get("phase") == "COMPLETED"]
    failed = [j for j in jobs if j.get("phase") in {"REJECTED", "EXPIRED", "TIMEOUT", "ERROR"}]

    lines = [
        "# OpenClaw Multi-Agent Report",
        "",
        f"- Generated (UTC): {now_utc_iso()}",
        f"- Query: {query}",
        f"- Candidates selected: {len(candidates)}",
        f"- Jobs tracked: {len(jobs)}",
        f"- Completed: {len(completed)}",
        f"- Failed/Timed out: {len(failed)}",
        f"- Raw JSON: {raw_json_path}",
        "",
        "## Selected Agents",
    ]
    for c in candidates:
        lines.append(
            f"- {c.agent_name} | wallet={c.wallet} | offering={c.offering_name} | requirements={json.dumps(c.requirements, ensure_ascii=False)}"
        )

    lines.extend(["", "## Job Results"])
    for j in jobs:
        lines.append(
            f"- job_id={j.get('job_id')} | phase={j.get('phase')} | agent={j.get('agent_name')} | offering={j.get('offering_name')}"
        )
        deliverable = j.get("deliverable")
        if deliverable is not None:
            text = str(deliverable).strip().replace("\n", " ")
            lines.append(f"  deliverable: {text[:600]}")

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(out)


def openclaw_multi_command(args: argparse.Namespace) -> int:
    try:
        runner = resolve_acp_runner(acp_cmd=args.acp_cmd, acp_dir=args.acp_dir)
        base_requirements = json.loads(args.requirements_json) if args.requirements_json.strip() else {}
        if not isinstance(base_requirements, dict):
            raise RuntimeError("--requirements-json must be a JSON object")
        req_map = _load_requirements_map(args.requirements_map)

        browse_payload = run_acp_json(runner, ["browse", args.query])
        agents = _get_agents_from_browse_result(browse_payload)
        if not agents:
            print("[warn] no agents found from acp browse")
            return 1

        candidates = select_candidates(
            agents=agents,
            max_agents=args.max_agents,
            offering_filter=args.offering_filter,
            base_requirements=base_requirements,
            requirements_map=req_map,
        )
        if not candidates:
            print("[warn] no executable candidates found (agents had no offerings)")
            return 1

        if args.dry_run:
            print(f"[done] dry-run candidates={len(candidates)}")
            for c in candidates:
                print(
                    f"- agent={c.agent_name} wallet={c.wallet} offering={c.offering_name} requirements={json.dumps(c.requirements, ensure_ascii=False)}"
                )
            return 0

        created_jobs: List[Dict[str, Any]] = []
        for c in candidates:
            try:
                payload = run_acp_json(
                    runner,
                    [
                        "job",
                        "create",
                        c.wallet,
                        c.offering_name,
                        "--requirements",
                        json.dumps(c.requirements, ensure_ascii=False),
                    ],
                )
                job_id = _extract_job_id(payload)
                if job_id is None:
                    print(
                        f"[warn] cannot read jobId for agent={c.agent_name} offering={c.offering_name}, skipped"
                    )
                    continue
                created_jobs.append(
                    {
                        "job_id": job_id,
                        "agent_name": c.agent_name,
                        "wallet": c.wallet,
                        "offering_name": c.offering_name,
                        "requirements": c.requirements,
                        "phase": "REQUEST",
                        "status_raw": payload,
                        "deliverable": None,
                        "memoHistory": [],
                    }
                )
                print(f"[ok] created job_id={job_id} agent={c.agent_name} offering={c.offering_name}")
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] create failed agent={c.agent_name} offering={c.offering_name}: {exc}")

        if not created_jobs:
            print("[warn] no jobs were created. Check ACP setup/login and requirements payload.")
            return 1

        deadline = time.time() + max(10, args.timeout_seconds)
        pending = {int(j["job_id"]) for j in created_jobs}

        while pending and time.time() < deadline:
            def fetch_status(job_id: int) -> Tuple[int, Dict[str, Any]]:
                data = run_acp_json(runner, ["job", "status", str(job_id)])
                return job_id, _normalize_status_payload(data)

            with ThreadPoolExecutor(max_workers=min(6, len(pending))) as ex:
                futures = {ex.submit(fetch_status, jid): jid for jid in list(pending)}
                for fut in as_completed(futures):
                    jid = futures[fut]
                    try:
                        _, st = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        st = {
                            "phase": "ERROR",
                            "deliverable": None,
                            "memoHistory": [],
                            "raw": {"error": str(exc)},
                        }
                    for job in created_jobs:
                        if int(job["job_id"]) == jid:
                            job["phase"] = st.get("phase", "UNKNOWN")
                            job["deliverable"] = st.get("deliverable")
                            job["memoHistory"] = st.get("memoHistory", [])
                            job["status_raw"] = st.get("raw")
                            break
                    if st.get("phase") in TERMINAL_PHASES:
                        pending.discard(jid)
            if pending:
                time.sleep(max(1, args.poll_interval))

        if pending:
            for job in created_jobs:
                if int(job["job_id"]) in pending:
                    job["phase"] = "TIMEOUT"

        output = Path(args.output).expanduser()
        ensure_parent_dir(str(output))
        raw_path = output.with_name(f"{output.stem}_jobs.json")
        raw_payload = {
            "generated_at_utc": now_utc_iso(),
            "query": args.query,
            "candidates": [
                {
                    "agent_name": c.agent_name,
                    "wallet": c.wallet,
                    "offering_name": c.offering_name,
                    "requirements": c.requirements,
                }
                for c in candidates
            ],
            "jobs": created_jobs,
        }
        raw_path.write_text(json.dumps(raw_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        report_path = _write_openclaw_report(
            output_path=str(output),
            query=args.query,
            candidates=candidates,
            jobs=created_jobs,
            raw_json_path=str(raw_path),
        )

        done = [j for j in created_jobs if j.get("phase") in TERMINAL_PHASES]
        print(
            f"[done] openclaw multi-agent jobs_total={len(created_jobs)} terminal={len(done)} report={report_path} raw={raw_path}"
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[error] openclaw-multi failed: {exc}")
        return 2
