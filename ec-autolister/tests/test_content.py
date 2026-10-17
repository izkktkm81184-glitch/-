from autolister.content import limits, page_builder
from autolister.images import composer
from autolister.models import SourceProduct


def test_width_and_truncate():
    assert limits.width("abc") == 3
    assert limits.width("あいう") == 6
    assert limits.truncate("あいうえ", 5) == "あい"


def test_enforce_truncates_and_flags_ng(listing_copy):
    c = listing_copy.model_copy(update={"yahoo_headline": "あ" * 40, "lead": "業界最安の一足"})
    fixed, warnings = limits.enforce(c)
    assert limits.width(fixed.yahoo_headline) <= 60
    assert any("yahoo_headline" in w for w in warnings)
    assert any("最安" in w for w in warnings)


def test_enforce_clean_copy_has_no_warnings(listing_copy):
    _, warnings = limits.enforce(listing_copy)
    assert warnings == []


def test_compose_and_build_pages(settings, listing_copy, photo, tmp_path):
    src = SourceProduct(sku="NK-DD1391", name="ダンク", cost_jpy=1, image_urls=[str(photo)])
    out = tmp_path / "out"
    imgs = composer.compose(
        [composer.fetch_image(str(photo))], listing_copy, "Nike DD1391-100", ["注意1"], out,
        lambda i: page_builder.cabinet_file_name(src.sku, i), size=600,
    )
    assert [p.split("/")[-1] for p in imgs] == ["nkdd1391_1.jpg", "nkdd1391_2.jpg", "nkdd1391_3.jpg"]
    content = page_builder.build(src, listing_copy, imgs, None, settings, [])
    assert "https://image.rakuten.co.jp/myshop/cabinet/auto/nkdd1391_1.jpg" in content.rakuten_sales_description
    assert "DD1391-100" in content.rakuten_pc_description
    assert "<" not in content.yahoo_explanation
    preview = page_builder.write_preview(src, content, None, out)
    assert "nkdd1391_1.jpg" in preview.read_text(encoding="utf-8")


def test_html_is_escaped(settings, listing_copy):
    c = listing_copy.model_copy(update={"lead": "<script>x</script>"})
    src = SourceProduct(sku="X", name="x", cost_jpy=1)
    content = page_builder.build(src, c, ["a.jpg"], None, settings, [])
    assert "<script>" not in content.rakuten_pc_description
