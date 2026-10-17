import httpx
import respx

from autolister.research.analyzer import analyze
from autolister.research.search import RakutenSearch, YahooSearch, filter_items
from autolister.models import CompetitorItem, SourceProduct

from conftest import FakeSearcher


@respx.mock
def test_rakuten_search_parses_v2(settings):
    respx.get(settings.rakuten_search_url).mock(
        return_value=httpx.Response(200, json={"count": 2, "Items": [
            {"itemName": "Nike DD1391-100", "itemPrice": 15000, "reviewCount": 4, "shopName": "A", "itemUrl": "u"},
            {"itemName": "Nike DD1391-100 中古", "itemPrice": 9000, "reviewCount": 0, "shopName": "B", "itemUrl": "u"},
        ]})
    )
    st = RakutenSearch(settings).search("Nike DD1391-100")
    assert st.total_hits == 2 and st.min_price == 9000
    assert respx.calls.last.request.url.params["sort"] == "+itemPrice"


@respx.mock
def test_yahoo_search_parses_v3(settings):
    respx.get(settings.yahoo_search_url).mock(
        return_value=httpx.Response(200, json={"totalResultsAvailable": 1, "hits": [
            {"name": "Nike DD1391-100", "price": 14800, "review": {"count": 7}, "seller": {"name": "S"}, "url": "u"},
        ]})
    )
    st = YahooSearch(settings).search("Nike")
    assert st.items[0].price == 14800 and st.items[0].review_count == 7


def test_filter_items_requires_model_no_and_drops_used():
    items = [
        CompetitorItem(marketplace="r", name="NIKE DD1391 100 ダンク", price=1),
        CompetitorItem(marketplace="r", name="Nike DD1391-100 中古", price=1),
        CompetitorItem(marketplace="r", name="Nike FD1234-001", price=1),
    ]
    out = filter_items(items, "DD1391-100", ("中古",))
    assert [i.name for i in out] == ["NIKE DD1391 100 ダンク"]


def test_analyze_combines_marketplaces(settings):
    src = SourceProduct(sku="A1", name="ダンク", brand="Nike", model_no="DD1391-100", cost_jpy=10000)
    res = analyze(src, [FakeSearcher("rakuten", [16000, 17000], reviews=60),
                        FakeSearcher("yahoo", [15500], reviews=60)], settings)
    assert set(res.decisions) == {"rakuten", "yahoo"}
    assert res.demand == "high"
    assert res.decisions["yahoo"].price == 15500 - settings.undercut_jpy
    assert res.grade in "SAB"
