"""TagForge 桌面端 GUI（Tkinter）。

设计约束：界面按 1440×900 的像素稿 1:1 绘制，因此不用 ttk 主题，
而是把全部可见元素画在一块 Canvas 上——只有需要输入的地方
（路径、搜索、批量字段、单元格编辑）才叠加真正的 Entry。

分层：
    Painter        画布绘制原语（圆角矩形 / 文本截断 / 按钮命中区）
    Dialog         无边框模态基类（自定义标题栏 + 复选框 + 按钮）
    TagForgeApp    主窗口：状态、渲染、交互
外部依赖只有 mutagen（tkinter 是标准库）。

启动：
    python -m tagforge.gui
    python -m tagforge.gui "D:/Music"
"""

from __future__ import annotations

import base64
import ctypes
import io
import os
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox

try:
    from . import adapters, library, naming
except ImportError:  # 允许直接 python gui.py 运行
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tagforge import adapters, library, naming

# ---------------------------------------------------------------- 主题
BG = "#14161A"
PANEL = "#1A1D23"
CTRL = "#21252C"
LINE = "#2F3540"
LINE_SOFT = "#262B33"
TEXT = "#DFE4EC"
DIM = "#8B94A3"
FAINT = "#5F6875"
ACCENT = "#4C9AFF"
DIRTY = "#E2B341"
OK = "#3FB96B"
DANGER = "#E2564C"
INK = "#0F1622"
CHK_EDGE = "#3A414D"

FONT_UI = "Segoe UI"             # 启动时替换为系统默认 UI 字族
FONT_MONO = "Consolas"
SCALE = 1.0                      # 设计单位 -> 设备像素的倍率（由 DPI 决定）


def S(value: float) -> int:
    """设计单位转设备像素。所有绘制坐标都过这个函数。"""
    return int(round(value * SCALE))


def asset_path(name: str) -> str | None:
    """定位随包资源。兼容源码运行与 PyInstaller 单文件包。"""
    pkg = os.path.dirname(os.path.abspath(__file__))
    roots = []
    if getattr(sys, "_MEIPASS", None):          # 打包后资源被解到临时目录
        roots.append(os.path.join(sys._MEIPASS, "assets"))
    roots += [
        os.path.join(pkg, "assets"),
        os.path.join(os.path.dirname(pkg), "assets"),
    ]
    for root in roots:
        path = os.path.join(root, name)
        if os.path.isfile(path):
            return path
    return None


# 预生成的图标尺寸（见 tools/make_icon.py），正好覆盖 100%–200% 的常见缩放
ICON_SIZES = (16, 20, 25, 30, 35, 40, 48, 60)


def load_icon(design_px: int):
    """按设计尺寸取最接近的预渲染 PNG，避免 Tk 只能整数倍缩放导致的模糊。"""
    target = S(design_px)
    exact = asset_path(f"app_icon_{target}.png")
    if exact:
        return tk.PhotoImage(file=exact)
    bigger = [s for s in ICON_SIZES if s >= target]
    size = bigger[0] if bigger else ICON_SIZES[-1]
    path = asset_path(f"app_icon_{size}.png")
    if not path:
        return None
    img = tk.PhotoImage(file=path)
    factor = max(1, round(size / target))
    return img.subsample(factor, factor) if factor > 1 else img


def _win_set_icons(root: tk.Tk, ico: str) -> bool:
    """直接给窗口句柄发 WM_SETICON。

    Tk 9 的 `wm iconbitmap` 在 Windows 上不生效（调用不报错，但 `wm iconbitmap`
    查询仍为空，标题栏退回系统占位图标），所以这里绕开 Tk 用 Win32。
    """
    try:
        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.LoadImageW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        ]
        user32.SendMessageW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
        ]
        hwnd = int(root.wm_frame(), 16)
    except Exception:
        return False

    IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x10, 0x0080
    ICON_SMALL, ICON_BIG = 0, 1
    ok = False
    for size, kind in ((16, ICON_SMALL), (32, ICON_BIG), (48, ICON_BIG)):
        handle = user32.LoadImageW(None, ico, IMAGE_ICON, size, size, LR_LOADFROMFILE)
        if handle:
            user32.SendMessageW(hwnd, WM_SETICON, kind, handle)
            ok = True
    return ok


def apply_window_icon(root: tk.Tk) -> None:
    """标题栏 + 任务栏图标。

    Windows 上必须先设 AppUserModelID，否则任务栏会沿用 python.exe / 打包器的
    图标与分组；标题栏小图标则由 WM_SETICON 决定，Tk 的 iconbitmap 不可靠。
    """
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TagForge.Desktop")
    except Exception:
        pass

    png = asset_path("app_icon.png")
    if png:
        try:
            img = tk.PhotoImage(file=png)
            root.iconphoto(True, img)
            root._app_icon = img          # 保持引用，否则会被回收成空白图标
        except tk.TclError:
            pass

    ico = asset_path("app_icon.ico")
    if not ico:
        return
    if os.name == "nt":
        def install() -> None:
            if not _win_set_icons(root, ico):
                try:                      # 兜底：非 Windows 或无句柄时走 Tk
                    root.iconbitmap(default=ico)
                except tk.TclError:
                    pass

        install()
        # 窗口映射后系统可能重绘标题栏，再铺一次更稳
        try:
            root.after(300, install)
        except Exception:
            pass
    else:
        try:
            root.iconbitmap(default=ico)
        except tk.TclError:
            pass


# ---------------------------------------------------------------- 布局常量
W, H = 1440, 900
A_H, B_H, HDR_H, ROW_H = 92, 82, 34, 29
MAIN_Y = 175
TABLE_W = 1119
PANEL_X = 1120

X_CK, X_FILE = 0, 34
X_TITLE, X_ART, X_ALB, X_AART = 312, 427, 542, 657
X_YEAR, X_TRACK, X_DISC, X_GENRE, X_COVER = 772, 834, 896, 958, 1073
W_FILE, W_FLEX, W_NUM, W_COVER = 278, 115, 62, 46
PAD = 22

GRID_FIELDS = ["title", "artist", "album", "albumartist", "year", "track", "disc", "genre"]

# Tab 在表格里横向移动的顺序与每列几何
EDIT_COLUMNS = ("title", "artist", "album", "albumartist", "year", "track", "disc", "genre")
COLUMN_GEOM = {
    "title": (X_TITLE, W_FLEX), "artist": (X_ART, W_FLEX),
    "album": (X_ALB, W_FLEX), "albumartist": (X_AART, W_FLEX),
    "year": (X_YEAR, W_NUM), "track": (X_TRACK, W_NUM),
    "disc": (X_DISC, W_NUM), "genre": (X_GENRE, W_FLEX),
}
FIELD_LABELS = {
    "title": "标题", "artist": "艺术家", "album": "专辑", "albumartist": "专辑艺术家",
    "year": "年份", "track": "音轨", "disc": "碟号", "genre": "流派",
}
ENGINE_TAG = {"id3": "ID3v2.4", "vorbis": "VORBIS", "mp4": "MP4", "apev2": "APEv2"}

COVER_HINT = {"Random Access Memories": "#3A4763", "In Rainbows": "#3E4553"}


def F(size: int, bold: bool = False, mono: bool = False):
    """字号同样按 DPI 缩放：13 设计像素在 125% 下渲染成 16 设备像素。"""
    return (FONT_MONO if mono else FONT_UI, -max(1, S(abs(size))), "bold" if bold else "normal")


def resolve_ui_font(root: tk.Tk) -> str:
    """采用系统默认 UI 字体（Tk 的 TkDefaultFont 字族）。

    不再随包字体：Windows 上是 Segoe UI，中文由系统字体链接自动回退到
    微软雅黑一类字形；换机器 / 换主题时界面自动跟随系统。
    """
    global FONT_UI
    try:
        family = tkfont.nametofont("TkDefaultFont").cget("family")
        if family:
            FONT_UI = family
    except Exception:
        pass
    return FONT_UI


