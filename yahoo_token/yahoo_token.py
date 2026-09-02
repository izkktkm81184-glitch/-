#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""yahoo_token.py — Yahoo! ID連携 v2 (YConnect) のトークンを自動管理する。

依存ライブラリなし（Python 3.8+ 標準ライブラリのみ）。

CLI:
    python yahoo_token.py status                 残り日数などを表示
    python yahoo_token.py token                  有効なアクセストークンを出力（必要なら自動更新）
    python yahoo_token.py refresh                アクセストークンを強制更新
    python yahoo_token.py exchange --code XXXX   認可コード → トークン一式
    python yahoo_token.py exchange --url "https://www.example.com/callback?code=XXXX"
    python yahoo_token.py url                    認可URLを表示
    python yahoo_token.py watchdog               期限が近ければ自動で再認可（+通知）

他スクリプトからの利用:
    from yahoo_token import get_access_token
    headers = {"Authorization": "Bearer " + get_access_token()}
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import secrets
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

AUTH_URL = "https://auth.login.yahoo.co.jp/yconnect/v2/authorization"
TOKEN_URL = "https://auth.login.yahoo.co.jp/yconnect/v2/token"

# リフレッシュトークンの有効期限（日）。公開鍵認証あり=4週間、なし=12時間に短縮される。
REFRESH_LIFETIME_DAYS = 28

JST = dt.timezone(dt.timedelta(hours=9))

# .env のキー名ゆれを吸収する。タプルの先頭が「書き込みに使う正式名」。
ALIASES = {
    "client_id": ("YAHOO_CLIENT_ID", "YAHOO_APP_ID", "YAHOO_APPID", "YAHOO_CLIENTID", "CLIENT_ID", "APP_ID"),
    "client_secret": ("YAHOO_CLIENT_SECRET", "YAHOO_SECRET", "CLIENT_SECRET"),
    "refresh_token": ("YAHOO_REFRESH_TOKEN", "REFRESH_TOKEN"),
    "access_token": ("YAHOO_ACCESS_TOKEN", "ACCESS_TOKEN"),
    "auth_code": ("YAHOO_AUTH_CODE", "AUTH_CODE"),
    "redirect_uri": ("YAHOO_REDIRECT_URI", "REDIRECT_URI"),
    "scope": ("YAHOO_SCOPE",),
    "access_expires_at": ("YAHOO_ACCESS_TOKEN_EXPIRES_AT",),
    "refresh_issued_at": ("YAHOO_REFRESH_TOKEN_ISSUED_AT",),
    "refresh_rotated_at": ("YAHOO_REFRESH_TOKEN_ROTATED_AT",),
    "last_error": ("YAHOO_TOKEN_LAST_ERROR",),
    # ログイン情報（任意）。Cookie が切れた時の自動ログインにのみ使う。
    # 誤って別サービスの認証情報を Yahoo! に送らないよう、候補は絞ってある。
    "yahoo_id": ("YAHOO_LOGIN_ID", "YAHOO_ID", "YAHOO_USER_ID"),
    "yahoo_password": ("YAHOO_LOGIN_PASSWORD", "YAHOO_PASSWORD", "YAHOO_PASS"),
    # 通知（任意）。未設定なら標準出力に出すだけ。
    "notify_webhook": ("YAHOO_NOTIFY_WEBHOOK",),
    "notify_email": ("YAHOO_NOTIFY_EMAIL",),
    "smtp_host": ("YAHOO_NOTIFY_SMTP_HOST",),
    "smtp_port": ("YAHOO_NOTIFY_SMTP_PORT",),
    "smtp_user": ("YAHOO_NOTIFY_SMTP_USER",),
    "smtp_password": ("YAHOO_NOTIFY_SMTP_PASSWORD",),
}

DEFAULT_REDIRECT_URI = "https://www.example.com/callback"
DEFAULT_SCOPE = "openid"

