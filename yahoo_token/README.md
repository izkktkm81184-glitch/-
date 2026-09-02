# Yahoo!ショッピングAPI トークン自動更新

「【Yahooトークン更新のお願い】推定失効まで残り N 日」のメールを受けて、手作業
（URLを開く → 同意 → code をコピー → .env に貼る → スクリプト実行）を無くすための一式。

---

## 1. まず結論：有効期間は延ばせない

| トークン | 有効期限 | 延長できるか |
|---|---|---|
| アクセストークン | 1時間 | 不可（リフレッシュで取り直す） |
| リフレッシュトークン | **4週間**（公開鍵認証あり） | **不可**。更新しても起算日は延びない |
| リフレッシュトークン | 12時間（公開鍵認証なし） | 公開鍵を登録すれば4週間になる |
| 認可コード | 数分 | 不可 |

Yahoo!側の仕様として「4週間ごとに再認可してトークン情報を更新する」必要がある。
つまり**期間を延ばす道は無く、再認可そのものを自動化するしかない**。これが本ツールの方針。

> 現在の推定失効日が「取得日 + 28日」になっているなら公開鍵認証は効いている。
> もし12時間で切れているなら、ストアクリエイターPro で公開鍵を登録すれば4週間に伸びる
> （公開鍵にも有効期限があるので、切れていないかも合わせて確認する）。

---

## 2. 仕組み

```
毎日 09:00  タスクスケジューラ
    └─ yahoo_token.py watchdog
         ├─ 残り 7 日超  → アクセストークンを更新するだけ（数秒）
         └─ 残り 7 日以下 → yahoo_reauth.py で再認可
              ├─ 成功 → 新しいトークンを .env に保存（起算日リセット）→ 通知
              └─ 失敗 → 認可URL付きで通知（Slack/Discord/メール）
```

再認可はヘッドレスChromiumで認可URLを開き、`https://www.example.com/callback?code=...`
への遷移を**ブラウザ内で横取り**して認可コードを読む。実際に example.com へアクセスしないので、
Yahoo!デベロッパーネットワーク側の redirect_uri 設定は**変更不要**。

**パスワードは保存しない。** ログイン状態はブラウザプロファイル（`.browser_profile/`）の
Cookie としてローカルにだけ残る。初回に一度だけ手動ログインすれば（SMS認証やパスワードレスでも可）、
以降はログイン済み・同意済みのため無人でリダイレクトまで進む。

---

## 3. 導入手順

### 3-1. 配置

`china_import_db\yahoo_token\` に置く（`.env` は自動検出されるので場所は問わない）。

```
china_import_db\
├── ID・パス.env
├── scripts\yahoo_api_stock_test.py
└── yahoo_token\          ← これ一式
```

### 3-2. 依存パッケージ

コア（`yahoo_token.py`）は**標準ライブラリのみ**で動く。ブラウザ自動操作を使う場合だけ:

```
pip install playwright
playwright install chromium
```

### 3-3. .env の確認

既存のキー名の揺れ（`YAHOO_CLIENT_ID` / `YAHOO_APP_ID` / `CLIENT_ID` など）は自動で吸収する。
必須は client_id と client_secret、それに現在のリフレッシュトークン。

```dotenv
YAHOO_CLIENT_ID=dmVyPTIwMjUwNyZpZD04WU05...
YAHOO_CLIENT_SECRET=（アプリケーションのシークレット）
YAHOO_REFRESH_TOKEN=（現在のリフレッシュトークン）
YAHOO_REDIRECT_URI=https://www.example.com/callback   # 省略時この値

