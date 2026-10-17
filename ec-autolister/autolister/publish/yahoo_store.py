"""Yahoo!ショッピング ストアAPI で出品する.

- editItem で商品を登録・更新
- uploadItemImage で商品画像をアップロード (ファイル名で商品と紐づく)
- setStock で在庫数を設定
- reservePublish で全反映 (これを実行するまでストアには反映されない)

認証は Yahoo! ID連携 (OAuth2) のアクセストークン + 公開鍵認証 (X-sws-signature)。
"""

from __future__ import annotations

import base64
import time
import urllib.parse
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

from ..config import Settings
from ..db import Store
from ..models import ListingContent, PriceDecision, SourceProduct
from .base import Recorder, slug

AUTH_URL = "https://auth.login.yahoo.co.jp/yconnect/v2/authorization"
TOKEN_URL = "https://auth.login.yahoo.co.jp/yconnect/v2/token"
MAX_SUB_IMAGES = 20


class YahooError(RuntimeError):
    pass


def item_code(sku: str) -> str:
    return slug(sku, allow="-_", max_len=99)


def sub_code(sku: str, size: str) -> str:
    return f"{item_code(sku)}-{slug(size, allow='_')}"[:99]


def image_file_name(code: str, index: int) -> str:
    """1枚目は {商品コード}.jpg、以降は {商品コード}_1.jpg … で商品に紐づく."""
    return f"{code}.jpg" if index == 1 else f"{code}_{index - 1}.jpg"


# ---- OAuth ----


def authorization_url(s: Settings, state: str = "autolister") -> str:
    q = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": s.yahoo_client_id,
            "redirect_uri": s.yahoo_redirect_uri,
            "scope": "openid",
            "state": state,
        }
    )
    return f"{AUTH_URL}?{q}"


def _token_request(s: Settings, data: dict, client: httpx.Client) -> dict:
    r = client.post(TOKEN_URL, data=data, auth=(s.yahoo_client_id, s.yahoo_client_secret))
    if r.status_code != 200:
        raise YahooError(f"トークン取得失敗: {r.status_code} {r.text[:500]}")
    return r.json()


def save_tokens(store: Store, tok: dict) -> None:
    store.kv_set("yahoo_access_token", tok["access_token"])
    store.kv_set("yahoo_access_expires", str(int(time.time()) + int(tok.get("expires_in", 3600)) - 60))
    if tok.get("refresh_token"):
        store.kv_set("yahoo_refresh_token", tok["refresh_token"])


def exchange_code(s: Settings, store: Store, code: str, client: httpx.Client | None = None) -> dict:
    tok = _token_request(
        s,
        {"grant_type": "authorization_code", "code": code, "redirect_uri": s.yahoo_redirect_uri},
        client or httpx.Client(timeout=30),
    )
    save_tokens(store, tok)
    return tok


