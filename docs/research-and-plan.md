# 音频标签管理器：调研结论、功能范围与实现方案

> 目标：写一个 Mp3tag 风格的工具，能查看/编辑单文件标签，能批量统一专辑与其它字段，
> 能从文件名或目录结构反推标签。
> 本文先给调研结论，再划功能边界，最后给可落地的实现方案（含已实现原型的验证结果）。

---

## 一、调研结论

### 1.1 调研对象

Mp3tag 本身**闭源、仅 Windows**，所以调研分两条线：它的功能全集（对标基准），
以及能在 GitHub 上读到源码的同类实现（对齐架构）。

| 项目 | 语言 / 技术栈 | 许可证 | 标签库 | UI 范式 | 与本需求的关系 |
|---|---|---|---|---|---|
| **Mp3tag** | 闭源，Windows | 免费闭源 | 自研 | 表格 + Action 组 | 功能对标基准 |
| **puddletag** | Python + PyQt6 | GPLv3 | **mutagen** | 电子表格内联编辑 | 架构最接近：Python + mutagen 已足够 |
| **Kid3** | C++ / Qt5-6、KDE | GPL-2.0 | **TagLib** | 单文件面板 + 批量对话框 | 证明 TagLib 路线；多标签版本共存处理成熟 |
| **MusicBrainz Picard** | Python + PyQt | GPL-2.0 | **mutagen** | 专辑簇 + 匹配候选 | 在线匹配/指纹识别的最佳参考 |
| **beets** | Python | MIT | **mutagen**（经 MediaFile） | CLI + SQLite 库 | 模板、导入流水线、插件机制参考 |
| **Music Tag Web** | Python + Docker + Vue | GPL-3.0 | **mutagen** + ffmpeg | 浏览器 | 证明"纯 mutagen + Web UI"可支撑真实曲库（约 6.1k star） |
| **TagLib** | C++ | LGPL / MPL | — | 库 | 标签读写库的工业标准 |

### 1.2 六个可复用的架构结论

**结论 1：所有成熟实现都在做同一件事——先做"字段抽象层"，再做业务。**
同一语义在四套标签体系里名字完全不同（下表）。puddletag 用 `puddlestuff/tags.py` 的
字段表把 ID3/Vorbis/APE/MP4 统一成一套 UI 字段；Kid3 用 `TagLibFile` 的多类封装做同一件事。
**如果不在最底层收敛差异，批量编辑、模板、导出每个功能都要写四份分支。** 这是本项目最重要的一条。

| 语义 | ID3v2 | Vorbis Comments | iTunes (MP4) | APEv2 |
|---|---|---|---|---|
| 标题 | `TIT2` | `TITLE` | `©nam` | `Title` |
| 艺术家 | `TPE1` | `ARTIST` | `©ART` | `Artist` |
| 专辑 | `TALB` | `ALBUM` | `©alb` | `Album` |
| **专辑艺术家** | `TPE2` | `ALBUMARTIST` | `aART` | `Album Artist` |
| 年份 | `TDRC`(2.4) / `TYER`(2.3) | `DATE` | `©day` | `Year` |
| 音轨号 / 总数 | `TRCK` = `n/m` | `TRACKNUMBER`+`TRACKTOTAL` | `trkn` = (n,m) | `Track` |
| 碟号 / 总数 | `TPOS` = `n/m` | `DISCNUMBER`+`DISCTOTAL` | `disk` = (n,m) | `Disc` |
| 流派 | `TCON` | `GENRE` | `©gen` | `Genre` |
| 封面 | `APIC`(type=3) | FLAC `PICTURE` 块 / Ogg `metadata_block_picture` | `covr` | `Cover Art (Front)` |

注意三处真实存在的"坑"，调研中都能看到成熟项目的应对：
- **音轨号形态不一致**：ID3/MP4 把编号和总数塞进一个字段（`n/m`、`(n,m)`），Vorbis 是两个键。
  写入时必须读旧值再合并，否则批量改 `tracktotal` 会把 `track` 冲掉。
- **Vorbis 的总数键不唯一**：`TRACKTOTAL` 与 `TOTALTRACKS` 并存，读要都试，写要统一。
- **封面的存储方式四种都不同**：ID3 是帧、FLAC 是独立 PICTURE 块、Ogg 是 base64 进了注释、MP4 是 atom 数组。