# 探索するファイル名。中黒は全角(U+30FB)と半角(U+FF65)の両方を見る。
ENV_FILENAMES = ("ID・パス.env", "ID･パス.env", "IDパス.env", ".env", "id_pass.env")


class TokenError(RuntimeError):
    """トークン取得・更新に失敗した。"""


# ---------------------------------------------------------------- .env 入出力

class EnvFile:
    """コメント・並び順・改行コード・文字コードを保ったまま .env を読み書きする。"""

    ENCODINGS = ("utf-8-sig", "utf-8", "cp932")

    def __init__(self, path: Path):
        self.path = Path(path)
        self.encoding = "utf-8"
        self.newline = "\r\n" if os.name == "nt" else "\n"
        self._lines: list[str] = []
        self._index: dict[str, int] = {}
        self._backed_up = False
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            raise FileNotFoundError(f".env が見つかりません: {self.path}")
        raw = self.path.read_bytes()
        text = None
        # BOM の有無は明示的に見る。utf-8 のファイルを utf-8-sig で読むと成功して
        # しまい、書き戻す際に BOM を足して元ファイルを変えてしまうため。
        candidates = ("utf-8-sig",) if raw.startswith(b"\xef\xbb\xbf") else self.ENCODINGS[1:]
        for enc in candidates:
            try:
                text = raw.decode(enc)
                self.encoding = enc
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise TokenError(f"文字コードを判別できません: {self.path}")
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self._lines = text.splitlines()
        for i, line in enumerate(self._lines):
            key = self._key_of(line)
            if key is not None and key not in self._index:
                self._index[key] = i

    @staticmethod
    def _key_of(line: str) -> str | None:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            return None
        head = stripped.split("=", 1)[0].strip()
        if head.startswith("export "):
            head = head[len("export "):].strip()
        return head or None

    @staticmethod
    def _value_of(line: str) -> str:
        value = line.split("=", 1)[1].strip()
        # 行末コメント（値がクォートされていない場合のみ）を落とす
        if value[:1] not in ('"', "'") and " #" in value:
            value = value.split(" #", 1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        return value

    def get(self, key: str, default: str | None = None) -> str | None:
        i = self._index.get(key)
        if i is None:
            return default
        value = self._value_of(self._lines[i])
        return value if value else default

    def set(self, key: str, value: str | None) -> None:
        text = "" if value is None else str(value)
        if any(c in text for c in (" ", "#", "\t")):
            text = '"%s"' % text.replace('"', '\\"')
        i = self._index.get(key)
        if i is None:
            self._index[key] = len(self._lines)
            self._lines.append(f"{key}={text}")
            return
        prefix = "export " if self._lines[i].strip().startswith("export ") else ""
        indent = self._lines[i][: len(self._lines[i]) - len(self._lines[i].lstrip())]
        self._lines[i] = f"{indent}{prefix}{key}={text}"

    def save(self) -> None:
        """.bak を残してから原子的に書き戻す（更新中の停電などで .env を壊さない）。"""
        if not self._backed_up:
            try:
                shutil.copy2(self.path, self.path.with_suffix(self.path.suffix + ".bak"))
            except OSError:
                pass
            self._backed_up = True
        text = self.newline.join(self._lines) + self.newline
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_bytes(text.encode(self.encoding))
        os.replace(tmp, self.path)


def find_env_file(explicit: str | None = None) -> Path:
    """.env の場所を推定する。--env → 環境変数 → カレント/スクリプトから上位へ探索。"""
    if explicit:
        return Path(explicit).expanduser().resolve()
    from_env = os.environ.get("YAHOO_ENV_FILE")
    if from_env:
        return Path(from_env).expanduser().resolve()

    roots: list[Path] = []
    for base in (Path.cwd(), Path(__file__).resolve().parent):
        cur = base.resolve()
        for _ in range(5):
            roots.append(cur)
            if cur.parent == cur:
                break
            cur = cur.parent

    for root in roots:
        for name in ENV_FILENAMES:
            for candidate in (root / name, root / "china_import_db" / name):
                if candidate.exists():
                    return candidate.resolve()
    raise FileNotFoundError(
        ".env を自動検出できませんでした。--env でパスを指定するか、"
        "環境変数 YAHOO_ENV_FILE を設定してください。"
    )


# ------------------------------------------------------------- トークン管理

def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_ts(text: str | None) -> dt.datetime | None:
    if not text:
        return None
    try:
        stamp = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)
    return stamp


