#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""yahoo_token.py の検証（ネットワーク非依存。_post_token を差し替える）。"""
import datetime as dt
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yahoo_token as yt

WORK = Path(tempfile.mkdtemp())
ENV = WORK / "ID・パス.env"

ORIGINAL = (
    "# 中国輸入DB 設定\r\n"
    "DB_PATH=C:\\data\\china.db\r\n"
    "\r\n"
    "# --- ヤフショ ---\r\n"
    "YAHOO_CLIENT_ID=dmVyPTIwMjUwNyZpZD1EVU1NWQ\r\n"
    "YAHOO_CLIENT_SECRET=s3cr3t\r\n"
    "YAHOO_REFRESH_TOKEN=old_refresh\r\n"
    "YAHOO_AUTH_CODE=\r\n"
    "export YAHOO_ACCESS_TOKEN=old_access\r\n"
    "OTHER=keep me  # 末尾コメント\r\n"
)
ok = 0
fail = 0


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  OK   {label}")
    else:
        fail += 1
        print(f"  FAIL {label} {extra}")


def reset(encoding="utf-8"):
    ENV.write_bytes(ORIGINAL.encode(encoding))
    return yt.YahooTokenManager(ENV)


print("== 1. .env の読み取りとキー名ゆれ ==")
m = reset()
check("client_id", m.client_id == "dmVyPTIwMjUwNyZpZD1EVU1NWQ", m.client_id)
check("client_secret", m.client_secret == "s3cr3t")
check("redirect_uri 既定値", m.redirect_uri == "https://www.example.com/callback")
check("scope 既定値", m.scope == "openid")
check("export 付き行", m._get("access_token") == "old_access")
check("空値は None 扱い", m._get("auth_code") is None)

print("\n== 2. 書き戻しで既存行・コメント・CRLF を壊さない ==")
m._set("access_token", "new_access")
m._set("refresh_token", "new_refresh")
m.env.save()
raw = ENV.read_bytes().decode("utf-8")
check("CRLF 維持（全行）", raw.count("\r\n") == raw.count("\n") and raw.count("\n") > 0,
      f"crlf={raw.count(chr(13)+chr(10))} lf={raw.count(chr(10))}")
check("コメント維持", "# 中国輸入DB 設定" in raw and "# --- ヤフショ ---" in raw)
check("無関係キー維持", "DB_PATH=C:\\data\\china.db" in raw)
check("export 接頭辞維持", "export YAHOO_ACCESS_TOKEN=new_access" in raw)
check("値の更新", "YAHOO_REFRESH_TOKEN=new_refresh" in raw)
check("行が増えていない", len(raw.strip().splitlines()) == len(ORIGINAL.strip().splitlines()),
      f"{len(raw.strip().splitlines())} vs {len(ORIGINAL.strip().splitlines())}")
check(".bak が残る", (ENV.parent / (ENV.name + ".bak")).exists())
check("行末コメント付きの値", yt.YahooTokenManager(ENV).env.get("OTHER") == "keep me")

print("\n== 3. cp932 の .env でも壊れない ==")
m = reset("cp932")
check("cp932 判定", m.env.encoding == "cp932", m.env.encoding)
m._set("access_token", "cp932_access")
m.env.save()
check("cp932 で書き戻し", "中国輸入DB" in ENV.read_bytes().decode("cp932"))

print("\n== 4. 認可URLの組み立て ==")
m = reset()
url, state = m.build_authorize_url()
check("エンドポイント", url.startswith(yt.AUTH_URL + "?"))
check("response_type=code", "response_type=code" in url)
check("redirect_uri エンコード", "redirect_uri=https%3A%2F%2Fwww.example.com%2Fcallback" in url)
check("state 付与", f"state={state}" in url and len(state) > 10)
check("既定では bail 無し", "bail=" not in url)
check("bail=True で付く", "bail=1" in m.build_authorize_url(bail=True)[0])

print("\n== 5. code の切り出し ==")
check("URL 全体から", yt._extract_code(
    "https://www.example.com/callback?code=ABC123&state=xyz") == "ABC123")
check("code 単体", yt._extract_code("ABC123") == "ABC123")
check("引用符付き", yt._extract_code('"https://www.example.com/callback?code=A%2FB"') == "A/B")
check("前後空白", yt._extract_code("  ABC123  ") == "ABC123")

print("\n== 6. 認可コード交換（レスポンスを差し替え） ==")
m = reset()
calls = []


def fake_post(data):
    calls.append(data)
    return {"access_token": "AT1", "expires_in": 3600,
            "refresh_token": "RT1", "token_type": "Bearer"}


