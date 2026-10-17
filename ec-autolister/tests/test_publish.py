import json

import httpx
import respx

from autolister.models import ListingContent, PriceDecision, SourceProduct
from autolister.publish.rakuten_rms import RakutenPublisher, manage_number
from autolister.publish.yahoo_store import YahooPublisher, image_file_name


def _content(copy, imgs):
    return ListingContent(copy_text=copy, rakuten_pc_description="pc", rakuten_sp_description="sp",
                          rakuten_sales_description="sales", yahoo_explanation="ex", yahoo_caption="cap",
                          yahoo_sp_additional="spa", images=imgs)


DEC = PriceDecision(marketplace="x", price=15490, fee=1, shipping=1000, cost=10000, profit=1, profit_rate=0.1,
                    competitive=True)
SRC = SourceProduct(sku="NK-DD1391", name="ダンク", cost_jpy=10000, sizes=["27.0cm", "27.5cm"], stock=1)


def test_rakuten_dry_run_payload(settings, listing_copy, tmp_path):
    out = tmp_path / "o"
    res = RakutenPublisher(settings).publish(SRC, _content(listing_copy, ["a.jpg", "b.jpg"]), DEC, out, True)
    assert res["manageNumber"] == "nk-dd1391"
    files = sorted(p.name for p in (out / "rakuten" / "requests").iterdir())
    assert files[0].startswith("01_cabinet_insert_nkdd1391_1.jpg")
    body = json.loads(next((out / "rakuten" / "requests").glob("*item_upsert.json")).read_text())["body"]
    assert body["hideItem"] is True
    assert body["images"][0]["location"] == "/auto/nkdd1391_1.jpg"
    assert set(body["variants"]) == {"nk-dd1391-270cm", "nk-dd1391-275cm"}
    assert body["variants"]["nk-dd1391-270cm"]["standardPrice"] == "15490"
    assert len(list((out / "rakuten" / "requests").glob("*inventory_*"))) == 2


@respx.mock
def test_rakuten_live_calls(settings, listing_copy, tmp_path, monkeypatch):
    s = settings.__class__(**{**settings.__dict__, "rakuten_service_secret": "sec", "rakuten_license_key": "lic"})
    img = tmp_path / "a.jpg"
    img.write_bytes(b"jpg")
    cab = respx.post(f"{s.rakuten_rms_base}/es/1.0/cabinet/file/insert").mock(
        return_value=httpx.Response(200, text="<result><FileId>123</FileId></result>"))
    item = respx.put(f"{s.rakuten_rms_base}/es/2.0/items/manage-numbers/nk-dd1391").mock(
        return_value=httpx.Response(204))
    inv = respx.put(url__startswith=f"{s.rakuten_rms_base}/es/2.1/inventories/").mock(
        return_value=httpx.Response(204))
    RakutenPublisher(s).publish(SRC, _content(listing_copy, [str(img)]), DEC, tmp_path / "o", False)
    assert cab.called and item.called and inv.call_count == 2
    assert item.calls.last.request.headers["Authorization"].startswith("ESA ")


def test_yahoo_dry_run_payload(settings, store, listing_copy, tmp_path):
    out = tmp_path / "o"
    pub = YahooPublisher(settings, store)
    res = pub.publish(SRC, _content(listing_copy, ["a.jpg", "b.jpg"]), DEC, out, True)
    assert res["item_code"] == "nk-dd1391"
    reqs = out / "yahoo" / "requests"
    form = json.loads(next(reqs.glob("*editItem.json")).read_text())
    assert form["price"] == "15490" and form["display"] == "0"
    assert form["options"] == "サイズ:27.0cm#27.5cm"
    assert "サイズ:27.5cm=nk-dd1391-275cm" in form["subcodes"]
    assert (reqs / "03_uploadItemImage_nk-dd1391_1.jpg.json").exists()
    assert image_file_name("abc", 1) == "abc.jpg"


@respx.mock
def test_yahoo_refreshes_token(settings, store):
    s = settings.__class__(**{**settings.__dict__, "yahoo_refresh_token": "r", "yahoo_client_id": "c",
                              "yahoo_client_secret": "x"})
    respx.post("https://auth.login.yahoo.co.jp/yconnect/v2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "AT", "expires_in": 3600}))
    pub = YahooPublisher(s, store)
    assert pub.access_token() == "AT"
    assert pub.access_token() == "AT"  # 2回目はキャッシュ
    assert respx.calls.call_count == 1
