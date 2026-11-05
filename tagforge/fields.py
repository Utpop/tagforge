"""统一字段模型（canonical schema）。

设计要点：**格式差异只允许出现在这一层**。
上层（UI、批量逻辑、模板引擎）只认识 canonical key，
适配器负责翻译成 ID3 帧 / Vorbis 注释键 / MP4 atom / APEv2 键。

为什么需要这一层：同一语义在四种标签体系里的名字完全不同，
例如"专辑艺术家"在 ID3 是 TPE2、Vorbis 是 ALBUMARTIST、
MP4 是 aART、APEv2 是 "Album Artist"。若不做抽象，
每个功能（批量编辑、模板、导出）都要写四份分支。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class FieldDef:
    key: str          # canonical key
    label: str        # 中文标签
    kind: str = "text"  # text | number | image | longtext
    hint: str = ""


FIELDS: tuple[FieldDef, ...] = (
    FieldDef("title", "标题"),
    FieldDef("artist", "艺术家"),
    FieldDef("album", "专辑"),
    FieldDef("albumartist", "专辑艺术家"),
    FieldDef("year", "年份"),
    FieldDef("track", "音轨号", "number"),
    FieldDef("tracktotal", "音轨总数", "number"),
    FieldDef("disc", "碟号", "number"),
    FieldDef("disctotal", "碟号总数", "number"),
    FieldDef("genre", "流派"),
    FieldDef("composer", "作曲"),
    FieldDef("comment", "备注", "longtext"),
    FieldDef("cover", "封面", "image"),
)

FIELD_KEYS: tuple[str, ...] = tuple(f.key for f in FIELDS)
FIELD_BY_KEY: dict[str, FieldDef] = {f.key: f for f in FIELDS}

# 允许一次写入多个值的字段（ID3 用 null 分隔 / Vorbis 用重复键 / MP4 用列表）
MULTI_VALUE_FIELDS: frozenset[str] = frozenset({"artist", "genre", "composer"})

# 数值型字段：写入前做规范化
NUMERIC_FIELDS: frozenset[str] = frozenset(
    {"track", "tracktotal", "disc", "disctotal"}
)

# 多值在 UI 中的分隔符（显示与输入都用它）
VALUE_SEPARATOR = "; "


def empty_record() -> dict:
    """一份空标签记录（cover 单独处理，不放在这里）。"""
    return {k: "" for k in FIELD_KEYS if k != "cover"}


def split_values(raw) -> list[str]:
    """把 UI 输入或快照里的原始多值拆成列表。

    同时接受字符串（`"A; B"`）与列表（`["A", "B"]`）。
    后者用于撤销：快照记录的是原始帧的 text 列表，不能再走字符串拼接，
    否则一个内部含分号的单值会被错误地拆成两个值。
    """
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(v).strip() for v in raw if str(v).strip()]
    parts = [p.strip() for p in str(raw).split(";")]
    return [p for p in parts if p]


def normalize_year(raw: str) -> str:
    """把 '2013'、'2013-05-17'、'2013/05' 统一成 '2013'。

    年份是 4 位年，完整日期属于另一个字段；不归一化会让"按年份排序"
    把所有带日期的行当成 0（ID3v2.4 的 TDRC 常带完整日期）。
    无法识别为 4 位年的输入原样返回。
    """
    raw = (raw or "").strip()
    match = re.match(r"^(\d{4})(?:[-/.]\d{1,2}){0,2}$", raw)
    return match.group(1) if match else raw


def normalize_number(raw: str) -> str:
    """'03' -> '3'，' 12 ' -> '12'，非数字原样返回。"""
    raw = (raw or "").strip()
    if raw.isdigit():
        return str(int(raw))
    return raw


def clean_patch(patch: dict) -> dict:
    """过滤掉未知键，规范化数值与空白。

    约定：值为 None 表示"删除该字段"，空字符串同样视为删除。
    cover 例外：None 与空值都表示"不改动"，删除必须显式给 "remove"，
    因为封面是二进制，误把"没选图"当成"删图"代价太大。
    """
    out: dict = {}
    for key, value in patch.items():
        if key not in FIELD_BY_KEY:
            continue
        if key == "cover":
            if value is not None and value != "":
                out[key] = value
            continue
        if value is None:
            out[key] = None
            continue
        if isinstance(value, (list, tuple)):
            values = split_values(value)
            text = VALUE_SEPARATOR.join(values) if values else ""
        else:
            text = str(value).strip()
        if key in NUMERIC_FIELDS:
            text = normalize_number(text)
        elif key == "year":
            text = normalize_year(text)
        out[key] = text or None
    return out
