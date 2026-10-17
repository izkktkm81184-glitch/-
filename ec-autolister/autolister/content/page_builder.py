"""生成した文章と画像から、各モールの商品説明 HTML とレビュー用プレビューを作る."""

from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup, escape

from ..config import Settings
from ..models import ListingContent, ListingCopy, ResearchResult, SourceProduct
from . import limits


def _nl2br(value: str) -> Markup:
    return Markup("<br>\n".join(escape(line) for line in str(value).splitlines()))


_env = Environment(
    loader=PackageLoader("autolister", "content/templates"),
    autoescape=select_autoescape(["j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)
_env.filters["nl2br"] = _nl2br


def cabinet_file_name(sku: str, index: int) -> str:
    """R-Cabinet のファイル名 (半角英数小文字・20文字以内)."""
    slug = re.sub(r"[^a-z0-9]", "", sku.lower())[:14] or "item"
    return f"{slug}_{index}.jpg"


def cabinet_url(s: Settings, file_name: str) -> str:
    folder = f"{s.rakuten_cabinet_folder_path}/" if s.rakuten_cabinet_folder_path else ""
    return f"https://image.rakuten.co.jp/{s.rakuten_shop_url}/cabinet/{folder}{file_name}"


def plain_explanation(c: ListingCopy, notes: list[str]) -> str:
    """Yahoo 商品情報 (explanation) はテキストのみ."""
    parts = [c.lead, "", *[f"・{p}" for p in c.selling_points], ""]
    parts += [f"{r.label}: {r.value}" for r in c.specs]
    parts += ["", *notes]
    return limits.truncate("\n".join(parts).strip(), limits.YAHOO_EXPLANATION_LIMIT)


def build(
    src: SourceProduct,
    copy: ListingCopy,
    images: list[str],
    research: ResearchResult | None,
    s: Settings,
    warnings: list[str],
) -> ListingContent:
    notes = [n for n in s.shop_notes.split("|") if n]
    image_urls = [cabinet_url(s, cabinet_file_name(src.sku, i)) for i in range(1, len(images) + 1)]
    ctx = {"c": copy, "notes": notes, "image_urls": image_urls, "alt": copy.yahoo_title}

    rakuten_pc = _env.get_template("rakuten_pc.html.j2").render(ctx)
    rakuten_sp = _env.get_template("rakuten_sp.html.j2").render(ctx)
    rakuten_sales = _env.get_template("rakuten_sales.html.j2").render(ctx)
    yahoo_caption = _env.get_template("yahoo_caption.html.j2").render(ctx)

    warnings = list(warnings)
    for name, html, limit in (
        ("楽天 PC用商品説明文", rakuten_pc, limits.RAKUTEN_DESCRIPTION_LIMIT),
        ("楽天 SP用商品説明文", rakuten_sp, limits.RAKUTEN_DESCRIPTION_LIMIT),
        ("Yahoo 商品説明", yahoo_caption, limits.YAHOO_CAPTION_LIMIT),
    ):
        if limits.width(html) > limit:
            warnings.append(f"{name} が上限 ({limit}) を超えています")

    return ListingContent(
        copy_text=copy,
        rakuten_pc_description=rakuten_pc,
        rakuten_sp_description=rakuten_sp,
        rakuten_sales_description=rakuten_sales,
        yahoo_explanation=plain_explanation(copy, notes),
        yahoo_caption=yahoo_caption,
        yahoo_sp_additional=rakuten_sp,
        images=images,
        warnings=warnings,
    )


def write_preview(
    src: SourceProduct, content: ListingContent, research: ResearchResult | None, out: Path
) -> Path:
    html = _env.get_template("preview.html.j2").render(
        src=src,
        c=content.copy_text,
        research=research,
        images=[Path(p).name for p in content.images],
        warnings=content.warnings,
        rakuten_pc=Markup(content.rakuten_pc_description),
        yahoo_caption=Markup(content.yahoo_caption),
        yahoo_explanation=content.yahoo_explanation,
    )
    path = out / "preview.html"
    path.write_text(html, encoding="utf-8")
    return path
