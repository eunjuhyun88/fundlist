from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

from .models import Item
from .sources.coingecko import collect_coingecko_markets
from .sources.fred import collect_fred_series
from .sources.openclaw import collect_openclaw_github
from .sources.sec import collect_sec_filings


@dataclass
class CollectorConfig:
    openclaw_repo: str
    openclaw_limit: int
    sec_tickers: Sequence[str]
    sec_limit: int
    sec_user_agent: str
    fred_series: Sequence[str]
    fred_api_key: Optional[str]
    coin_ids: Sequence[str]
    coingecko_limit: int
    coingecko_api_key: Optional[str]
    github_token: Optional[str]


def parse_csv_list(value: str) -> List[str]:
    return [s.strip() for s in value.split(",") if s.strip()]


def collect_from_sources(selected_sources: Sequence[str], cfg: CollectorConfig) -> Dict[str, List[Item]]:
    out: Dict[str, List[Item]] = {}

    for source in selected_sources:
        src = source.strip().lower()
        if not src:
            continue

        if src == "openclaw":
            out[src] = collect_openclaw_github(
                repo=cfg.openclaw_repo,
                per_type=cfg.openclaw_limit,
                github_token=cfg.github_token,
            )
        elif src == "sec":
            out[src] = collect_sec_filings(
                tickers=cfg.sec_tickers,
                per_ticker=cfg.sec_limit,
                user_agent=cfg.sec_user_agent,
            )
        elif src == "fred":
            out[src] = collect_fred_series(
                series_ids=cfg.fred_series,
                api_key=cfg.fred_api_key,
            )
        elif src == "coingecko":
            out[src] = collect_coingecko_markets(
                coin_ids=cfg.coin_ids,
                per_page=cfg.coingecko_limit,
                api_key=cfg.coingecko_api_key,
            )
        else:
            raise ValueError(f"unknown source: {src}")

    return out

