"""功能有效性审查探针（对应 docs/review-2026-11-03.md 的全部证据）。

    python tests/review_probe.py

每组对应报告中的一节；[BUG] 表示当前实现与预期不符，[OK] 表示符合预期。
脚本只在临时目录与临时副本上操作，不修改 samples/。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mutagen.apev2 import APEv2  # noqa: E402
from mutagen.id3 import (  # noqa: E402
    APIC, COMM, ID3, TALB, TDRC, TIT2, TPE1, TRCK, TXXX, TYER, USLT,
)

from tagforge import adapters, gui, library, naming  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="tf-review-"))
SRC = ROOT / "samples" / "Music"
DEMO = ROOT / "samples" / "DemoLibrary"
COVER = (ROOT / "samples" / "assets" / "cover_ref.jpg").read_bytes()
results: list[tuple[bool, str, str]] = []
MB_LAST: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((bool(ok), name, detail))
    print(("  [OK]   " if ok else "  [BUG]  ") + name + ("" if ok else f"\n         {detail}"))


def fresh(ext: str, name: str) -> Path:
    dst = TMP / name
    shutil.copy2(next(SRC.rglob(f"*{ext}")), dst)
    return dst


def group(title: str) -> None:
    print(f"\n{title}")


def mk_mp3(path: Path, **frames) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(next(SRC.rglob("*.mp3")), path)
    tag = ID3()
    for frame in frames.values():
        tag.add(frame)
    tag.save(path, v2_version=frames.pop("_v2", 4) if "_v2" in frames else 4)
    return path


# ---------------------------------------------------------------- P0
group("【P0-1】写封面是否删除其它图片帧（封底 / 艺人照）")
p = fresh(".mp3", "apic.mp3")
t = ID3()
t.add(TIT2(encoding=3, text=["pic"]))
t.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=COVER))
t.add(APIC(encoding=3, mime="image/jpeg", type=4, desc="Back", data=COVER))
t.add(APIC(encoding=3, mime="image/jpeg", type=8, desc="Artist", data=COVER))
t.save(p, v2_version=4)
before = len(ID3(str(p)).getall("APIC"))
adapters.write_tags(str(p), {"cover": adapters.Cover(COVER, "image/jpeg")}, mode="overwrite")
after = ID3(str(p)).getall("APIC")
check("写入封面后保留 type=4 / type=8 图片帧", before == 3 and len(after) == 3,
      f"写入前 {before} 张 → 写入后 {len(after)} 张，types={[f.type for f in after]}")

group("【P0-2】写备注是否保留其它语言的 COMM")
p = fresh(".mp3", "comm.mp3")
t = ID3()
t.add(TIT2(encoding=3, text=["c"]))
t.add(COMM(encoding=3, lang="eng", desc="", text=["英文备注"]))
t.add(COMM(encoding=3, lang="jpn", desc="", text=["日本語コメント"]))
t.save(p, v2_version=4)
adapters.write_tags(str(p), {"comment": "新的中文备注"}, mode="overwrite")
after = ID3(str(p)).getall("COMM")
check("写备注后保留其它语言 COMM", len(after) >= 2, f"写入后 {len(after)} 个，langs={[c.lang for c in after]}")

group("【P0-3】快照读取失败的文件，撤销是否被清空标签")
p = fresh(".mp3", "wipe.mp3")
t = ID3()
t.add(TIT2(encoding=3, text=["必须保留"]))
t.add(TDRC(encoding=3, text=["2010"]))
t.save(p, v2_version=4)
real_read, real_cover = adapters.read_record, adapters.read_cover
calls = {"n": 0}


def flaky(path):
    calls["n"] += 1
    if calls["n"] == 1:                       # 只让快照阶段失败
        raise OSError("模拟：文件被占用")
    return real_read(path)


adapters.read_record = flaky
try:
    bid = library.snapshot([str(p)], "读取失败")
finally:
    adapters.read_record = real_read
library.undo(bid)
after = adapters.read_tags(str(p))
check("快照失败的文件不应被 undo 清空", after.get("title") == "必须保留", f"undo 后: {after}")

group("【P0-4】模板不含 '/' 却按目录匹配时，路径是否被写成音轨号")
d = TMP / "Daft Punk" / "RAM"
d.mkdir(parents=True)
p = d / "01 - Give Life Back to Music.mp3"
shutil.copy2(next(DEMO.rglob("01 - Give Life Back to Music.mp3")), p)
pv = library.preview_from_names([{"path": str(p), "fields": adapters.read_tags(str(p))}],
                                "%track:2% - %title%", str(TMP), True, True)
ch = pv["rows"][0]["changes"]
if ch:
    library.apply_changes([{"path": str(p), "patch": ch}], label="污染测试")
track = adapters.read_tags(str(p)).get("track", "")
check("音轨号字段不含路径前缀", track.isdigit(), f"parsed={pv['rows'][0]['parsed']} 落盘 track={track!r}")

group("【P0-5】APEv2 的音轨总数与 n/m 拆分")
p = fresh(".flac", "ape_write.wv")
adapters.write_tags(str(p), {"track": "5", "tracktotal": "9"}, mode="overwrite")
got = adapters.read_tags(str(p))
check("APEv2 写入 tracktotal 后能读回", got.get("tracktotal") == "9", f"写入后: {got}")

p = fresh(".flac", "ape_split.wv")
tag = APEv2()
tag["Title"] = "ape"
tag["Track"] = "3/13"
tag.save(str(p))
got = adapters.read_tags(str(p))
check("APEv2 的 '3/13' 拆成 track + tracktotal",
      got.get("track") == "3" and got.get("tracktotal") == "13", f"读到: {got}")

group("【P0-6】四种引擎只写 tracktotal 时的一致性")
for ext, name in ((".flac", "t.flac"), (".mp3", "t.mp3"), (".m4a", "t.m4a"), (".flac", "t.wv")):
    q = fresh(ext, name)
    adapters.write_tags(str(q), {"track": None, "tracktotal": "12"}, mode="overwrite")
    val = adapters.read_tags(str(q)).get("tracktotal")
    print(f"         {name:8s} tracktotal={val!r}")
    check(f"{name} 只写 tracktotal 能读回", val == "12", f"读到 {val!r}")

group("【P0-7】ID3v2.3 文件的多值往返")
p = fresh(".mp3", "multi23.mp3")
t = ID3()
t.add(TIT2(encoding=3, text=["x"]))
t.save(p, v2_version=3)
adapters.write_tags(str(p), {"artist": "A; B"}, mode="overwrite")
back = adapters.read_tags(str(p)).get("artist")
check("v2.3 多值往返保持 'A; B'", back == "A; B", f"回读: {back!r}")

group("【P0-8】撤销对「含分号的单值」是否保真")
p = fresh(".mp3", "single.mp3")
t = ID3()
t.add(TPE1(encoding=3, text=["A; B"]))
t.save(p, v2_version=4)
bid = library.snapshot([str(p)], "多值")
library.undo(bid)
txt = list(ID3(str(p)).get("TPE1").text)
check("撤销后 TPE1 仍是单值 ['A; B']", txt == ["A; B"], f"撤销后={txt}")

# ---------------------------------------------------------------- P1
group("【P1-1】重命名「跳过目标已存在的文件」是否真的参与计算")
import inspect  # noqa: E402

sig = inspect.signature(library.plan_rename)
check("plan_rename 接受 skip_exist 参数", "skip_exist" in sig.parameters, str(sig))

group("【P1-2】互换编号的重命名")
swap = TMP / "swap"
swap.mkdir()
a = mk_mp3(swap / "01.mp3", title=TIT2(encoding=3, text=["1"]))
b = mk_mp3(swap / "02.mp3", title=TIT2(encoding=3, text=["2"]))
adapters.write_tags(str(a), {"track": "2"}, mode="overwrite")
adapters.write_tags(str(b), {"track": "1"}, mode="overwrite")
plan = library.plan_rename([{"path": str(x), "fields": adapters.read_tags(str(x))} for x in (a, b)],
                           "%track:2%", str(swap), True)
errs = [r.get("error") for r in plan["rows"]]
check("互换编号时全部可执行（两阶段改名）", all(e is None for e in errs), f"被判冲突: {errs}")

group("【P1-9 / P1-10】扫描截断的提示与边界")
sub = TMP / "trunc"
for i in range(3):
    dd = sub / f"a{i}"
    dd.mkdir(parents=True)
    for j in range(3):
        shutil.copy2(next(SRC.rglob("*.mp3")), dd / f"{j}.mp3")
r = library.scan(str(sub), recursive=True, limit=3)
check("9 个文件 limit=3 时 truncated 为 True", r["truncated"] is True,
      f"count={r['count']} truncated={r['truncated']}")
src_txt = (ROOT / "tagforge" / "gui.py").read_text(encoding="utf-8")
check("GUI 使用并提示 truncated", "truncated" in src_txt, "gui.py 从未引用 data['truncated']")

group("【P1-11】扫描中单个文件消失")
sub2 = TMP / "vanish"
sub2.mkdir()
shutil.copy2(next(SRC.rglob("*.mp3")), sub2 / "a.mp3")
orig_stat = Path.stat


def boom(self, *a, **kw):
    if self.name == "a.mp3":
        raise FileNotFoundError("模拟：扫描中途被删")
    return orig_stat(self, *a, **kw)


Path.stat = boom
try:
    ok, detail = True, ""
    try:
        library.scan(str(sub2))
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"
finally:
    Path.stat = orig_stat
check("单个文件消失不让整次扫描失败", ok, detail)

group("【P1-12】保存时文件已不存在的上报口径")
res = library.apply_changes([{"path": str(TMP / "不存在.mp3"), "patch": {"title": "x"}}], label="缺失")
check("不存在的文件被显式报告", bool(res["failed"]) or res["skipped"] == 0,
      f"failed={res['failed']} skipped={res['skipped']}")

group("【P1-13】虚拟字段是否进 changes")
pv = library.preview_from_names([{"path": str(TMP / "01 - Song.mp3"), "fields": {}}],
                                "%_filename%", str(TMP), False, True)
check("虚拟字段不进 changes（否则统计与实际写入不符）",
      not pv["rows"][0]["changes"], f"changes={pv['rows'][0]['changes']}")

# ---------------------------------------------------------------- GUI
def _record_msg(*a, **k):
    MB_LAST.append(str(a[1]) if len(a) > 1 else "")


gui.messagebox.showinfo = _record_msg
gui.messagebox.showwarning = lambda *a, **k: None
gui.messagebox.showerror = lambda *a, **k: None
gui.messagebox.askyesno = lambda *a, **k: True

GLIB = TMP / "gui"
(GLIB / "RAM").mkdir(parents=True)
for name in ("01 - Give Life Back to Music.mp3", "02 - The Game of Love.mp3", "03 - Giorgio by Moroder.mp3"):
    shutil.copy2(DEMO / "Daft Punk" / "RAM" / name, GLIB / "RAM" / name)

gui.SCALE = 1.0
root = tk.Tk()
root.withdraw()
app = gui.TagForgeApp(root, str(GLIB))
root.update()

group("【P1-3】预览后改模板，写入按钮是否仍可点")
items = [{"path": r["path"], "fields": dict(r["fields"]), "rel": r["rel"]} for r in app.records]
dlg = gui.FromNamesDialog(app, items, str(GLIB))
root.update()
dlg.overwrite = True
dlg.e_tpl.delete(0, "end")
dlg.e_tpl.insert(0, "%title%")
dlg.preview()
can_before = dlg.can_write
dlg.e_tpl.delete(0, "end")
dlg.e_tpl.insert(0, "%artist%")
bound = bool([h for h in dlg.p.hits if h[4] == dlg.apply])
check("改模板后写入按钮失效", not (can_before and bound),
      f"预览后 can_write={can_before}，改模板后仍绑定 apply={bound}")
dlg.close()

group("【P0-0】关闭对话框后主窗口是否仍可用")
gui.Painter.current = app.p        # 让被测状态回到健康（模拟刚启动）
before_items = len(app.canvas.find_all())
dlgx = gui.ConfirmUndo(app, library.list_backups(1)[0], "10:24", 0)
root.update()
dlgx.close()
root.update()
try:
    app.redraw()
    ok0, detail0 = True, ""
except Exception as exc:
    ok0, detail0 = False, f"{type(exc).__name__}: {exc}（Painter.current 仍指向已销毁的对话框画布）"
after_items = len(app.canvas.find_all())
check("关闭对话框后主窗口仍可重绘", ok0 and after_items > before_items * 0.8,
      f"{detail0} 画布元素 {before_items} → {after_items}")
gui.Painter.current = app.p        # 后续用例继续

group("【P1-4】重命名预览后改模板，执行的是哪套方案")
items2 = [{"path": r["path"], "fields": {k: app.value(r, k) for k in
           ("title", "artist", "album", "albumartist", "year", "track", "disc", "genre")}}
          for r in app.records]
dlg2 = gui.RenameDialog(app, items2, str(GLIB))
root.update()
dlg2.e_tpl.delete(0, "end")
dlg2.e_tpl.insert(0, "%album% - %title%")      # 会产生变更，can_apply 才可能为真
dlg2.preview()
plan_a = [r.get("rel") for r in dlg2.plan]
dlg2.e_tpl.delete(0, "end")
dlg2.e_tpl.insert(0, "%genre%")
dlg2._invalidate()                      # 用户敲键盘时由 KeyRelease 触发
MB_LAST.clear()
dlg2.apply()                            # 即便按钮还在，也必须被拒绝
check("改模板后执行被拒绝（不会跑旧方案）",
      not dlg2.can_apply and MB_LAST and "重新预览" in MB_LAST[-1],
      f"can_apply={dlg2.can_apply} 提示={MB_LAST[-1:] or '无'}")
dlg2.close()
gui.Painter.current = app.p

group("【P1-5】部分保存失败时，失败文件的改动是否保留")
p_ok, p_bad = app.records[0]["path"], app.records[1]["path"]
app.staged = {p_ok: {"title": "会成功"}, p_bad: {"title": "会失败"}}
Path(p_bad).chmod(0o444)
try:
    app.save()
finally:
    Path(p_bad).chmod(0o644)
check("保存后失败文件的改动仍在 staged", bool(app.staged), f"保存后 staged={app.staged}")

group("【P1-6】批量应用是否吞掉未保存的单元格改动")
app2 = gui.TagForgeApp(root, str(GLIB))
root.update()
a2, b2 = app2.records[0]["path"], app2.records[1]["path"]
app2.staged = {a2: {"title": "我手改的标题"}}
app2.checks = {b2}
app2.b_fields["album"].set_value("批量专辑")
app2.apply_batch()
check("批量后未保存改动仍在", a2 in app2.staged, f"批量后 staged={app2.staged}")

group("【P1-7】撤销是否消费备份")
p = fresh(".mp3", "repeat.mp3")
t = ID3()
t.add(TIT2(encoding=3, text=["第一版"]))
t.save(p, v2_version=4)
library.snapshot([str(p)], "基线")
library.apply_changes([{"path": str(p), "patch": {"title": "第二版"}}], label="改一")
library.apply_changes([{"path": str(p), "patch": {"title": "第三版"}}], label="改二")
top = library.list_backups(1)[0]["id"]
library.undo(top)
check("撤销后最新备份被标记为已消费", library.list_backups(1)[0]["id"] != top,
      f"仍是 {top}，标题={adapters.read_tags(str(p)).get('title')!r}")

group("【P1-8】撤销后未保存修改是否保留（与确认框文案一致）")
app3 = gui.TagForgeApp(root, str(GLIB))
root.update()
tgt = app3.records[0]["path"]
app3.staged = {tgt: {"title": "我还没保存的改动"}}
before_n = sum(len(v) for v in app3.staged.values())
library.undo(library.list_backups(1)[0]["id"])
app3.scan(preserve=True)                # undo_last 就是这么做的
app3.redraw()
after_n = sum(len(v) for v in app3.staged.values())
check("撤销后未保存修改仍保留", after_n == before_n, f"撤销前 {before_n} 处 → 撤销后 {after_n} 处")

group("【P1-8b】撤销自身是否产生快照（可反悔）")
newest = library.list_backups(1)[0]["id"]
library.undo(newest)
check("撤销操作可被再次撤销（产生了新的撤销前快照）",
      library.list_backups(1)[0]["id"] != newest,
      f"撤销前最新={newest}，撤销后最新={library.list_backups(1)[0]['id']}")

# ---------------------------------------------------------------- P2
group("【P2】界面状态与实现一致性")
check("排序箭头随 sort_key 变化", 'color = ACCENT if label == "标题" else DIM' not in src_txt,
      "draw_table 把高亮硬编码在「标题」，默认 sort_key 却是 'rel'")
v23 = fresh(".mp3", "badge23.mp3")
tag23 = ID3()
tag23.add(TIT2(encoding=3, text=["x"]))
tag23.save(v23, v2_version=3)
rec23 = {"path": str(v23), "engine": "id3", "ext": ".mp3"}
badge = app.engine_badge(rec23)
check("引擎徽标反映真实 ID3 版本", badge == "ID3v2.3", f"v2.3 文件显示 {badge!r}")
seg = src_txt[src_txt.index("# 表头"):src_txt.index("headers = [")]
check("表头勾选框外观随选择状态变化",
      not ('p.rect(17, MAIN_Y + 16.2, 8, 2, INK)' in seg and "self.checks" not in seg),
      "表头永远画半选态，全选/未选外观相同")
seg2 = src_txt[src_txt.index("def draw_row"):src_txt.index("# 文件列")]
check("异常行成为当前行时仍保留异常底色", 'elif rec.get("error")' not in seg2,
      "当前行分支在前，异常底色被覆盖")
check("预览表可查看全部文件（非前 5/4 行截断）",
      "data[\"rows\"][:5]" not in src_txt and "self.plan[:4]" not in src_txt,
      "预览只渲染前 5/4 行，写入却作用于全部")
check("批量失败后保留输入", "for entry in self.b_fields.values():\n            entry.set_value(\"\")" not in src_txt,
      "apply_batch 无条件清空批量栏")
lib_txt = (ROOT / "tagforge" / "library.py").read_text(encoding="utf-8")
check("备份有清理策略", "_prune_backups" in lib_txt and "MAX_BACKUPS" in lib_txt,
      f"当前 {len(list(library.BACKUP_DIR.glob('*.json')))} 条备份，上限 {library.MAX_BACKUPS}")
check("详情面板字段可编辑", bool([h for h in app.p.hits if h[0] >= 1120 and h[1] > 300]),
      "面板 12 个「输入框」无命中区，实为只读")
check("路径输入框可回车扫描", bool(app.e_path.bind("<Return>")), "e_path 未绑定 Return")
rec0 = app.records[0]
app.staged = {rec0["path"]: {"genre": ""}}
val = app.value(rec0, "genre")
dirty = app.is_dirty(rec0, "genre")
color = "DIRTY" if dirty else ("FAINT" if not val else "TEXT")   # 与 draw_row 同式
check("清空字段（待删除）可与空值区分", not val and dirty and color == "DIRTY",
      f"value={val!r} dirty={dirty} → {color}")

root.destroy()

print("\n" + "=" * 64)
bad = [r for r in results if not r[0]]
print(f"共 {len(results)} 项，异常 {len(bad)} 项")
for _, name, detail in bad:
    print("  - " + name)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if bad else 0)
