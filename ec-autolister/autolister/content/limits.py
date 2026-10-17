"""各モールの文字数上限と禁止表現チェック.

文字数は「全角=2, 半角=1」で数える (両モールともバイト換算で上限を定めているため)。
上限値は各モールの最新の仕様書で確認し、変わっていればここを更新すること。
"""

from __future__ import annotations

import unicodedata

from ..models import ListingCopy

# フィールド名 → 上限 (半角換算)
LIMITS: dict[str, int] = {
    "rakuten_title": 255,  # 楽天 商品名 全角127文字
    "rakuten_catch_copy": 174,  # 楽天 キャッチコピー 全角87文字
    "yahoo_title": 150,  # Yahoo 商品名 全角75文字
    "yahoo_headline": 60,  # Yahoo キャッチコピー 全角30文字
    "meta_description": 160,  # Yahoo meta description 全角80文字
}
RAKUTEN_DESCRIPTION_LIMIT = 10240  # PC/SP 商品説明文・販売説明文
YAHOO_EXPLANATION_LIMIT = 1000  # 商品情報 全角500文字
YAHOO_CAPTION_LIMIT = 10000  # 商品説明 (HTML)

# 景品表示法・各モールのガイドラインで問題になりやすい表現
NG_WORDS = (
    "最安",
    "日本一",
    "世界一",
    "No.1",
    "NO.1",
    "ナンバーワン",
    "業界初",
    "業界最",
    "絶対",
    "完全",
    "100%",
    "激安",
    "永久",
    "公式",
)


def width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "FWA" else 1 for c in s)


def truncate(s: str, limit: int) -> str:
    out, w = [], 0
    for c in s:
        cw = width(c)
        if w + cw > limit:
            break
        out.append(c)
        w += cw
    return "".join(out)


def enforce(copy: ListingCopy) -> tuple[ListingCopy, list[str]]:
    """上限超過は切り詰め、NG表現は警告として返す."""
    warnings: list[str] = []
    data = copy.model_dump()
    for field, limit in LIMITS.items():
        if width(data[field]) > limit:
            warnings.append(f"{field} が上限 ({limit}) を超えたため切り詰めました")
            data[field] = truncate(data[field], limit)
    fixed = ListingCopy.model_validate(data)
    warnings += find_ng_words(fixed)
    return fixed, warnings


def find_ng_words(copy: ListingCopy) -> list[str]:
    text = "\n".join(
        [
            copy.rakuten_title,
            copy.rakuten_catch_copy,
            copy.yahoo_title,
            copy.yahoo_headline,
            copy.meta_description,
            copy.lead,
            copy.description,
            *copy.selling_points,
        ]
    )
    return [f"NG表現の可能性: 「{w}」" for w in NG_WORDS if w in text]
