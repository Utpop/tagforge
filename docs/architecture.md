# TagForge 架构（桌面版）

> 当前实现是 **Tkinter 单窗口桌面应用**：`python -m tagforge.gui`。
> 本文说明模块职责、依赖方向、调用链与数据流，供改代码和改界面时对齐。
> 调研背景见 `research-and-plan.md`，界面规格见 `gui-spec.md`。

代码规模：Python 约 3050 行（5 个模块）+ 演示库生成脚本。

---

## 1. 代码地图

| 文件 | 行数 | 层 | 职责 | 对外符号 | 依赖 |
|---|---|---|---|---|---|
| `tagforge/gui.py` | ~1585 | L4 表现层 | 窗口、绘制原语、事件、三个浮层、字体与 DPI | `TagForgeApp`, `Painter`, `FromNamesDialog`, `RenameDialog`, `ConfirmUndo`, `main()` | L3, L2, L1 |
| `tagforge/library.py` | 412 | L3 用例层 | 扫描、批量写入事务、快照/回滚/撤销、模板预览、重命名 | `scan`, `detail`, `apply_changes`, `undo`, `preview_from_names`, `plan_rename` | L2, L1, L0 |
| `tagforge/naming.py` | 258 | L2 引擎层 | 模板解析、正向渲染、反向编译正则、虚拟字段 | `parse_template`, `render`, `parse`, `compile_pattern`, `guess_template` | L0 |
| `tagforge/fields.py` | 98 | L0 契约层 | 13 个 canonical 字段、四套键名映射、patch 清洗 | `FIELDS`, `FIELD_BY_KEY`, `clean_patch` | 标准库 |
| `tagforge/adapters.py` | 707 | L1 适配层 | 四类标签引擎、封面读写、技术信息探测、扩展名分派 | `read_record`, `read_tags`, `read_cover`, `write_tags`, `probe`, `Cover` | L0 + mutagen |
| `tagforge/__init__.py` | 11 | — | 版本与分层说明 | `__version__` | — |
| `tests/make_demo.py` | — | 构建期 | 生成 23 曲演示库（需要 ffmpeg + Pillow） | — | — |
| `tests/test_flow.py` | — | 测试 | 后端端到端 40 项 | — | 全部 |
| `tests/test_gui.py` | — | 测试 | 桌面端端到端 25 项 | — | `gui` |

**依赖铁律：只有上层 import 下层，反向零引用。**
`fields.py` 不 import 本项目任何模块（纯契约）；`adapters.py` 不知道有界面；
`library.py` 不知道有窗口；`gui.py` 不碰标签帧与文件格式。

## 2. 分层

```
┌────────────────────────────────────────────────────────────┐
│ L4 gui.py        Canvas 自绘窗口 · 事件分发 · 三个浮层        │
│                  设计单位(1440×900) → S() → 设备像素          │
├────────────────────────────────────────────────────────────┤
│ L3 library.py    扫描 · 批量事务 · 快照回滚 · 撤销 · 重命名    │
│ L2 naming.py     模板 ⇄ 正则（双向同源）                     │
├────────────────────────────────────────────────────────────┤
│ L0 fields.py     13 个统一字段 + 4 张键名映射（横切 L1–L3）    │
├────────────────────────────────────────────────────────────┤
│ L1 adapters.py   id3 │ vorbis │ mp4 │ apev2                 │
└────────────────────────────────────────────────────────────┘
                          ↓
                    mutagen 1.48
```

### L4 表现层（gui.py）
- 整窗画在一块 `tk.Canvas` 上（不用 ttk）：圆角、色值、29px 行高、14% 透明底都需要精确控制。
- 只有需要输入的地方叠加真正的 `tk.Entry`（路径、搜索、批量字段、单元格编辑），
  圆心角靠底下的圆角块伪装。
- `Painter` 封装全部绘制原语与命中区（`hits`），**所有入参都是设计单位**，
  内部乘 `SCALE` 换成设备像素 —— 因此 DPI 变化时上层代码零改动。
- 持有全部界面状态（见 §5.5），不做业务判断：格式、模板、事务一律交给下层。

### L3 用例层（library.py）
- 一次用户动作 = 一个函数，**事务边界只在这里**：`snapshot → 逐文件写 → 失败回滚 → 返回清单`。
- 负责把 L1 的字段 + 文件系统元数据拼成 `record`。
- 不做格式细节，不做模板语法。

### L2 引擎层（naming.py）
纯函数、无 IO：模板字符串 → Token 流 → 格式化函数（正向）/ 正则（反向）。

### L0 契约层（fields.py）
定义"什么是一个标签字段"以及"脏输入怎么洗"（`clean_patch`）。横切层，自身零依赖。

### L1 适配层（adapters.py）
四套引擎 `_id3_*` / `_vorbis_*` / `_mp4_*` / `_apev2_*`，三张注册表
（`_READERS` / `_COVER_READERS` / `_WRITERS`）按扩展名分派。
屏蔽的差异：`n/m` 形态的音轨号、Vorbis 别名键、封面四种存法、ID3 版本保留。

