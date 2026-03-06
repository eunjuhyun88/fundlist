from __future__ import annotations

import os
import sqlite3
from typing import List, Optional, Sequence

from .models import Item


def ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


class SQLiteStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        ensure_parent_dir(db_path)
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS investment_items (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              source TEXT NOT NULL,
              category TEXT NOT NULL,
              symbol TEXT NOT NULL,
              title TEXT NOT NULL,
              url TEXT NOT NULL,
              published_at TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              fingerprint TEXT NOT NULL UNIQUE,
              collected_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_items_source_time ON investment_items(source, published_at DESC)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_items_symbol_time ON investment_items(symbol, published_at DESC)"
        )
        self.conn.commit()

    def insert_items(self, items: Sequence[Item]) -> int:
        sql = """
            INSERT OR IGNORE INTO investment_items (
              source, category, symbol, title, url, published_at, payload_json, fingerprint
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        before = self.conn.total_changes
        with self.conn:
            self.conn.executemany(
                sql,
                [
                    (
                        item.source,
                        item.category,
                        item.symbol,
                        item.title,
                        item.url,
                        item.published_at,
                        item.payload_json,
                        item.fingerprint,
                    )
                    for item in items
                ],
            )
        return self.conn.total_changes - before

    def list_items(
        self,
        limit: int = 30,
        source: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> List[sqlite3.Row]:
        self.conn.row_factory = sqlite3.Row
        where_parts = []
        args: List[object] = []

        if source:
            where_parts.append("source = ?")
            args.append(source)
        if symbol:
            where_parts.append("symbol = ?")
            args.append(symbol)

        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        sql = f"""
            SELECT id, source, category, symbol, title, url, published_at, collected_at
            FROM investment_items
            {where_sql}
            ORDER BY published_at DESC, id DESC
            LIMIT ?
        """
        args.append(limit)
        cur = self.conn.execute(sql, tuple(args))
        return cur.fetchall()

