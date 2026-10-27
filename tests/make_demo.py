"""生成 GUI 演示曲库：两张专辑共 23 首，标签与设计稿一致。

用途：TagForge 桌面版启动后默认打开这个目录，界面即为设计稿的数据状态。

    python tests/make_demo.py
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402

from tagforge import adapters  # noqa: E402
from tagforge.adapters import Cover  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent / "samples" / "DemoLibrary"

RAM = ["Give Life Back to Music", "The Game of Love", "Giorgio by Moroder", "Within",
       "Instant Crush", "Lose Yourself to Dance", "Touch", "Get Lucky", "Beyond",
       "Motherboard", "Fragments of Time", "Doin' It Right", "Contact"]
IR = ["15 Step", "Bodysnatchers", "Nude", "Weird Fishes / Arpeggi", "All I Need",
      "Faust Arp", "Reckoner", "House of Cards", "Jigsaw Falling Into Place", "Videotape"]


def safe_name(text: str) -> str:
    """去掉 Windows 文件名非法字符（"Weird Fishes / Arpeggi" 这类带斜杠的曲名）。"""
    for ch in '<>:"/\\|?*':
        text = text.replace(ch, " ")
    return " ".join(text.split()).strip(". ")


def cover_png(kind: str) -> bytes:
    """用参考封面生成两个变体（RAM 原色 / In Rainbows 冷蓝调），PNG 便于 Tk 直接显示。"""
    src = Path(__file__).resolve().parent.parent / "samples" / "assets" / "cover_ref.jpg"
    im = Image.open(src).convert("RGB").resize((300, 300), Image.LANCZOS)
    if kind == "ir":
        r, g, b = im.split()
        im = Image.merge("RGB", (
            r.point(lambda v: int(v * 0.62)),
            g.point(lambda v: min(255, int(v * 0.86 + 8))),
            b.point(lambda v: min(255, int(v * 1.15 + 14))),
        ))
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def make_audio(path: Path, fmt: str, freq: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    codec = {"mp3": ["-c:a", "libmp3lame", "-q:a", "7", "-id3v2_version", "4"],
             "m4a": ["-c:a", "aac", "-b:a", "96k"]}[fmt]
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", f"sine=frequency={freq}:duration=3", *codec, str(path)], check=True)


def main() -> int:
    covers = {"ram": cover_png("ram"), "ir": cover_png("ir")}
    plan = []

    for i, title in enumerate(RAM, start=1):
        plan.append(dict(
            rel=f"Daft Punk/RAM/{i:02d} - {safe_name(title)}.mp3",
            fmt="mp3", freq=380 + i * 17,
            tags=dict(title=title, artist="Daft Punk", album="Random Access Memories",
                      albumartist="Daft Punk", year="2013", track=str(i), tracktotal="13",
                      disc="1", genre="Electronic", composer="Bangalter / Homem-Christo"),
            cover=covers["ram"], cover_key="ram",
        ))
    # 设计稿里第 6 首流派留空
    plan[5]["tags"]["genre"] = ""

    for i, title in enumerate(IR, start=1):
        ext = "m4a" if i == 3 else "mp3"
        plan.append(dict(
            rel=f"Radiohead/InRainbows/{i:02d} - {safe_name(title)}.{ext}",
            fmt=ext, freq=300 + i * 23,
            tags=dict(title=title, artist="Radiohead", album="In Rainbows",
                      albumartist="Radiohead", year="2007", track=str(i), tracktotal="10",
                      disc="1", genre="Alternative"),
            cover=covers["ir"], cover_key="ir",
        ))
    plan[13 + 3]["tags"]["year"] = ""      # 第 04 首年份留空
    plan[13 + 5]["cover"] = None           # 第 06 首无封面

    for item in plan:
        path = ROOT / item["rel"]
        if path.exists():
            path.unlink()
        make_audio(path, item["fmt"], item["freq"])
        patch = {k: v for k, v in item["tags"].items() if v}
        adapters.write_tags(str(path), patch, mode="overwrite")
        if item["cover"]:
            adapters.write_tags(str(path), {"cover": Cover(item["cover"], "image/png")}, "overwrite")
        print(f"  {item['rel']}")

    print(f"\n共 {len(plan)} 个文件 -> {ROOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
