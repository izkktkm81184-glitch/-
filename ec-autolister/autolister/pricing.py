"""販売価格の決定・利益計算・ランク判定."""

from __future__ import annotations

import math

from .config import Settings
from .models import MarketStats, PriceDecision

GRADE_ORDER = "SABCD"


def _ceil10(x: float) -> int:
    return int(math.ceil(x / 10.0) * 10)


def fee_rate_for(marketplace: str, s: Settings) -> float:
    return {"rakuten": s.rakuten_fee_rate, "yahoo": s.yahoo_fee_rate}[marketplace]


def profit_of(price: int, cost: int, shipping: int, fee_rate: float) -> tuple[int, int]:
    fee = int(math.ceil(price * fee_rate))
    return fee, price - fee - shipping - cost


def floor_price(cost: int, shipping: int, fee_rate: float, s: Settings) -> int:
    """最低利益 (率・額の両方) を満たす最低販売価格."""
    by_rate = (cost * (1 + s.min_profit_rate) + shipping) / (1 - fee_rate)
    by_yen = (cost + s.min_profit_jpy + shipping) / (1 - fee_rate)
    return _ceil10(max(by_rate, by_yen))


def decide_price(
    marketplace: str, stats: MarketStats | None, cost: int, shipping: int, s: Settings
) -> PriceDecision:
    """競合最安値から少し下げた価格を基本に、最低利益ラインを割らない価格を返す."""
    rate = fee_rate_for(marketplace, s)
    floor = floor_price(cost, shipping, rate, s)
    lowest = stats.min_price if stats else None
    if lowest is None:
        price = _ceil10(floor * s.no_competitor_markup)
        competitive = True
    else:
        target = lowest - s.undercut_jpy
        price = max(target, floor)
        competitive = price <= lowest
    gap_rate = round(max(price / lowest - 1, 0.0), 4) if lowest else 0.0
    fee, profit = profit_of(price, cost, shipping, rate)
    return PriceDecision(
        marketplace=marketplace,
        price=price,
        fee=fee,
        shipping=shipping,
        cost=cost,
        profit=profit,
        profit_rate=round(profit / cost, 4) if cost else 0.0,
        competitive=competitive,
        gap_rate=gap_rate,
    )


def demand_level(stats: dict[str, MarketStats]) -> str:
    """競合商品のレビュー件数合計を需要の目安にする."""
    reviews = sum(st.review_total for st in stats.values())
    hits = sum(len(st.items) for st in stats.values())
    if reviews >= 100:
        return "high"
    if reviews >= 20:
        return "mid"
    if reviews >= 3 or hits >= 3:
        return "low"
    return "none"


def grade(best: PriceDecision | None, demand: str, max_gap_rate: float = 0.05) -> str:
    """POIZON Calc のランク (利益率 × 回転) を需要レベルに置き換えた判定.

    最低利益を確保すると競合最安値より max_gap_rate 以上高くなる場合は売れないので D.
    """
    if best is None or best.profit <= 0 or best.gap_rate > max_gap_rate:
        return "D"
    pr = best.profit_rate * 100
    d = {"high": 3, "mid": 2, "low": 1, "none": 0}[demand]
    if best.competitive and pr >= 20 and d >= 3:
        return "S"
    if best.competitive and pr >= 15 and d >= 2:
        return "A"
    if pr >= 10 and d >= 1:
        return "B"
    if pr >= 5:
        return "C"
    return "D"


def grade_at_least(g: str, threshold: str) -> bool:
    if not threshold:
        return False
    return GRADE_ORDER.index(g) <= GRADE_ORDER.index(threshold)
