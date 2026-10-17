#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""generate.py — 生成 PWA 图标（icon-192 / icon-512 / apple-touch-icon）。

用法：
    python tools/make_icons.py                     # 输出到 ../github-pages
    python tools/make_icons.py --out <目录>

只依赖 Pillow（Anaconda 自带）。设计：深色圆角底 + 青色音符 + 右下角绿色对勾。
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BG_TOP = (15, 23, 42)
BG_BOTTOM = (30, 41, 59)
ACCENT = (56, 189, 248)
SUCCESS = (16, 185, 129)
FONT_CANDIDATES = (
    r"C:\Windows\Fonts\seguisym.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arial.ttf",
)


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in FONT_CANDIDATES:
        if os.path.exists(candidate):
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _vertical_gradient(size: int) -> Image.Image:
    gradient = Image.new("RGBA", (size, size))
    pixels = gradient.load()
    for y in range(size):
        ratio = y / max(1, size - 1)
        color = tuple(
            int(round(BG_TOP[index] + (BG_BOTTOM[index] - BG_TOP[index]) * ratio))
            for index in range(3)
        ) + (255,)
        for x in range(size):
            pixels[x, y] = color
    return gradient


def build_icon(size: int, opaque: bool = False) -> Image.Image:
    base = _vertical_gradient(size)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=255
    )
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    icon.paste(base, (0, 0), mask if not opaque else Image.new("L", (size, size), 255))

    draw = ImageDraw.Draw(icon)
    # 青色音符
    note_font = _load_font(int(size * 0.56))
    note = "♪"
    bbox = draw.textbbox((0, 0), note, font=note_font)
    note_w, note_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        ((size - note_w) / 2 - bbox[0], (size - note_h) / 2 - bbox[1] - size * 0.04),
        note,
        font=note_font,
        fill=ACCENT,
    )
    # 右下角绿色对勾徽标
    badge = int(size * 0.30)
    cx, cy = int(size * 0.72), int(size * 0.72)
    draw.ellipse(
        [cx - badge // 2, cy - badge // 2, cx + badge // 2, cy + badge // 2],
        fill=SUCCESS,
    )
    check_font = _load_font(int(badge * 0.72))
    check = "✓"
    bbox = draw.textbbox((0, 0), check, font=check_font)
    draw.text(
        (cx - (bbox[2] - bbox[0]) / 2 - bbox[0], cy - (bbox[3] - bbox[1]) / 2 - bbox[1]),
        check,
        font=check_font,
        fill=(5, 46, 31, 255),
    )
    return icon


def _fit_square(image: Image.Image, size: int) -> Image.Image:
    """把任意尺寸图片居中裁成正方形并缩放到目标尺寸。"""
    image = image.convert("RGBA")
    width, height = image.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    cropped = image.crop((left, top, left + side, top + side))
    resample = getattr(Image, "Resampling", Image).LANCZOS
    return cropped.resize((size, size), resample)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 PWA 图标")
    default_out = Path(__file__).resolve().parent.parent / "github-pages"
    parser.add_argument("--out", type=Path, default=default_out, help="输出目录")
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="用现成的 PNG 生成（例如 AI 生成的 logo），默认用内置绘制",
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    targets = (
        ("icon-192.png", 192, False),
        ("icon-512.png", 512, False),
        ("apple-touch-icon.png", 180, True),
    )
    for filename, size, opaque in targets:
        if args.source is not None:
            if not args.source.exists():
                raise SystemExit(f"找不到源文件：{args.source}")
            with Image.open(args.source) as source:
                icon = _fit_square(source, size)
                if opaque:
                    background = Image.new("RGBA", icon.size, (15, 23, 42, 255))
                    background.alpha_composite(icon)
                    icon = background
        else:
            icon = build_icon(size, opaque=opaque)
        path = args.out / filename
        icon.save(path, "PNG", optimize=True)
        print(f"已生成 {path} ({size}x{size})")


if __name__ == "__main__":
    main()
