from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Item:
    source: str
    category: str
    symbol: str
    title: str
    url: str
    published_at: str
    payload_json: str

    @property
    def fingerprint(self) -> str:
        raw = "|".join(
            [
                self.source,
                self.category,
                self.symbol,
                self.title,
                self.url,
                self.published_at,
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def to_item(
    source: str,
    category: str,
    symbol: str,
    title: str,
    url: str,
    published_at: Optional[str],
    payload: Dict[str, Any],
) -> Item:
    return Item(
        source=source,
        category=category,
        symbol=symbol or "-",
        title=(title or "(untitled)").strip()[:500],
        url=(url or "").strip(),
        published_at=published_at or now_utc_iso(),
        payload_json=json.dumps(payload, ensure_ascii=False),
    )