m._post_token = fake_post
m.exchange_code("CODE123")
check("grant_type", calls[-1]["grant_type"] == "authorization_code")
check("code 送信", calls[-1]["code"] == "CODE123")
check("redirect_uri 送信", calls[-1]["redirect_uri"] == "https://www.example.com/callback")
saved = yt.YahooTokenManager(ENV)
check("access_token 保存", saved._get("access_token") == "AT1")
check("refresh_token 保存", saved._get("refresh_token") == "RT1")
check("起算日を記録", saved._get("refresh_issued_at") is not None)
check("認可コードを消す", saved._get("auth_code") is None)
st = saved.status()
check("残り約28日", 27.9 < st["refresh_remaining_days"] <= 28.0,
      st["refresh_remaining_days"])

print("\n== 7. リフレッシュ ==")
m = yt.YahooTokenManager(ENV)
calls.clear()


def fake_refresh(data):
    calls.append(data)
    return {"access_token": "AT2", "expires_in": 3600}  # refresh_token 無しのケース


m._post_token = fake_refresh
m.refresh()
check("grant_type", calls[-1]["grant_type"] == "refresh_token")
check("既存 refresh_token を送る", calls[-1]["refresh_token"] == "RT1")
saved = yt.YahooTokenManager(ENV)
check("access_token 更新", saved._get("access_token") == "AT2")
check("refresh_token は据え置き", saved._get("refresh_token") == "RT1")
issued_before = saved._get("refresh_issued_at")

# ローテーションされる場合
m = yt.YahooTokenManager(ENV)
m._post_token = lambda data: {"access_token": "AT3", "expires_in": 3600,
                              "refresh_token": "RT2"}
m.refresh()
saved = yt.YahooTokenManager(ENV)
check("ローテートを保存", saved._get("refresh_token") == "RT2")
check("ローテート日時を記録", saved._get("refresh_rotated_at") is not None)
check("起算日は延ばさない", saved._get("refresh_issued_at") == issued_before)

print("\n== 8. get_access_token の期限判定 ==")
m = yt.YahooTokenManager(ENV)
refreshed = []
m._post_token = lambda data: (refreshed.append(1) or
                              {"access_token": "AT9", "expires_in": 3600})
check("有効なら更新しない", m.get_access_token() == "AT3" and not refreshed)

m._set("access_expires_at", (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60)).isoformat())
m.env.save()
m2 = yt.YahooTokenManager(ENV)
m2._post_token = lambda data: {"access_token": "AT9", "expires_in": 3600}
check("期限間近なら更新する", m2.get_access_token() == "AT9")

print("\n== 9. 失効時のエラー記録 ==")
m = yt.YahooTokenManager(ENV)


def boom(data):
    raise yt.TokenError("HTTP 400 invalid_grant: expired")


m._post_token = boom
try:
    m.refresh()
    check("例外が上がる", False)
except yt.TokenError:
    check("例外が上がる", True)
check("last_error を記録", "invalid_grant" in (yt.YahooTokenManager(ENV)._get("last_error") or ""))

print("\n== 10. watchdog の判定（--dry-run） ==")
m = yt.YahooTokenManager(ENV)
m._set("refresh_issued_at",
       (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=25)).isoformat())
m._set("last_error", "")
m.env.save()
rc = yt.main(["--env", str(ENV), "watchdog", "--dry-run", "--threshold-days", "7"])
check("残り3日 → 再認可が必要と判定", rc == 0)

print("\n== 11. .env が無い場合 ==")
try:
    yt.YahooTokenManager(WORK / "nope.env")
    check("FileNotFoundError", False)
except FileNotFoundError:
    check("FileNotFoundError", True)

print("\n== 12. CLI status / url ==")
check("status 終了コード0", yt.main(["--env", str(ENV), "status"]) == 0)
check("url 終了コード0", yt.main(["--env", str(ENV), "url"]) == 0)


print("\n== 13. 文字コード・BOM を書き戻しで変えない ==")
for label, enc, bom in (("BOM無しUTF-8", "utf-8", b""),
                        ("BOM付きUTF-8", "utf-8-sig", b"\xef\xbb\xbf"),
                        ("cp932", "cp932", b"")):
    path = WORK / f"enc_{enc}.env"
    body = "# 中国輸入DB\r\nYAHOO_CLIENT_ID=cid\r\nYAHOO_CLIENT_SECRET=sec\r\n"
    path.write_bytes(bom + body.encode("utf-8" if enc == "utf-8-sig" else enc))
    before = path.read_bytes()
    mgr = yt.YahooTokenManager(path)
    mgr._set("access_token", "AT")
    mgr.env.save()
    after = path.read_bytes()
    check(f"{label}: 判定", mgr.env.encoding == enc, mgr.env.encoding)
    check(f"{label}: BOM 不変",
          before.startswith(b"\xef\xbb\xbf") == after.startswith(b"\xef\xbb\xbf"))
    check(f"{label}: 既存行を保持", after.startswith(before.rstrip(b"\r\n")))

shutil.rmtree(WORK, ignore_errors=True)
print(f"\n===== 成功 {ok} / 失敗 {fail} =====")
sys.exit(1 if fail else 0)
