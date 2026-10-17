"""検索結果から販売価格・利益・ランクを算出する."""

from __future__ import annotations

from typing import Protocol

from ..config import Settings
from ..models import MarketStats, ResearchResult, SourceProduct
from .. import pricing
from .search import filter_items


class Searcher(Protocol):
    marketplace: str

    def search(self, keyword: str, hits: int = 30) -> MarketStats: ...


def analyze(src: SourceProduct, searchers: list[Searcher], s: Settings) -> ResearchResult:
    keyword = src.keyword()
    shipping = src.shipping_jpy if src.shipping_jpy is not None else s.default_shipping_jpy
    stats: dict[str, MarketStats] = {}
    decisions = {}
    reasons: list[str] = []

    for searcher in searchers:
        mp = searcher.marketplace
        st = searcher.search(keyword)
        st.items = filter_items(st.items, src.model_no, s.exclude_words)
        stats[mp] = st
        d = pricing.decide_price(mp, st, src.cost_jpy, shipping, s)
        decisions[mp] = d
        lowest = f"¥{st.min_price:,}" if st.min_price else "競合なし"
        reasons.append(
            f"[{mp}] 競合{len(st.items)}件 最安{lowest} → 販売¥{d.price:,} "
            f"利益¥{d.profit:,} ({d.profit_rate * 100:.1f}%)"
            + ("" if d.competitive else f" ※最安値より{d.gap_rate * 100:.1f}%高い")
        )

    demand = pricing.demand_level(stats)
    # 最安値との差が小さい中で利益が最大のモールを基準にランク付け
    best = min(decisions.values(), key=lambda d: (d.gap_rate > s.max_gap_rate, -d.profit), default=None)
    g = pricing.grade(best, demand, s.max_gap_rate)
    reasons.append(f"需要: {demand} / ランク: {g}")
    return ResearchResult(
        sku=src.sku, stats=stats, decisions=decisions, demand=demand, grade=g, reasons=reasons
    )
