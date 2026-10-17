import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from autolister.config import Settings
from autolister.db import Store
from autolister.models import CompetitorItem, ListingCopy, MarketStats, SpecRow


@pytest.fixture
def settings(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("RAKUTEN_", "YAHOO_", "DRY_RUN", "AUTO_APPROVE")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AUTOLISTER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RESEARCH_THROTTLE_SEC", "0")
    monkeypatch.setenv("RAKUTEN_SHOP_URL", "myshop")
    monkeypatch.setenv("RAKUTEN_CABINET_FOLDER_PATH", "auto")
    monkeypatch.setenv("YAHOO_SELLER_ID", "seller")
    return Settings.from_env(str(tmp_path / "nonexistent.env"))


@pytest.fixture
def store(settings):
    s = Store(settings.db_path)
    yield s
    s.close()


@pytest.fixture
def photo(tmp_path) -> Path:
    img = Image.new("RGB", (800, 600), "white")
    ImageDraw.Draw(img).ellipse([200, 150, 600, 450], fill=(30, 60, 200))
    p = tmp_path / "photo.png"
    img.save(p)
    return p


@pytest.fixture
def listing_copy() -> ListingCopy:
    return ListingCopy(
        rakuten_title="Nike ダンク ロー レトロ DD1391-100 ホワイト ブラック スニーカー メンズ",
        rakuten_catch_copy="定番のモノトーンカラー",
        yahoo_title="Nike ダンク ロー レトロ DD1391-100 ホワイト ブラック",
        yahoo_headline="定番のモノトーン",
        meta_description="Nike ダンク ロー レトロ ホワイト/ブラック",
        lead="定番のモノトーン配色。どんな服にも合わせやすい一足です。",
        selling_points=["合わせやすいモノトーン配色", "新品・未使用品", "サイズ豊富"],
        specs=[SpecRow(label="ブランド", value="Nike"), SpecRow(label="型番", value="DD1391-100")],
        description="定番モデルです。\n普段使いにおすすめです。",
        search_keywords=["ダンク", "DD1391-100"],
    )


class FakeSearcher:
    def __init__(self, marketplace, prices, reviews=10):
        self.marketplace = marketplace
        self.prices = prices
        self.reviews = reviews

    def search(self, keyword, hits=30):
        return MarketStats(
            marketplace=self.marketplace,
            keyword=keyword,
            total_hits=len(self.prices),
            items=[
                CompetitorItem(marketplace=self.marketplace, name=f"Nike DD1391-100 {i}", price=p,
                               review_count=self.reviews)
                for i, p in enumerate(self.prices)
            ],
        )


class FakeCopywriter:
    def __init__(self, copy):
        self.copy = copy
        self.calls = 0

    def write(self, src, research):
        self.calls += 1
        return self.copy