## 3. 功能清单（18 项）

| # | 功能 | GUI 入口 | 处理函数 | 后端链路 |
|---|---|---|---|---|
| F1 | 目录扫描（递归开关、2 万上限） | 目录框 + 扫描 | `choose_dir` / `scan` | `library.scan` → `adapters.read_record` × N |
| F2 | 文件列表（路径分色、异常行标红） | 表格 | `draw_table` / `draw_row` | — |
| F3 | **单元格单击即编辑**（Enter/Tab/Esc） | `td` 命中区 | `begin_edit` / `_commit_edit` | 仅进 staged，不写盘 |
| F4 | 排序（点表头，数值列按数值） | 表头 | `sort_by` / `visible` | — |
| F5 | 过滤（路径 + 全部字段，含暂存值） | 搜索框 | `_on_search` / `visible` | — |
| F6 | 选择（勾选 / 全选可见 / 当前行） | 复选框 | `toggle_check` / `toggle_all` | — |
| F7 | 详情面板（12 字段 + 封面 + 技术信息） | 右侧面板 | `draw_panel` / `probe` | `adapters.read_record`、`probe` |
| F8 | 封面缩略图列 | 表格末列 | `draw_row` / `cover_color` | 仅 `has_cover` 布尔 |
| F9 | 批量写字段（专辑/专辑艺术家/年份/流派 + 覆盖/补空） | 批量条 | `apply_batch` | `library.apply_changes` |
| F10 | 批量写封面 | 批量条选图 | `pick_cover` + `apply_batch` | 同上（patch 含 cover） |
| F11 | 批量移除封面 | 批量条 | `remove_cover` | 同上（`{op:remove}`） |
| F12 | 从文件名/目录结构生成（先预览） | 「生成」 | `FromNamesDialog` | `preview_from_names` → `naming.parse` → 可选写入 |
| F13 | 按标签重命名（冲突检测 + 预览） | 「改名」 | `RenameDialog` | `plan_rename` / `apply_rename` |
| F14 | 保存暂存（Ctrl+S，徽标显脏字段数） | 「保存修改」 | `save` | `apply_changes(mode=overwrite)` |
| F15 | 撤销上一步（整批回退） | 「撤销」 | `undo_last` / `ConfirmUndo` | `list_backups` / `undo` |
| F16 | 计数与空态 | 顶栏、批量条 | `dirty_count` / 各 `draw_*` | — |
| F17 | 字体与 DPI 适配 | 启动 | `resolve_ui_font` / `detect_scale` | `TkDefaultFont`、`GetDpiForSystem` |
| F18 | 安全底账（写前快照、失败回滚） | 隐式（F15 可见） | — | `snapshot` / `_restore_one` |

## 4. 调用关系（4 条主链）

### A. 扫描与展示
```
「扫描」→ choose_dir() → library.scan(root, recursive)
    → os.walk 收集扩展名命中 ENGINE_BY_EXT 的文件
    → 逐文件 _record(): stat() + adapters.read_record()
         → engine_for() 选引擎 → _xxx_read() 解析成 canonical 字段
         → 同一次打开里取 has_cover，不重复解析
→ 返回 {root, count, truncated, records[]}
→ TagForgeApp.scan() 重置 staged/checks/scroll/封面缓存，active 指向首行
→ redraw(): draw_zone_a → draw_zone_b → draw_table → draw_panel
→ 选中某行时 draw_panel() 才去读封面与 probe（懒加载）
```

### B. 编辑与保存
```
单元格失焦 / 输入框 change
→ _commit_edit(): 比较新值与 record.fields[field]
    ├─ 相同 → 从 staged 删键（无其他键则整条删除）
    └─ 不同 → staged[path][field] = 新值，单元格转琥珀色
→ dirty_count() 更新徽标与「保存修改」可用性
→ 「保存修改」/Ctrl+S → save()
→ library.apply_changes(changes, mode="overwrite")
    → normalize_changes(): 丢不存在路径、clean_patch() 清洗、封面解码
    → snapshot(): 读目标标签 + 封面 → ~/.tagforge/backups/<id>.json
    → 逐文件 adapters.write_tags()，失败即 _restore_one() 回滚该文件
→ 返回 {applied, failed[], backup}
→ 前端重扫、清空 staged、失败弹清单
```

### C. 从文件名生成（三段式）
```
「生成」→ 目标集 = 勾选集 ∪（无勾选时）当前可见集
→ FromNamesDialog：guess_template() 预填模板（含目录层级时带上 %albumartist%/%album%）
→ 「预览」→ preview_from_names(dry_run)
    → naming.parse_template() 编译一次，逐文件复用
    → 目标文本 = relative_path_text()（路径模式）或 relative_stem()
    → naming.parse() 正则反解 → 与现有字段做差 → changes{}
→ 预览表：解析值琥珀、缺失红「—」、无变化灰、写入按钮在预览前禁用
→ 「写入标签」→ apply_changes(overwrite)
```