def _fmt_ts(stamp: dt.datetime | None) -> str:
    if stamp is None:
        return "(不明)"
    return stamp.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S JST")


class YahooTokenManager:
    def __init__(self, env_path: str | Path | None = None):
        self.env = EnvFile(find_env_file(str(env_path) if env_path else None))

    # --- .env アクセス -------------------------------------------------
    def _get(self, field: str, default: str | None = None) -> str | None:
        for name in ALIASES[field]:
            value = self.env.get(name)
            if value:
                return value
        return default

    def _set(self, field: str, value: str | None) -> None:
        for name in ALIASES[field]:
            if name in self.env._index:
                self.env.set(name, value)
                return
        self.env.set(ALIASES[field][0], value)

    @property
    def client_id(self) -> str:
        value = self._get("client_id")
        if not value:
            raise TokenError(f"{ALIASES['client_id'][0]} が .env にありません: {self.env.path}")
        return value

    @property
    def client_secret(self) -> str:
        value = self._get("client_secret")
        if not value:
            raise TokenError(f"{ALIASES['client_secret'][0]} が .env にありません: {self.env.path}")
        return value

    @property
    def redirect_uri(self) -> str:
        return self._get("redirect_uri", DEFAULT_REDIRECT_URI)

    @property
    def scope(self) -> str:
        return self._get("scope", DEFAULT_SCOPE)

    # --- 認可URL -------------------------------------------------------
    def build_authorize_url(self, state: str | None = None, bail: bool = False) -> tuple[str, str]:
        state = state or secrets.token_urlsafe(16)
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.scope,
            "state": state,
        }
        # bail=1 は「未ログインならログイン画面を出さずエラー」。自動再認可では
        # ログイン画面が出せないと詰むので既定では付けない。
        if bail:
            params["bail"] = "1"
        return f"{AUTH_URL}?{urllib.parse.urlencode(params)}", state

    # --- HTTP ----------------------------------------------------------
    def _post_token(self, data: dict[str, str]) -> dict:
        body = urllib.parse.urlencode(data).encode("utf-8")
        basic = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode("utf-8")
        ).decode("ascii")
        attempts = (
            {"Authorization": f"Basic {basic}"},          # client_secret_basic（Yahoo 推奨）
            {},                                            # client_secret_post へフォールバック
        )
        last_error: Exception | None = None
        for i, extra in enumerate(attempts):
            payload = body
            if not extra:
                payload = urllib.parse.urlencode(
                    dict(data, client_id=self.client_id, client_secret=self.client_secret)
                ).encode("utf-8")
            req = urllib.request.Request(
                TOKEN_URL,
                data=payload,
                method="POST",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                    **extra,
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as res:
                    return json.loads(res.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")
                try:
                    parsed = json.loads(detail)
                    code = parsed.get("error", "")
                    message = parsed.get("error_description", detail)
                except (ValueError, AttributeError):
                    code, message = "", detail
                last_error = TokenError(f"HTTP {exc.code} {code}: {message}")
                # クライアント認証方式の問題以外はリトライしても無駄
                if i == 0 and exc.code in (400, 401) and code in ("invalid_client", ""):
                    continue
                raise last_error from exc
            except urllib.error.URLError as exc:
                last_error = TokenError(f"通信エラー: {exc.reason}")
                raise last_error from exc
        raise last_error or TokenError("トークンエンドポイントへの接続に失敗しました")

    # --- 保存 ----------------------------------------------------------
    def _store(self, payload: dict, *, fresh_authorization: bool) -> None:
        now = _now()
        access = payload.get("access_token")
        if not access:
            raise TokenError(f"access_token が応答に含まれません: {payload}")
        self._set("access_token", access)
        expires_in = int(payload.get("expires_in", 3600))
        self._set("access_expires_at", (now + dt.timedelta(seconds=expires_in)).isoformat())

        new_refresh = payload.get("refresh_token")
        if new_refresh and new_refresh != self._get("refresh_token"):
            self._set("refresh_token", new_refresh)
            self._set("refresh_rotated_at", now.isoformat())
        if fresh_authorization:
            # 4週間の起算点は「認可コードからの取得」。更新では延びない。
            self._set("refresh_issued_at", now.isoformat())
            self._set("auth_code", "")
        self._set("last_error", "")
        self.env.save()

    # --- 操作 ----------------------------------------------------------
    def exchange_code(self, code: str) -> dict:
        payload = self._post_token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
            }
        )
        self._store(payload, fresh_authorization=True)
        return payload

    def refresh(self) -> dict:
        refresh_token = self._get("refresh_token")
        if not refresh_token:
            raise TokenError("リフレッシュトークンが .env にありません。再認可が必要です。")
        try:
            payload = self._post_token(
                {"grant_type": "refresh_token", "refresh_token": refresh_token}
            )
        except TokenError as exc:
            self._set("last_error", f"{_now().isoformat()} {exc}")
            self.env.save()
            raise
        self._store(payload, fresh_authorization=False)
        return payload

    def get_access_token(self, margin_seconds: int = 300) -> str:
        """有効なアクセストークンを返す。期限が近ければ自動で更新する。"""
        token = self._get("access_token")
        expires_at = _parse_ts(self._get("access_expires_at"))
        if token and expires_at and expires_at - _now() > dt.timedelta(seconds=margin_seconds):
            return token
        return self.refresh()["access_token"]

    # --- 状態 ----------------------------------------------------------
    def status(self) -> dict:
        issued = _parse_ts(self._get("refresh_issued_at"))
        expires = issued + dt.timedelta(days=REFRESH_LIFETIME_DAYS) if issued else None
        remaining = (expires - _now()).total_seconds() / 86400 if expires else None
        access_expires = _parse_ts(self._get("access_expires_at"))
        return {
            "env_path": str(self.env.path),
            "client_id": (self._get("client_id") or "")[:12] + "…",
            "redirect_uri": self.redirect_uri,
            "has_refresh_token": bool(self._get("refresh_token")),
            "refresh_issued_at": issued,
            "refresh_expires_at": expires,
            "refresh_remaining_days": remaining,
            "refresh_rotated_at": _parse_ts(self._get("refresh_rotated_at")),
            "access_expires_at": access_expires,
            "access_valid": bool(access_expires and access_expires > _now()),
            "last_error": self._get("last_error") or "",
        }


