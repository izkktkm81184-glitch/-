#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""yahoo_reauth._step の検証。

ブラウザを立てずに、偽の page オブジェクトでログイン〜同意の判断を確かめる。
特に「失敗時に再試行せず即中断する」（アカウントロック回避）を担保する。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yahoo_reauth as yr
from yahoo_token import TokenError

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


def expect_error(label, fn, must_contain=""):
    try:
        fn()
    except TokenError as exc:
        check(label, must_contain in str(exc), f"実際のメッセージ: {exc}")
    except Exception as exc:  # noqa: BLE001
        check(label, False, f"想定外の例外: {exc!r}")
    else:
        check(label, False, "例外が発生しなかった")


class FakeLocator:
    def __init__(self, page, selector, value):
        self.page = page
        self.selector = selector
        self.value = value

    def count(self):
        return 1 if self.selector in self.page.elements else 0

    def is_visible(self):
        return self.selector in self.page.elements

    def fill(self, value, timeout=None):
        self.page.actions.append(("fill", self.selector, value))
        self.value = value

    def click(self, timeout=None):
        self.page.actions.append(("click", self.selector))

    def input_value(self):
        return self.value


class FakeSelection:
    def __init__(self, page, selector):
        self.first = FakeLocator(page, selector, page.elements.get(selector, ""))


class FakePage:
    """elements: {セレクタ: 初期値} を「表示されている要素」とみなす。"""

    def __init__(self, elements, body_text=""):
        self.elements = elements
        self.body_text = body_text
        self.actions = []
        self.url = "https://auth.login.yahoo.co.jp/"

    def locator(self, selector):
        return FakeSelection(self, selector)

    def inner_text(self, selector):
        return self.body_text


CREDS = {"id": "store_admin", "password": "p@ssw0rd!"}
NO_CREDS = {"id": None, "password": None}

print("== 1. 同意画面 ==")
page = FakePage({'button:has-text("同意する")': ""})
check("同意すると判定", yr._step(page, CREDS, {}, True) == "consent")
check("クリックした", ("click", 'button:has-text("同意する")') in page.actions)

print("\n== 2. パスワード画面（認証情報あり） ==")
page = FakePage({'input[name="password"]': "", 'button[type="submit"]': ""})
state = {}
check("パスワード投入と判定", yr._step(page, CREDS, state, True) == "password")
check("パスワードを入力した", ("fill", 'input[name="password"]', "p@ssw0rd!") in page.actions)
check("送信した", ("click", 'button[type="submit"]') in page.actions)
check("投入済みを記録", state.get("password_submitted") is True)

print("\n== 3. ロック回避：2回目のパスワード投入はしない ==")
page = FakePage({'input[name="password"]': "", 'button[type="submit"]': ""})
expect_error("2回目は中断する",
             lambda: yr._step(page, CREDS, {"password_submitted": True}, True),
             "再試行はしません")
check("2回目は何も入力しない", page.actions == [], page.actions)

print("\n== 4. ロック回避：失敗文言を見たら即中断 ==")
for hint in ("ID、またはパスワードが違います", "アカウントがロックされました"):
    page = FakePage({'input[name="password"]': ""}, body_text=f"エラー\n{hint}\n再入力")
    expect_error(f"「{hint[:8]}…」で中断", lambda p=page: yr._step(p, CREDS, {}, True),
                 "再試行はしません")
    check("失敗後は入力しない", page.actions == [], page.actions)

page = FakePage({'button:has-text("同意する")': ""},
                body_text="この操作は取り消せません。ログインできません等のご案内はヘルプへ")
check("同意画面の本文では誤爆しない", yr._step(page, CREDS, {}, True) == "consent")

print("\n== 5. 確認コード（SMS）画面 ==")
page = FakePage({'input[autocomplete="one-time-code"]': ""}, body_text="確認コードを入力")
expect_error("無人では中断", lambda: yr._step(page, CREDS, {}, True), "assist")
page = FakePage({'input[autocomplete="one-time-code"]': ""}, body_text="確認コードを入力")
check("画面付きなら人を待つ", yr._step(page, CREDS, {}, False) == "wait_human_otp")

print("\n== 6. パスワード欄と確認コード欄が併存する画面 ==")
# 「パスワードを使わずにログイン」の導線がある画面で SMS と誤判定しないこと
page = FakePage({'input[name="password"]': "", 'input[name="code"]': "",
                 'button[type="submit"]': ""},
                body_text="パスワードを使わずにログインすることもできます")
check("パスワードを優先する", yr._step(page, CREDS, {}, True) == "password")

print("\n== 7. 認証情報が無い場合 ==")
page = FakePage({'input[name="password"]': ""})
expect_error("無人ならエラー", lambda: yr._step(page, NO_CREDS, {}, True),
             "YAHOO_LOGIN_PASSWORD")
page = FakePage({'input[name="password"]': ""})
check("画面付きなら人を待つ", yr._step(page, NO_CREDS, {}, False) == "wait_human_password")

print("\n== 8. ID入力画面 ==")
page = FakePage({'input[name="handle"]': "", 'button[type="submit"]': ""})
state = {}
check("ID投入と判定", yr._step(page, CREDS, state, True) == "id")
check("IDを入力した", ("fill", 'input[name="handle"]', "store_admin") in page.actions)
check("投入済みを記録", state.get("id_submitted") is True)

page = FakePage({'input[name="handle"]': "既に入っている値"})
check("入力済みなら触らない", yr._step(page, CREDS, {}, True) == "waiting")
check("上書きしない", page.actions == [], page.actions)

print("\n== 9. 遷移待ち（該当要素なし） ==")
page = FakePage({}, body_text="読み込み中")
check("何もしない", yr._step(page, CREDS, {}, True) == "waiting")
check("操作していない", page.actions == [])

print("\n== 10. パスワードが例外メッセージに漏れない ==")
message = yr._redact(f"ログイン失敗 password={CREDS['password']} です", [CREDS["password"]])
check("マスクされる", CREDS["password"] not in message and "********" in message, message)
check("None を渡しても落ちない", yr._redact("そのまま", [None]) == "そのまま")

print("\n== 11. セレクタが古くて例外になっても落ちない ==")


class BrokenPage(FakePage):
    def locator(self, selector):
        raise RuntimeError("セレクタが無効")


check("例外を握りつぶして続行", yr._step(BrokenPage({}), CREDS, {}, True) == "waiting")

print(f"\n===== 成功 {ok} / 失敗 {fail} =====")
sys.exit(1 if fail else 0)
