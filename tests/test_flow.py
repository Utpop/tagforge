"""端到端验证：扫描 -> 文件名反解 -> 批量写入 -> 封面 -> 补空模式 -> 撤销。

直接跑 pytest 也行，但这里刻意写成可独立执行的脚本，
方便在没装 pytest 的机器上一条命令验证。
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tagforge import adapters, library, naming  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples" / "Music"

passed = 0
failed = 0


def check(label: str, condition: bool, extra: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label} {extra}")


def demo_cover() -> bytes:
    from PIL import Image

    image = Image.new("RGB", (600, 600), (28, 42, 68))
    for x in range(600):
        for y in range(0, 600, 12):
            if (x + y) % 120 < 40:
                image.putpixel((x, y), (90, 150, 220))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def main() -> int:
    if not SAMPLES.is_dir():
        print("缺少 samples，请先运行 tests/make_samples.py")
        return 1

    print("\n[1] 扫描")
    result = library.scan(str(SAMPLES), recursive=True)
    print(f"     找到 {result['count']} 个文件")
    check("扫描到 8 个文件", result["count"] == 8, str(result["count"]))
    engines = sorted({r["engine"] for r in result["records"]})
    print(f"     引擎分布 {engines}")
    check("格式分派正确（wav 走 id3 引擎）", set(engines) == {"id3", "vorbis", "mp4"}, str(engines))
    check("无解析错误", all(r["error"] is None for r in result["records"]))

    print("\n[2] 从目录结构生成标签（dry-run）")
    items = [{"path": r["path"], "fields": r["fields"]} for r in result["records"]]
    template = "%albumartist%/%album%/%track% - %title%"
    preview = library.preview_from_names(items, template, str(SAMPLES), True, True)
    check("全部匹配", preview["matched"] == preview["total"], f"{preview['matched']}/{preview['total']}")
    sample = next(r for r in preview["rows"] if "Daft Punk" in r["path"])
    print(f"     {sample['target']}")
    print(f"     -> {sample['parsed']}")
    check("解析出艺术家", sample["parsed"].get("albumartist") == "Daft Punk")
    check("解析出音轨号", sample["parsed"].get("track") == "1")

    print("\n[3] 写入标签")
    changes = [{"path": r["path"], "patch": r["changes"]} for r in preview["rows"] if r["changes"]]
    applied = library.apply_changes(changes, mode="overwrite", label="文件名生成")
    check("写入成功数 = 文件数", applied["applied"] == 8, str(applied))
    check("没有失败", not applied["failed"], str(applied["failed"]))

    print("\n[4] 回读验证（跨格式字段映射）")
    by_ext = {}
    by_name = {}
    for record in result["records"]:
        fields, has_cover = adapters.read_record(record["path"])
        by_ext[Path(record["path"]).suffix] = fields
        by_name[Path(record["path"]).name] = fields
    for ext, fields in sorted(by_ext.items()):
        print(f"     {ext:6s} {fields}")
    check(
        "mp3 标题正确",
        by_name["01 - Give Life Back to Music.mp3"].get("title") == "Give Life Back to Music",
        str(by_name.get("01 - Give Life Back to Music.mp3")),
    )
    check(
        "mp3 音轨号带前导零已归一",
        by_name["03 - Giorgio by Moroder.mp3"].get("track") == "3",
    )
    check("flac 专辑正确", by_ext[".flac"].get("album") == "In Rainbows", str(by_ext[".flac"]))
    check("m4a 专辑艺术家正确", by_ext[".m4a"].get("albumartist") == "Radiohead", str(by_ext[".m4a"]))
    check("ogg 音轨号正确", by_ext[".ogg"].get("track") == "1", str(by_ext[".ogg"]))
    check("wav 标题正确", by_ext[".wav"].get("title") == "An Eagle in Your Mind", str(by_ext[".wav"]))
    check("全部写入了 year 之外的字段", all("album" in f for f in by_ext.values()))

    print("\n[5] 批量统一专辑（覆盖模式）")
    paths = [r["path"] for r in result["records"]]
    batch = library.apply_changes(
        [
            {
                "path": path,
                "patch": {
                    "album": "Now That's What I Call Test",
                    "albumartist": "Various Artists",
                    "year": "1999",
                    "genre": "Electronic",
                },
            }
            for path in paths
        ],
        mode="overwrite",
        label="批量设置专辑",
    )
    check("批量写入 8 个", batch["applied"] == 8, str(batch))
    albums = {adapters.read_tags(p).get("album") for p in paths}
    check("专辑已统一", albums == {"Now That's What I Call Test"}, str(albums))
    years = {adapters.read_tags(p).get("year") for p in paths}
    print(f"     年份回读 {years}")
    check("年份已统一", years == {"1999"}, str(years))

    print("\n[6] 仅补空模式")
    fill = library.apply_changes(
        [{"path": paths[0], "patch": {"album": "不应写入", "composer": "应写入"}}],
        mode="fill",
        label="补空测试",
    )
    tags = adapters.read_tags(paths[0])
    check("已有专辑未被覆盖", tags.get("album") == "Now That's What I Call Test", str(tags))
    check("空字段被补上", tags.get("composer") == "应写入", str(tags))

    print("\n[7] 封面写入与读取")
    cover_bytes = demo_cover()
    cover_patch = {"cover": {"op": "set", "mime": "image/jpeg", "data_b64": __import__("base64").b64encode(cover_bytes).decode()}}
    cover_result = library.apply_changes(
        [{"path": p, "patch": cover_patch} for p in paths], label="批量加封面"
    )
    check("封面写入 8 个", cover_result["applied"] == 8, str(cover_result))
    for p in paths:
        cover = adapters.read_cover(p)
        ok = cover is not None and len(cover.data) > 100
        check(f"封面可读 {Path(p).name}", ok, str(cover))
    _, has_cover = adapters.read_record(paths[0])
    check("has_cover 标记为真", has_cover)

    print("\n[8] 撤销")
    undo = library.undo(cover_result["backup"])
    check("撤销成功 8 个", undo["restored"] == 8, str(undo))
    covers_left = [adapters.read_cover(p) for p in paths]
    check("封面已移除", all(c is None for c in covers_left))
    check("专辑仍在", adapters.read_tags(paths[0]).get("album") == "Now That's What I Call Test")

    print("\n[9] 重命名计划")
    items = [{"path": p, "fields": adapters.read_tags(p)} for p in paths]
    plan = library.plan_rename(items, "%artist2%", str(SAMPLES)) if False else library.plan_rename(
        items, "%albumartist% - %title%", str(SAMPLES)
    )
    errors = [r for r in plan["rows"] if r.get("error")]
    check("无冲突", not errors, str(errors[:2]))
    print(f"     示例 {Path(plan['rows'][0]['path']).name} -> {plan['rows'][0].get('rel')}")

    print("\n[10] 模板引擎边界")
    tpl = naming.parse_template("[[%disc%-]%track% ]%title%")
    check("可选段匹配", naming.parse(tpl, "1-05 Song").get("track") == "5")
    check("可选段省略", naming.parse(tpl, "Song") == {"title": "Song"})
    check("补零渲染", naming.render(naming.parse_template("%track:2%"), {"track": "3"}) == "03")
    try:
        naming.parse_template("%unknown%")
        check("未知字段应报错", False)
    except naming.TemplateError:
        check("未知字段应报错", True)

    print("\n[11] APEv2 引擎（用合成的 .wv 文件验证代码路径）")
    import shutil, tempfile

    tmp = Path(tempfile.mkdtemp()) / "fake.wv"
    shutil.copy2(next(p for p in paths if p.endswith(".flac")), tmp)
    adapters.write_tags(str(tmp), {"title": "Ape 标题", "artist": "Ape 歌手", "track": "9"}, "overwrite")
    ape_tags = adapters.read_tags(str(tmp))
    check("APEv2 写入并回读", ape_tags.get("title") == "Ape 标题", str(ape_tags))
    adapters.write_tags(str(tmp), {"cover": adapters.Cover(cover_bytes, "image/jpeg")}, "overwrite")
    ape_cover = adapters.read_cover(str(tmp))
    check("APEv2 封面可读", ape_cover is not None and len(ape_cover.data) > 100)
    shutil.rmtree(tmp.parent, ignore_errors=True)

    print(f"\n{'=' * 46}\n通过 {passed} 项，失败 {failed} 项\n{'=' * 46}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
