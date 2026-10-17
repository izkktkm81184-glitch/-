"""環境変数 (.env) から設定を読み込む."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _str(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _bool(key: str, default: bool) -> bool:
    v = os.getenv(key)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(key: str, default: int) -> int:
    v = os.getenv(key)
    return int(v) if v not in (None, "") else default


def _float(key: str, default: float) -> float:
    v = os.getenv(key)
    return float(v) if v not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    # 全般
    data_dir: Path
    dry_run: bool
    marketplaces: tuple[str, ...]

    # Claude (商品ページ文章生成)
    anthropic_model: str
    anthropic_effort: str

    # リサーチ API
    rakuten_app_id: str
    rakuten_access_key: str
    rakuten_search_url: str
    rakuten_ranking_url: str
    yahoo_app_id: str
    yahoo_search_url: str
    research_throttle_sec: float
    exclude_words: tuple[str, ...]

    # 価格・利益
    rakuten_fee_rate: float
    yahoo_fee_rate: float
    default_shipping_jpy: int
    min_profit_rate: float
    min_profit_jpy: int
    undercut_jpy: int
    no_competitor_markup: float
    max_gap_rate: float
    auto_approve_grade: str

    # 画像
    font_path: str
    image_size: int

    # 楽天 RMS
    rakuten_service_secret: str
    rakuten_license_key: str
    rakuten_shop_url: str
    rakuten_cabinet_folder_id: int
    rakuten_cabinet_folder_path: str
    rakuten_genre_id: str
    rakuten_hide_item: bool
    rakuten_rms_base: str

    # Yahoo! ストア API
    yahoo_seller_id: str
    yahoo_client_id: str
    yahoo_client_secret: str
    yahoo_redirect_uri: str
    yahoo_refresh_token: str
    yahoo_public_key_path: str
    yahoo_public_key_version: str
    yahoo_store_api_base: str
    yahoo_store_path: str
    yahoo_product_category: str
    yahoo_display: bool

    # 商品ページ共通
    shop_notes: str

    @property
    def db_path(self) -> Path:
        return self.data_dir / "autolister.db"

    @property
    def output_dir(self) -> Path:
        return self.data_dir / "output"

    @classmethod
    def from_env(cls, env_file: str | None = None) -> "Settings":
        load_dotenv(env_file or ".env", override=False)
        return cls(
            data_dir=Path(_str("AUTOLISTER_DATA_DIR", "./data")),
            dry_run=_bool("DRY_RUN", True),
            marketplaces=tuple(
                m.strip() for m in _str("MARKETPLACES", "rakuten,yahoo").split(",") if m.strip()
            ),
            anthropic_model=_str("ANTHROPIC_MODEL", "claude-opus-5-5"),
            anthropic_effort=_str("ANTHROPIC_EFFORT", "medium"),
            rakuten_app_id=_str("RAKUTEN_APP_ID"),
            rakuten_access_key=_str("RAKUTEN_ACCESS_KEY"),
            rakuten_search_url=_str(
                "RAKUTEN_SEARCH_URL",
                "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20220601",
            ),
            rakuten_ranking_url=_str(
                "RAKUTEN_RANKING_URL",
                "https://app.rakuten.co.jp/services/api/IchibaItem/Ranking/20220601",
            ),
            yahoo_app_id=_str("YAHOO_APP_ID"),
            yahoo_search_url=_str(
                "YAHOO_SEARCH_URL",
                "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch",
            ),
            research_throttle_sec=_float("RESEARCH_THROTTLE_SEC", 1.1),
            exclude_words=tuple(
                w.strip()
                for w in _str("EXCLUDE_WORDS", "中古,USED,ユーズド,訳あり,ジャンク").split(",")
                if w.strip()
            ),
            rakuten_fee_rate=_float("RAKUTEN_FEE_RATE", 0.12),
            yahoo_fee_rate=_float("YAHOO_FEE_RATE", 0.10),
            default_shipping_jpy=_int("DEFAULT_SHIPPING_JPY", 1000),
            min_profit_rate=_float("MIN_PROFIT_RATE", 0.10),
            min_profit_jpy=_int("MIN_PROFIT_JPY", 1000),
            undercut_jpy=_int("UNDERCUT_JPY", 10),
            no_competitor_markup=_float("NO_COMPETITOR_MARKUP", 1.15),
            max_gap_rate=_float("MAX_GAP_RATE", 0.05),
            auto_approve_grade=_str("AUTO_APPROVE_GRADE", ""),
            font_path=_str("FONT_PATH"),
            image_size=_int("IMAGE_SIZE", 1200),
            rakuten_service_secret=_str("RAKUTEN_SERVICE_SECRET"),
            rakuten_license_key=_str("RAKUTEN_LICENSE_KEY"),
            rakuten_shop_url=_str("RAKUTEN_SHOP_URL"),
            rakuten_cabinet_folder_id=_int("RAKUTEN_CABINET_FOLDER_ID", 0),
            rakuten_cabinet_folder_path=_str("RAKUTEN_CABINET_FOLDER_PATH", "").strip("/"),
            rakuten_genre_id=_str("RAKUTEN_GENRE_ID"),
            rakuten_hide_item=_bool("RAKUTEN_HIDE_ITEM", True),
            rakuten_rms_base=_str("RAKUTEN_RMS_BASE", "https://api.rms.rakuten.co.jp"),
            yahoo_seller_id=_str("YAHOO_SELLER_ID"),
            yahoo_client_id=_str("YAHOO_CLIENT_ID"),
            yahoo_client_secret=_str("YAHOO_CLIENT_SECRET"),
            yahoo_redirect_uri=_str("YAHOO_REDIRECT_URI", "oob"),
            yahoo_refresh_token=_str("YAHOO_REFRESH_TOKEN"),
            yahoo_public_key_path=_str("YAHOO_PUBLIC_KEY_PATH"),
            yahoo_public_key_version=_str("YAHOO_PUBLIC_KEY_VERSION"),
            yahoo_store_api_base=_str(
                "YAHOO_STORE_API_BASE",
                "https://circus.shopping.yahooapis.jp/ShoppingWebService/V1",
            ),
            yahoo_store_path=_str("YAHOO_STORE_PATH"),
            yahoo_product_category=_str("YAHOO_PRODUCT_CATEGORY"),
            yahoo_display=_bool("YAHOO_DISPLAY", False),
            shop_notes=_str(
                "SHOP_NOTES",
                "モニターの発色により実物と色味が異なる場合がございます。"
                "|お客様都合による返品・交換はお受けできません。",
            ),
        )