def get_access_token(env_path: str | Path | None = None, margin_seconds: int = 300) -> str:
    """既存スクリプトから 1 行で使うためのショートカット。"""
    return YahooTokenManager(env_path).get_access_token(margin_seconds)


# ------------------------------------------------------------------- CLI

def _extract_code(text: str) -> str:
    """認可コードそのもの、またはリダイレクト先URL全体から code を取り出す。"""
    text = text.strip().strip('"').strip("'")
    if "code=" in text:
        query = urllib.parse.urlparse(text).query or text.split("?", 1)[-1]
        values = urllib.parse.parse_qs(query).get("code")
        if values:
            return values[0]
    return text


def _print_status(status: dict) -> None:
    remaining = status["refresh_remaining_days"]
    print(f".env               : {status['env_path']}")
    print(f"client_id          : {status['client_id']}")
    print(f"redirect_uri       : {status['redirect_uri']}")
    print(f"アクセストークン   : {'有効' if status['access_valid'] else '要更新'}"
          f" (期限 {_fmt_ts(status['access_expires_at'])})")
    print(f"リフレッシュ取得日 : {_fmt_ts(status['refresh_issued_at'])}")
    print(f"推定失効日         : {_fmt_ts(status['refresh_expires_at'])}")
    if remaining is None:
        print("残り日数           : 不明（YAHOO_REFRESH_TOKEN_ISSUED_AT 未記録）")
    else:
        print(f"残り日数           : {remaining:.1f} 日")
    if status["refresh_rotated_at"]:
        print(f"最終ローテート     : {_fmt_ts(status['refresh_rotated_at'])}")
    if status["last_error"]:
        print(f"直近のエラー       : {status['last_error']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Yahoo! ID連携 v2 トークン管理")
    parser.add_argument("--env", help=".env のパス（省略時は自動検出）")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="トークンの状態を表示")
    sub.add_parser("token", help="有効なアクセストークンを標準出力（必要なら更新）")
    sub.add_parser("refresh", help="アクセストークンを強制更新")

    p_url = sub.add_parser("url", help="認可URLを表示")
    p_url.add_argument("--bail", action="store_true", help="bail=1 を付ける（未ログイン時はエラー）")

    p_ex = sub.add_parser("exchange", help="認可コードをトークンに交換")
    p_ex.add_argument("--code", help="認可コード。省略時は .env の YAHOO_AUTH_CODE を使う")
    p_ex.add_argument("--url", dest="callback_url", help="リダイレクト先URL全体を貼り付けてもよい")

    p_wd = sub.add_parser("watchdog", help="期限が近ければ自動で再認可し、失敗時は通知")
    p_wd.add_argument("--threshold-days", type=float, default=7.0,
                      help="この日数を切ったら再認可する（既定 7）")
    p_wd.add_argument("--mode", default="auto", choices=("auto", "assist", "manual"),
                      help="再認可の方式（yahoo_reauth.py に渡す）")
    p_wd.add_argument("--dry-run", action="store_true", help="判定だけ行い再認可はしない")
    p_wd.add_argument("--log-dir", help="標準出力・エラーを日付別ファイルにも書き出す")

    args = parser.parse_args(argv)

    try:
        manager = YahooTokenManager(args.env)
    except (FileNotFoundError, TokenError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "status":
            _print_status(manager.status())
            return 0

        if args.command == "token":
            print(manager.get_access_token())
            return 0

        if args.command == "refresh":
            payload = manager.refresh()
            print(f"アクセストークンを更新しました（有効 {payload.get('expires_in', '?')} 秒）")
            _print_status(manager.status())
            return 0

        if args.command == "url":
            url, state = manager.build_authorize_url(bail=args.bail)
            print(url)
            print(f"\n(state={state} / このURLを開いて「同意する」→ リダイレクト先の code= を控える)",
                  file=sys.stderr)
            return 0

        if args.command == "exchange":
            source = args.callback_url or args.code or manager._get("auth_code")
            if not source:
                print("エラー: --code / --url もしくは .env の YAHOO_AUTH_CODE が必要です",
                      file=sys.stderr)
                return 2
            manager.exchange_code(_extract_code(source))
            print("新しいトークンを保存しました。")
            _print_status(manager.status())
            return 0

        if args.command == "watchdog":
            return _watchdog(manager, args)
    except TokenError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1

    return 0


class _Tee:
    """標準出力をログファイルにも複製する（タスクスケジューラ実行の記録用）。"""

    def __init__(self, stream, handle):
        self._stream = stream
        self._handle = handle

    def write(self, text: str) -> int:
        self._handle.write(text)
        return self._stream.write(text)

    def flush(self) -> None:
        self._handle.flush()
        self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _open_log(log_dir: str):
    directory = Path(log_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"watchdog_{dt.datetime.now(JST):%Y%m%d}.log"
    handle = path.open("a", encoding="utf-8")
    handle.write(f"\n===== {dt.datetime.now(JST):%Y-%m-%d %H:%M:%S} =====\n")
    return handle


def _watchdog(manager: YahooTokenManager, args) -> int:
    from notify import notify  # 同ディレクトリの notify.py（任意設定）

    log_handle = None
    saved_streams = None
    if getattr(args, "log_dir", None):
        try:
            log_handle = _open_log(args.log_dir)
            saved_streams = (sys.stdout, sys.stderr)
            sys.stdout = _Tee(sys.stdout, log_handle)
            sys.stderr = _Tee(sys.stderr, log_handle)
        except OSError as exc:
            print(f"警告: ログを開けません ({exc})。標準出力のみで継続します。", file=sys.stderr)
    try:
        return _watchdog_body(manager, args, notify)
    finally:
        if saved_streams:
            sys.stdout, sys.stderr = saved_streams
        if log_handle:
            log_handle.close()


def _watchdog_body(manager: YahooTokenManager, args, notify) -> int:
    status = manager.status()
    _print_status(status)
    remaining = status["refresh_remaining_days"]
    expired_early = False

    if remaining is not None and remaining > args.threshold_days:
        # まだ余裕がある。ついでにアクセストークンだけ温めて疎通も確かめておく。
        try:
            manager.get_access_token()
        except TokenError as exc:
            if "invalid_grant" not in str(exc):
                # 通信障害や一時的な 5xx。翌日の実行で回復するので通知はしない。
                print(f"\n警告: アクセストークンを更新できませんでした: {exc}", file=sys.stderr)
                return 1
            # リフレッシュトークンが想定より早く失効している（公開鍵認証が未設定だと
            # 12時間で切れる）。残り日数を待たずに再認可へ進む。
            expired_early = True
            print("\nリフレッシュトークンが既に失効しています。ただちに再認可します。",
                  file=sys.stderr)
            notify(
                manager,
                "【要確認】Yahooリフレッシュトークンが予定より早く失効",
                "推定失効日より前に invalid_grant となりました。ヤフショの公開鍵認証が"
                "未設定・期限切れだと有効期限が12時間に短縮されます。設定をご確認ください。",
            )
        else:
            print(f"\n再認可は不要です（残り {remaining:.1f} 日 > しきい値 {args.threshold_days} 日）。")
            return 0

    if expired_early:
        print(f"\n再認可を実行します（リフレッシュトークン失効済み）。mode={args.mode}")
    else:
        label = "不明" if remaining is None else f"残り {remaining:.1f} 日"
        print(f"\n再認可が必要です（{label}）。mode={args.mode}")
    if args.dry_run:
        return 0

    try:
        from yahoo_reauth import reauth
    except ImportError as exc:
        notify(manager, "【要対応】Yahoo再認可が必要", f"yahoo_reauth を読み込めません: {exc}")
        return 1

    try:
        reauth(manager, mode=args.mode)
    except Exception as exc:  # 自動化は落ちうる。必ず人間に届ける。
        url, _ = manager.build_authorize_url()
        notify(
            manager,
            "【要対応】Yahooトークンの自動再認可に失敗",
            f"{exc}\n\n手動で再認可してください:\n{url}\n\n"
            f"取得した code を次のコマンドに渡してください:\n"
            f"  python yahoo_token.py exchange --url \"<リダイレクト先URL>\"",
        )
        print(f"自動再認可に失敗しました: {exc}", file=sys.stderr)
        return 1

    status = manager.status()
    _print_status(status)
    notify(
        manager,
        "Yahooトークンを自動更新しました",
        f"次回の推定失効日: {_fmt_ts(status['refresh_expires_at'])}",
    )
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