### D. 重命名 / 撤销
```
重命名：plan_rename(keep_dir=True) → 逐文件 render(template, fields+虚拟字段)
        → 保留原目录只换文件名；模板含 "/" 且取消勾选时生成子目录
        → 冲突检测（已存在 / 撞名 / 未变化）→ 预览 → apply_rename()
撤销：list_backups(1) → ConfirmUndo 确认 → undo(id)
      → 用快照覆盖回写全部 canonical 字段（缺失写 None 即删除）+ 还原封面
```

## 5. 数据模型

### 5.1 `record`（列表的唯一数据源）
```python
{
  "path": "C:/Music/Daft Punk/RAM/01 - ....mp3",
  "name": "01 - Give Life Back to Music.mp3",
  "rel":  "Daft Punk/RAM/01 - ....mp3",
  "dir":  "Daft Punk/RAM",
  "ext": ".mp3", "size": 812345, "mtime": 1761552000,
  "engine": "id3",
  "fields": {"title": "...", "album": "...", "track": "1"},   # 只含实际存在的字段
  "has_cover": True,
  "error": None,
}
```

### 5.2 `patch`（写请求的最小单位）
```python
{"album": "New", "year": "1999", "comment": None,
 "cover": {"op": "set", "mime": "image/png", "data_b64": "..."}}
```
语义三条：**键不存在 = 不动；值为 None = 删除；有值 = 写入（或补空）**。
`cover` 另有 `{"op": "remove"}`。

### 5.3 `Cover`
`{data: bytes, mime: str}`。写时按引擎翻译成 APIC / FLAC Picture /
`metadata_block_picture`(base64) / MP4Cover / APEv2 键。

### 5.4 备份快照（undo 日志）
```python
{"id": "20261027-181645-856", "created": 1761552000.0, "label": "批量设置专辑",
 "files": [{"path": "...", "fields": {...}, "cover": {"mime": "image/png", "b64": "..."}}]}
```

### 5.5 界面状态（`TagForgeApp` 的属性）
```python
records: list[dict]          # 磁盘现状
staged:  dict[path, dict]    # 未提交意图
checks:  set[path]           # 勾选集合（批量作用域）
active:  str | None          # 当前文件（详情面板主体）
sort_key / sort_dir / query  # 视图变换
batch_mode / batch_cover     # 批量条参数
scroll                       # 表格滚动起点
```
`staged` 是**唯一可变层**：`value(rec, field)` 优先取它，保存成功后合并回 `record.fields` 再清空。

## 6. 状态机

```
        编辑并失焦              点保存 / Ctrl+S          写盘成功
record ────────────→ staged ────────────────→ 写入中 ───────────→ record'
   ↑                    │                                        │
   └──── 改回原值 / Esc ──┘                              staged 清空
```
- 单元格"脏"= 该字段在 staged 中存在且值不同。
- 保存失败：staged 保留（不丢用户输入），只弹失败清单。
- 撤销作用于磁盘历史，与 staged 无关；有未保存修改时确认框会说明这一点。

## 7. 两条数据流约束

1. **单向读**：磁盘 → 适配器 → canonical 字段 → 视图。视图永不改 `record.fields`。
2. **单向写**：意图 → staged → patch → 快照 → 适配器 → 磁盘 → 回填 record。

所有写操作收口在 `apply_changes`，所以只需在那里做一次快照，全站就有撤销能力。

## 8. 区域 ↔ 状态映射

| 区域 | 依赖状态 | 派生规则 |
|---|---|---|
| ① 命令区 | records, staged, 最近备份 | 徽标 = 所有 staged 字段总数；为 0 时保存禁用 |
| ② 批量条 | checks, active, batch_* | `已选 N` 取 checks；无勾选时显示「当前文件 1 个」 |
| ③ 主表格 | records, staged, checks, active, sort, query, scroll | 行底色：当前行主色 14%、异常行危险色 8% |
| ④ 详情面板 | active + probe + 封面 | 字段值优先取 staged |
| ⑤ 浮层 | 目标集 + 模板 + dry-run 结果 | 预览前写入按钮禁用 |

## 9. 扩展点

| 想加的能力 | 落点 | 是否动界面 |
|---|---|---|
| 新音频格式 | `adapters.ENGINE_BY_EXT` + 键名映射 | 否 |
| 新标签字段（如 bpm、歌词） | `fields.FIELDS` + 4 张映射表 + `DETAIL_FIELDS` | 加一组输入框 |
| 正则批量替换 | `library` 新增用例 + `gui` 一个浮层 | 加一个入口 |
| ID3 版本转换 | `adapters` 新增函数 | 详情面板加一行操作 |
| 进度 / 取消 | `apply_changes` 加回调 + 状态栏 | 顶栏加进度条 |

## 10. 已知限制

- 多值字段只读不保真（`; ` 拼接），暂不能做多艺术家分离编辑。
- 无索引，万级目录首次扫描慢（线性读标签）。
- 写入串行、无进度回调，大批量时只能显示忙碌态。
- 裁剪与缩略图依赖 Tk 的整数倍 `zoom/subsample`，非整数缩放用有理近似（如 1.25 = 5/4）。
- 单用户单窗口，无多标签页/多实例协调。
