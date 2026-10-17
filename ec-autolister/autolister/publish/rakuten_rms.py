"""楽天 RMS API で出品する.

- R-Cabinet (cabinet.file.insert) に画像をアップロード
- 商品API 2.0 (items/manage-numbers/{manageNumber}) で商品を登録・更新
- 在庫API 2.1 で在庫数を設定

認証は RMS の serviceSecret / licenseKey (ライセンスキーは3か月ごとに更新が必要)。
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from xml.sax.saxutils import escape

import httpx

from ..config import Settings
from ..content.page_builder import cabinet_file_name
from ..models import ListingContent, PriceDecision, SourceProduct
from .base import Recorder, slug


class RakutenError(RuntimeError):
    pass


def manage_number(sku: str) -> str:
    return slug(sku, allow="-_", max_len=32)


def variant_id(sku: str, size: str | None) -> str:
    base = manage_number(sku)
    if not size:
        return base
    return f"{base}-{slug(size, allow='_')}"[:32]


class RakutenPublisher:
    marketplace = "rakuten"

    def __init__(self, s: Settings, client: httpx.Client | None = None):
        self.s = s
        self.client = client or httpx.Client(timeout=60)

    def _headers(self) -> dict[str, str]:
        if not (self.s.rakuten_service_secret and self.s.rakuten_license_key):
            raise RakutenError("RAKUTEN_SERVICE_SECRET / RAKUTEN_LICENSE_KEY が未設定です")
        token = base64.b64encode(
            f"{self.s.rakuten_service_secret}:{self.s.rakuten_license_key}".encode()
        ).decode()
        return {"Authorization": f"ESA {token}"}

    def _cabinet_location(self, file_name: str) -> str:
        folder = f"/{self.s.rakuten_cabinet_folder_path}" if self.s.rakuten_cabinet_folder_path else ""
        return f"{folder}/{file_name}"

    # ---- R-Cabinet ----

    def upload_image(self, path: str, file_name: str, title: str, rec: Recorder) -> str:
        xml = (
            "<?xml version='1.0' encoding='UTF-8'?><request><fileInsertRequest><file>"
            f"<fileName>{escape(title[:50])}</fileName>"
            f"<folderId>{self.s.rakuten_cabinet_folder_id}</folderId>"
            f"<filePath>{file_name}</filePath>"
            "<overWrite>true</overWrite>"
            "</file></fileInsertRequest></request>"
        )
        rec.record(f"cabinet_insert_{file_name}", {"xml": xml, "file": path})
        if not rec.dry_run:
            with open(path, "rb") as f:
                r = self.client.post(
                    f"{self.s.rakuten_rms_base}/es/1.0/cabinet/file/insert",
                    headers=self._headers(),
                    files={"xml": (None, xml), "file": (file_name, f, "image/jpeg")},
                )
            if r.status_code != 200 or not re.search(r"<FileId>\d+</FileId>", r.text):
                raise RakutenError(f"画像アップロード失敗 {file_name}: {r.status_code} {r.text[:500]}")
        return self._cabinet_location(file_name)

    # ---- 商品API 2.0 ----

    def build_item(
        self, src: SourceProduct, content: ListingContent, decision: PriceDecision, locations: list[str]
    ) -> dict:
        c = content.copy_text
        item: dict = {
            "itemNumber": src.sku,
            "title": c.rakuten_title,
            "tagline": c.rakuten_catch_copy,
            "productDescription": {
                "pc": content.rakuten_pc_description,
                "sp": content.rakuten_sp_description,
            },
            "salesDescription": content.rakuten_sales_description,
            "itemType": "NORMAL",
            "genreId": src.rakuten_genre_id or self.s.rakuten_genre_id,
            "images": [{"type": "CABINET", "location": loc, "alt": c.rakuten_title[:50]} for loc in locations],
            # 初期値は倉庫 (非公開)。RMS 上で確認してから公開する運用を推奨
            "hideItem": self.s.rakuten_hide_item,
            "unlimitedInventoryFlag": False,
        }
        variant_common = {
            "standardPrice": str(decision.price),
            "shipping": {"postageIncluded": True},
        }
        if src.sizes:
            item["variantSelectors"] = [
                {"key": "size", "displayName": "サイズ", "values": [{"displayValue": sz} for sz in src.sizes]}
            ]
            item["variants"] = {
                variant_id(src.sku, sz): {"selectorValues": {"size": sz}, **variant_common} for sz in src.sizes
            }
        else:
            item["variants"] = {variant_id(src.sku, None): dict(variant_common)}
        return item

    def upsert_item(self, mn: str, item: dict, rec: Recorder) -> None:
        rec.record("item_upsert", {"manageNumber": mn, "body": item})
        if rec.dry_run:
            return
        r = self.client.put(
            f"{self.s.rakuten_rms_base}/es/2.0/items/manage-numbers/{mn}",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=item,
        )
        if r.status_code not in (200, 204):
            raise RakutenError(f"商品登録失敗 {mn}: {r.status_code} {r.text[:1000]}")

    def set_inventory(self, mn: str, vid: str, qty: int, rec: Recorder) -> None:
        body = {"mode": "ABSOLUTE", "quantity": qty}
        rec.record(f"inventory_{vid}", {"manageNumber": mn, "variantId": vid, "body": body})
        if rec.dry_run:
            return
        r = self.client.put(
            f"{self.s.rakuten_rms_base}/es/2.1/inventories/manage-numbers/{mn}/variants/{vid}",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=body,
        )
        if r.status_code not in (200, 204):
            raise RakutenError(f"在庫設定失敗 {mn}/{vid}: {r.status_code} {r.text[:500]}")

    # ---- まとめて出品 ----

    def finalize(self, out_dir: Path, dry_run: bool) -> None:
        """楽天は商品APIの更新が即時反映されるため何もしない."""

    def publish(
        self, src: SourceProduct, content: ListingContent, decision: PriceDecision, out_dir: Path, dry_run: bool
    ) -> dict:
        rec = Recorder(out_dir / "rakuten", dry_run)
        mn = manage_number(src.sku)
        locations = [
            self.upload_image(p, cabinet_file_name(src.sku, i), content.copy_text.rakuten_title, rec)
            for i, p in enumerate(content.images, 1)
        ]
        item = self.build_item(src, content, decision, locations)
        self.upsert_item(mn, item, rec)
        for vid in item["variants"]:
            self.set_inventory(mn, vid, src.stock, rec)
        return {"manageNumber": mn, "price": decision.price, "hidden": self.s.rakuten_hide_item, "dry_run": dry_run}
