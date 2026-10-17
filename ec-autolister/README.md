# ec-autolister

楽天市場・Yahoo!ショッピングへの出品を **リサーチ → 画像生成 → 商品ページ作成 → 出品** まで自動で行うツールです。

```
仕入れ候補CSV ──▶ ① リサーチ ──▶ ② 承認 ──▶ ③ 画像・商品ページ生成 ──▶ ④ 出品
                  楽天/Yahoo 検索API   手動 or      Claude (文章)              楽天 RMS API
                  競合最安値・需要     ランクで     Pillow (画像)              Yahoo ストアAPI
                  販売価格・利益・ランク 自動承認    HTML テンプレート
```

| ステージ | 内容 |
| --- | --- |
| ① リサーチ | 型番で楽天・Yahoo を検索し、型番が一致しない商品や中古品を除外して競合最安値を取得。最安値から `UNDERCUT_JPY` 円下げた価格 (ただし最低利益ラインは割らない) で利益を計算し、需要 (競合のレビュー数) と合わせて S〜D のランクを付けます。ランクの考え方は POIZON Calc と同じ「利益率 × 回転」です。 |
| ② 承認 | ランクを見て `approve` / `reject`。`AUTO_APPROVE_GRADE=A` などにすると、そのランク以上は自動承認されます。D (赤字、または最安値より大幅に高くなる) は自動で見送ります。 |
| ③ 生成 | Claude が商品名・キャッチコピー・説明文・仕様表を作成し、モールごとの文字数上限と NG 表現 (「最安」「日本一」など) をチェックします。仕入れ元の写真から、白背景のメイン画像・訴求画像・仕様画像を作ります。楽天 PC/SP 説明文、Yahoo の説明文 HTML とレビュー用の `preview.html` を出力します。 |
| ④ 出品 | 楽天: R-Cabinet に画像をアップロード → 商品API 2.0 で登録 → 在庫API で在庫を設定。Yahoo: editItem → uploadItemImage → setStock → reservePublish (反映)。 |

## 安全のための既定値

- **`DRY_RUN=true`**: 出品APIを呼ばず、送るはずのリクエストを `data/output/<sku>/{rakuten,yahoo}/requests/` に保存するだけです。本番では `DRY_RUN=false` にし、`publish --yes` で実行します。
- **非公開で登録**: 楽天は倉庫 (`RAKUTEN_HIDE_ITEM=true`)、Yahoo は非公開 (`YAHOO_DISPLAY=false`) で登録されます。管理画面で確認してから公開してください。
- **NG 表現・文字数超過の警告がある商品は出品しません**。`preview.html` で確認し、問題なければ `publish --force` で出品します。
- 自動承認 (`AUTO_APPROVE_GRADE`) は既定で無効です。

## セットアップ

```bash
cd ec-autolister
pip install -e ".[dev]"
cp .env.example .env   # API キー等を設定
```

必要なもの:

| 用途 | 取得先 | .env |
| --- | --- | --- |
| 文章生成 | Claude API キー | `ANTHROPIC_API_KEY` |
| 楽天の競合検索 | 楽天ウェブサービス アプリID | `RAKUTEN_APP_ID` |
| Yahoo の競合検索 | Yahoo!デベロッパーネットワーク Client ID | `YAHOO_APP_ID` |
| 楽天への出品 | RMS WEB SERVICE の serviceSecret / licenseKey (RMS で利用申請。licenseKey は約3か月で失効) | `RAKUTEN_SERVICE_SECRET`, `RAKUTEN_LICENSE_KEY`, `RAKUTEN_SHOP_URL` |
| Yahoo への出品 | ストアAPI の Client ID / シークレット、公開鍵 (ストアクリエイターPro で発行) | `YAHOO_SELLER_ID`, `YAHOO_CLIENT_ID`, `YAHOO_CLIENT_SECRET`, `YAHOO_PUBLIC_KEY_PATH`, `YAHOO_PUBLIC_KEY_VERSION` |

Yahoo は初回だけ OAuth 認可が必要です:

```bash
autolister yahoo-auth            # 表示された URL をブラウザで開いて同意
autolister yahoo-auth --code XXX # 表示された code を渡すとトークンを DB に保存
```

## 使い方