def blend(fg: str, bg: str, alpha: float) -> str:
    f = tuple(int(fg[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(bg[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02X%02X%02X" % tuple(int(f[k] * alpha + b[k] * (1 - alpha)) for k in range(3))


ROW_CURRENT = blend(ACCENT, BG, 0.14)
ROW_ERROR = blend(DANGER, BG, 0.08)
ROW_HOVER = blend("#FFFFFF", BG, 0.022)


def cover_color(album: str) -> str:
    if album in COVER_HINT:
        return COVER_HINT[album]
    h = sum((i + 1) * ord(c) for i, c in enumerate(album or "?")) % 360
    lo = 0.18 + (h % 7) * 0.012
    r, g, b = (int(255 * lo * (0.7 + 0.3 * abs(((h + k * 120) % 360) / 180 - 1))) for k in range(3))
    return "#%02X%02X%02X" % (max(r, 34), max(g, 40), max(b, 52))


# ---------------------------------------------------------------- 绘制原语
class Painter:
    """画布绘制原语。

    约定：**所有入参都是 1440×900 稿面上的设计单位**，
    内部统一乘以 SCALE 换成设备像素，因此 DPI 变化时上层代码零改动。
    """

    def __init__(self, canvas: tk.Canvas):
        self.c = canvas
        self.hits: list[tuple[int, int, int, int, object]] = []
        self._fonts: dict = {}

    def font(self, spec) -> tkfont.Font:
        if spec not in self._fonts:
            self._fonts[spec] = tkfont.Font(family=spec[0], size=spec[1],
                                             weight=spec[2] if len(spec) > 2 else "normal")
        return self._fonts[spec]

    def measure(self, text: str, spec) -> int:
        """返回设计单位下的宽度。"""
        return self.font(spec).measure(text) / SCALE

    def clip_left(self, text: str, width: int, spec) -> str:
        if width <= 0 or not text:
            return ""
        f = self.font(spec)
        limit = S(width)
        if f.measure(text) <= limit:
            return text
        ell = "…"
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi) // 2
            if f.measure(ell + text[len(text) - mid:]) <= limit:
                lo = mid + 1
            else:
                hi = mid
        keep = max(lo - 1, 0)
        return ell + text[len(text) - keep:] if keep else ell

    def clip(self, text: str, width: int, spec) -> str:
        if width <= 0 or not text:
            return ""
        f = self.font(spec)
        limit = S(width)
        if f.measure(text) <= limit:
            return text
        ell = "…"
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi) // 2
            if f.measure(text[:mid] + ell) <= limit:
                lo = mid + 1
            else:
                hi = mid
        return text[:max(lo - 1, 0)] + ell

    def rect(self, x, y, w, h, fill, outline=None, width=1):
        return self.c.create_rectangle(S(x), S(y), S(x + w), S(y + h),
                                       fill=fill, outline=outline or fill,
                                       width=max(1, S(width)))

    def rrect(self, x, y, w, h, r, fill=None, outline=None, width=1):
        r = min(r, w / 2, h / 2)
        pts = [x + r, y, x + w - r, y, x + w, y, x + w, y + r,
               x + w, y + h - r, x + w, y + h, x + w - r, y + h,
               x + r, y + h, x, y + h, x, y + h - r, x, y + r, x, y]
        flat = []
        for i in range(0, len(pts), 2):
            flat.extend((S(pts[i]), S(pts[i + 1])))
        return self.c.create_polygon(flat, smooth=True, splinesteps=14,
                                     fill=fill or "", outline=outline or "",
                                     width=max(1, S(width)))

    def oval(self, x0, y0, x1, y1, fill=None, outline=None, width=1):
        return self.c.create_oval(S(x0), S(y0), S(x1), S(y1),
                                  fill=fill or "", outline=outline or "",
                                  width=max(1, S(width)))

    def polyline(self, points, color, width=1, capstyle="butt", joinstyle="miter"):
        flat = []
        for px, py in points:
            flat.extend((S(px), S(py)))
        return self.c.create_line(*flat, fill=color, width=max(1, S(width)),
                                  capstyle=capstyle, joinstyle=joinstyle)

    def text(self, x, y, s, spec, color, anchor="w", width=None):
        if width is not None:
            s = self.clip(s, width, spec)
        return self.c.create_text(S(x), S(y), text=s, font=spec, fill=color, anchor=anchor)

    def paragraph(self, x, y, s, spec, color, width):
        return self.c.create_text(S(x), S(y), text=s, font=spec, fill=color,
                                  anchor="nw", width=S(width), justify="left")

    def image(self, x, y, img):
        return self.c.create_image(S(x), S(y), image=img, anchor="nw")

    def line(self, x0, y0, x1, y1, color, width=1):
        return self.c.create_line(S(x0), S(y0), S(x1), S(y1),
                                  fill=color, width=max(1, S(width)))

    def hit(self, x, y, w, h, callback):
        self.hits.append((S(x), S(y), S(x + w), S(y + h), callback))

    def find(self, x, y):
        for x0, y0, x1, y1, cb in reversed(self.hits):
            if x0 <= x <= x1 and y0 <= y <= y1:
                return cb
        return None

    def button(self, x, y, w, h, label, kind="secondary", command=None, spec=None,
               radius=7, icon=None):
        spec = spec or F(13)
        if kind == "primary":
            fill, edge, color = ACCENT, ACCENT, INK
        elif kind == "disabled":
            fill = blend(ACCENT, PANEL, 0.32)
            edge, color = fill, blend(INK, PANEL, 0.55)
        elif kind == "danger":
            fill, edge, color = DANGER, DANGER, "#FFFFFF"
        elif kind == "ghost":
            fill, edge, color = PANEL, LINE, FAINT
        else:
            fill, edge, color = CTRL, LINE, TEXT
        self.rrect(x, y, w, h, radius, fill=fill, outline=edge)
        cx = x + w / 2
        if icon:
            iw = self.measure(label, spec)
            total = 13 + 6 + iw
            ix = cx - total / 2
            icon(ix, y + h / 2 - 5.5)
            self.text(ix + 19, y + h / 2, label, spec, color)
        else:
            self.text(cx, y + h / 2, label, spec, color, anchor="center")
        if command:
            self.hit(x, y, w, h, command)

    def checkbox(self, x, y, label, checked, command, size=14, spec=None, color=TEXT):
        spec = spec or F(13)
        self.rrect(x, y, size, size, 3, fill=ACCENT if checked else CTRL,
                   outline=ACCENT if checked else CHK_EDGE)
        if checked:
            self.polyline([(x + 3.2, y + size / 2), (x + size * 0.44, y + size - 4),
                           (x + size - 3, y + 3.4)], INK, 2, "round", "round")
        self.text(x + size + 7, y + size / 2, label, spec, color)
        self.hit(x - 2, y - 3, size + 6 + self.measure(label, spec) + 6, size + 6, command)

    def radio(self, x, y, label, on, command, color=None):
        r = 6
        cy = y + 7
        self.oval(x, cy - r, x + 2 * r, cy + r, outline=ACCENT if on else FAINT, width=1.4)
        if on:
            self.oval(x + 3, cy - 3, x + 9, cy + 3, fill=ACCENT, outline=ACCENT)
        spec = F(13)
        self.text(x + 19, cy, label, spec, color or (TEXT if on else DIM))
        self.hit(x - 2, y - 4, 19 + self.measure(label, spec) + 6, 22, command)


def icon_folder(x, y, color=FAINT):
    c = Painter.current
    c.line(x, y + 2.4, x + 4.2, y + 2.4, color)
    c.line(x + 4.2, y + 2.4, x + 5.2, y + 3.6, color)
    c.line(x + 5.2, y + 3.6, x + 10, y + 3.6, color)
    c.line(x + 10, y + 3.6, x + 10, y + 8.6, color)
    c.line(x + 10, y + 8.6, x, y + 8.6, color)
    c.line(x, y + 8.6, x, y + 2.4, color)


def icon_search(x, y, color=FAINT):
    c = Painter.current
    c.oval(x, y, x + 7.4, y + 7.4, outline=color, width=1.2)
    c.line(x + 6.4, y + 6.4, x + 10, y + 10, color, 1.2)


def icon_image(x, y, color=DIM):
    c = Painter.current
    c.rrect(x, y, 11, 11, 2, fill=None, outline=color)
    c.oval(x + 2.6, y + 2.6, x + 4.4, y + 4.4, outline=color, width=1)
    c.line(x + 1.6, y + 9.4, x + 4.6, y + 6.2, color)
    c.line(x + 4.6, y + 6.2, x + 6.6, y + 8.2, color)
    c.line(x + 6.6, y + 8.2, x + 8.2, y + 6.6, color)
    c.line(x + 8.2, y + 6.6, x + 9.6, y + 8, color)


def icon_alert(x, y, color=DANGER):
    c = Painter.current
    c.oval(x, y, x + 11, y + 11, outline=color, width=1.2)
    c.line(x + 5.5, y + 3.2, x + 5.5, y + 6.4, color, 1.3)
    c.oval(x + 4.9, y + 7.6, x + 6.1, y + 8.8, fill=color, outline=color)


def icon_close(x, y, color=DIM):
    c = Painter.current
    c.line(x, y, x + 9, y + 9, color, 1.3)
    c.line(x + 9, y, x, y + 9, color, 1.3)


class PhEntry(tk.Entry):
    """带占位文字的输入框（Tk 原生 Entry，外观由底下的圆角块负责）。"""

    def __init__(self, master, placeholder="", ph_color=FAINT, color=TEXT, **kw):
        kw.setdefault("bg", CTRL)
        kw.setdefault("fg", color)
        kw.setdefault("insertbackground", color)
        kw.setdefault("bd", 0)
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("font", F(13))
        super().__init__(master, **kw)
        self._ph, self._ph_color, self._color = placeholder, ph_color, color
        self._on = False
        self.bind("<FocusIn>", self._focus_in)
        self.bind("<FocusOut>", self._focus_out)
        if placeholder:
            self._focus_out()

    def _focus_in(self, _=None):
        if self._on:
            self.delete(0, "end")
            self.config(fg=self._color)
            self._on = False

    def _focus_out(self, _=None):
        if not self.get().strip() and self._ph:
            self._on = True
            self.insert(0, self._ph)
            self.config(fg=self._ph_color)

    def value(self) -> str:
        return "" if self._on else self.get().strip()

    def set_value(self, text: str):
        self.delete(0, "end")
        self._on = False
        self.config(fg=self._color)
        if text:
            self.insert(0, text)
        else:
            self._focus_out()


# ---------------------------------------------------------------- 主窗口
class TagForgeApp:
    def __init__(self, root: tk.Tk, start_dir: str | None = None):
        resolve_ui_font(root)
        self.root = root
        self.root.title("TagForge")
        apply_window_icon(root)
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        self.canvas = tk.Canvas(root, width=S(W), height=S(H), bg=BG, highlightthickness=0)
        self.canvas.pack()
        self.p = Painter(self.canvas)
        Painter.current = self.p

        self.root_dir = start_dir or ""
        self.records: list[dict] = []
        self.staged: dict[str, dict] = {}
        self.checks: set[str] = set()
        self.active: str | None = None
        self.sort_key = "rel"
        self.sort_dir = 1
        self.query = ""
        self.scroll = 0
        self.batch_mode = "fill"
        self.batch_cover: tuple[bytes, str] | None = None
        self.batch_cover_name = ""
        self.editor: tk.Entry | None = None
        self.edit_target: tuple[str, str] | None = None
        self.recursive = True
        self._probe_cache: dict[str, dict] = {}
        self._photos: dict[str, object] = {}
        self._logo = None
        self.last_backup: str | None = None
        self.truncated = False

        self._build_inputs()
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.root.bind("<Control-s>", lambda e: self.save())
        self.root.bind("<Escape>", lambda e: self._cancel_edit())

        if not self.root_dir:
            demo = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "samples", "DemoLibrary")
            if os.path.isdir(demo):
                self.root_dir = demo
        self.refresh_backup_label()
        self.scan(self.root_dir, silent=True)
        self.redraw()

    # ---------------- 输入控件（一次创建，随画布定位） ----------------
    def _build_inputs(self):
        self.e_path = PhEntry(self.canvas, "", color=DIM, font=F(12, mono=True))
        self.e_path.bind("<Return>", lambda e: self.scan_from_entry())
        self.e_path.place(x=S(14 + 27), y=S(46 + 7), width=S(300 - 27 - 12), height=S(17))
        self.e_search = PhEntry(self.canvas, "搜索文件名…")
        self.e_search.place(x=S(955 + 26), y=S(46 + 7), width=S(190 - 26 - 10), height=S(17))
        self.e_search.bind("<KeyRelease>", self._on_search)

        self.b_fields: dict[str, PhEntry] = {}
        geom = {"album": (12, 132), "albumartist": (152, 140), "year": (300, 74), "genre": (382, 110)}
        for key, (x, w) in geom.items():
            e = PhEntry(self.canvas, FIELD_LABELS[key])
            e.place(x=S(x + 9), y=S(134 + 6), width=S(w - 18), height=S(17))
            self.b_fields[key] = e

    # ---------------- 数据 ----------------
    def scan(self, path: str | None = None, silent: bool = False, preserve: bool = False):
        """重新扫描。

        preserve=True 时保留当前行、勾选与未保存的暂存值——
        保存/批量/撤销之后不应该把用户的选择上下文清空。
        """
        prev_active = self.active
        prev_checks = set(self.checks)
        prev_staged = dict(self.staged)
        if path:
            self.root_dir = path
        if not self.root_dir or not os.path.isdir(self.root_dir):
            self.records = []
            self.truncated = False
            return
        try:
            data = library.scan(self.root_dir, recursive=self.recursive)
        except Exception as exc:
            if not silent:
                messagebox.showerror("扫描失败", str(exc))
            self.records = []
            return
        self.records = data["records"]
        self.truncated = bool(data.get("truncated"))
        self.scroll = 0
        self._probe_cache.clear()
        self._photos.clear()

        if preserve:
            self.staged = {p: v for p, v in prev_staged.items() if self.record(p)}
            self.checks = {p for p in prev_checks if self.record(p)}
            self.active = prev_active if self.record(prev_active) else (
                self.records[0]["path"] if self.records else None)
        else:
            self.staged.clear()
            self.checks.clear()
            self.active = self.records[0]["path"] if self.records else None
        self.refresh_backup_label()

    def record(self, path: str | None) -> dict | None:
        if not path:
            return None
        for r in self.records:
            if r["path"] == path:
                return r
        return None

    def value(self, rec: dict, field: str) -> str:
        patch = self.staged.get(rec["path"])
        if patch and field in patch:
            return patch[field] or ""
        return rec["fields"].get(field, "") or ""

    def is_dirty(self, rec: dict, field: str) -> bool:
        patch = self.staged.get(rec["path"])
        return bool(patch and field in patch)

    def visible(self) -> list[dict]:
        rows = self.records
        q = self.query.strip().lower()
        if q:
            def match(rec):
                if q in rec["rel"].lower():
                    return True
                return any(q in str(self.value(rec, f)).lower() for f in
                           ("title", "artist", "album", "albumartist", "year", "genre",
                            "composer", "track", "disc", "comment"))
            rows = [r for r in rows if match(r)]
        if self.sort_key == "rel":
            return sorted(rows, key=lambda r: r["rel"].lower(), reverse=self.sort_dir < 0)
        numeric = self.sort_key in ("track", "disc", "year")
        if numeric:
            return sorted(rows, key=lambda r: int(self.value(r, self.sort_key) or 0) if
                          str(self.value(r, self.sort_key)).isdigit() else 0,
                          reverse=self.sort_dir < 0)
        return sorted(rows, key=lambda r: str(self.value(r, self.sort_key)).lower(),
                      reverse=self.sort_dir < 0)

    def selected_paths(self) -> list[str]:
        if self.checks:
            return list(self.checks)
        return [self.active] if self.active else []

    def dirty_count(self) -> int:
        return sum(len(p) for p in self.staged.values())

    def refresh_backup_label(self):
        try:
            items = library.list_backups(1)
        except Exception:
            items = []
        self._backup_text = ""
        if items:
            b = items[0]
            self.last_backup = b["id"]
            import time
            stamp = time.strftime("%H:%M", time.localtime(b["created"]))
            self._backup_text = f"最近备份 {stamp} · {b['files']} 个文件"
        else:
            self.last_backup = None

    # ---------------- 渲染 ----------------
    def redraw(self):
        Painter.current = self.p        # 兜底：任何路径都不该让绘制指向别的画布
        self.p.hits.clear()
        self.canvas.delete("all")
        self.draw_zone_a()
        self.draw_zone_b()
        self.draw_table()
        self.draw_panel()

    def draw_zone_a(self):
        p = self.p
        p.rect(0, 0, W, A_H, PANEL)

        # logo：应用图标（缺失时退回手绘标记，保证界面不会开天窗）
        logo = self.logo_photo()
        if logo is not None:
            p.image(14, 13, logo)
        else:
            p.rrect(14, 15, 20, 20, 6, fill=ACCENT)
            for i, hh in enumerate((6, 10, 8)):
                p.rect(19 + i * 4, 30 - hh, 2, hh, INK)
        p.text(42, 25, "TagForge", F(14, True), TEXT)
        meta = f"{len(self.records)} 个文件 · {self.root_dir}"
        if self.truncated:
            meta = f"{len(self.records)} 个文件（已达扫描上限，更多文件未列出）· {self.root_dir}"
        p.text(114, 25, meta, F(12), DIRTY if self.truncated else DIM, width=740)
        if self._backup_text:
            p.text(1426, 25, self._backup_text, F(12), FAINT, anchor="e")

        # 路径输入框
        p.rrect(14, 46, 300, 30, 7, fill=CTRL, outline=LINE)
        icon_folder(24.4, 56.4)
        self.e_path.place_configure(x=S(14 + 27), y=S(46 + 7), width=S(300 - 27 - 12), height=S(17))
        if self.e_path.focus_get() is not self.e_path and self.e_path.value() != self.root_dir:
            self.e_path.set_value(self.root_dir)

        p.button(322, 46, 54, 30, "扫描", "primary", self.choose_dir)
        p.checkbox(384.5, 54.5, "递归", self.recursive, self.toggle_recursive, size=13)

        # 搜索
        p.rrect(955, 46, 190, 30, 7, fill=CTRL, outline=LINE)
        icon_search(966.5, 56.5)

        p.button(1153, 46, 49, 30, "生成", "secondary", self.open_from_names)
        p.button(1210, 46, 49, 30, "改名", "secondary", self.open_rename)
        p.button(1267, 46, 49, 30, "撤销", "ghost", self.undo_last)

        # 保存修改 + 脏字段徽标
        dirty = str(self.dirty_count())
        p.rrect(1324, 46, 102, 30, 7, fill=ACCENT, outline=ACCENT)
        p.text(1338, 61, "保存修改", F(13, True), INK)
        p.rrect(1397, 52, 19, 18, 4, fill=INK)
        p.text(1406.5, 61, dirty, F(11, mono=True), "#9DC7FF", anchor="center")
        p.hit(1324, 46, 102, 30, self.save)

    def draw_zone_b(self):
        p = self.p
        p.rect(0, A_H + 1, W, B_H, PANEL)
        p.line(0, A_H, W, A_H, LINE_SOFT)
        p.text(12, 114, "批量编辑", F(12, True), DIM)
        hint_x = 12 + p.measure("批量编辑", F(12, True)) + 10
        p.text(hint_x, 114, "留空的字段不会改动", F(12), FAINT)
        if self.checks:
            label, color = f"已选 {len(self.checks)} 个文件", ACCENT
        elif self.active:
            label, color = "当前文件 1 个", ACCENT
        else:
            label, color = "未选中（批量操作将无目标）", FAINT
        p.text(1426, 114, label, F(13, True), color, anchor="e")

        geom = {"album": (12, 132), "albumartist": (152, 140), "year": (300, 74), "genre": (382, 110)}
        for key, (x, w) in geom.items():
            p.rrect(x, 134, w, 28, 6, fill=CTRL, outline=LINE)
            self.b_fields[key].place_configure(x=S(x + 9), y=S(134 + 6), width=S(w - 18), height=S(17))

        p.rect(500, 139, 1, 18, LINE)
        label = "选择图片…" if not self.batch_cover_name else p.clip(self.batch_cover_name, 62, F(12))
        p.button(509, 134, 100, 28, label, "secondary", self.pick_cover, spec=F(12), icon=icon_image)
        p.rrect(617, 134, 46, 28, 7, fill=CTRL, outline=LINE)
        p.text(640, 148, "移除", F(12), DANGER, anchor="center")
        p.hit(617, 134, 46, 28, self.remove_cover)

        p.radio(1212, 141, "覆盖", self.batch_mode == "overwrite",
                lambda: self.set_mode("overwrite"))
        p.radio(1271, 141, "仅补空", self.batch_mode == "fill",
                lambda: self.set_mode("fill"))
        p.button(1337, 134, 91, 28, "应用到选中", "primary", self.apply_batch, spec=F(12))

    def draw_table(self):
        p = self.p
        rows = self.visible()
        p.rect(0, MAIN_Y, TABLE_W, H, BG)
        p.rect(0, MAIN_Y, TABLE_W, HDR_H, PANEL)
        p.line(0, MAIN_Y + HDR_H, TABLE_W, MAIN_Y + HDR_H, LINE)
        p.rect(TABLE_W, MAIN_Y, 1, H - MAIN_Y, LINE_SOFT)

        # 表头：勾选框按 未选 / 半选 / 全选 三态绘制
        hy = MAIN_Y + HDR_H / 2
        rows_now = rows          # 复用上面算好的一份，避免重复排序
        checked = sum(1 for r in rows_now if r["path"] in self.checks)
        state = "all" if rows_now and checked == len(rows_now) else ("some" if checked else "none")
        p.rrect(14.5, MAIN_Y + 10.5, 13, 13, 3,
                fill=ACCENT if state == "all" else (None if state == "none" else ACCENT),
                outline=ACCENT if state != "none" else CHK_EDGE)
        if state == "all":
            p.polyline([(17, hy), (19.6, hy + 2.6), (25, hy - 3.2)], INK, 2, "round", "round")
        elif state == "some":
            p.rect(17, hy - 1, 8, 2, INK)
        p.hit(10, MAIN_Y + 6, 22, 22, self.toggle_all)

        headers = [
            (X_FILE, "文件", "rel"), (X_TITLE, "标题", "title"), (X_ART, "艺术家", "artist"),
            (X_ALB, "专辑", "album"), (X_AART, "专辑艺术家", "albumartist"),
            (X_YEAR, "年份", "year"), (X_TRACK, "音轨", "track"), (X_DISC, "碟号", "disc"),
            (X_GENRE, "流派", "genre"), (X_COVER, "封面", None),
        ]
        for x, label, key in headers:
            sorted_here = key is not None and key == self.sort_key
            color = ACCENT if sorted_here else DIM
            p.text(x + PAD, hy, label, F(12), color)
            if sorted_here:
                ax = x + PAD + p.measure(label, F(12)) + 4
                # 升序画下箭头，降序画上箭头
                if self.sort_dir > 0:
                    p.polyline([(ax, hy - 1.5), (ax + 4.5, hy + 3), (ax + 9, hy - 1.5)],
                               ACCENT, 1.4, "round", "round")
                else:
                    p.polyline([(ax, hy + 3), (ax + 4.5, hy - 1.5), (ax + 9, hy + 3)],
                               ACCENT, 1.4, "round", "round")
        for label, x, kind in (("文件", X_FILE, "rel"), ("标题", X_TITLE, "title"),
                               ("艺术家", X_ART, "artist"), ("专辑", X_ALB, "album"),
                               ("专辑艺术家", X_AART, "albumartist"), ("年份", X_YEAR, "year"),
                               ("音轨", X_TRACK, "track"), ("碟号", X_DISC, "disc"),
                               ("流派", X_GENRE, "genre")):
            p.hit(x, MAIN_Y, W_FLEX if kind not in ("rel", "year", "track", "disc") else
                  (W_FILE if kind == "rel" else W_NUM), HDR_H, lambda k=kind: self.sort_by(k))

        top = MAIN_Y + HDR_H + 1
        capacity = int((H - top) / ROW_H)
        self.scroll = max(0, min(self.scroll, max(0, len(rows) - capacity)))
        for i, rec in enumerate(rows[self.scroll:self.scroll + capacity]):
            self.draw_row(rec, top + i * ROW_H)

    def draw_row(self, rec: dict, y: int):
        p = self.p
        is_active = rec["path"] == self.active
        if rec.get("error"):
            # 异常底色优先，当前行改用左侧主色竖条表达，两种状态可叠加辨认
            p.rect(0, y, TABLE_W, ROW_H, ROW_ERROR)
            if is_active:
                p.rect(0, y, 2, ROW_H, ACCENT)
        elif is_active:
            p.rect(0, y, TABLE_W, ROW_H, ROW_CURRENT)

        cy = y + ROW_H / 2
        # 勾选框
        on = rec["path"] in self.checks
        p.rrect(14.5, cy - 6.5, 13, 13, 3,
                fill=ACCENT if on else None,
                outline=ACCENT if on else CHK_EDGE)
        if on:
            p.polyline([(17, cy), (19.6, cy + 2.6), (25, cy - 3.2)], INK, 2, "round", "round")
        p.hit(6, y, 30, ROW_H, lambda path=rec["path"]: self.toggle_check(path))

        # 文件列
        name_y = cy + 1
        dir_txt = rec["rel"].rsplit("/", 1)[0] + "/" if "/" in rec["rel"] else ""
        base = rec["rel"].rsplit("/", 1)[-1]
        avail = W_FILE - PAD - 10
        x = X_FILE + PAD
        if dir_txt:
            dspec = F(12, mono=True)
            nspec = F(12, mono=True)
            name_w = p.measure(base, nspec)
            if p.measure(dir_txt, dspec) + name_w <= avail:
                dtxt = dir_txt
            else:
                # 目录太长就从左侧截断，保证文件名始终可见
                name_w = min(name_w, int(avail * 0.55))
                dtxt = p.clip_left(dir_txt, max(0, avail - name_w - 2), dspec)
            p.text(x, name_y, dtxt, dspec, FAINT)
            used = p.measure(dtxt, dspec)
            rest = avail - used
            if rest > 18:
                p.text(x + used, name_y, base, nspec, DIM, width=rest)
        else:
            p.text(x, name_y, base, F(12, mono=True), DIM, width=avail)

        if rec.get("error"):
            p.hit(X_FILE, y, W_FILE, ROW_H, lambda r=rec: self.set_active(r["path"]))
            p.text(X_TITLE + PAD, cy, "", F(13), TEXT)
            icon_alert(X_TITLE + PAD, cy - 5.5)
            p.text(X_TITLE + PAD + 16, cy, "解析失败", F(13), DANGER)
            for x, w in ((X_ART, W_FLEX), (X_ALB, W_FLEX), (X_AART, W_FLEX),
                         (X_YEAR, W_NUM), (X_TRACK, W_NUM), (X_DISC, W_NUM),
                         (X_GENRE, W_FLEX), (X_COVER, W_COVER)):
                p.text(x + PAD, cy, "—", F(13), FAINT)
            return

        cols = [(X_TITLE, "title", W_FLEX), (X_ART, "artist", W_FLEX),
                (X_ALB, "album", W_FLEX), (X_AART, "albumartist", W_FLEX),
                (X_YEAR, "year", W_NUM), (X_TRACK, "track", W_NUM),
                (X_DISC, "disc", W_NUM), (X_GENRE, "genre", W_FLEX)]
        for x, field, w in cols:
            val = self.value(rec, field)
            empty = not val
            dirty = self.is_dirty(rec, field)
            # 脏值优先判：清空的字段是"待删除"，必须和"从未填过"区分开
            color = DIRTY if dirty else (FAINT if empty else TEXT)
            p.text(x + PAD, cy, "—" if empty else val, F(13), color, width=w - PAD - 8)
            p.hit(x, y, w, ROW_H, lambda r=rec, f=field, cx=x, cw=w, ry=y:
                  self.begin_edit(r, f, cx, ry, cw))

        # 封面
        if rec.get("has_cover"):
            color = cover_color(rec["fields"].get("album", ""))
            p.rrect(X_COVER + PAD, cy - 11, 22, 22, 3, fill=color)
        else:
            p.text(X_COVER + PAD, cy, "—", F(13), FAINT)
        p.hit(X_COVER, y, W_COVER, ROW_H, lambda r=rec: self.set_active(r["path"]))
        p.hit(X_FILE, y, W_FILE, ROW_H, lambda r=rec: self.set_active(r["path"]))

    def draw_panel(self):
        p = self.p
        p.rect(PANEL_X, MAIN_Y, W - PANEL_X, H - MAIN_Y, PANEL)
        rec = self.record(self.active)
        x = PANEL_X + 14
        inner = W - PANEL_X - 28

        if not rec:
            p.text(x, 199, "未选择文件", F(12), FAINT)
            return

        base = rec["rel"].rsplit("/", 1)[-1]
        tag = self.engine_badge(rec)
        tw = max(54, p.measure(tag, F(10, mono=True)) + 12)
        p.text(x, 199, base, F(12, mono=True), TEXT, width=inner - tw - 10)
        p.rrect(W - 14 - tw, 191, tw, 17, 4, fill=CTRL, outline=LINE)
        p.text(W - 14 - tw / 2, 199.5, tag, F(10, mono=True), DIM, anchor="center")

        # 封面
        self.draw_cover(rec, x, 217)

        fields = [
            ("标题", "title", None), ("艺术家", "artist", None), ("专辑", "album", None),
            ("专辑艺术家", "albumartist", None),
        ]
        y = 325
        for label, key, _ in fields:
            self.draw_field(label, self.value(rec, key), x, y, inner, rec, key)
            y += 54

        pairs = [(("年份", "year"), ("流派", "genre")),
                 (("音轨号", "track"), ("音轨总数", "tracktotal")),
                 (("碟号", "disc"), ("碟号总数", "disctotal"))]
        for left, right in pairs:
            half = (inner - 8) // 2
            self.draw_field(left[0], self.value(rec, left[1]), x, y, half, rec, left[1])
            self.draw_field(right[0], self.value(rec, right[1]), x + half + 8, y, half, rec, right[1])
            y += 54

        self.draw_field("作曲", self.value(rec, "composer"), x, y, inner, rec, "composer")
        y += 54

        p.text(x, y + 8, "备注", F(11), FAINT)
        note = self.value(rec, "comment")
        dirty_note = self.is_dirty(rec, "comment")
        p.rrect(x, y + 20, inner, 40, 6, fill=CTRL, outline=ACCENT if dirty_note else LINE)
        p.text(x + 8, y + 32, note or "—", F(13),
               DIRTY if dirty_note else (TEXT if note else FAINT), width=inner - 16)
        p.hit(x, y + 20, inner, 40,
              lambda: self.begin_edit(rec, "comment", x, y + 20, inner, height=40, pad=0))

        info = self.probe(rec)
        size = rec["size"] / 1048576
        dur = int(info.get("length") or 0)
        tech = [rec["rel"].replace(self.root_dir.replace("\\", "/") + "/", "") if self.root_dir else rec["rel"]]
        line2 = f"{size:.1f} MB"
        if dur:
            line2 += f" · {dur // 60}:{dur % 60:02d}"
        if info.get("bitrate"):
            line2 += f" · {int(info['bitrate'] / 1000)} kbps"
        tech.append(line2)
        if info.get("sample_rate"):
            ch = "Stereo" if info.get("channels", 2) == 2 else f"{info.get('channels')} ch"
            tech.append(f"{info['sample_rate'] / 1000:.1f} kHz · {ch}")
        for i, line in enumerate(tech):
            p.text(x, 832 + i * 18, line, F(11, mono=True), FAINT, width=inner)

    def engine_badge(self, rec: dict) -> str:
        """引擎徽标：ID3 显示文件的真实版本，而不是固定写 v2.4。"""
        engine = rec.get("engine") or ""
        if engine == "id3":
            version = (self.probe(rec) or {}).get("id3_version")
            return f"ID3v{version}" if version else "ID3"
        return ENGINE_TAG.get(engine, (rec.get("ext") or "").lstrip(".").upper() or "?")

    def draw_field(self, label, value, x, y, w, rec, key):
        p = self.p
        p.text(x, y + 8, label, F(11), FAINT)
        dirty = self.is_dirty(rec, key)
        p.rrect(x, y + 20, w, 26, 6, fill=CTRL, outline=ACCENT if dirty else LINE)
        color = DIRTY if dirty else (TEXT if value else FAINT)
        p.text(x + 8, y + 33, value or "—", F(13), color, width=w - 16)
        # 面板与表格共用同一份 staged：点这里也能改，不必回到表格
        p.hit(x, y + 20, w, 26,
              lambda r=rec, k=key: self.begin_edit(r, k, x, y + 20, w, height=26, pad=0))

    def draw_cover(self, rec, x, y):
        p = self.p
        photo = self.photo(rec)
        if photo is not None:
            p.image(x, y, photo)
            return
        p.rrect(x, y, 100, 100, 6, fill=cover_color(rec["fields"].get("album", "")),
                outline=LINE_SOFT)
        if not rec.get("has_cover"):
            p.text(x + 50, y + 74, "无封面", F(11), FAINT, anchor="center")

    def logo_photo(self):
        # 单独缓存：_photos 会在重新扫描时清空（那是封面缓存），logo 不该跟着丢
        if self._logo is None:
            try:
                self._logo = load_icon(20)
            except Exception:
                self._logo = None
        return self._logo

    def probe(self, rec: dict) -> dict:
        path = rec["path"]
        if path not in self._probe_cache:
            try:
                self._probe_cache[path] = adapters.probe(path)
            except Exception:
                self._probe_cache[path] = {}
        return self._probe_cache[path]

    def photo(self, rec: dict):
        if not rec.get("has_cover"):
            return None
        if rec["path"] in self._photos:
            return self._photos[rec["path"]]
        img = None
        try:
            cover = adapters.read_cover(rec["path"])
            if cover:
                img = self._to_photo(cover.data)
        except Exception:
            img = None
        self._photos[rec["path"]] = img
        return img

    def _to_photo(self, data: bytes):
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            try:
                img = tk.PhotoImage(data=base64.b64encode(data), format="png")
                return self._fit(img, S(100))
            except Exception:
                return None
        try:
            from PIL import Image  # 可选：装了 Pillow 就能显示 JPEG 封面
        except Exception:
            return None
        try:
            size = max(1, S(100))
            im = Image.open(io.BytesIO(data)).convert("RGB")
            im.thumbnail((size, size))
            canvas_img = Image.new("RGB", (size, size), (26, 29, 35))
            canvas_img.paste(im, ((size - im.width) // 2, (size - im.height) // 2))
            buf = io.BytesIO()
            canvas_img.save(buf, format="PNG")
            return tk.PhotoImage(data=base64.b64encode(buf.getvalue()), format="png")
        except Exception:
            return None

    @staticmethod
    def _fit(img, target: int):
        """把封面缩放到 target 设备像素（Tk 只支持 zoom/subsample 的整数倍，
        所以用有理近似 a/b 精确命中常见 DPI：1.25 = 5/4、1.5 = 3/2、1.75 = 7/4）。"""
        w, h = img.width(), img.height()
        if not w or not h:
            return img
        frac = target / w
        if abs(frac - 1) < 0.02:
            return img
        best = None
        for b in range(1, 17):
            a = int(round(frac * b))
            if a < 1:
                continue
            err = abs(a / b - frac)
            if best is None or err < best[0]:
                best = (err, a, b)
        if best is None:
            return img
        _, a, b = best
        if a * max(w, h) > 3200:          # 控制中间位图大小
            a, b = 1, max(1, int(round(1 / frac)))
        out = img.zoom(a, a) if a > 1 else img
        if b > 1:
            out = out.subsample(b, b)
        return out

    # ---------------- 交互 ----------------
    def _on_click(self, event):
        if self.editor is not None:
            self._commit_edit()
        cb = self.p.find(event.x, event.y)
        if cb:
            cb()

    def _on_wheel(self, event):
        rows = self.visible()
        capacity = int((H - MAIN_Y - HDR_H - 1) / ROW_H)
        limit = max(0, len(rows) - capacity)
        new = max(0, min(limit, self.scroll + (-1 if event.delta > 0 else 1) * 2))
        if new != self.scroll:
            self.scroll = new
            self.redraw()

    def _on_search(self, _=None):
        self.query = self.e_search.value()
        self.scroll = 0
        self.redraw()

    def scan_from_entry(self):
        path = self.e_path.value()
        if not path or not os.path.isdir(path):
            messagebox.showerror("目录无效", f"不是有效的文件夹：\n{path}")
            return
        self.scan(path)
        self.redraw()

    def choose_dir(self):
        path = filedialog.askdirectory(initialdir=self.root_dir or os.getcwd(), title="选择音乐目录")
        if path:
            self.scan(path)
            self.redraw()

    def toggle_recursive(self):
        self.recursive = not self.recursive
        self.scan()
        self.redraw()

    def sort_by(self, key):
        if self.sort_key == key:
            self.sort_dir *= -1
        else:
            self.sort_key, self.sort_dir = key, 1
        self.redraw()

    def set_active(self, path):
        self.active = path
        self.redraw()

    def toggle_check(self, path):
        if path in self.checks:
            self.checks.discard(path)
        else:
            self.checks.add(path)
        self.redraw()

    def toggle_all(self):
        rows = self.visible()
        if all(r["path"] in self.checks for r in rows) and rows:
            self.checks.clear()
        else:
            self.checks.update(r["path"] for r in rows)
        self.redraw()

    def set_mode(self, mode):
        self.batch_mode = mode
        self.redraw()

    # ---------------- 单元格编辑 ----------------
    def begin_edit(self, rec, field, col_x, row_y, col_w, height: int = 0, pad: int = 4):
        self.set_active(rec["path"])
        self._cancel_edit()
        cur = self.value(rec, field)
        height = height or (ROW_H - 8)
        e = tk.Entry(self.canvas, bg=CTRL if pad == 0 else BG, fg=TEXT,
                     insertbackground=TEXT, bd=0,
                     highlightthickness=1, highlightbackground=ACCENT,
                     highlightcolor=ACCENT, font=F(13))
        e.insert(0, cur)
        e.select_range(0, "end")
        e.place(x=S(col_x + pad), y=S(row_y + pad),
                width=S(max(40, col_w - pad * 2)), height=S(height - pad * 2))
        e.focus_set()
        e.bind("<Return>", lambda ev: self._commit_edit())
        e.bind("<Escape>", lambda ev: self._cancel_edit())
        e.bind("<FocusOut>", lambda ev: self._commit_edit())
        e.bind("<Tab>", lambda ev: self._tab_edit(rec, field, 1))
        e.bind("<Shift-Tab>", lambda ev: self._tab_edit(rec, field, -1))
        e.bind("<ISO_Left_Tab>", lambda ev: self._tab_edit(rec, field, -1))
        self.editor = e
        self.edit_target = (rec["path"], field)
        self.redraw()
        e.lift()

    def _commit_edit(self):
        if self.editor is None or self.edit_target is None:
            return
        value = self.editor.get().strip()
        path, field = self.edit_target
        self.editor.destroy()
        self.editor, self.edit_target = None, None
        rec = self.record(path)
        if rec:
            original = rec["fields"].get(field, "") or ""
            patch = self.staged.get(path)
            if value == original:
                if patch:
                    patch.pop(field, None)
                    if not patch:
                        self.staged.pop(path, None)
            else:
                self.staged.setdefault(path, {})[field] = value
                if not patch:
                    pass
        self.redraw()

    def _tab_edit(self, rec, field, delta):
        """提交当前单元格并横向/纵向移动到下一个可编辑单元格。"""
        self._commit_edit()
        rows = self.visible()
        paths = [r["path"] for r in rows]
        if rec["path"] not in paths or field not in EDIT_COLUMNS:
            return "break"

        row_i = paths.index(rec["path"])
        col_i = EDIT_COLUMNS.index(field) + delta
        if col_i >= len(EDIT_COLUMNS):
            col_i, row_i = 0, row_i + 1
        elif col_i < 0:
            col_i, row_i = len(EDIT_COLUMNS) - 1, row_i - 1
        if not (0 <= row_i < len(rows)):
            return "break"

        target = rows[row_i]
        if target.get("error"):        # 解析失败的行没有可编辑字段
            return "break"

        capacity = int((H - MAIN_Y - HDR_H - 1) / ROW_H)
        if row_i < self.scroll:
            self.scroll = row_i
        elif row_i >= self.scroll + capacity:
            self.scroll = row_i - capacity + 1

        x, w = COLUMN_GEOM[EDIT_COLUMNS[col_i]]
        y = MAIN_Y + HDR_H + 1 + (row_i - self.scroll) * ROW_H
        self.begin_edit(target, EDIT_COLUMNS[col_i], x, y, w)
        return "break"

    def _cancel_edit(self):
        if self.editor is not None:
            self.editor.destroy()
            self.editor, self.edit_target = None, None
            self.redraw()

    # ---------------- 写盘动作 ----------------
    def save(self):
        if not self.staged:
            messagebox.showinfo("TagForge", "没有需要保存的修改。")
            return
        changes = [{"path": p, "patch": dict(patch)} for p, patch in self.staged.items()]
        try:
            result = library.apply_changes(changes, mode="overwrite", label="编辑标签")
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        # 只丢弃"确实写成功"的暂存；失败与已丢失文件保留，便于重试或另存
        bad = {f["path"] for f in result["failed"]} | set(result.get("missing") or [])
        self.staged = {p: v for p, v in self.staged.items() if p in bad}
        self.scan(preserve=True)
        self.redraw()

        if result["failed"] or result.get("missing"):
            lines = [f"{os.path.basename(f['path'])}: {f['error']}" for f in result["failed"][:6]]
            lines += [f"{os.path.basename(p)}: 文件已不存在" for p in (result.get("missing") or [])[:6]]
            messagebox.showwarning(
                "部分文件保存失败",
                f"成功 {result['applied']} 个，失败/丢失 {len(bad)} 个\n\n" + "\n".join(lines))
        else:
            messagebox.showinfo("TagForge", f"已保存 {result['applied']} 个文件。")

    def apply_batch(self):
        paths = self.selected_paths()
        if not paths:
            messagebox.showinfo("TagForge", "请先勾选要处理的文件。")
            return
        if self.staged:
            # 批量写入会重扫列表；不提示就重扫会把手改的内容一起丢掉
            if not messagebox.askyesno(
                "还有未保存的修改",
                f"当前有 {self.dirty_count()} 处未保存的单元格修改。\n"
                f"批量写入不会保存它们，确定继续吗？"):
                return
        patch = {k: self.b_fields[k].value() for k in ("album", "albumartist", "year", "genre")}
        patch = {k: v for k, v in patch.items() if v}
        if self.batch_cover:
            patch["cover"] = {"op": "set", "mime": self.batch_cover[1],
                              "data_b64": base64.b64encode(self.batch_cover[0]).decode()}
        if not patch:
            messagebox.showinfo("TagForge", "批量栏里没有要写入的内容。")
            return
        changes = [{"path": p, "patch": dict(patch)} for p in paths]
        try:
            result = library.apply_changes(changes, mode=self.batch_mode,
                                           label="批量编辑")
        except Exception as exc:
            messagebox.showerror("批量写入失败", str(exc))
            return
        if result["applied"]:
            # 只有真的写成功才清空输入；失败时保留便于重试
            for entry in self.b_fields.values():
                entry.set_value("")
            self.batch_cover, self.batch_cover_name = None, ""
        self.scan(preserve=True)
        self.redraw()
        if result["failed"]:
            detail = "\n".join(f"{os.path.basename(f['path'])}: {f['error']}"
                               for f in result["failed"][:6])
            messagebox.showwarning("部分文件写入失败",
                                   f"成功 {result['applied']} 个，失败 {len(result['failed'])} 个\n\n{detail}")
        else:
            messagebox.showinfo("TagForge", f"已写入 {result['applied']} 个文件。")

    def pick_cover(self):
        path = filedialog.askopenfilename(
            title="选择封面图片", filetypes=[("图片", "*.png *.jpg *.jpeg *.webp *.bmp")])
        if not path:
            return
        try:
            data = open(path, "rb").read()
        except OSError as exc:
            messagebox.showerror("读取失败", str(exc))
            return
        mime = adapters.sniff_mime(data)
        self.batch_cover = (data, mime)
        self.batch_cover_name = os.path.basename(path)
        self.redraw()

    def remove_cover(self):
        paths = self.selected_paths()
        if not paths:
            messagebox.showinfo("TagForge", "请先勾选要处理的文件。")
            return
        if not messagebox.askyesno("移除封面", f"移除 {len(paths)} 个文件的封面？"):
            return
        changes = [{"path": p, "patch": {"cover": {"op": "remove"}}} for p in paths]
        result = library.apply_changes(changes, label="移除封面")
        self.scan(preserve=True)
        self.redraw()
        if result["failed"]:
            messagebox.showwarning("部分文件移除失败",
                                   f"成功 {result['applied']} 个，失败 {len(result['failed'])} 个")

    @staticmethod
    def _backup_sample(backup_id: str) -> str:
        """取备份里第一个文件的相对位置，让确认框能说清"要回退的是哪一批"。"""
        try:
            data = library._load_backup(backup_id)
        except Exception:
            return ""
        for entry in data.get("files", []):
            return os.path.basename(entry.get("path", ""))
        return ""

    def undo_last(self):
        items = library.list_backups(1)
        if not items:
            messagebox.showinfo("TagForge", "没有可撤销的操作。")
            return
        backup = items[0]
        import time
        stamp = time.strftime("%H:%M", time.localtime(backup["created"]))
        dlg = ConfirmUndo(self, backup, stamp, self.dirty_count(),
                          sample=self._backup_sample(backup["id"]))
        self.root.wait_window(dlg)
        if dlg.result:
            result = library.undo(backup["id"])
            # preserve：确认框承诺"未保存的修改会保留"，这里必须兑现
            self.scan(preserve=True)
            self.redraw()
            msg = f"已恢复 {result['restored']} 个文件。"
            if result.get("skipped"):
                msg += f"\n\n{len(result['skipped'])} 个文件缺少可用快照，已跳过（未改动）。"
            if result["failed"]:
                msg += f"\n{len(result['failed'])} 个文件回退失败。"
            messagebox.showinfo("TagForge", msg)

    # ---------------- 对话框 ----------------
    def target_paths(self) -> list[str]:
        """批量类对话框的作用范围：有勾选就用勾选，否则用当前筛选出的全部。"""
        if self.checks:
            return [r["path"] for r in self.visible() if r["path"] in self.checks]
        return [r["path"] for r in self.visible()]

    def open_from_names(self):
        targets = self.target_paths()
        if not targets:
            messagebox.showinfo("TagForge", "没有可处理的文件。")
            return
        items = []
        for path in targets:
            rec = self.record(path) or {"fields": {}, "rel": os.path.basename(path)}
            items.append({"path": path, "fields": dict(rec["fields"]), "rel": rec["rel"]})
        dlg = FromNamesDialog(self, items, self.root_dir)
        self.root.wait_window(dlg)
        if dlg.applied:
            self.scan()
            self.redraw()

    def open_rename(self):
        targets = self.target_paths()
        if not targets:
            messagebox.showinfo("TagForge", "没有可处理的文件。")
            return
        items = []
        for path in targets:
            rec = self.record(path) or {"fields": {}}
            items.append({"path": path, "fields": {k: self.value(rec, k) for k in
                                                   ("title", "artist", "album", "albumartist",
                                                    "year", "track", "disc", "genre")}})
        dlg = RenameDialog(self, items, self.root_dir)
        self.root.wait_window(dlg)
        if dlg.applied:
            self.scan()
            self.redraw()


# ---------------------------------------------------------------- 模态基类
class Dialog(tk.Toplevel):
    w, h = 1080, 520
    title_text = ""

    def __init__(self, master: TagForgeApp, title: str, w: int, h: int):
        super().__init__(master.root)
        self.app = master
        self.w, self.h = w, h
        self.result = None
        self.applied = False
        self.configure(bg=LINE)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        x = master.root.winfo_rootx() + (S(W) - S(w)) // 2
        y = master.root.winfo_rooty() + max(0, (S(H) - S(h)) // 2)
        self.geometry(f"{S(w)}x{S(h)}+{x}+{y}")

        self.frame = tk.Frame(self, bg=PANEL, highlightthickness=0)
        self.frame.place(x=S(1), y=S(1), width=S(w - 2), height=S(h - 2))
        self.canvas = tk.Canvas(self.frame, width=S(w - 2), height=S(h - 2),
                                bg=PANEL, highlightthickness=0)
        self.canvas.pack()
        self.p = Painter(self.canvas)
        Painter.current = self.p

        Painter.current = self.p
        self.canvas.bind("<Button-1>", self._on_click)
        self.bind("<Escape>", lambda e: self.close())
        for widget in (self.canvas,):
            widget.bind("<ButtonPress-1>", self._drag_start, add="+")
            widget.bind("<B1-Motion>", self._drag_move, add="+")

        self.transient(master.root)
        self.after(30, self._grab)

    def _grab(self):
        try:
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass

    def _drag_start(self, event):
        self._dx, self._dy = event.x_root - self.winfo_x(), event.y_root - self.winfo_y()

    def _drag_move(self, event):
        self.geometry(f"+{event.x_root - self._dx}+{event.y_root - self._dy}")

    def _on_click(self, event):
        cb = self.p.find(event.x, event.y)
        if cb:
            cb()

    def close(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        # 关键：Painter.current 被对话框覆盖过，销毁后必须归还给主窗口。
        # 否则主窗口下一次重绘会去操作已销毁的画布，抛 TclError 并留下空白界面。
        Painter.current = self.app.p
        self.destroy()

    def draw_head(self):
        p = self.p
        p.rect(0, 0, self.w - 2, 52, PANEL)
        p.line(0, 52, self.w - 2, 52, LINE_SOFT)
        p.text(20, 26, self.title_text, F(15, True), TEXT)
        p.rrect(self.w - 2 - 44, 14, 24, 24, 6, fill=CTRL, outline=CTRL)
        icon_close(self.w - 2 - 44 + 7.5, 21.5)
        p.hit(self.w - 2 - 44, 14, 24, 24, self.close)


class FromNamesDialog(Dialog):
    title_text = "从文件名生成标签"

    def __init__(self, master, items, root_dir):
        super().__init__(master, self.title_text, 1080, 520)
        self.items = items
        self.root_dir = root_dir
        self.use_path = any("/" in (i.get("rel") or "") for i in items)
        self.overwrite = False
        self.rows = []
        self.stat = ""
        self.can_write = False
        self.previewed_key = None     # 预览时的参数快照，用于阻止"改了模板直接写"
        self.quota = 5
        self.offset = 0

        self.tpl = naming.guess_template([i["path"] for i in items], root_dir, self.use_path)
        self.e_tpl = tk.Entry(self.canvas, bg=CTRL, fg=TEXT, insertbackground=TEXT, bd=0,
                              highlightthickness=1, highlightbackground=ACCENT,
                              highlightcolor=ACCENT, font=F(13, mono=True))
        self.e_tpl.insert(0, self.tpl)
        self.e_tpl.place(x=S(20 + 44), y=S(52 + 20 + 4), width=S(1080 - 62 - 44 - 20), height=S(24))
        self.e_tpl.bind("<Return>", lambda e: self.preview())
        self.e_tpl.bind("<KeyRelease>", lambda e: self._invalidate())
        self.canvas.bind("<MouseWheel>", self._on_wheel, add="+")
        self.draw()

    def _invalidate(self):
        """模板/选项变了，旧的预览结果不再可信 → 立即禁用写入。"""
        if self.can_write or self.rows:
            self.can_write = False
            self.previewed_key = None
            self.rows = []
            self.stat = "参数已修改，请重新预览"
            self.draw()

    def _key(self):
        return (self.e_tpl.get().strip(), self.use_path, self.overwrite)

    def draw(self):
        p = self.p
        p.c.delete("all")
        p.hits.clear()
        self.draw_head()
        y = 52 + 20
        p.text(20, y + 17, "模板", F(13), DIM)
        p.rrect(20 + 40, y, self.w - 82 - 40, 34, 6, fill=CTRL, outline=ACCENT)
        y += 34 + 14

        p.checkbox(20, y, "匹配相对路径（目录结构）", self.use_path, self.toggle_path)
        p.checkbox(20 + 210, y, "覆盖已有值", self.overwrite, self.toggle_overwrite)
        y += 18 + 14

        p.text(20, y + 8, "可用占位符　%title%　%artist%　%album%　%albumartist%　%track%　"
                          "%track:2%（补零）　%year%，方括号为可选段。", F(12), FAINT)
        y += 16 + 14

        p.button(20, y, 58, 30, "预览", "secondary", self.preview, spec=F(13))
        p.button(86, y, 84, 30, "写入标签", "primary" if self.can_write else "disabled",
                 self.apply if self.can_write else None, spec=F(13))
        if self.stat:
            p.text(self.w - 22, y + 15, self.stat, F(13), DIM, anchor="e")
        y += 30 + 14

        # 预览表
        tw = self.w - 42
        th = self.h - 2 - y - 20
        p.rrect(20, y, tw, th, 8, fill=BG, outline=LINE_SOFT)
        p.rect(21, y + 1, tw - 2, 32, PANEL)
        p.line(21, y + 33, 20 + tw - 1, y + 33, LINE_SOFT)
        widths = [230, 0, 120, 64, 0, 96]
        flex = (tw - sum(widths)) / 2
        widths = [w if w else flex for w in widths]
        self.cols_x = []
        x = 21
        for width in widths:
            self.cols_x.append((x, width))
            x += width
        for (cx, cw), label in zip(self.cols_x,
                                   ("文件", "解析文本", "albumartist", "track", "title", "将写入")):
            p.text(cx + 10, y + 17, label, F(12), DIM, width=cw - 20)

        top = y + 34
        self.quota = max(1, int((th - 34 - 16) / 30))      # 底部留 16px 给翻页提示
        visible_rows = self.rows[self.offset:self.offset + self.quota]
        for i, row in enumerate(visible_rows):
            self.draw_preview_row(row, top + i * 30)
        if len(self.rows) > self.quota:
            p.text(self.w - 30, y + th - 12,
                   f"{self.offset + 1}-{self.offset + len(visible_rows)} / 共 {len(self.rows)} 行"
                   "（滚轮翻页）", F(11), FAINT, anchor="e")

    def draw_preview_row(self, row, y):
        p = self.p
        if not row:
            return
        if row.get("kind") == "bad":
            p.rect(21, y, self.w - 44, 30, ROW_ERROR)
        for (x, w), (text, tone) in zip(self.cols_x, row["cells"]):
            color = {"amber": DIRTY, "ok": OK, "bad": DANGER, "faint": FAINT}.get(tone, TEXT)
            p.text(x + 10, y + 15, text, F(13), color, width=w - 20)

    def _on_wheel(self, event):
        if len(self.rows) <= self.quota:
            return
        offset = self.offset + (-1 if event.delta > 0 else 1)
        offset = max(0, min(offset, len(self.rows) - self.quota))
        if offset != self.offset:
            self.offset = offset
            self.draw()

    def toggle_path(self):
        self.use_path = not self.use_path
        self._invalidate()

    def toggle_overwrite(self):
        self.overwrite = not self.overwrite
        self._invalidate()

    def preview(self):
        tpl = self.e_tpl.get().strip()
        try:
            data = library.preview_from_names(self.items, tpl, self.root_dir,
                                              self.use_path, self.overwrite)
        except Exception as exc:
            messagebox.showerror("模板错误", str(exc), parent=self)
            return
        self.rows = []
        for row in data["rows"]:
            name = os.path.basename(row["path"])
            if not row["matched"]:
                reason = row.get("reason") or "无法匹配模板"
                self.rows.append({"kind": "bad", "cells": [
                    (name, ""), (reason, "bad"),
                    ("—", "bad"), ("—", "bad"), ("—", "bad"), ("解析失败", "bad")]})
                continue
            if not row["changes"]:
                self.rows.append({"kind": "faint", "cells": [
                    (name, ""), (row["target"], "faint"),
                    ("—", "faint"), ("—", "faint"), ("—", "faint"), ("无变化", "faint")]})
                continue
            parsed = row["parsed"]
            self.rows.append({"kind": "ok", "cells": [
                (name, ""),
                (row["target"], "amber"),
                (parsed.get("albumartist", "—"), "amber"),
                (parsed.get("track", "—"), "amber"),
                (parsed.get("title", "—"), "amber"),
                (f"{len(row['changes'])} 字段", "ok")]})

        writable = sum(1 for r in data["rows"] if r["changes"])
        failed = sum(1 for r in data["rows"] if not r["matched"])
        unchanged = sum(1 for r in data["rows"] if r["matched"] and not r["changes"])
        parts = []
        if writable:
            parts.append(f"{writable} 个可写")
        if failed:
            parts.append(f"{failed} 个解析失败")
        if unchanged:
            parts.append(f"{unchanged} 个无变化")
        self.stat = " · ".join(parts) or "没有可写入的字段"
        self.can_write = writable > 0
        self.previewed_key = self._key()
        self.offset = 0
        self.draw()

    def apply(self):
        if self._key() != self.previewed_key:
            messagebox.showinfo("TagForge", "模板或选项已修改，请先重新预览。", parent=self)
            return
        tpl, use_path, overwrite = self.previewed_key
        try:
            data = library.preview_from_names(self.items, tpl, self.root_dir,
                                              use_path, overwrite)
        except Exception as exc:
            messagebox.showerror("模板错误", str(exc), parent=self)
            return
        changes = [{"path": r["path"], "patch": r["changes"]}
                   for r in data["rows"] if r["changes"]]
        if not changes:
            messagebox.showinfo("TagForge", "没有可写入的字段。", parent=self)
            return
        result = library.apply_changes(changes, mode="overwrite", label="从文件名生成标签")
        self.applied = True
        self.close()
        msg = f"已写入 {result['applied']} 个文件。"
        if result["failed"]:
            msg += f"\n{len(result['failed'])} 个失败。"
        messagebox.showinfo("TagForge", msg)


class RenameDialog(Dialog):
    title_text = "按标签重命名文件"

    def __init__(self, master, items, root_dir):
        super().__init__(master, self.title_text, 1080, 420)
        self.items = items
        self.root_dir = root_dir
        self.keep_dir = True
        self.skip_exist = True
        self.rows = []
        self.stat = ""
        self.plan = []
        self.can_apply = False
        self.previewed_key = None
        self.quota = 4
        self.offset = 0

        self.e_tpl = tk.Entry(self.canvas, bg=CTRL, fg=TEXT, insertbackground=TEXT, bd=0,
                              highlightthickness=1, highlightbackground=LINE,
                              highlightcolor=ACCENT, font=F(13, mono=True))
        self.e_tpl.insert(0, "%track:2% - %title%")
        self.e_tpl.place(x=S(20 + 44), y=S(52 + 20 + 4), width=S(1080 - 62 - 44 - 20), height=S(24))
        self.e_tpl.bind("<Return>", lambda e: self.preview())
        self.e_tpl.bind("<KeyRelease>", lambda e: self._invalidate())
        self.canvas.bind("<MouseWheel>", self._on_wheel, add="+")
        self.draw()

    def _invalidate(self):
        if self.can_apply or self.plan:
            self.can_apply = False
            self.previewed_key = None
            self.plan = []
            self.rows = []
            self.stat = "参数已修改，请重新预览"
            self.draw()

    def _key(self):
        return (self.e_tpl.get().strip(), self.keep_dir, self.skip_exist)

    def draw(self):
        p = self.p
        p.c.delete("all")
        p.hits.clear()
        self.draw_head()
        y = 52 + 20
        p.text(20, y + 17, "模板", F(13), DIM)
        p.rrect(20 + 40, y, self.w - 82 - 40, 34, 6, fill=CTRL, outline=LINE)
        y += 34 + 14

        p.checkbox(20, y, "保留目录结构", self.keep_dir, self.toggle_dir)
        p.checkbox(20 + 150, y, "跳过目标已存在的文件", self.skip_exist, self.toggle_skip)
        y += 18 + 14
        p.text(20, y + 8, "重命名直接作用于磁盘，不进入未保存修改；冲突行不会被执行。", F(12), FAINT)
        y += 16 + 14

        p.button(20, y, 58, 30, "预览", "secondary", self.preview, spec=F(13))
        p.button(86, y, 96, 30, "执行重命名", "primary" if self.can_apply else "disabled",
                 self.apply if self.can_apply else None, spec=F(13))
        if self.stat:
            p.text(self.w - 22, y + 15, self.stat, F(13), DIM, anchor="e")
        y += 30 + 14

        tw = self.w - 42
        th = self.h - 2 - y - 20
        p.rrect(20, y, tw, th, 8, fill=BG, outline=LINE_SOFT)
        p.rect(21, y + 1, tw - 2, 32, PANEL)
        p.line(21, y + 33, 20 + tw - 1, y + 33, LINE_SOFT)
        flex = (tw - 140) / 2
        self.cols_x = [(21, flex), (21 + flex, flex), (21 + 2 * flex, 140)]
        for (x, w), label in zip(self.cols_x, ("原文件名", "新文件名", "状态")):
            p.text(x + 10, y + 17, label, F(12), DIM, width=w - 20)

        top = y + 34
        self.quota = max(1, int((th - 34 - 16) / 30))      # 底部留 16px 给翻页提示
        visible_rows = self.rows[self.offset:self.offset + self.quota]
        for i, row in enumerate(visible_rows):
            self.draw_row(row, top + i * 30)
        if len(self.rows) > self.quota:
            p.text(self.w - 30, y + th - 12,
                   f"{self.offset + 1}-{self.offset + len(visible_rows)} / 共 {len(self.rows)} 行"
                   "（滚轮翻页）", F(11), FAINT, anchor="e")

    def draw_row(self, row, y):
        p = self.p
        if row["bad"]:
            p.rect(21, y, self.w - 44, 30, ROW_ERROR)
        for (x, w), (text, tone) in zip(self.cols_x, row["cells"]):
            color = {"amber": DIRTY, "ok": OK, "bad": DANGER, "faint": FAINT}.get(tone, TEXT)
            p.text(x + 10, y + 15, text, F(13), color, width=w - 20)

    def _on_wheel(self, event):
        if len(self.rows) <= self.quota:
            return
        offset = self.offset + (-1 if event.delta > 0 else 1)
        offset = max(0, min(offset, len(self.rows) - self.quota))
        if offset != self.offset:
            self.offset = offset
            self.draw()

    def toggle_dir(self):
        self.keep_dir = not self.keep_dir
        self._invalidate()

    def toggle_skip(self):
        self.skip_exist = not self.skip_exist
        self._invalidate()

    def preview(self):
        tpl = self.e_tpl.get().strip()
        try:
            data = library.plan_rename(self.items, tpl, self.root_dir,
                                       self.keep_dir, self.skip_exist)
        except Exception as exc:
            messagebox.showerror("模板错误", str(exc), parent=self)
            return
        self.plan = data["rows"]
        self.rows = []
        for row in self.plan:
            old = os.path.basename(row["path"])
            if row.get("error"):
                self.rows.append({"bad": True, "cells": [
                    (old, "bad"), (row.get("rel") or "—", "bad"), (row["error"], "bad")]})
            elif row.get("unchanged"):
                self.rows.append({"bad": False, "cells": [
                    (old, "faint"), (old, "faint"), ("未变化", "faint")]})
            else:
                new_name = row.get("rel") or old
                if self.keep_dir:      # 保留目录结构时只显示文件名，目录不变
                    new_name = new_name.rsplit("/", 1)[-1]
                label = "将覆盖" if row.get("overwrite") else "可重命名"
                self.rows.append({"bad": False, "cells": [
                    (old, "faint"), (new_name, "amber"), (label, "ok")]})

        conflicts = sum(1 for r in self.plan if r.get("error"))
        movable = sum(1 for r in self.plan
                      if r.get("target") and not r.get("error") and not r.get("unchanged"))
        unchanged = sum(1 for r in self.plan if r.get("unchanged"))
        parts = []
        if movable:
            parts.append(f"{movable} 个可重命名")
        if conflicts:
            parts.append(f"{conflicts} 个冲突")
        if unchanged:
            parts.append(f"{unchanged} 个未变化")
        self.stat = " · ".join(parts) or "没有可重命名的文件"
        self.can_apply = movable > 0
        self.previewed_key = self._key()
        self.offset = 0
        self.draw()

    def apply(self):
        if self._key() != self.previewed_key:
            messagebox.showinfo("TagForge", "模板或选项已修改，请先重新预览。", parent=self)
            return
        rows = [r for r in self.plan
                if r.get("target") and not r.get("error") and not r.get("unchanged")]
        if not rows:
            messagebox.showinfo("TagForge", "没有可重命名的文件。", parent=self)
            return
        result = library.apply_rename(rows)
        self.applied = True
        self.close()
        msg = f"已重命名 {result['moved']} 个文件。"
        if result["failed"]:
            msg += f"\n{len(result['failed'])} 个失败。"
        messagebox.showinfo("TagForge", msg)


class ConfirmUndo(Dialog):
    def __init__(self, master, backup, stamp, dirty, sample: str = ""):
        self.backup = backup
        self.stamp = stamp
        self.dirty = dirty
        self.sample = sample
        super().__init__(master, "撤销上次保存？", 420, 190)
        self.draw()

    def draw(self):
        p = self.p
        p.c.delete("all")
        p.hits.clear()
        p.text(20, 30, "撤销上次保存？", F(15, True), TEXT)
        # \u00A0 绑定"数字 + 量词"，避免折行时把 "5 处" 拆到两行
        msg = (f"将回退 {self.stamp} 的备份，涉及 {self.backup['files']}\u00A0个文件。"
               f"撤销只回退已写入的部分；你还有 {self.dirty}\u00A0处未保存修改，它们会保留。")
        p.paragraph(20, 56, msg, F(13), DIM, self.w - 34)
        where = self.sample or self.backup.get("label", "")
        p.text(20, 132, f"备份点 {self.stamp} · {self.backup['files']} 个文件 · {where}",
               F(11, mono=True), FAINT, width=self.w - 40)

        p.button(self.w - 42 - 78 - 90, 148, 78, 30, "取消", "secondary", self.close, spec=F(13))
        p.button(self.w - 42 - 90 + 12, 148, 100, 30, "确认撤销", "danger", self.confirm, spec=F(13))

    def confirm(self):
        self.result = True
        self.close()


# ---------------------------------------------------------------- 入口
def enable_dpi():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def detect_scale() -> float:
    """读取系统 DPI 换算成缩放倍率（96 DPI = 1.0）。"""
    dpi = 96
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()      # Win10 1607+
    except Exception:
        try:
            hdc = ctypes.windll.user32.GetDC(0)
            dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
            ctypes.windll.user32.ReleaseDC(0, hdc)
        except Exception:
            dpi = 96
    return max(1.0, round(dpi / 96.0, 2))


def main(argv: list[str] | None = None) -> int:
    global SCALE
    enable_dpi()

    args = list(sys.argv[1:] if argv is None else argv)
    if any(a in ("-h", "--help") for a in args):
        print("TagForge 桌面版\n"
              "用法: python -m tagforge.gui [目录] [--scale 1.25]\n\n"
              "  目录          启动时扫描的文件夹（默认 samples/DemoLibrary）\n"
              "  -s, --scale   强制缩放倍率（默认按系统 DPI 自动判定）")
        return 0
    start, scale_arg = None, None
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--scale", "-s") and i + 1 < len(args):
            scale_arg = float(args[i + 1])
            i += 2
        elif a.startswith("--scale="):
            scale_arg = float(a.split("=", 1)[1])
            i += 1
        elif start is None and not a.startswith("-"):
            start = a
            i += 1
        else:
            i += 1

    SCALE = scale_arg or float(os.environ.get("TAGFORGE_SCALE") or detect_scale())

    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.0)
    except tk.TclError:
        pass

    # 高 DPI 下窗口可能超出屏幕，逐档回退
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    while SCALE > 1.0 and (W * SCALE > sw - 40 or H * SCALE > sh - 90):
        SCALE = round(SCALE - 0.05, 2)
    SCALE = max(1.0, SCALE)

    root.geometry(f"{S(W)}x{S(H)}+40+40")
    TagForgeApp(root, start)
    root.mainloop()
    return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
