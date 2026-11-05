"""从源图生成应用图标（PNG 多尺寸 + 多分辨率 .ico）。

    python tools/make_icon.py [源图]

源图是白底的方形插画，直接当图标会在深色标题栏 / 任务栏上出现白框。
这里从四角做容差泛洪，把**与边缘连通的近白区域**变透明，保留图形自带的
柔和投影（它比纯白暗，不会被泛洪吃掉）。

产物：
    assets/app_icon.png         256×256，供 Tk 标题栏 / 任务栏
    assets/app_icon_<n>.png     按 DPI 取用的整数尺寸
    assets/app_icon.ico         打包 exe / 快捷方式用
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

# Tk 只能做整数倍缩放，所以为常见缩放档预生成精确尺寸：
# 设计尺寸 20px 在 100% / 125% / 150% / 175% / 200% 下分别是 20/25/30/35/40
HEADER_SIZES = (20, 25, 30, 35, 40, 48, 60)
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def drop_white_background(img: Image.Image, tolerance: int = 26) -> Image.Image:
    """把与图像四边连通的近白像素设为透明。"""
    img = img.convert("RGBA")
    w, h = img.size
    px = img.load()
    visited = bytearray(w * h)
    queue: deque[tuple[int, int]] = deque()

    def near_white(x: int, y: int) -> bool:
        r, g, b, _ = px[x, y]
        return r >= 255 - tolerance and g >= 255 - tolerance and b >= 255 - tolerance

    for x in range(w):
        for y in (0, h - 1):
            if near_white(x, y) and not visited[y * w + x]:
                visited[y * w + x] = 1
                queue.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            if near_white(x, y) and not visited[y * w + x]:
                visited[y * w + x] = 1
                queue.append((x, y))

    while queue:
        x, y = queue.popleft()
        px[x, y] = (255, 255, 255, 0)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and not visited[ny * w + nx] and near_white(nx, ny):
                visited[ny * w + nx] = 1
                queue.append((nx, ny))

    bbox = img.getbbox()
    return img.crop(bbox) if bbox else img


def square(img: Image.Image, margin_ratio: float = 0.03) -> Image.Image:
    """补成正方形并留一点边距，避免不同尺寸下图形贴边。"""
    w, h = img.size
    side = max(w, h)
    margin = int(side * margin_ratio)
    canvas = Image.new("RGBA", (side + margin * 2, side + margin * 2), (0, 0, 0, 0))
    canvas.paste(img, (margin + (side - w) // 2, margin + (side - h) // 2), img)
    return canvas


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ASSETS / "app_icon_src.jpg"
    if not src.is_file():
        print(f"源图不存在：{src}")
        return 1

    ASSETS.mkdir(exist_ok=True)
    base = square(drop_white_background(Image.open(src)))

    # 256 主图，Tk 的 iconphoto 与 README 都用它
    base.resize((256, 256), Image.LANCZOS).save(ASSETS / "app_icon.png", optimize=True)
    for size in HEADER_SIZES:
        base.resize((size, size), Image.LANCZOS).save(
            ASSETS / f"app_icon_{size}.png", optimize=True)

    # .ico 必须用 BMP 条目：Tk 的 ICO 加载器不认 PNG 压缩条目，
    # 否则 iconbitmap 会静默失败、标题栏退回系统占位图标。
    base.resize((256, 256), Image.LANCZOS).save(
        ASSETS / "app_icon.ico",
        format="ICO",
        sizes=[(s, s) for s in ICO_SIZES],
        bitmap_format="bmp",
    )

    print("生成：")
    for name in ("app_icon.png", "app_icon.ico", *(f"app_icon_{s}.png" for s in HEADER_SIZES)):
        path = ASSETS / name
        print(f"  {name:20s} {path.stat().st_size / 1024:6.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
