"""パイプライン全体で使うデータモデル."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SourceProduct(BaseModel):
    """仕入れ候補 (CSV の1行)."""

    sku: str
    name: str
    brand: str = ""
    model_no: str = ""
    category: str = ""
    color: str = ""
    sizes: list[str] = Field(default_factory=list)
    condition: str = "新品"
    cost_jpy: int
    shipping_jpy: int | None = None
    stock: int = 1
    image_urls: list[str] = Field(default_factory=list)
    jan: str = ""
    notes: str = ""
    search_keyword: str = ""
    rakuten_genre_id: str = ""
    yahoo_product_category: str = ""

    def keyword(self) -> str:
        if self.search_keyword:
            return self.search_keyword
        kw = " ".join(p for p in (self.brand, self.model_no) if p)
        return kw or self.name


class CompetitorItem(BaseModel):
    marketplace: str
    name: str
    price: int
    review_count: int = 0
    shop: str = ""
    url: str = ""


class MarketStats(BaseModel):
    marketplace: str
    keyword: str
    total_hits: int = 0
    items: list[CompetitorItem] = Field(default_factory=list)

    @property
    def prices(self) -> list[int]:
        return sorted(i.price for i in self.items if i.price > 0)

    @property
    def min_price(self) -> int | None:
        p = self.prices
        return p[0] if p else None

    @property
    def median_price(self) -> int | None:
        p = self.prices
        if not p:
            return None
        mid = len(p) // 2
        return p[mid] if len(p) % 2 else (p[mid - 1] + p[mid]) // 2

    @property
    def review_total(self) -> int:
        return sum(i.review_count for i in self.items)


class PriceDecision(BaseModel):
    marketplace: str
    price: int
    fee: int
    shipping: int
    cost: int
    profit: int
    profit_rate: float  # 利益 / 仕入原価 (POIZON Calc と同じ定義)
    competitive: bool  # 最安値以下で出せているか
    gap_rate: float = 0.0  # 競合最安値に対して何%高いか (競合なし・最安以下は 0)


class ResearchResult(BaseModel):
    sku: str
    stats: dict[str, MarketStats]
    decisions: dict[str, PriceDecision]
    demand: str  # high / mid / low / none
    grade: str  # S / A / B / C / D
    reasons: list[str] = Field(default_factory=list)


# ---- Claude が生成する商品ページ文章 (structured outputs のスキーマ) ----


class SpecRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    value: str


class ListingCopy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rakuten_title: str = Field(description="楽天 商品名。全角127文字以内。検索キーワードを前方に")
    rakuten_catch_copy: str = Field(description="楽天 キャッチコピー。全角87文字以内")
    yahoo_title: str = Field(description="Yahoo 商品名。全角75文字以内")
    yahoo_headline: str = Field(description="Yahoo キャッチコピー。全角30文字以内")
    meta_description: str = Field(description="検索結果用の説明。全角80文字以内")
    lead: str = Field(description="商品ページ冒頭の紹介文。2〜3文")
    selling_points: list[str] = Field(description="訴求ポイント。3〜5個、各全角30文字以内")
    specs: list[SpecRow] = Field(description="仕様表。与えられた情報のみ")
    description: str = Field(description="商品説明本文。改行区切り、全角400文字程度")
    search_keywords: list[str] = Field(description="検索キーワード候補 5〜10個")


class ListingContent(BaseModel):
    """文章 + 生成済み HTML + 画像 + 検査結果."""

    copy_text: ListingCopy
    rakuten_pc_description: str
    rakuten_sp_description: str
    rakuten_sales_description: str
    yahoo_explanation: str
    yahoo_caption: str
    yahoo_sp_additional: str
    images: list[str]  # ローカルの画像パス (先頭がメイン画像)
    warnings: list[str] = Field(default_factory=list)
