"""リサーチ → 承認 → 画像・商品ページ生成 → 出品 を順に実行する."""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Callable

from . import pricing
from .config import Settings
from .content import limits, page_builder
from .content.copywriter import Copywriter
from .db import Store
from .images import composer
from .models import ListingContent, ResearchResult, SourceProduct
from .publish.base import slug
from .research.analyzer import Searcher, analyze

log = logging.getLogger("autolister")

LIST_FIELDS = ("sizes", "image_urls")
INT_FIELDS = ("cost_jpy", "shipping_jpy", "stock")


def read_sources(path: Path) -> list[SourceProduct]:
    """CSV を読み込む。sizes / image_urls は | 区切り."""
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            data: dict = {}
            for k, v in row.items():
                if k is None:
                    continue
                v = (v or "").strip()
                if k in LIST_FIELDS:
                    data[k] = [x.strip() for x in v.split("|") if x.strip()]
                elif k in INT_FIELDS:
                    if v:
                        data[k] = int(v.replace(",", ""))
                elif v:
                    data[k] = v
            out.append(SourceProduct.model_validate(data))
    return out


class Pipeline:
    def __init__(
        self,
        s: Settings,
        store: Store,
        searchers: list[Searcher] | None = None,
        copywriter: Copywriter | None = None,
        publishers: dict | None = None,
    ):
        self.s = s
        self.store = store
        self._searchers = searchers
        self._copywriter = copywriter
        self._publishers = publishers

    # 外部APIクライアントは使うときに初めて作る (未使用のキーを要求しないため)
    @property
    def searchers(self) -> list[Searcher]:
        if self._searchers is None:
            from .research.search import RakutenSearch, YahooSearch

            avail = {"rakuten": (RakutenSearch, self.s.rakuten_app_id), "yahoo": (YahooSearch, self.s.yahoo_app_id)}
            self._searchers = []
            for mp in self.s.marketplaces:
                cls, key = avail[mp]
                if key:
                    self._searchers.append(cls(self.s))
                else:
                    log.warning("%s の検索APIキーが未設定のためリサーチ対象から外します", mp)
        return self._searchers

    @property
    def copywriter(self) -> Copywriter:
        if self._copywriter is None:
            self._copywriter = Copywriter(self.s)
        return self._copywriter

    @property
    def publishers(self) -> dict:
        if self._publishers is None:
            from .publish.rakuten_rms import RakutenPublisher
            from .publish.yahoo_store import YahooPublisher

            make: dict[str, Callable] = {
                "rakuten": lambda: RakutenPublisher(self.s),
                "yahoo": lambda: YahooPublisher(self.s, self.store),
            }
            self._publishers = {mp: make[mp]() for mp in self.s.marketplaces}
        return self._publishers

    def out_dir(self, sku: str) -> Path:
        d = self.s.output_dir / (slug(sku, allow="-_", max_len=64) or "item")
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _src(row) -> SourceProduct:
        return SourceProduct.model_validate_json(row["source_json"])

    @staticmethod
    def _research(row) -> ResearchResult | None:
        return ResearchResult.model_validate_json(row["research_json"]) if row["research_json"] else None

    def _fail(self, sku: str, stage: str, e: Exception) -> None:
        log.exception("%s: %s で失敗", sku, stage)
        self.store.update(sku, status="error", error=f"{stage}: {e}")

    # ---- 1. 取り込み ----

    def import_csv(self, path: Path) -> int:
        items = read_sources(path)
        for src in items:
            self.store.upsert_source(src.sku, src.model_dump())
        return len(items)

    # ---- 2. リサーチ ----

    def research(self, skus: list[str] | None = None, include_done: bool = False) -> list[ResearchResult]:
        if not self.searchers:
            raise RuntimeError("検索APIキーが未設定です (RAKUTEN_APP_ID / YAHOO_APP_ID)")
        statuses = ("new", "researched", "skipped") if include_done else ("new",)
        results = []
        for row in self.store.iter_rows(skus, *statuses):
            sku = row["sku"]
            try:
                res = analyze(self._src(row), self.searchers, self.s)
            except Exception as e:
                self._fail(sku, "research", e)
                continue
            if res.grade == "D":
                status = "skipped"
            elif pricing.grade_at_least(res.grade, self.s.auto_approve_grade):
                status = "approved"
            else:
                status = "researched"
            self.store.update(
                sku, status=status, grade=res.grade, research_json=res.model_dump_json(), error=None
            )
            log.info("%s: %s → %s", sku, " / ".join(res.reasons), status)
            results.append(res)
        return results

    # ---- 3. 承認 ----

    def set_status(self, skus: list[str], status: str) -> int:
        n = 0
        for sku in skus:
            if self.store.get(sku):
                self.store.update(sku, status=status, error=None)
                n += 1
        return n

    # ---- 4. 画像・商品ページ生成 ----

    def build(self, skus: list[str] | None = None) -> list[str]:
        built = []
        for row in self.store.iter_rows(skus, "approved"):
            sku = row["sku"]
            try:
                self._build_one(row)
            except Exception as e:
                self._fail(sku, "build", e)
                continue
            built.append(sku)
        return built

    def _build_one(self, row) -> ListingContent:
        src, research = self._src(row), self._research(row)
        out = self.out_dir(src.sku)

        copy = self.copywriter.write(src, research)
        copy, warnings = limits.enforce(copy)

        photos = [composer.fetch_image(ref) for ref in src.image_urls]
        header = " ".join(p for p in (src.brand, src.model_no) if p) or src.name
        notes = [n for n in self.s.shop_notes.split("|") if n]
        images = composer.compose(
            photos,
            copy,
            header,
            notes,
            out,
            lambda i: page_builder.cabinet_file_name(src.sku, i),
            size=self.s.image_size,
            font_path=self.s.font_path,
        )

        content = page_builder.build(src, copy, images, research, self.s, warnings)
        (out / "content.json").write_text(content.model_dump_json(indent=2), encoding="utf-8")
        preview = page_builder.write_preview(src, content, research, out)
        self.store.update(src.sku, status="built", content_json=content.model_dump_json(), error=None)
        log.info("%s: 生成完了 %s (警告 %d件)", src.sku, preview, len(warnings))
        return content

    # ---- 5. 出品 ----

    def publish(self, skus: list[str] | None = None, force: bool = False) -> dict[str, dict]:
        dry = self.s.dry_run
        done: dict[str, dict] = {}
        for row in self.store.iter_rows(skus, "built"):
            sku = row["sku"]
            src, research = self._src(row), self._research(row)
            content = ListingContent.model_validate_json(row["content_json"])
            if content.warnings and not force:
                log.warning("%s: 要確認の警告があるため出品をスキップ (--force で強行): %s", sku, content.warnings)
                continue
            shipping = src.shipping_jpy if src.shipping_jpy is not None else self.s.default_shipping_jpy
            results = json.loads(row["publish_json"] or "{}")
            try:
                for mp, pub in self.publishers.items():
                    decision = (research.decisions.get(mp) if research else None) or pricing.decide_price(
                        mp, None, src.cost_jpy, shipping, self.s
                    )
                    results[mp] = pub.publish(src, content, decision, self.out_dir(sku), dry)
            except Exception as e:
                self.store.update(sku, publish_json=results)
                self._fail(sku, "publish", e)
                continue
            self.store.update(sku, status="built" if dry else "published", publish_json=results, error=None)
            done[sku] = results
            log.info("%s: 出品%s %s", sku, "(ドライラン)" if dry else "", results)
        if done:
            for pub in self.publishers.values():
                pub.finalize(self.s.output_dir, dry)
        return done

    # ---- 一括 ----

    def run(self, csv_path: Path | None = None, publish: bool = True) -> None:
        if csv_path:
            self.import_csv(csv_path)
        self.research()
        self.build()
        if publish:
            self.publish()
