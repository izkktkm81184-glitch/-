import json

from autolister.pipeline import Pipeline, read_sources

from conftest import FakeCopywriter, FakeSearcher


def _csv(tmp_path, photo):
    p = tmp_path / "src.csv"
    p.write_text(
        "sku,name,brand,model_no,sizes,cost_jpy,shipping_jpy,stock,image_urls\n"
        f"NK-1,ダンク ロー,Nike,DD1391-100,27.0cm|27.5cm,10000,1000,1,{photo}\n"
        f"NK-2,赤字商品,Nike,DD1391-100,,30000,1000,1,{photo}\n",
        encoding="utf-8",
    )
    return p


def test_read_sources(tmp_path, photo):
    items = read_sources(_csv(tmp_path, photo))
    assert items[0].sizes == ["27.0cm", "27.5cm"] and items[0].cost_jpy == 10000
    assert items[1].sizes == []


def test_end_to_end_dry_run(settings, store, listing_copy, photo, tmp_path):
    cw = FakeCopywriter(listing_copy)
    p = Pipeline(settings, store,
                 searchers=[FakeSearcher("rakuten", [16000], reviews=60), FakeSearcher("yahoo", [15800], reviews=60)],
                 copywriter=cw)
    assert p.import_csv(_csv(tmp_path, photo)) == 2

    p.research()
    assert store.get("NK-1")["status"] == "researched"
    assert store.get("NK-2")["status"] == "skipped"  # 原価が競合価格を上回る

    p.build()
    assert cw.calls == 0  # 未承認は生成しない
    p.set_status(["NK-1"], "approved")
    assert p.build() == ["NK-1"]
    assert (p.out_dir("NK-1") / "preview.html").exists()

    done = p.publish()
    assert set(done["NK-1"]) == {"rakuten", "yahoo"}
    row = store.get("NK-1")
    assert row["status"] == "built"  # ドライランなので published にはしない
    assert json.loads(row["publish_json"])["rakuten"]["dry_run"] is True
    assert (settings.output_dir / "_yahoo_publish" / "requests").exists()


def test_auto_approve_and_warning_gate(settings, store, listing_copy, photo, tmp_path):
    s = settings.__class__(**{**settings.__dict__, "auto_approve_grade": "B"})
    bad = listing_copy.model_copy(update={"lead": "日本一の人気"})
    p = Pipeline(s, store, searchers=[FakeSearcher("rakuten", [16000], reviews=60)], copywriter=FakeCopywriter(bad))
    p.import_csv(_csv(tmp_path, photo))
    p.run()
    assert store.get("NK-1")["status"] == "built"
    assert store.get("NK-1")["publish_json"] is None  # NG表現の警告で出品保留
    assert p.publish(force=True)


def test_build_error_is_recorded(settings, store, listing_copy, tmp_path):
    p = Pipeline(settings, store, searchers=[], copywriter=FakeCopywriter(listing_copy))
    store.upsert_source("X", {"sku": "X", "name": "x", "cost_jpy": 1, "image_urls": []})
    p.set_status(["X"], "approved")
    assert p.build() == []
    assert store.get("X")["status"] == "error"
    assert "商品写真" in store.get("X")["error"]


def test_research_without_search_keys_fails_loudly(settings, store):
    import pytest

    with pytest.raises(RuntimeError, match="検索APIキー"):
        Pipeline(settings, store).research()
