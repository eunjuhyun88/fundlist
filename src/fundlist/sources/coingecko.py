from __future__ import annotations

import urllib.parse
from typing import List, Optional, Sequence

from ..http import http_get_json
from ..models import Item, now_utc_iso, to_item


def collect_coingecko_markets(
    coin_ids: Sequence[str],
    per_page: int = 50,
    api_key: Optional[str] = None,
) -> List[Item]:
    ids = ",".join([coin.strip().lower() for coin in coin_ids if coin.strip()])
    if not ids:
        return []

    query = urllib.parse.urlencode(
        {
            "vs_currency": "usd",
            "ids": ids,
            "order": "market_cap_desc",
            "per_page": max(1, min(per_page, 250)),
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "24h",
        }
    )
    url = f"https://api.coingecko.com/api/v3/coins/markets?{query}"
    headers = {"User-Agent": "fundlist-agent"}
    if api_key:
        headers["x-cg-demo-api-key"] = api_key

    data = http_get_json(url, headers=headers)
    if not isinstance(data, list):
        return []

    ts = now_utc_iso()
    out: List[Item] = []
    for row in data:
        symbol = str(row.get("symbol", "")).upper() or row.get("id", "-")
        current_price = row.get("current_price")
        pct = row.get("price_change_percentage_24h")
        out.append(
            to_item(
                source="coingecko",
                category="crypto_market",
                symbol=symbol,
                title=f"{row.get('name', symbol)} ${current_price} ({pct}% 24h)",
                url=f"https://www.coingecko.com/en/coins/{row.get('id', '')}",
                published_at=ts,
                payload=row,
            )
        )
    return out