**结论 2：批量写入必须"增量 + 可回滚"，不能整体重写标签。**
Mp3tag 的批量之所以安全，是因为它只改你动过的字段。反过来，如果把文件标签整体删了重建，
用户精心保存的歌词、ReplayGain、`TXXX` 自定义帧会一起消失。工程上要规定 patch 语义：
**键不存在 = 不动，值为空 = 删除，有值 = 写入**。

**结论 3：写入失败是常态，不是异常。**
只读文件、被播放器占用、ID3v1 与 v2 冲突、文件头损坏都会发生。成熟工具的做法是
逐文件 try/except + 汇报成功/失败清单（beets 的 import 会记录 skip 日志，Mp3tag 会弹失败列表），
而不是中途抛错让前面的文件白改。因此"批量"本质是**尽力而为 + 事务快照**。

**结论 4：两种数据模型，选"文件即真相"。**
- Picard / beets：元数据先进入库（SQLite），再写回文件 —— 适合"整理整个曲库"。
- Mp3tag / puddletag：文件本身是唯一真相，不建库、改完即走 —— 适合"修标签"。

本需求（查看/编辑/批量统一/文件名反解）是典型的后者。建库反而引入同步问题：
用户在别的软件改了标签，库就脏了。所以选 **file-is-truth + 会话级 staging + 备份快照**。

**结论 5：UI 范式选电子表格内联编辑。**
puddletag 的立身之本就是"所有字段在一张表里直接改"，这比"选文件 → 右侧表单 → 保存"
快一个数量级，尤其批量修同一列字段时。Mp3tag 也是同一个思路。
所以主界面 = 可编辑表格 + 右侧详情（封面/技术信息），批量栏独立置顶。

**结论 6：模板引擎要做成双向的，且两边共用一套语法。**
Mp3tag 的 Converte 功能里，`标签←文件名` 和 `文件名←标签` 用的是同一套 `%字段%` 语法。
拆成两套实现必然漂移。做法是把模板编译成正则（反向）+ 格式化函数（正向），同源产出。

### 1.3 标签库选型：mutagen vs TagLib

| 维度 | mutagen（Python） | TagLib（C++） |
|---|---|---|
| 部署 | `pip install mutagen`，纯 Python 零依赖 | 需编译/预编译 wheel（`pytaglib`） |
| 格式覆盖 | ID3v1/2.2/2.3/2.4、FLAC、Ogg Vorbis/Opus/Speex、MP4、APE、WavPack、AIFF、DSF、ASF | 覆盖同样广，工业界使用最多 |
| 性能 | 单文件毫秒级，批量 1 万文件在秒级 | 读标签约为 id3lib 的 6 倍、libvorbisfile 的 3 倍 |
| 底层控制 | 可拿到帧级对象（`APIC`、`Picture`、`MP4Cover`） | 提供统一抽象，特殊帧需自行扩展 |
| 生态验证 | beets / Picard / puddletag / QuodLibet / Music Tag Web 全在用 | Kid3、大量播放器 |

**结论：选 mutagen。** 理由不是性能（TagLib 更快，但标签操作是 IO 密集型，优势不明显），
而是**零编译依赖带来的分发便利**，以及 Python 生态里同类项目的压倒性共识。
若将来需要处理十万级曲库的扫描性能，可把 `adapters.py` 换成 `pytaglib` 实现而不影响上层
——这正是把标签库关在适配器里的收益。

（另外两个库不选：`eyeD3` 只支持 MP3；`tinytag` 只读。）

---

## 二、功能范围划分

### P0 — 本次已实现（可用原型）

| 模块 | 能力 |
|---|---|
| 扫描 | 递归/单层扫描，8 类扩展名，1 个文件只开一次标签（字段 + 是否有封面一次拿到） |
| 单文件 | 表格内联编辑、右侧详情（13 字段 + 封面大图 + 时长/码率/采样率/ID3 版本） |
| 批量 | 统一专辑 / 专辑艺术家 / 年份 / 流派 / 封面；`覆盖` 与 `仅补空` 两种模式 |
| 文件名反解 | 双向模板引擎、目录结构匹配、补零、可选段、dry-run 预览后再写 |
| 重命名 | 标签 → 文件名，支持生成子目录，冲突检测 |
| 安全 | 写前快照、逐文件失败回滚、整批撤销 |
| 覆盖格式 | MP3/WAV/AIFF/DSF、FLAC/OGG/OPUS、M4A/MP4、APE/WV/MPC |

### P1 — 下一轮（按收益排序）

