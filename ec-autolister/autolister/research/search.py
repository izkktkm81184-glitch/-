"""楽天市場 商品検索API / Yahoo!ショッピング 商品検索API (v3) のクライアント."""

from __future__ import annotations

import re
import time

import httpx

from ..config import Settings
from ..models import CompetitorItem, MarketStats


def _norm(s: str) -> str:
    """型番照合用: 記号・空白を除いて大文字化."""
    return re.sub(r"[\s\-_/・.]", "", s).upper()


def filter_items(
    items: list[CompetitorItem], model_no: str, exclude_words: tuple[str, ...]
) -> list[CompetitorItem]:
    """型番を含まない商品や中古品などを競合から外す."""
    key = _norm(model_no) if model_no else ""
    out = []
    for it in items:
        if any(w.lower() in it.name.lower() for w in exclude_words):
            continue
        if key and key not in _norm(it.name):
            continue
        out.append(it)
    return out


class _Throttle:
    def __init__(self, interval: float):
        self.interval = interval
        self._last = 0.0

    def wait(self) -> None:
        delta = time.monotonic() - self._last
        if delta < self.interval:
            time.sleep(self.interval - delta)
        self._last = time.monotonic()


class RakutenSearch:
    marketplace = "rakuten"

    def __init__(self, s: Settings, client: httpx.Client | None = None):
        self.s = s
        self.client = client or httpx.Client(timeout=20)
        self.throttle = _Throttle(s.research_throttle_sec)

    def _params(self, **extra) -> dict:
        p = {"applicationId": self.s.rakuten_app_id, "format": "json", "formatVersion": 2}
        if self.s.rakuten_access_key:
            p["accessKey"] = self.s.rakuten_access_key
        p.update(extra)
        return p

    def search(self, keyword: str, hits: int = 30) -> MarketStats:
        self.throttle.wait()
        r = self.client.get(
            self.s.rakuten_search_url,
            params=self._params(keyword=keyword, hits=hits, sort="+itemPrice", availability=1),
        )
        r.raise_for_status()
        data = r.json()
        items = [
            CompetitorItem(
                marketplace=self.marketplace,
                name=it.get("itemName", ""),
                price=int(it.get("itemPrice") or 0),
                review_count=int(it.get("reviewCount") or 0),
                shop=it.get("shopName", ""),
                url=it.get("itemUrl", ""),
            )
            for it in data.get("Items", [])
        ]
        return MarketStats(
            marketplace=self.marketplace, keyword=keyword, total_hits=int(data.get("count", 0)), items=items
        )

    def ranking(self, genre_id: str = "", page: int = 1) -> list[dict]:
        """売れ筋ランキング (商品発掘用)."""
        self.throttle.wait()
        params = self._params(page=page)
        if genre_id:
            params["genreId"] = genre_id
        r = self.client.get(self.s.rakuten_ranking_url, params=params)
        r.raise_for_status()
        return [
            {
                "rank": it.get("rank"),
                "name": it.get("itemName", ""),
                "price": it.get("itemPrice"),
                "review_count": it.get("reviewCount"),
                "review_average": it.get("reviewAverage"),
                "shop": it.get("shopName", ""),
                "url": it.get("itemUrl", ""),
            }
            for it in r.json().get("Items", [])
        ]


class YahooSearch:
    marketplace = "yahoo"

    def __init__(self, s: Settings, client: httpx.Client | None = None):
        self.s = s
        self.client = client or httpx.Client(timeout=20)
        self.throttle = _Throttle(s.research_throttle_sec)

    def search(self, keyword: str, hits: int = 30) -> MarketStats:
        self.throttle.wait()
        r = self.client.get(
            self.s.yahoo_search_url,
            params={
                "appid": self.s.yahoo_app_id,
                "query": keyword,
                "results": hits,
                "sort": "+price",
                "in_stock": "true",
                "condition": "new",
            },
        )
        r.raise_for_status()
        data = r.json()
        items = [
            CompetitorItem(
                marketplace=self.marketplace,
                name=h.get("name", ""),
                price=int(h.get("price") or 0),
                review_count=int((h.get("review") or {}).get("count") or 0),
                shop=(h.get("seller") or {}).get("name", ""),
                url=h.get("url", ""),
            )
            for h in data.get("hits", [])
        ]
        return MarketStats(
            marketplace=self.marketplace,
            keyword=keyword,
            total_hits=int(data.get("totalResultsAvailable", 0)),
            items=items,
        )