仕入れ候補を CSV で用意します (`data/sources.sample.csv` 参照)。`sizes` と `image_urls` は `|` 区切り、`image_urls` は URL かローカルパスです。

| 列 | 必須 | 内容 |
| --- | --- | --- |
| `sku` | ○ | 自社の商品管理番号 (楽天の商品管理番号・Yahoo の商品コードの元になる) |
| `name` | ○ | 商品名 |
| `cost_jpy` | ○ | 仕入原価 (送料・関税込み) |
| `brand`, `model_no` | | 検索キーワードと、競合を型番で絞り込むのに使用 |
| `sizes` | | サイズ展開。楽天はバリエーション、Yahoo は個別商品コードになる |
| `shipping_jpy` | | お客様への送料 (未指定なら `DEFAULT_SHIPPING_JPY`) |
| `stock` | | 在庫数 (サイズごと) |
| `image_urls` | | 仕入れ元の商品写真 (生成に必須) |
| `search_keyword` | | 検索キーワードを上書き |
| `rakuten_genre_id`, `yahoo_product_category` | | カテゴリ (未指定なら .env の既定値) |

```bash
autolister import data/sources.csv   # 取り込み
autolister research                  # リサーチ (ランク・価格・利益を表示)
autolister list                      # 一覧
autolister approve NK-DD1391-100     # 承認 (reject で却下)
autolister build                     # 画像・商品ページ生成 → data/output/<sku>/preview.html を確認
autolister publish                   # 出品 (DRY_RUN=false のときは --yes が必要)

autolister run --csv data/sources.csv  # 上記を一括 (承認は AUTO_APPROVE_GRADE 次第)
autolister discover --genre-id 558885  # 楽天ランキングを CSV 出力 (仕入れ候補探し)
autolister reset SKU --to approved     # エラーになった商品をやり直す
```

ステータスは `new → researched → approved → built → published` と進みます (`skipped` = 見送り、`rejected` = 却下、`error` = 失敗。理由は `list` に表示)。

## 定期実行

**GitHub Actions** (`.github/workflows/ec-autolister.yml`): リポジトリ変数 `AUTOLISTER_SCHEDULE=true` で毎朝実行されます。
API キーは Secrets、店舗設定は Variables に登録します。`AUTO_APPROVE_GRADE` を設定しないと承認待ちのまま止まります。
`AUTOLISTER_PUBLISH=true` にするまでは出品せず、生成物を Artifact として残すだけです。仕入れ候補は `ec-autolister/sources/sources.csv` に置きます。

**cron** で動かす場合:

```cron
0 9 * * * cd /path/to/ec-autolister && autolister run --csv data/sources.csv --yes >> data/run.log 2>&1
```

## 調整ポイント

- **手数料率** (`RAKUTEN_FEE_RATE` / `YAHOO_FEE_RATE`): 契約プラン・決済手数料・ポイント原資・アフィリエイト料率の合計に合わせてください。
- **ランク基準**: `autolister/pricing.py` の `grade()`。
- **画像の体裁**: `autolister/images/composer.py`。メイン画像は楽天の商品画像ガイドラインに合わせ、文字や枠を入れていません。
- **説明文のテンプレート**: `autolister/content/templates/`。
- **文字数上限・NG 表現**: `autolister/content/limits.py`。

## 注意事項

- API の項目名や上限値は、実装時点で確認できた仕様をもとにしています。モール側で変更されることがあるので、初回は `DRY_RUN=true` で `requests/` の内容を確認し、Yahoo は検証環境 (`YAHOO_STORE_API_BASE` を `https://test.circus...` に) で試してから本番に切り替えてください。特に次の項目は、最新の仕様書と照らし合わせて確認してください: 楽天のバリエーション・送料フィールド、Yahoo の `options` / `subcodes` の書式と `delivery` (送料無料) の値。
- ブランド品は、権利者の画像を無断で使わず、自社で撮影した写真や使用許諾のある写真を使ってください。並行輸入品であることなど、必要な表示は `notes` か `SHOP_NOTES` に入れてください。
- 生成された文章は、根拠のない表現が含まれていないか、出品前に `preview.html` で確認することをおすすめします。

## テスト

```bash
pytest -q
```

外部 API はすべてモックしているので、キーがなくても実行できます。
