"""コマンドライン: `autolister <command>`."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path

from .config import Settings
from .db import Store
from .pipeline import Pipeline


def _skus(args) -> list[str] | None:
    return args.sku or None


def cmd_import(p: Pipeline, args) -> None:
    print(f"{p.import_csv(Path(args.csv))} 件を取り込みました")


def cmd_discover(p: Pipeline, args) -> None:
    from .research.search import RakutenSearch

    rows = RakutenSearch(p.s).ranking(genre_id=args.genre_id, page=args.page)
    w = csv.DictWriter(sys.stdout, fieldnames=list(rows[0]) if rows else ["rank"])
    w.writeheader()
    w.writerows(rows)


def cmd_research(p: Pipeline, args) -> None:
    for r in p.research(_skus(args), include_done=args.all):
        print(f"[{r.grade}] {r.sku}")
        for line in r.reasons:
            print(f"    {line}")


def cmd_list(p: Pipeline, args) -> None:
    rows = p.store.by_status(*(args.status or []))
    for r in rows:
        err = f"  ! {r['error']}" if r["error"] else ""
        print(f"{r['status']:<10} {r['grade'] or '-':<2} {r['sku']}{err}")
    print(f"計 {len(rows)} 件")


def cmd_show(p: Pipeline, args) -> None:
    row = p.store.get(args.sku[0])
    if not row:
        sys.exit("見つかりません")
    print(json.dumps({k: row[k] for k in row.keys()}, ensure_ascii=False, indent=2))


def cmd_approve(p: Pipeline, args) -> None:
    print(f"{p.set_status(args.sku, 'approved')} 件を承認しました")


def cmd_reject(p: Pipeline, args) -> None:
    print(f"{p.set_status(args.sku, 'rejected')} 件を却下しました")


def cmd_reset(p: Pipeline, args) -> None:
    print(f"{p.set_status(args.sku, args.to)} 件を {args.to} に戻しました")


def cmd_build(p: Pipeline, args) -> None:
    built = p.build(_skus(args))
    for sku in built:
        print(f"生成: {p.out_dir(sku) / 'preview.html'}")


def cmd_publish(p: Pipeline, args) -> None:
    if not p.s.dry_run and not args.yes:
        sys.exit("本番出品です。確認のため --yes を付けて実行してください (DRY_RUN=false)")
    done = p.publish(_skus(args), force=args.force)
    print(f"{len(done)} 件 出品{'(ドライラン)' if p.s.dry_run else ''}")


def cmd_run(p: Pipeline, args) -> None:
    if not p.s.dry_run and args.publish and not args.yes:
        sys.exit("本番出品を含みます。--yes を付けて実行してください")
    p.run(Path(args.csv) if args.csv else None, publish=args.publish)
    cmd_list(p, argparse.Namespace(status=None))


def cmd_yahoo_auth(p: Pipeline, args) -> None:
    from .publish import yahoo_store

    if not args.code:
        print("以下のURLをブラウザで開き、同意後に表示される code を `--code` で渡してください:")
        print(yahoo_store.authorization_url(p.s))
        return
    yahoo_store.exchange_code(p.s, p.store, args.code)
    print("Yahoo のトークンを保存しました")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="autolister", description="楽天市場・Yahoo!ショッピング 出品自動化")
    ap.add_argument("--env", help=".env ファイルのパス")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn)
        return sp

    sp = add("import", cmd_import, "仕入れ候補CSVを取り込む")
    sp.add_argument("csv")

    sp = add("discover", cmd_discover, "楽天ランキングから売れ筋をCSV出力 (商品発掘用)")
    sp.add_argument("--genre-id", default="")
    sp.add_argument("--page", type=int, default=1)

    sp = add("research", cmd_research, "競合価格を調べて販売価格・利益・ランクを算出")
    sp.add_argument("sku", nargs="*")
    sp.add_argument("--all", action="store_true", help="リサーチ済みも再調査")

    sp = add("list", cmd_list, "商品とステータスの一覧")
    sp.add_argument("--status", nargs="*")

    sp = add("show", cmd_show, "商品の詳細 (JSON)")
    sp.add_argument("sku", nargs=1)

    sp = add("approve", cmd_approve, "出品を承認")
    sp.add_argument("sku", nargs="+")

    sp = add("reject", cmd_reject, "出品を却下")
    sp.add_argument("sku", nargs="+")

    sp = add("reset", cmd_reset, "ステータスを戻す (エラー後の再実行など)")
    sp.add_argument("sku", nargs="+")
    sp.add_argument("--to", default="approved", choices=["new", "approved", "built"])

    sp = add("build", cmd_build, "承認済み商品の画像・商品ページを生成")
    sp.add_argument("sku", nargs="*")

    sp = add("publish", cmd_publish, "生成済み商品を出品")
    sp.add_argument("sku", nargs="*")
    sp.add_argument("--yes", action="store_true", help="本番出品の確認")
    sp.add_argument("--force", action="store_true", help="要確認の警告があっても出品")

    sp = add("run", cmd_run, "取り込み→リサーチ→生成→出品 を一括実行")
    sp.add_argument("--csv")
    sp.add_argument("--no-publish", dest="publish", action="store_false")
    sp.add_argument("--yes", action="store_true")

    sp = add("yahoo-auth", cmd_yahoo_auth, "Yahoo ストアAPIの認可 (初回・トークン失効時)")
    sp.add_argument("--code")
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    s = Settings.from_env(args.env)
    store = Store(s.db_path)
    try:
        args.fn(Pipeline(s, store), args)
    except RuntimeError as e:
        sys.exit(f"エラー: {e}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