class YahooPublisher:
    marketplace = "yahoo"

    def __init__(self, s: Settings, store: Store, client: httpx.Client | None = None):
        self.s = s
        self.store = store
        self.client = client or httpx.Client(timeout=60)

    # ---- 認証 ----

    def access_token(self) -> str:
        tok = self.store.kv_get("yahoo_access_token")
        exp = int(self.store.kv_get("yahoo_access_expires") or 0)
        if tok and exp > time.time():
            return tok
        refresh = self.store.kv_get("yahoo_refresh_token") or self.s.yahoo_refresh_token
        if not refresh:
            raise YahooError("Yahoo のリフレッシュトークンがありません。`autolister yahoo-auth` を実行してください")
        data = _token_request(self.s, {"grant_type": "refresh_token", "refresh_token": refresh}, self.client)
        save_tokens(self.store, data)
        return data["access_token"]

    def _signature_headers(self) -> dict[str, str]:
        if not self.s.yahoo_public_key_path:
            return {}
        key = serialization.load_pem_public_key(Path(self.s.yahoo_public_key_path).read_bytes())
        plain = f"{self.s.yahoo_seller_id}:{int(time.time())}".encode()
        sig = base64.b64encode(key.encrypt(plain, padding.PKCS1v15())).decode()
        return {"X-sws-signature": sig, "X-sws-signature-version": self.s.yahoo_public_key_version}

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token()}", **self._signature_headers()}

    def _check(self, r: httpx.Response, what: str) -> None:
        if r.status_code != 200 or "<Error>" in r.text or 'Status="NG"' in r.text:
            raise YahooError(f"{what} 失敗: {r.status_code} {r.text[:1000]}")

    # ---- API ----

    def build_item(self, src: SourceProduct, content: ListingContent, decision: PriceDecision) -> dict:
        c = content.copy_text
        form = {
            "seller_id": self.s.yahoo_seller_id,
            "item_code": item_code(src.sku),
            "path": self.s.yahoo_store_path,
            "name": c.yahoo_title,
            "product_category": src.yahoo_product_category or self.s.yahoo_product_category,
            "price": str(decision.price),
            "headline": c.yahoo_headline,
            "caption": content.yahoo_caption,
            "explanation": content.yahoo_explanation,
            "sp_additional": content.yahoo_sp_additional,
            "meta_desc": c.meta_description,
            "condition": "0" if src.condition.startswith("新品") else "1",
            "delivery": "1",  # 送料無料 (送料は販売価格に含めて計算済み)
            # 0: 非公開。ストアクリエイターPro で確認してから公開する運用を推奨
            "display": "1" if self.s.yahoo_display else "0",
        }
        if src.jan:
            form["jan"] = src.jan
        if src.sizes:
            form["options"] = "サイズ:" + "#".join(src.sizes)
            form["subcodes"] = "&".join(f"サイズ:{sz}={sub_code(src.sku, sz)}" for sz in src.sizes)
        return {k: v for k, v in form.items() if v != ""}

    def edit_item(self, form: dict, rec: Recorder) -> None:
        rec.record("editItem", form)
        if rec.dry_run:
            return
        r = self.client.post(f"{self.s.yahoo_store_api_base}/editItem", headers=self._headers(), data=form)
        self._check(r, f"editItem {form['item_code']}")

    def upload_image(self, path: str, file_name: str, rec: Recorder) -> None:
        rec.record(f"uploadItemImage_{file_name}", {"file": path, "name": file_name})
        if rec.dry_run:
            return
        with open(path, "rb") as f:
            r = self.client.post(
                f"{self.s.yahoo_store_api_base}/uploadItemImage",
                params={"seller_id": self.s.yahoo_seller_id},
                headers=self._headers(),
                files={"file": (file_name, f, "image/jpeg")},
            )
        self._check(r, f"uploadItemImage {file_name}")

    def set_stock(self, code: str, qty: int, rec: Recorder) -> None:
        form = {"seller_id": self.s.yahoo_seller_id, "item_code": code, "quantity": str(qty)}
        rec.record(f"setStock_{code.replace(':', '_')}", form)
        if rec.dry_run:
            return
        r = self.client.post(f"{self.s.yahoo_store_api_base}/setStock", headers=self._headers(), data=form)
        self._check(r, f"setStock {code}")

    def reserve_publish(self, rec: Recorder) -> None:
        form = {"seller_id": self.s.yahoo_seller_id, "mode": "1"}
        rec.record("reservePublish", form)
        if rec.dry_run:
            return
        r = self.client.post(f"{self.s.yahoo_store_api_base}/reservePublish", headers=self._headers(), data=form)
        self._check(r, "reservePublish")

    def finalize(self, out_dir: Path, dry_run: bool) -> None:
        """まとめて出品した後に一度だけ全反映する."""
        self.reserve_publish(Recorder(out_dir / "_yahoo_publish", dry_run))

    def publish(
        self, src: SourceProduct, content: ListingContent, decision: PriceDecision, out_dir: Path, dry_run: bool
    ) -> dict:
        rec = Recorder(out_dir / "yahoo", dry_run)
        form = self.build_item(src, content, decision)
        code = form["item_code"]
        self.edit_item(form, rec)
        for i, p in enumerate(content.images[: MAX_SUB_IMAGES + 1], 1):
            self.upload_image(p, image_file_name(code, i), rec)
        if src.sizes:
            for sz in src.sizes:
                self.set_stock(f"{code}:{sub_code(src.sku, sz)}", src.stock, rec)
        else:
            self.set_stock(code, src.stock, rec)
        return {"item_code": code, "price": decision.price, "display": self.s.yahoo_display, "dry_run": dry_run}

