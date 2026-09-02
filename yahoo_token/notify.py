#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""notify.py — トークン更新の結果・失敗を人に届ける（任意設定）。

自動化は必ずいつか失敗する。失敗に気づけないと在庫更新が止まるので、
watchdog は成功も失敗もここを通して通知する。

.env に下記のいずれかを書けば有効になる。両方書けば両方に飛ぶ。
未設定なら標準出力に出すだけ（それでもタスクスケジューラのログには残る）。

    # Slack / Discord / Teams などの Incoming Webhook URL
    YAHOO_NOTIFY_WEBHOOK=https://hooks.slack.com/services/xxx/yyy/zzz

    # メール（Gmail ならアプリパスワードを使う）
    YAHOO_NOTIFY_EMAIL=you@example.com
    YAHOO_NOTIFY_SMTP_HOST=smtp.gmail.com
    YAHOO_NOTIFY_SMTP_PORT=587
    YAHOO_NOTIFY_SMTP_USER=you@gmail.com
    YAHOO_NOTIFY_SMTP_PASSWORD=xxxxxxxxxxxxxxxx
"""
from __future__ import annotations

import json
import smtplib
import sys
import urllib.error
import urllib.request
from email.message import EmailMessage


def _post_webhook(url: str, subject: str, body: str) -> None:
    # Slack は "text"、Discord は "content" を見る。両方入れて使い回す。
    payload = json.dumps({"text": f"{subject}\n{body}", "content": f"{subject}\n{body}"})
    request = urllib.request.Request(
        url,
        data=payload.encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15):
        pass


def _send_mail(manager, subject: str, body: str) -> None:
    to_address = manager._get("notify_email")
    host = manager._get("smtp_host")
    user = manager._get("smtp_user")
    password = manager._get("smtp_password")
    if not (to_address and host and user and password):
        return
    port = int(manager._get("smtp_port", "587"))

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = user
    message["To"] = to_address
    message.set_content(body)

    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30) as server:
            server.login(user, password)
            server.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(message)


def notify(manager, subject: str, body: str) -> None:
    """設定されている経路すべてに通知する。通知の失敗で処理は止めない。"""
    print(f"\n[通知] {subject}\n{body}")

    webhook = manager._get("notify_webhook")
    if webhook:
        try:
            _post_webhook(webhook, subject, body)
        except (urllib.error.URLError, OSError) as exc:
            print(f"[通知] Webhook 送信に失敗: {exc}", file=sys.stderr)

    try:
        _send_mail(manager, subject, body)
    except (smtplib.SMTPException, OSError, ValueError) as exc:
        print(f"[通知] メール送信に失敗: {exc}", file=sys.stderr)