1. **标签版本管理**：ID3v2.3 ↔ v2.4 转换、清理冗余 ID3v1、APEv2 与 ID3 并存时的取舍。
2. **批量文本操作**：正则替换、大小写规范化、去首尾空格、繁简转换（Mp3tag 的 Action 雏形）。
3. **可组合动作组**：把若干操作保存成一条可复用的 Action（批量流程自动化的关键）。
4. **多值语义**：`artist` 多值（ID3 空字符分隔 / Vorbis 重复键 / MP4 数组）的保真读写。
5. **封面批处理**：批量导出封面到文件、按尺寸/格式规范化、多图（前/后/艺人）管理。
6. **导出**：CSV / HTML 报表；导入 CSV 回写。
7. **并发写入**：线程池 + 取消；目前串行（磁盘顺序写反而更快，且避免同盘竞争）。
8. **命令行模式**：`tagforge from-names ./album "%track% - %title%"`，便于脚本化。

### P2 — 中长期

在线元数据源（MusicBrainz / Discogs / AcoustID 指纹）、CUE 整轨分割、歌词、ReplayGain、
重复文件检测、库数据库（beets 式增量索引）、NAS/WebDAV 远程目录。

### 明确不做

音频转码（交给 ffmpeg）、音频编辑、播放器、音乐下载、云同步。

---

## 三、整体实现方案

### 3.1 分层

```
┌──────────────────────────────────────────────┐
│  gui.py   表格内联编辑 · 详情面板 · 批量栏 · 预览弹窗 │
└───────────────────┬──────────────────────────┘
                    │  JSON over HTTP（本地，单用户）
┌───────────────────▼──────────────────────────┐
│  (Tkinter 单窗口，事件与绘制都在 gui.py 内)        │
├──────────────────────────────────────────────┤
│  library.py  扫描 · 批量写入 · 快照 · 回滚 · 撤销  │
│  naming.py   双向模板引擎（正向渲染 / 反向正则）    │
├──────────────────────────────────────────────┤
│  fields.py   13 个 canonical 字段 + 四套键名映射   │
├──────────────────────────────────────────────┤
│  adapters.py id3 │ vorbis │ mp4 │ apev2        │
└───────────────────┬──────────────────────────┘
                    │
                 mutagen
```

依赖方向严格单向：下层不认识上层。`adapters.py` 不知道有 HTTP，`library.py` 不知道有 UI。

### 3.2 数据模型

canonical 字段 13 个：
`title, artist, album, albumartist, year, track, tracktotal, disc, disctotal, genre, composer, comment, cover`

设计取舍：
- `year` 存**字符串**而非整数。真实世界里 `2021-05-01`、`1999`、`1998-03` 都存在，
  强转整数会丢精度（这也是 ID3v2.4 用 `TDRC` 时间戳取代 `TYER` 的原因）。
- `track` 与 `tracktotal` 拆开，由适配器负责合成/拆分 `n/m` 形态。
- 封面不进字段字典，单独用 `Cover(data, mime)`，避免列表接口被二进制拖慢。

### 3.3 写入算法

核心是"增量 patch + 快照 + 逐文件回滚"：

```python
def apply_changes(changes, mode="overwrite", backup=True):
    changes = normalize(changes)              # 过滤不存在路径、清洗未知键
    backup_id = snapshot(all_paths)           # 字段 + 封面字节 -> ~/.tagforge/backups/*.json
    for change in changes:
        try:
            write_tags(change.path, change.patch, mode)
        except Exception as exc:
            record_failure(change.path, exc)
            restore_one(change.path, backup_id)   # 只回滚这一个文件
    return {applied, failed, backup_id}
```

`mode` 的两种语义：
- `overwrite`：`None` 删除字段；有值覆盖。
- `fill`：目标字段已有值就跳过，只补空 —— 这是"给一堆散装文件补专辑信息"的正确默认。

`write_tags` 内部再按引擎分派。以 ID3 为例，几个必须处理的细节：

```python
v2_version = 3 if (已有标签且原版本==3) else 4   # 不擅自把 v2.3 曲库升级成 v2.4
TRCK 写入 = 读旧值 -> 改 num 或 total -> 合成 "n/m"  # 避免改总数时冲掉编号
APIC 写入 = delall("APIC") + APIC(type=3, mime, data)  # 保证"唯一一张前封面"
```

### 3.4 模板引擎

同一份模板两用：

