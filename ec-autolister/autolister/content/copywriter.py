"""Claude で商品名・キャッチコピー・説明文を生成する."""

from __future__ import annotations

import json

import anthropic

from ..config import Settings
from ..models import ListingCopy, ResearchResult, SourceProduct

SYSTEM_PROMPT = """\
あなたは楽天市場とYahoo!ショッピングに出店しているECショップの商品ページ担当者です。
渡された商品情報だけを根拠に、購入を検討している人に伝わる商品ページ文章を作成してください。

守ること:
- 素材・機能・サイズ感など、渡されていない情報は書かない (推測で仕様を作らない)。仕様表も渡された情報のみ。
- 「最安」「日本一」「No.1」「絶対」「完全」「100%」「公式」など、根拠を示せない最上級表現・断定表現は使わない。
- ブランドの正規取扱店・公式ショップであるかのような表現は使わない。
- 商品名は検索されやすい語 (ブランド名・型番・商品種別・カラー・サイズ) を前方に並べ、記号の多用や無関係なキーワードの羅列はしない。
- 各フィールドの文字数上限を必ず守る (全角換算)。
"""


def build_prompt(src: SourceProduct, research: ResearchResult | None) -> str:
    info = {
        "商品名": src.name,
        "ブランド": src.brand,
        "型番": src.model_no,
        "カテゴリ": src.category,
        "カラー": src.color,
        "サイズ展開": src.sizes,
        "状態": src.condition,
        "JAN": src.jan,
        "補足": src.notes,
    }
    info = {k: v for k, v in info.items() if v}
    lines = ["# 商品情報", json.dumps(info, ensure_ascii=False, indent=2)]
    if research:
        names = [i.name for st in research.stats.values() for i in st.items[:5]]
        if names:
            lines += [
                "",
                "# 参考: 競合商品の商品名 (キーワード選定の参考のみ。文章をコピーしないこと)",
                *[f"- {n}" for n in names],
            ]
    lines += ["", "上記の商品の楽天市場・Yahoo!ショッピング用の商品ページ文章を作成してください。"]
    return "\n".join(lines)


class Copywriter:
    def __init__(self, s: Settings, client: anthropic.Anthropic | None = None):
        self.s = s
        self.client = client or anthropic.Anthropic()

    def write(self, src: SourceProduct, research: ResearchResult | None) -> ListingCopy:
        schema = ListingCopy.model_json_schema()
        with self.client.beta.messages.stream(
            model=self.s.anthropic_model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_prompt(src, research)}],
            output_config={
                "effort": self.s.anthropic_effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            # 安全性フィルタで拒否された場合にサーバー側で別モデルへ自動フォールバック
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            msg = stream.get_final_message()

        if msg.stop_reason == "refusal":
            raise RuntimeError(f"Claude が生成を拒否しました: {msg.stop_details}")
        if msg.stop_reason == "max_tokens":
            raise RuntimeError("出力が max_tokens に達し途中で切れました")
        text = "".join(b.text for b in msg.content if b.type == "text")
        return ListingCopy.model_validate_json(text)
