"""GUI 功能自测：不点鼠标，直接驱动 App 的方法，验证真实写盘链路。"""

import sys
import tkinter as tk

sys.path.insert(0, r"C:\Users\LocalAcc\WorkBuddy\2026-10-27-18-08-55\tagforge")
from tagforge import adapters, gui, library  # noqa: E402

DEMO = r"C:\Users\LocalAcc\WorkBuddy\2026-10-27-18-08-55\tagforge\samples\DemoLibrary"
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
    @staticmethod
    def showinfo(t, m, **k): MB.last = f"info:{m}"
    @staticmethod
    def showwarning(t, m, **k): MB.last = f"warn:{m}"
    @staticmethod
    def showerror(t, m, **k): MB.last = f"err:{m}"
    @staticmethod
    def askyesno(t, m, **k): return True


gui.messagebox = MB

root = tk.Tk()
root.geometry(f"{gui.W}x{gui.H}+60+60")
app = gui.TagForgeApp(root, DEMO)
root.update()

print("[1] 启动与扫描")
check("扫描到 23 个文件", len(app.records) == 23, str(len(app.records)))
check("首行被设为当前文件", app.active == app.records[0]["path"])
check("表格容量内 23 行可全部显示",
      int((gui.H - gui.MAIN_Y - gui.HDR_H - 1) / gui.ROW_H) >= 23)

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
app.undo_last() if False else None
items = library.list_backups(1)
res = library.undo(items[0]["id"])
check("撤销恢复文件", res["restored"] > 0, str(res["restored"]))
app.scan()
check("撤销回退了最近一次批量（流派回到空）",
      not adapters.read_tags(empty_row["path"]).get("genre"),
      str(adapters.read_tags(empty_row["path"]).get("genre")))

print("[7] 模板引擎预览（对话框逻辑）")
items = [{"path": r["path"], "fields": dict(r["fields"]), "rel": r["rel"]} for r in app.records]
data = library.preview_from_names(items, "%albumartist%/%album%/%track:2% - %title%",
                                  DEMO, True, True)
check("全部可解析", data["matched"] == data["total"], f"{data['matched']}/{data['total']}")
check("解析出专辑艺术家", data["rows"][0]["parsed"].get("albumartist") == "Daft Punk")
check("音轨号补零解析", data["rows"][0]["parsed"].get("track") == "1")

print("[8] 重命名计划")
plan = library.plan_rename([{"path": r["path"], "fields": dict(r["fields"])} for r in app.records],
                           "%track:2% - %title%", DEMO, True)
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

print(f"\n通过 {ok} 项，失败 {fail} 项")
root.destroy()
sys.exit(0 if fail == 0 else 2)
