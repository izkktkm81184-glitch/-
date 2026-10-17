"""仕入れ元の商品写真から出品用画像を生成する.

- 1枚目 (メイン): 白背景・正方形・文字なし (楽天の商品画像ガイドラインに合わせ、テキストや枠を入れない)
- 2枚目以降: 他の商品写真を同じ体裁に整えたもの
- 訴求画像: 商品写真 + おすすめポイント
- 仕様画像: 仕様表 + ご注意
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Callable

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageOps

from ..models import ListingCopy

FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "C:/Windows/Fonts/meiryo.ttc",
)

ACCENT = (191, 0, 0)
TEXT = (34, 34, 34)
SUB_BG = (245, 245, 245)
MAX_EXTRA_PHOTOS = 5


def load_font(size: int, font_path: str = "") -> ImageFont.FreeTypeFont:
    for p in (font_path, *FONT_CANDIDATES):
        if p and Path(p).exists():
            return ImageFont.truetype(p, size)
    raise FileNotFoundError("日本語フォントが見つかりません。FONT_PATH を設定してください")


def fetch_image(ref: str, client: httpx.Client | None = None) -> Image.Image:
    """URL またはローカルパスから画像を読み込む."""
    if ref.startswith(("http://", "https://")):
        c = client or httpx.Client(timeout=30, follow_redirects=True)
        r = c.get(ref)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content))
    else:
        img = Image.open(ref)
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img)
    return img.convert("RGB")


def trim_white(img: Image.Image, threshold: int = 245) -> Image.Image:
    """周囲の白い余白を取り除く."""
    gray = img.convert("L").point(lambda v: 255 if v < threshold else 0)
    box = gray.getbbox()
    return img.crop(box) if box else img


def fit_square(img: Image.Image, size: int, fill: float = 0.88) -> Image.Image:
    canvas = Image.new("RGB", (size, size), (255, 255, 255))
    inner = int(size * fill)
    product = trim_white(img)
    product.thumbnail((inner, inner), Image.LANCZOS)
    canvas.paste(product, ((size - product.width) // 2, (size - product.height) // 2))
    return canvas


def wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    lines, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) > max_w:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def feature_image(photo: Image.Image, copy: ListingCopy, header: str, size: int, font_path: str) -> Image.Image:
    canvas = Image.new("RGB", (size, size), (255, 255, 255))
    d = ImageDraw.Draw(canvas)
    pad = size // 20
    head_font = load_font(size // 24, font_path)
    point_font = load_font(size // 30, font_path)

    # 上部帯
    band_h = size // 10
    d.rectangle([0, 0, size, band_h], fill=ACCENT)
    d.text((pad, band_h // 2), header, font=head_font, fill=(255, 255, 255), anchor="lm")

    # 商品写真
    photo_area = int(size * 0.52)
    product = trim_white(photo)
    product.thumbnail((size - pad * 2, photo_area - pad), Image.LANCZOS)
    canvas.paste(product, ((size - product.width) // 2, band_h + (photo_area - product.height) // 2))

    # おすすめポイント
    y = band_h + photo_area + pad // 2
    box_h = size - y - pad
    d.rounded_rectangle([pad, y, size - pad, y + box_h], radius=size // 60, fill=SUB_BG)
    y += pad // 2
    line_h = int(point_font.size * 1.5)
    for i, p in enumerate(copy.selling_points[:4], 1):
        for j, line in enumerate(wrap(d, p, point_font, size - pad * 4 - point_font.size * 2)):
            if y + line_h > size - pad:
                break
            if j == 0:
                d.text((pad * 2, y), f"{i}", font=point_font, fill=ACCENT)
            d.text((pad * 2 + point_font.size * 2, y), line, font=point_font, fill=TEXT)
            y += line_h
        y += line_h // 3
    return canvas


def spec_image(copy: ListingCopy, notes: list[str], size: int, font_path: str) -> Image.Image:
    canvas = Image.new("RGB", (size, size), (255, 255, 255))
    d = ImageDraw.Draw(canvas)
    pad = size // 16
    title_font = load_font(size // 22, font_path)
    font = load_font(size // 28, font_path)
    small = load_font(size // 36, font_path)

    y = pad
    d.text((pad, y), "商品仕様", font=title_font, fill=ACCENT)
    y += int(title_font.size * 1.8)
    label_w = int(size * 0.28)
    row_h = int(font.size * 2.0)
    for r in copy.specs[:10]:
        d.rectangle([pad, y, pad + label_w, y + row_h], fill=SUB_BG)
        d.line([pad, y + row_h, size - pad, y + row_h], fill=(220, 220, 220), width=2)
        d.text((pad + font.size // 2, y + row_h // 2), r.label, font=font, fill=TEXT, anchor="lm")
        value = wrap(d, r.value, font, size - pad * 2 - label_w - font.size)[0]
        d.text((pad + label_w + font.size // 2, y + row_h // 2), value, font=font, fill=TEXT, anchor="lm")
        y += row_h

    if notes:
        y += pad // 2
        d.text((pad, y), "ご注意", font=font, fill=ACCENT)
        y += int(font.size * 1.6)
        for n in notes:
            for line in wrap(d, f"・{n}", small, size - pad * 2):
                if y > size - pad:
                    break
                d.text((pad, y), line, font=small, fill=TEXT)
                y += int(small.size * 1.5)
    return canvas


def compose(
    photos: list[Image.Image],
    copy: ListingCopy,
    header: str,
    notes: list[str],
    out_dir: Path,
    file_names: Callable[[int], str],
    size: int = 1200,
    font_path: str = "",
) -> list[str]:
    """画像を生成して保存し、パスのリストを返す (先頭がメイン画像)."""
    if not photos:
        raise ValueError("商品写真がありません (image_urls を指定してください)")
    out_dir.mkdir(parents=True, exist_ok=True)
    images = [fit_square(photos[0], size)]
    images += [fit_square(p, size) for p in photos[1 : 1 + MAX_EXTRA_PHOTOS]]
    images.append(feature_image(photos[0], copy, header, size, font_path))
    images.append(spec_image(copy, notes, size, font_path))

    paths = []
    for i, img in enumerate(images, 1):
        path = out_dir / file_names(i)
        img.save(path, "JPEG", quality=90, optimize=True)
        paths.append(str(path))
    return paths
