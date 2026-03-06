from __future__ import annotations

import urllib.parse
from typing import List, Optional, Sequence

from ..http import http_get_json
from ..models import Item, to_item


def collect_fred_series(series_ids: Sequence[str], api_key: Optional[str]) -> List[Item]:
    if not api_key:
        return []

    out: List[Item] = []
    for raw_series_id in series_ids:
        series_id = raw_series_id.strip().upper()
        if not series_id:
            continue

        query = urllib.parse.urlencode(
            {
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 1,
            }
        )
        url = f"https://api.stlouisfed.org/fred/series/observations?{query}"
        data = http_get_json(url, headers={"User-Agent": "fundlist-agent"})

        observations = data.get("observations") or []
        if not observations:
            continue

        latest = observations[-1]
        date = latest.get("date")
        value = latest.get("value")
        out.append(
            to_item(
                source="fred",
                category="macro_series",
                symbol=series_id,
                title=f"{series_id} latest value: {value}",
                url=f"https://fred.stlouisfed.org/series/{series_id}",
                published_at=f"{date}T00:00:00+00:00" if date else None,
                payload={"series_id": series_id, "observation": latest},
            )
        )

    return out

