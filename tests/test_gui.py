"""GUI 功能自测：不点鼠标，直接驱动 App 的方法，验证真实写盘链路。

    python tests/test_gui.py

用临时目录里的副本运行，不改动仓库内的 samples/。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tagforge import adapters, gui, library  # noqa: E402

DEMO = ROOT / "samples" / "DemoLibrary"
ok = fail = 0


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [PASS] {label}")
    else:
        fail += 1
        print(f"  [FAIL] {label} {extra}")


class MB:
    last = ""
    asked = 0

    @staticmethod
    def showinfo(t, m, **k): MB.last = f"info:{m}"
    @staticmethod
    def showwarning(t, m, **k): MB.last = f"warn:{m}"
    @staticmethod
    def showerror(t, m, **k): MB.last = f"err:{m}"
    @staticmethod
    def askyesno(t, m, **k):
        MB.asked += 1
        return True


gui.messagebox = MB

# 在副本上跑，避免污染受版本控制的样例
WORK = Path(tempfile.mkdtemp(prefix="tf-gui-test-"))
shutil.copytree(DEMO, WORK / "DemoLibrary")
LIB = str(WORK / "DemoLibrary")

root = tk.Tk()
root.geometry(f"{gui.S(gui.W)}x{gui.S(gui.H)}+60+60")
app = gui.TagForgeApp(root, LIB)
root.update()

print("[1] 启动与扫描")
check("扫描到 23 个文件", len(app.records) == 23, str(len(app.records)))
check("首行被设为当前文件", app.active == app.records[0]["path"])
check("表格容量内 23 行可全部显示",
      int((gui.H - gui.MAIN_Y - gui.HDR_H - 1) / gui.ROW_H) >= 23)
check("画布已完整绘制", len(app.canvas.find_all()) > 300, str(len(app.canvas.find_all())))

print("[2] 单元格编辑 -> staged")
rec = app.records[0]
before = adapters.read_tags(rec["path"]).get("title")
app.begin_edit(rec, "title", gui.X_TITLE, 210, gui.W_FLEX)
check("编辑器被创建", app.editor is not None)
app.editor.delete(0, "end")
app.editor.insert(0, "Edited Title")
app._commit_edit()
check("staged 记录了新值", app.staged.get(rec["path"], {}).get("title") == "Edited Title")
check("脏字段计数为 1", app.dirty_count() == 1)
check("磁盘尚未被改动", adapters.read_tags(rec["path"]).get("title") == before)

print("[3] 保存 -> 真实写盘")
app.save()
after = adapters.read_tags(rec["path"]).get("title")
check("磁盘标题已更新", after == "Edited Title", str(after))
check("staged 已清空", app.staged == {})
check("产生备份可供撤销", bool(library.list_backups(1)))

print("[4] 批量：统一专辑")
paths = [app.records[1]["path"], app.records[2]["path"]]
app.checks = set(paths)
app.b_fields["album"].set_value("Batch Album")
app.b_fields["year"].set_value("2020")
app.set_mode("overwrite")
app.apply_batch()
albums = {adapters.read_tags(p).get("album") for p in paths}
check("两张专辑已统一", albums == {"Batch Album"}, str(albums))
check("年份已写入", adapters.read_tags(paths[0]).get("year") == "2020")
check("批量后输入框已清空", app.b_fields["album"].value() == "")
check("批量后勾选仍保留", app.checks == set(paths), str(len(app.checks)))

print("[5] 仅补空模式")
empty_row = next(r for r in app.records if not r["fields"].get("genre"))
app.checks = {empty_row["path"]}
app.b_fields["genre"].set_value("Should Fill")
app.b_fields["album"].set_value("Should Not Override")
app.set_mode("fill")
app.apply_batch()
tags = adapters.read_tags(empty_row["path"])
check("空流派被补上", tags.get("genre") == "Should Fill", str(tags.get("genre")))
check("已有专辑未被覆盖", tags.get("album") != "Should Not Override", str(tags.get("album")))

print("[6] 撤销")
items = library.list_backups(1)
res = library.undo(items[0]["id"])
check("撤销恢复文件", res["restored"] > 0, str(res["restored"]))
app.scan()
check("撤销回退了最近一次批量（流派回到空）",
      not adapters.read_tags(empty_row["path"]).get("genre"),
      str(adapters.read_tags(empty_row["path"]).get("genre")))
check("撤销后该备份被消费（不会重复回退）",
      all(b["id"] != items[0]["id"] for b in library.list_backups(5)))

print("[7] 模板引擎预览（对话框逻辑）")
items = [{"path": r["path"], "fields": dict(r["fields"]), "rel": r["rel"]} for r in app.records]
data = library.preview_from_names(items, "%albumartist%/%album%/%track:2% - %title%",
                                  LIB, True, True)
check("全部可解析", data["matched"] == data["total"], f"{data['matched']}/{data['total']}")
check("解析出专辑艺术家", data["rows"][0]["parsed"].get("albumartist") == "Daft Punk")
check("音轨号补零解析", data["rows"][0]["parsed"].get("track") == "1")

print("[8] 重命名计划")
plan = library.plan_rename([{"path": r["path"], "fields": dict(r["fields"])} for r in app.records],
                           "%track:2% - %title%", LIB, True)
errs = [r for r in plan["rows"] if r.get("error")]
check("无冲突", not errs, str(errs[:2]))
check("生成了目标路径", bool(plan["rows"][0].get("rel")))

print("[9] 过滤与排序")
app.query = "radiohead"
app.redraw()
check("过滤命中 10 条", len(app.visible()) == 10, str(len(app.visible())))
app.query = ""
app.sort_by("year")
years = [int(r["fields"]["year"]) for r in app.visible() if r["fields"].get("year")]
check("按年份升序排列", years == sorted(years), str(years[:3]))
check("缺年份的行排在最前（空值视为 0）",
      not app.visible()[0]["fields"].get("year"), str(app.visible()[0]["fields"].get("year")))

print("[10] 对话框关闭后主窗口仍可用")
dlg = gui.ConfirmUndo(app, library.list_backups(1)[0], "10:24", 0)
root.update()
dlg.close()
root.update()
before_items = len(app.canvas.find_all())
ok_redraw = True
try:
    app.redraw()
except Exception as exc:          # noqa: BLE001
    ok_redraw = False
    print("    重绘异常:", exc)
check("关闭对话框后重绘不抛错", ok_redraw)
check("画布仍完整", len(app.canvas.find_all()) > 300, str(len(app.canvas.find_all())))
check("Painter 已归还主窗口", gui.Painter.current is app.p)

print("[11] 详情面板可编辑")
panel_hit = [h for h in app.p.hits if h[0] >= gui.PANEL_X and h[1] > 300]
check("面板字段有命中区", bool(panel_hit), str(len(panel_hit)))

print("[12] 保存失败时保留暂存")
p_ok, p_bad = app.records[0]["path"], app.records[1]["path"]
app.staged = {p_ok: {"title": "会成功"}, p_bad: {"title": "会失败"}}
Path(p_bad).chmod(0o444)
try:
    app.save()
finally:
    Path(p_bad).chmod(0o644)
check("失败文件的改动仍在暂存区", p_bad in app.staged, str(list(app.staged)))

print("[13] 有未保存改动时批量会先确认")
app.staged = {p_ok: {"title": "别丢"}}
MB.asked = 0
app.checks = {app.records[2]["path"]}
app.b_fields["album"].set_value("确认测试")
app.apply_batch()
check("弹出了确认", MB.asked == 1, str(MB.asked))

print("[14] 预览后改模板会失效")
dlg = gui.FromNamesDialog(app, items[:3], LIB)
root.update()
dlg.overwrite = True
dlg.use_path = False          # 模板不含目录段，按文件名匹配才可能解析成功
dlg.e_tpl.delete(0, "end")
dlg.e_tpl.insert(0, "%title%")
dlg.preview()
was = dlg.can_write
dlg.e_tpl.delete(0, "end")
dlg.e_tpl.insert(0, "%artist%")
dlg._invalidate()
check("改模板后写入按钮不可用", was is True and dlg.can_write is False,
      f"预览后={was} 改模板后={dlg.can_write}")
dlg.close()

print(f"\n通过 {ok} 项，失败 {fail} 项")
root.destroy()
shutil.rmtree(WORK, ignore_errors=True)
sys.exit(0 if fail == 0 else 2)
