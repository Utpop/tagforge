"""文件名 / 目录结构 <-> 标签 的双向模板引擎。

模板语法（对齐 Mp3tag 的心智模型，但更严格）：

    %title%            占位符
    %track:2%          数值补零到 2 位
    %album%/%track% %title%   目录分隔符按路径逐级匹配
    [%disc%-]          可选段：段内所有占位符都为空时整段省略

两个方向共用同一份模板：
    正向 render()  标签 -> 文件名（重命名）
    反向 parse()   文件名 -> 标签（本项目的核心需求之一）

反向解析时把模板"正则化"：字面量转义，占位符变成非贪婪捕获组，
必须用 `$` 锚定结尾，否则最后一个字段只能吃到最短匹配。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .fields import FIELD_KEYS, NUMERIC_FIELDS, normalize_number

# 仅在"渲染方向"可用的虚拟字段
VIRTUAL_FIELDS = {
    "_filename": "文件名（不含扩展名）",
    "_ext": "扩展名（不含点）",
    "_dirname": "所在目录名",
    "_filepath": "完整绝对路径",
}

ALL_PLACEHOLDERS = set(FIELD_KEYS) | set(VIRTUAL_FIELDS)

PLACEHOLDER_RE = re.compile(r"%(_?[a-z]+)(?::(\d+))?%")


class TemplateError(ValueError):
    pass


@dataclass
class Token:
    kind: str  # "lit" | "field"
    text: str = ""
    field: str = ""
    pad: int = 0
    optional: bool = False


@dataclass
class Template:
    raw: str
    tokens: list[Token] = field(default_factory=list)

    @property
    def fields(self) -> list[str]:
        return [t.field for t in self.tokens if t.kind == "field"]


def parse_template(raw: str) -> Template:
    raw = (raw or "").strip()
    if not raw:
        raise TemplateError("模板不能为空")

    tokens: list[Token] = []
    buffer: list[str] = []
    depth = 0
    i = 0
    n = len(raw)

    def flush() -> None:
        if buffer:
            tokens.append(Token("lit", text="".join(buffer), optional=depth > 0))
            buffer.clear()

    while i < n:
        ch = raw[i]
        if ch == "[":
            flush()
            depth += 1
            i += 1
        elif ch == "]":
            flush()
            depth = max(0, depth - 1)
            i += 1
        elif ch == "%":
            match = PLACEHOLDER_RE.match(raw, i)
            if not match:
                raise TemplateError(f"位置 {i} 处的占位符未闭合或名称非法")
            name = match.group(1)
            if name not in ALL_PLACEHOLDERS:
                raise TemplateError(f"未知字段 %{name}%")
            flush()
            tokens.append(
                Token(
                    "field",
                    field=name,
                    pad=int(match.group(2) or 0),
                    optional=depth > 0,
                )
            )
            i = match.end()
        else:
            buffer.append(ch)
            i += 1

    flush()
    if depth != 0:
        raise TemplateError("方括号 [ ] 未配对")
    if not any(t.kind == "field" for t in tokens):
        raise TemplateError("模板中至少需要一个 %字段% 占位符")
    return Template(raw, tokens)


def render(template: Template, values: dict) -> str:
    out: list[str] = []
    i = 0
    tokens = template.tokens
    while i < len(tokens):
        tok = tokens[i]
        if tok.optional:
            j = i
            segment: list[Token] = []
            while j < len(tokens) and tokens[j].optional:
                segment.append(tokens[j])
                j += 1
            if any(_field_text(t, values) for t in segment if t.kind == "field"):
                out.append("".join(_render_token(t, values) for t in segment))
            i = j
        else:
            out.append(_render_token(tok, values))
            i += 1
    return "".join(out)


def _field_text(tok: Token, values: dict) -> str:
    return str(values.get(tok.field, "") or "")


def _render_token(tok: Token, values: dict) -> str:
    if tok.kind == "lit":
        return tok.text
    text = _field_text(tok, values)
    if tok.pad and text:
        text = text.zfill(tok.pad)
    return text


def compile_pattern(template: Template) -> tuple[re.Pattern, list[str]]:
    """把模板编译成正则，返回 (pattern, group_index -> field)。"""
    pattern_parts: list[str] = []
    group_fields: list[str] = []
    counter = [0]

    def token_pattern(tok: Token) -> str:
        if tok.kind == "lit":
            return re.escape(tok.text)
        group_fields.append(tok.field)
        name = f"f{len(group_fields) - 1}"
        counter[0] += 1
        return f"(?P<{name}>.+?)"

    i = 0
    tokens = template.tokens
    while i < len(tokens):
        if tokens[i].optional:
            j = i
            body: list[str] = []
            while j < len(tokens) and tokens[j].optional:
                body.append(token_pattern(tokens[j]))
                j += 1
            pattern_parts.append("(?:" + "".join(body) + ")?")
            i = j
        else:
            pattern_parts.append(token_pattern(tokens[i]))
            i += 1

    # $ 锚定：没有它最后一个非贪婪组只会匹配一个字符
    pattern = re.compile("^" + "".join(pattern_parts) + "$", re.IGNORECASE)
    return pattern, group_fields


def parse(template: Template, text: str) -> dict:
    """从字符串反解出标签字段。未匹配到的字段不出现在结果里。"""
    pattern, group_fields = compile_pattern(template)
    match = pattern.match(text)
    if not match:
        return {}
    out: dict = {}
    for index, field_name in enumerate(group_fields):
        value = match.group(f"f{index}")
        if value is None:
            continue
        value = value.strip()
        if not value:
            continue
        if field_name in NUMERIC_FIELDS:
            value = normalize_number(value)
        out[field_name] = value
    return out


# --------------------------------------------------------------------------
# 目标字符串构造
# --------------------------------------------------------------------------


def relative_stem(path: str, root: str | None) -> str:
    """单文件模式：只取文件名主体。"""
    return Path(path).stem


def relative_path_text(path: str, root: str | None) -> str:
    """目录模式：相对 root 的路径文本（正斜杠、去扩展名）。"""
    p = Path(path)
    if root:
        try:
            rel = p.relative_to(Path(root))
        except ValueError:
            rel = Path(p.name)
    else:
        rel = Path(p.name)
    return rel.with_suffix("").as_posix()


def render_values(path: str) -> dict:
    p = Path(path)
    return {
        "_filename": p.stem,
        "_ext": p.suffix.lstrip("."),
        "_dirname": p.parent.name,
        "_filepath": str(p),
    }


def guess_template(paths: list[str], root: str | None, use_path: bool = False) -> str:
    """给一组文件猜一个合适的默认模板（基于命名规律）。"""
    if not paths:
        return "%artist% - %title%"
    stem = Path(paths[0]).stem
    if re.match(r"^\d{2}\s*[-._ ]\s*\S", stem):
        leaf = "%track:2% - %title%"
    elif re.match(r"^\d{1,3}\s*[-._ ]\s*\S", stem):
        leaf = "%track% - %title%"
    elif re.match(r"^\d{1,3}[._ ]\s*\S", stem):
        leaf = "%track% %title%"
    elif " - " in stem:
        leaf = "%artist% - %title%"
    else:
        leaf = "%title%"
    if use_path:
        rel = relative_path_text(paths[0], root)
        if rel.count("/") >= 2:
            return "%albumartist%/%album%/" + leaf
    return leaf
