#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""yahoo_reauth.py — 4週間ごとの「再認可」を自動化する。

リフレッシュトークンの寿命は最長4週間で、更新しても延びない。よって
定期的に認可コードを取り直すしかない。ここではその工程を自動化する。

  mode=auto    ヘッドレスブラウザで認可URLを開き、「同意する」まで自動で進む。
               ログイン画面が出た場合は .env のIDとパスワードで自動ログインする。
  mode=assist  画面付きブラウザを開く。SMSの確認コードなど自動化できない画面が
               出た時に人が操作するためのモード。認可コードの取得・交換・保存は
               自動なのでコピペは不要。
  mode=manual  ブラウザを使わない。URLを表示し、リダイレクト先URLを丸ごと
               貼り付けてもらう（code の切り出しは自動）。

ログイン状態はブラウザプロファイル (.browser_profile/) の Cookie として残るため、
2回目以降はログイン画面自体が出ないのが普通で、認証情報は使われない。
認証情報はあくまで Cookie が切れた時の保険。

■ アカウントロック対策
パスワードの投入は 1 回だけ行い、失敗したら即座に中断する。誤った値で
繰り返しログインを試みてアカウントがロックされるのを防ぐため、
リトライは一切しない。

認可コードはリダイレクト先へのリクエストを Playwright 側で横取りして読む。
そのため redirect_uri (https://www.example.com/callback) を実際にホストする
必要はなく、Yahoo!デベロッパーネットワーク側の設定変更も不要。

使い方:
    pip install playwright && playwright install chromium
    python yahoo_reauth.py --mode assist      # 初回：様子を見ながら
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

# 画面はしばしば作り変えられるので、候補を順に試す。
CONSENT_SELECTORS = (
    'button:has-text("同意する")',
    'input[value="同意する"]',
    'button:has-text("許可する")',
    'button:has-text("続行")',
    "#consent-submit",
)
ID_SELECTORS = (
    'input[name="handle"]',
    "input#username",
    'input[name="login"]',
)
PASSWORD_SELECTORS = (
    'input[name="password"]',
    "input#passwd",
    'input[type="password"]',
)
# SMS・アプリのワンタイムコード入力欄。これが出たら自動では進めない。
OTP_SELECTORS = (
    'input[autocomplete="one-time-code"]',
    'input[name="code"]',
    'input[name="otp"]',
    'input[name="verification_code"]',
)
SUBMIT_SELECTORS = (
    'button[type="submit"]',
    'input[type="submit"]',
    "#btnNext",
    "#btnSubmit",
)
# ログイン失敗を示す文言。見つけたら即中断する（ロック回避）。
LOGIN_ERROR_HINTS = (
    "ID、またはパスワードが違います",
    "IDまたはパスワードが違います",
    "パスワードが違います",
    "正しくありません",
    "ログインできません",
    "ロックされ",
)


def _visible(page, selector: str):
    """表示中の要素を返す。無ければ None（セレクタが古くても落とさない）。"""
    try:
        locator = page.locator(selector).first
        if locator.count() and locator.is_visible():
            return locator
    except Exception:
        return None
    return None


def _find(page, selectors) -> object | None:
    for selector in selectors:
        found = _visible(page, selector)
        if found is not None:
            return found
    return None


def _body_text(page) -> str:
    try:
        return page.inner_text("body")
    except Exception:
        return ""


def _click_submit(page) -> None:
    button = _find(page, SUBMIT_SELECTORS)
    if button is not None:
        try:
            button.click(timeout=5_000)
        except Exception:
            pass


def _step(page, credentials: dict, state: dict, headless: bool) -> str:
    """ログイン〜同意の画面を 1 手だけ進める。実行した内容を文字列で返す。

    Playwright に依存しない形（page はダックタイピング）にしてあるので、
    偽の page を渡してテストできる。
    """
    consent = _find(page, CONSENT_SELECTORS)
    if consent is not None:
        try:
            consent.click(timeout=5_000)
        except Exception:
            return "consent_click_failed"
        return "consent"

    password_box = _find(page, PASSWORD_SELECTORS)
    otp_box = _find(page, OTP_SELECTORS)
    id_box = _find(page, ID_SELECTORS)

    # 失敗文言の判定はログインフォームが出ている時だけ行う。
    # 同意画面などの本文で同じ語に当たって誤って中断するのを避ける。
    if password_box is not None or id_box is not None:
        text = _body_text(page)
        for hint in LOGIN_ERROR_HINTS:
            if hint in text:
                raise TokenError(
                    f"ログインに失敗しました（画面の文言: {hint}）。"
                    "アカウントロックを避けるため再試行はしません。"
                    ".env のIDとパスワードを確認してください。"
                )

    # パスワード欄が無く確認コード欄だけがある = SMS等の二要素認証画面
    if otp_box is not None and password_box is None:
        if headless:
            raise TokenError(
                "SMS等の確認コード入力を求められたため無人では進めません。"
                "`python yahoo_reauth.py --mode assist` で一度手動で通すと、"
                "以降はブラウザプロファイルが再利用され無人化できます。"
            )
        return "wait_human_otp"

    if password_box is not None:
        if not credentials.get("password"):
            if headless:
                raise TokenError(
                    "パスワード入力を求められましたが .env に "
                    "YAHOO_LOGIN_PASSWORD がありません。"
                    "設定するか `--mode assist` を使ってください。"
                )
            return "wait_human_password"
        if state.get("password_submitted"):
            # 1 回投入済みでまだこの画面にいる = 失敗している。
            # 繰り返すとロックされるのでここで止める。
            raise TokenError(
                "パスワードを投入しましたがログイン画面から進みませんでした。"
                "アカウントロックを避けるため再試行はしません。"
                "`--mode assist` で画面を確認してください。"
            )
        try:
            password_box.fill(credentials["password"], timeout=5_000)
        except Exception:
            return "password_fill_failed"
        state["password_submitted"] = True
        _click_submit(page)
        return "password"

    if id_box is not None:
        if not credentials.get("id"):
            if headless:
                raise TokenError(
                    "ログインIDの入力を求められましたが .env に "
                    "YAHOO_LOGIN_ID がありません。"
                    "設定するか `--mode assist` を使ってください。"
                )
            return "wait_human_id"
        if state.get("id_submitted"):
            return "waiting"
        try:
            if (id_box.input_value() or "").strip():
                return "waiting"  # 既に入っている（Cookieによる補完など）
            id_box.fill(credentials["id"], timeout=5_000)
        except Exception:
            return "id_fill_failed"
        state["id_submitted"] = True
        _click_submit(page)
        return "id"

    return "waiting"


def _redact(text: str, secrets: list) -> str:
    """例外メッセージにパスワードが混ざらないようにする。"""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "********")
    return text


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

    url, state_token = manager.build_authorize_url()
    callback_prefix = manager.redirect_uri.split("?", 1)[0]
    credentials = {"id": manager._get("yahoo_id"), "password": manager._get("yahoo_password")}
    secrets = [credentials.get("password")]
    captured: dict = {}
    step_state: dict = {}
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

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            deadline = time.time() + timeout
            while time.time() < deadline and "url" not in captured:
                _step(page, credentials, step_state, headless)
                page.wait_for_timeout(700)

            if "url" not in captured:
                raise TokenError(
                    f"{timeout:.0f} 秒以内に認可コードを取得できませんでした。"
                    f"最後のURL: {page.url} / 画面: {_body_text(page)[:200]}"
                )
        except TokenError as exc:
            raise TokenError(_redact(str(exc), secrets)) from None
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
    if returned_state and returned_state != state_token:
        raise TokenError("state が一致しません（CSRF の疑い）。処理を中断しました。")
    code = query.get("code", [None])[0]
    if not code:
        raise TokenError(f"リダイレクトURLに code がありません: {captured['url']}")
    return code


def _manual_reauth(manager: YahooTokenManager) -> str:
    url, state_token = manager.build_authorize_url(bail=True)
    print("\n下記URLをブラウザで開き、「同意する」を押してください。")
    print(url)
    print("\nリダイレクト先はエラー表示で構いません。アドレスバーのURLを丸ごと貼り付けてください。")
    answer = input("リダイレクト先URL (または code の値): ").strip()
    if not answer:
        raise TokenError("入力が空でした。")
    if "state=" in answer and state_token not in answer:
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