# 任意：通知先（未設定ならログに出るだけ）
YAHOO_NOTIFY_WEBHOOK=https://hooks.slack.com/services/...
```

`YAHOO_ACCESS_TOKEN` / `YAHOO_ACCESS_TOKEN_EXPIRES_AT` /
`YAHOO_REFRESH_TOKEN_ISSUED_AT` はツールが書き込む。書き込み前に `.bak` を残す。

### 3-4. 起算日を教える（初回だけ）

`YAHOO_REFRESH_TOKEN_ISSUED_AT` が無いと残り日数を計算できない。今回の再認可時に自動で
記録されるので、まずは 1 回再認可してしまうのが早い:

```
python yahoo_reauth.py --mode assist
```

画面付きブラウザが開くのでログインだけ行う。「同意する」以降は自動で進み、
認可コードの取得・交換・保存まで済む（コピペ不要）。

### 3-5. 無人実行の確認

```
python yahoo_reauth.py --mode auto
python yahoo_token.py status
```

`--mode auto` がそのまま通れば完全自動化が成立している。

### 3-6. タスクスケジューラに登録

```
powershell -ExecutionPolicy Bypass -File .\setup_windows_task.ps1
```

毎日 09:00 に点検し、残り 7 日を切ったら自動で再認可する。ログは `logs\` に日付別で残る。

```
powershell -ExecutionPolicy Bypass -File .\setup_windows_task.ps1 -At 08:30 -ThresholdDays 10
powershell -ExecutionPolicy Bypass -File .\setup_windows_task.ps1 -Unregister
```

---

## 4. 既存スクリプトへの組み込み

`scripts\yahoo_api_stock_test.py` 側は 1 行変えるだけでよい。
アクセストークンの期限切れ判定と更新はライブラリが行う。

```python
import sys
sys.path.insert(0, r"C:\...\china_import_db\yahoo_token")
from yahoo_token import get_access_token

headers = {"Authorization": f"Bearer {get_access_token()}"}
```

`get_access_token()` は .env のアクセストークンが残り 5 分未満なら自動で更新し、
新しい値を .env に書き戻す。

---

## 5. コマンド一覧

```
python yahoo_token.py status                 状態と残り日数
python yahoo_token.py token                  有効なアクセストークンを出力
python yahoo_token.py refresh                アクセストークンを強制更新
python yahoo_token.py url                    認可URLを表示
python yahoo_token.py exchange --url "..."   リダイレクト先URLを貼るだけで交換
python yahoo_token.py watchdog --dry-run     判定だけ実行

python yahoo_reauth.py --mode assist         初回・ログインが切れた時
python yahoo_reauth.py --mode auto           無人
python yahoo_reauth.py --mode manual         ブラウザ自動操作なし（従来手順の簡略版）
```

---

## 6. うまくいかない時

| 症状 | 原因と対処 |
|---|---|
| `--mode auto` で「ログインが切れている」 | Cookie の期限切れ。`--mode assist` を一度実行すれば復帰する |
| 12時間で失効する | 公開鍵認証が未設定か公開鍵が期限切れ。ストアクリエイターProで登録し直す |
| `invalid_grant` | リフレッシュトークンが失効済み。再認可が必要（watchdog が自動で行う） |
| `invalid_client` | client_id / client_secret の誤り。Basic 認証と POST 両方を自動で試すので、通らなければ値そのものを疑う |
| 残り日数が「不明」 | `YAHOO_REFRESH_TOKEN_ISSUED_AT` 未記録。一度再認可すれば記録される |

---

## 7. テスト

ネットワークに繋がずに動作確認できる（トークンエンドポイントの応答を差し替えている）。

```
python test_yahoo_token.py
```

.env の書き戻しでコメント・改行コード・文字コード・BOM・無関係なキーが
壊れないことを含めて 58 項目を検証する。

---

## 8. 注意

- `.browser_profile/` には Yahoo! のログイン Cookie が入る。`.env` と同じ扱いで、
  リポジトリにコミットしないこと（`.gitignore` 済み）。
- 自動再認可は Yahoo! 側の画面変更で壊れうる。だから watchdog は失敗時に必ず通知し、
  手動用の認可URLを添える。しきい値 7 日は「壊れてから気づいて直すまでの猶予」。
- 認可コードの寿命は数分。取得後は即座に交換している。

## 出典

- [Yahoo! ID連携 FAQ](https://developer.yahoo.co.jp/yconnect/faq.html)
- [Tokenエンドポイント](https://developer.yahoo.co.jp/yconnect/v2/authorization_code/token.html)
- [Yahoo!ショッピングAPI よくあるご質問](https://developer.yahoo.co.jp/webapi/shopping/faq.html)
- [リフレッシュトークン再取得時の仕様変更](https://developer.yahoo.co.jp/changelog/2020-02-28-yconnect.html)
