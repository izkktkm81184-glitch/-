"""出品処理の共通部品 (ドライラン時のリクエスト記録など)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def slug(s: str, allow: str = "", max_len: int = 32) -> str:
    """モールのコード用に半角英数小文字 (+ allow の記号) へ正規化."""
    out = re.sub(rf"[^a-z0-9{re.escape(allow)}]", "", s.lower())
    return out[:max_len]


class Recorder:
    """API リクエスト内容をファイルに残す。ドライラン時は実際には送信しない."""

    def __init__(self, out_dir: Path, dry_run: bool):
        self.dir = out_dir / "requests"
        self.dry_run = dry_run
        self._n = 0

    def record(self, name: str, payload: Any) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._n += 1
        path = self.dir / f"{self._n:02d}_{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
