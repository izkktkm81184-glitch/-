"""SQLite で商品ごとの進捗 (ステータス) と各ステージの結果を保持する."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# new → researched → approved → built → published
#             ↘ skipped (利益が出ない)   ↘ rejected (手動却下)   * → error
STATUSES = ("new", "researched", "skipped", "approved", "rejected", "built", "published", "error")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    sku          TEXT PRIMARY KEY,
    status       TEXT NOT NULL DEFAULT 'new',
    grade        TEXT,
    source_json  TEXT NOT NULL,
    research_json TEXT,
    content_json TEXT,
    publish_json TEXT,
    error        TEXT,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ---- products ----

    def upsert_source(self, sku: str, source: dict[str, Any]) -> None:
        """仕入れ情報を登録。既存なら情報だけ更新しステータスは維持."""
        self.conn.execute(
            """INSERT INTO products (sku, source_json, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(sku) DO UPDATE SET source_json=excluded.source_json,
               updated_at=excluded.updated_at""",
            (sku, json.dumps(source, ensure_ascii=False), _now()),
        )
        self.conn.commit()

    def update(self, sku: str, **fields: Any) -> None:
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError(f"unknown status: {fields['status']}")
        cols, vals = [], []
        for k, v in fields.items():
            if k.endswith("_json") and not isinstance(v, str) and v is not None:
                v = json.dumps(v, ensure_ascii=False)
            cols.append(f"{k}=?")
            vals.append(v)
        cols.append("updated_at=?")
        vals.append(_now())
        self.conn.execute(f"UPDATE products SET {', '.join(cols)} WHERE sku=?", (*vals, sku))
        self.conn.commit()

    def get(self, sku: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM products WHERE sku=?", (sku,)).fetchone()

    def by_status(self, *statuses: str) -> list[sqlite3.Row]:
        q = "SELECT * FROM products"
        if statuses:
            q += f" WHERE status IN ({','.join('?' * len(statuses))})"
        return list(self.conn.execute(q + " ORDER BY sku", statuses))

    def iter_rows(self, skus: list[str] | None, *statuses: str) -> Iterator[sqlite3.Row]:
        rows = self.by_status(*statuses)
        if skus:
            wanted = set(skus)
            rows = [r for r in rows if r["sku"] in wanted]
        yield from rows

    # ---- kv (トークン等) ----

    def kv_get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def kv_set(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()