```
模板  %albumartist%/%album%/%track:2% - %title%
  ├─ 正向 render(tokens, values) -> "Daft Punk/Random Access Memories/01 - Give Life..."
  └─ 反向 compile_pattern(tokens) -> 正则 ^(?P<f0>.+?)/(?P<f1>.+?)/(?P<f2>.+?) - (?P<f3>.+?)$
```

三个关键点：
1. **必须用 `$` 锚定**。否则最后一个非贪婪组只吃一个字符（`%title%` 只能匹配 "G"）。
2. **可选段 `[...]`**：段内占位符全为空时整段省略（渲染 `(?:...)?`），解析时整段可缺省。
3. **字段名白名单校验**。模板拼错时立刻报 `TemplateError`，而不是静默产出空标签。

反向解析的结果再走一遍"只保留变化字段"的差分，作为 patch 提交——预览界面显示的
就是这份差分，所见即所写。

### 3.5 并发与性能

- **扫描**：`os.walk` 跳过隐藏目录；每个文件只 open 一次同时取字段和封面存在性；
  上限 2 万文件（超出截断提示）。缩略图按 `(path, mtime)` 内存缓存，400 条 LRU 清理。
- **写入**：串行。同盘并行写会互相拖慢，且多线程同时改同一文件会产生竞争；
  收益远小于复杂度。批量规模到万级时再引入线程池 + 进度推送。
- **列表不加载图片**：只记 `has_cover` 布尔，封面在选中文件时才解码。

### 3.6 安全模型

1. 写前快照到 `~/.tagforge/backups/<时间戳>-<标签>.json`（含封面 base64）。
2. 单文件失败即时回滚，不影响其余文件。
3. 整批可撤销（`library.undo()` 用快照覆盖回写）。
4. 危险操作先 dry-run 预览（文件名反解、重命名都是"预览 → 确认 → 执行"两步）。
5. 备份目录独立于音乐目录，避免被扫描器当成曲库文件。

### 3.7 技术选型与替代路线

| 决策 | 选择 | 理由 | 什么时候该换 |
|---|---|---|---|
| 语言 | Python 3.10+ | 与标签库生态一致，改起来快 | 需要处理十万级文件或原生安装包 → 换 Rust/Tauri 或 C++/Qt |
| 标签库 | mutagen | 零依赖、格式全、生态共识 | 极限性能 → `pytaglib`（只换 adapters） |
| 界面层 | Tkinter（Canvas 自绘） | 标准库自带，双击即用，像素级可控 | 需要原生控件/复杂动画 → Qt |
| 前端 | 原生 HTML/JS，无构建 | 改一行刷新即可，无工具链负担 | 组件复杂度上升 → Vite + Vue/React |
| 存储 | 无数据库 | file-is-truth，不引入同步问题 | 需要曲库索引/搜索 → SQLite（beets 式） |

---

## 四、验证结果

原型已通过 40 项端到端检查（`python tests/test_flow.py`），覆盖真实文件而非 mock：

- 8 个样例文件、4 种容器（MP3 / FLAC / M4A / OGG / WAV）
- 从目录结构反解：`Daft Punk/Random Access Memories/01 - Give Life Back to Music`
  → `albumartist=Daft Punk, album=Random Access Memories, track=1, title=...`
- 跨格式回读：同一字段在 ID3 / Vorbis / MP4 上写读一致
- 批量统一专辑 + 年份：8/8 成功
- `仅补空` 模式：已有专辑未被覆盖、空字段被补上
- 封面写入与读取：4 种引擎全部可读写（含 APEv2）
- 撤销：整批恢复，封面按快照移除
- 模板边界：可选段、补零、未知字段报错

界面用无头浏览器做了实际渲染校验（表格对齐、封面缩略图、空态显示）。

## 五、已知限制

1. **多值字段有损**：读多值时用 `; ` 拼接、写回为单值。ID3 的 `\x00` 分隔、Vorbis 的重复键
   暂不保真（P1 第 4 项）。
2. **ID3v2.2 只读**：极老文件可读不可写（mutagen 限制）。
3. **WAV 的 ID3 兼容性**：写的是 `id3 ` chunk，部分老播放器不识别。
4. **`fill` 模式判断"空"的标准**：字段不存在即视为空，不做"空白字符串也算空"之外的语义推断。
5. **无索引**：每次扫描全量读标签，万级以上目录首次加载会慢（P2 引入 SQLite 索引）。
