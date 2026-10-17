from autolister import pricing
from autolister.models import CompetitorItem, MarketStats, PriceDecision


def stats(*prices, reviews=0):
    return MarketStats(
        marketplace="rakuten",
        keyword="k",
        items=[CompetitorItem(marketplace="rakuten", name="x", price=p, review_count=reviews) for p in prices],
    )


def test_undercuts_lowest_competitor(settings):
    d = pricing.decide_price("rakuten", stats(20000, 18000, 25000), 10000, 1000, settings)
    assert d.price == 18000 - settings.undercut_jpy
    assert d.competitive
    assert d.profit == d.price - d.fee - 1000 - 10000


def test_never_below_floor(settings):
    d = pricing.decide_price("rakuten", stats(9000), 10000, 1000, settings)
    floor = pricing.floor_price(10000, 1000, settings.rakuten_fee_rate, settings)
    assert d.price == floor
    assert not d.competitive
    assert d.gap_rate == round(d.price / 9000 - 1, 4)
    assert d.profit >= settings.min_profit_jpy
    assert d.profit_rate >= settings.min_profit_rate


def test_no_competitor_uses_markup(settings):
    d = pricing.decide_price("yahoo", stats(), 10000, 1000, settings)
    assert d.price > pricing.floor_price(10000, 1000, settings.yahoo_fee_rate, settings)
    assert d.price % 10 == 0


def test_grades():
    def dec(rate, competitive=True):
        return PriceDecision(marketplace="r", price=1, fee=0, shipping=0, cost=100,
                             profit=int(rate * 100), profit_rate=rate, competitive=competitive)

    assert pricing.grade(dec(0.25), "high") == "S"
    assert pricing.grade(dec(0.25, competitive=False), "high") == "B"
    far = dec(0.25, competitive=False).model_copy(update={"gap_rate": 0.2})
    assert pricing.grade(far, "high") == "D"
    assert pricing.grade(dec(0.16), "mid") == "A"
    assert pricing.grade(dec(0.06), "none") == "C"
    assert pricing.grade(dec(-0.1), "high") == "D"
    assert pricing.grade_at_least("S", "A") and not pricing.grade_at_least("B", "A")
    assert not pricing.grade_at_least("S", "")
