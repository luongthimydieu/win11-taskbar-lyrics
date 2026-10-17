#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_ico.py — 把一张 PNG 转成 Windows 多尺寸 .ico（给 exe / 快捷方式用）。

用法：
    python tools/make_ico.py desktop/logo.png desktop/logo.ico
    python tools/make_ico.py logo.png            # 默认输出同目录 logo.ico

只依赖 Pillow。会自动居中裁成正方形，再生成 16~256 多档尺寸。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

SIZES = [(16, 16), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)]


def fit_square(image: Image.Image, size: int) -> Image.Image:
    """居中裁成正方形并缩放到目标尺寸。"""
    image = image.convert("RGBA")
    width, height = image.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    cropped = image.crop((left, top, left + side, top + side))
    resample = getattr(Image, "Resampling", Image).LANCZOS
    return cropped.resize((size, size), resample)


def main() -> None:
    parser = argparse.ArgumentParser(description="PNG 转 Windows .ico")
    parser.add_argument("source", type=Path, help="源 PNG（建议 1024x1024）")
    parser.add_argument("output", type=Path, nargs="?", default=None, help="输出 .ico 路径")
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(f"找不到源文件：{args.source}")
    output = args.output or args.source.with_suffix(".ico")
    output.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(args.source) as source:
        # ICO 的 256 档要求原图至少 256，这里先用最大尺寸统一缩放
        base = fit_square(source, 256)
        base.save(output, format="ICO", sizes=SIZES)
    print(f"已生成 {output}（{', '.join(f'{w}x{h}' for w, h in SIZES)}）")


if __name__ == "__main__":
    main()
