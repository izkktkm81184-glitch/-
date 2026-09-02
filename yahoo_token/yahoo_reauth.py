#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""yahoo_reauth.py — 4週間ごとの「再認可」を自動化する。

リフレッシュトークンの寿命は最長4週間で、更新しても延びない。よって
定期的に認可コードを取り直すしかない。ここではその工程を自動化する。

  mode=assist  画面付きブラウザを開く。人がやるのはログインだけ
               （SMS/パスワードレス認証もそのまま通る）。認可コードの
               取得・交換・保存はスクリプトが行うのでコピペは不要。
               ブラウザプロファイルを保存するので、これは初回だけでよい。
  mode=auto    保存済みプロファイルを使いヘッドレスで開く。ログイン状態と
               同意履歴が残っていれば、そのままリダイレクトされて完全無人で
               完了する。ログインを求められた場合は中断し、assist を促す。
  mode=manual  ブラウザを使わない。URLを表示し、リダイレクト先URLを丸ごと
               貼り付けてもらう（code の切り出しは自動）。

パスワードはどこにも保存しない。認証情報はブラウザプロファイル
(.browser_profile) の Cookie としてローカルにのみ残る。

認可コードはリダイレクト先へのリクエストを Playwright 側で横取りして読む。
そのため redirect_uri (https://www.example.com/callback) を実際にホストする
必要はなく、Yahoo!デベロッパーネットワーク側の設定変更も不要。

使い方:
    pip install playwright && playwright install chromium
    python yahoo_reauth.py --mode assist      # 初回：ログインだけ手動
    python yahoo_reauth.py --mode auto        # 以降：無人実行
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from yahoo_token import TokenError, YahooTokenManager, _extract_code, _print_status  # noqa: E402

DEFAULT_PROFILE_DIR = Path(__file__).resolve().parent / ".browser_profile"

# 同意画面はしばしば作り変えられるので、候補を順に試す。
CONSENT_SELECTORS = (
    'button:has-text("同意する")',
    'input[value="同意する"]',
    'button:has-text("許可する")',
    'button:has-text("続行")',
    "#consent-submit",
)
# ログインを求められたことを示す手掛かり（auto モードでは続行できない）
LOGIN_REQUIRED_HINTS = ("ログイン", "確認コード", "パスワード", "SMS")


def _visible(page, selector: str):
    try:
        locator = page.locator(selector).first
        if locator.count() and locator.is_visible():
            return locator
    except Exception:
        return None
    return None


def _browser_reauth(
    manager: YahooTokenManager,
    *,
    headless: bool,
    timeout: float,
    profile_dir: Path,
) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise TokenError(
            "playwright が入っていません。`pip install playwright` と "
            "`playwright install chromium` を実行してください。"
        ) from exc

    url, state = manager.build_authorize_url()
    callback_prefix = manager.redirect_uri.split("?", 1)[0]
    captured: dict = {}
    profile_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile_dir),
            headless=headless,
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            viewport={"width": 1280, "height": 900},
        )

        def intercept(route):
            # リダイレクト先には実際にアクセスさせず、URL だけ受け取る。
            captured["url"] = route.request.url
            route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                body="<h1>認可コードを取得しました。このタブは閉じて構いません。</h1>",
            )

        context.route(callback_prefix + "*", intercept)
        page = context.pages[0] if context.pages else context.new_page()
        last_text = ""

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            deadline = time.time() + timeout
            while time.time() < deadline and "url" not in captured:
                consent = None
                for selector in CONSENT_SELECTORS:
                    consent = _visible(page, selector)
                    if consent:
                        break
                if consent is not None:
                    try:
                        consent.click(timeout=5_000)
                    except Exception:
                        pass
                page.wait_for_timeout(700)

            if "url" not in captured:
                try:
                    last_text = page.inner_text("body")[:400]
                except Exception:
                    last_text = ""
                if headless and any(hint in last_text for hint in LOGIN_REQUIRED_HINTS):
                    raise TokenError(
                        "Yahoo!のログインが切れているため無人での再認可ができませんでした。"
                        "`python yahoo_reauth.py --mode assist` を一度実行して手動ログイン"
                        "すると、以降は再び無人で更新できます。"
                    )
                raise TokenError(
                    f"{timeout:.0f} 秒以内に認可コードを取得できませんでした。"
                    f"最後のURL: {page.url} / 画面: {last_text[:200]}"
                )
        finally:
            try:
                context.close()
            except Exception:
                pass

    query = parse_qs(urlparse(captured["url"]).query)
    if "error" in query:
        raise TokenError(
            f"認可が拒否されました: {query.get('error', [''])[0]} "
            f"{query.get('error_description', [''])[0]}"
        )
    returned_state = query.get("state", [None])[0]
    if returned_state and returned_state != state:
        raise TokenError("state が一致しません（CSRF の疑い）。処理を中断しました。")
    code = query.get("code", [None])[0]
    if not code:
        raise TokenError(f"リダイレクトURLに code がありません: {captured['url']}")
    return code


def _manual_reauth(manager: YahooTokenManager) -> str:
    url, state = manager.build_authorize_url(bail=True)
    print("\n下記URLをブラウザで開き、「同意する」を押してください。")
    print(url)
    print("\nリダイレクト先はエラー表示で構いません。アドレスバーのURLを丸ごと貼り付けてください。")
    answer = input("リダイレクト先URL (または code の値): ").strip()
    if not answer:
        raise TokenError("入力が空でした。")
    if "state=" in answer and state not in answer:
        raise TokenError("state が一致しません。もう一度やり直してください。")
    return _extract_code(answer)


def reauth(
    manager: YahooTokenManager | None = None,
    mode: str = "auto",
    *,
    timeout: float = 180.0,
    profile_dir: Path | None = None,
) -> dict:
    """再認可を実行し、新しいトークン一式を .env に保存する。"""
    manager = manager or YahooTokenManager()
    profile_dir = profile_dir or DEFAULT_PROFILE_DIR

    if mode == "manual":
        code = _manual_reauth(manager)
    elif mode in ("auto", "assist"):
        code = _browser_reauth(
            manager,
            headless=(mode == "auto"),
            timeout=timeout,
            profile_dir=profile_dir,
        )
    else:
        raise TokenError(f"未知の mode: {mode}")

    # 認可コードの寿命は数分。取得したら即座に交換する。
    return manager.exchange_code(code)


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Yahoo! ID連携の再認可を自動化する")
    parser.add_argument("--mode", default="auto", choices=("auto", "assist", "manual"))
    parser.add_argument("--env", help=".env のパス（省略時は自動検出）")
    parser.add_argument("--timeout", type=float, default=180.0, help="ブラウザ操作の上限秒数")
    parser.add_argument("--profile-dir", help="ブラウザプロファイルの保存先")
    args = parser.parse_args(argv)

    try:
        manager = YahooTokenManager(args.env)
        reauth(
            manager,
            mode=args.mode,
            timeout=args.timeout,
            profile_dir=Path(args.profile_dir) if args.profile_dir else None,
        )
    except (TokenError, FileNotFoundError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1

    print("再認可が完了し、新しいトークンを保存しました。")
    _print_status(manager.status())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
