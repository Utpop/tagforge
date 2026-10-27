"""扫描、批量写入、事务快照与回滚。

安全模型（这是整个工具最容易被低估的部分）：

1. **增量写入**：patch 里没有的字段绝不触碰。批量改专辑不会顺手抹掉歌词、ReplayGain、
   自定义 TXXX 帧。
2. **先快照后写**：每次写盘前把涉及的标签（含封面字节）序列化到备份文件。
3. **单文件失败可回滚**：某个文件写坏（只读、被占用、非标准标签）时，立即用快照还原，
   其余文件继续，最后如实报告成功/失败清单。
4. **可撤销**：备份文件本身就是 undo 日志，可整批回退。
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

from . import adapters, naming
from .adapters import Cover, is_audio
from .fields import FIELD_KEYS, clean_patch

BACKUP_DIR = Path.home() / ".tagforge" / "backups"

MAX_SCAN = 20000


# --------------------------------------------------------------------------
# 扫描
# --------------------------------------------------------------------------


def scan(root: str, recursive: bool = True, limit: int = MAX_SCAN) -> dict:
    base = Path(root).resolve()
    if not base.is_dir():
        raise NotADirectoryError(str(base))

    files: list[Path] = []
    if recursive:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(
                d for d in dirnames if not d.startswith(".") and d != "System Volume Information"
            )
            for name in sorted(filenames):
                if is_audio(name):
                    files.append(Path(dirpath) / name)
            if len(files) >= limit:
                break
    else:
        for entry in sorted(base.iterdir()):
            if entry.is_file() and is_audio(entry.name):
                files.append(entry)

    truncated = len(files) > limit
    files = files[:limit]

    records = [_record(path, base) for path in files]
    return {
        "root": str(base),
        "count": len(records),
        "truncated": truncated,
        "records": records,
    }


def _record(path: Path, base: Path) -> dict:
    stat = path.stat()
    record = {
        "path": str(path).replace("\\", "/"),
        "name": path.name,
        "rel": path.relative_to(base).as_posix(),
        "dir": path.parent.relative_to(base).as_posix() if path.parent != base else "",
        "ext": path.suffix.lower(),
        "size": stat.st_size,
        "mtime": int(stat.st_mtime),
        "engine": adapters.engine_for(path),
        "fields": {},
        "has_cover": False,
        "error": None,
    }
    try:
        fields, has_cover = adapters.read_record(str(path))
        record["fields"] = fields
        record["has_cover"] = has_cover
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
    return record


def detail(path: str) -> dict:
    record = {
        "path": path.replace("\\", "/"),
        "fields": {},
        "has_cover": False,
        "probe": {},
        "error": None,
    }
    try:
        fields, has_cover = adapters.read_record(path)
        record["fields"] = fields
        record["has_cover"] = has_cover
        record["probe"] = adapters.probe(path)
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
    return record


# --------------------------------------------------------------------------
# 备份 / 快照
# --------------------------------------------------------------------------


def _cover_to_json(cover: Cover | None) -> dict | None:
    if cover is None:
        return None
    return {"mime": cover.mime, "b64": base64.b64encode(cover.data).decode("ascii")}


def _cover_from_json(payload: dict | None) -> Cover | None:
    if not payload:
        return None
    return Cover(base64.b64decode(payload["b64"]), payload.get("mime") or "image/jpeg")


def snapshot(paths: list[str], label: str) -> str:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    entries = []
    for path in paths:
        try:
            fields, _ = adapters.read_record(path)
            cover = adapters.read_cover(path)
            entries.append(
                {"path": path, "fields": fields, "cover": _cover_to_json(cover)}
            )
        except Exception as exc:
            entries.append({"path": path, "fields": {}, "cover": None, "error": str(exc)})

    backup_id = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    payload = {
        "id": backup_id,
        "created": time.time(),
        "label": label,
        "files": entries,
    }
    target = BACKUP_DIR / f"{backup_id}.json"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return backup_id


def list_backups(limit: int = 20) -> list[dict]:
    if not BACKUP_DIR.is_dir():
        return []
    items = []
    for file in sorted(BACKUP_DIR.glob("*.json"), reverse=True)[:limit]:
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except Exception:
            continue
        items.append(
            {
                "id": data.get("id", file.stem),
                "created": data.get("created", 0),
                "label": data.get("label", ""),
                "files": len(data.get("files", [])),
            }
        )
    return items


def _load_backup(backup_id: str) -> dict:
    file = BACKUP_DIR / f"{backup_id}.json"
    if not file.is_file():
        raise FileNotFoundError(f"备份不存在：{backup_id}")
    return json.loads(file.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# 写入
# --------------------------------------------------------------------------


def _coerce_cover(value) -> Cover | str | None:
    if value is None:
        return None
    if isinstance(value, Cover) or value == "remove":
        return value
    if isinstance(value, str) and value == "remove":
        return "remove"
    if isinstance(value, dict):
        op = value.get("op", "set")
        if op == "remove":
            return "remove"
        data = value.get("data_b64") or ""
        if not data:
            return None
        raw = base64.b64decode(data)
        mime = value.get("mime") or adapters.sniff_mime(raw)
        return Cover(raw, mime)
    return None


def normalize_changes(changes: list[dict]) -> list[dict]:
    out = []
    for change in changes:
        path = change.get("path")
        if not path or not os.path.isfile(path):
            continue
        patch = clean_patch(change.get("patch") or {})
        if "cover" in patch:
            patch["cover"] = _coerce_cover(patch["cover"])
            if patch["cover"] is None:
                patch.pop("cover")
        if patch:
            out.append({"path": path, "patch": patch})
    return out


def apply_changes(
    changes: list[dict],
    mode: str = "overwrite",
    label: str = "编辑标签",
    backup: bool = True,
) -> dict:
    """批量写入。返回逐文件结果，任何单点失败都不会中断整批。"""
    normalized = normalize_changes(changes)
    if not normalized:
        return {"applied": 0, "failed": [], "backup": None, "skipped": len(changes)}

    backup_id = None
    if backup:
        backup_id = snapshot([c["path"] for c in normalized], label)

    applied = 0
    failed = []
    for change in normalized:
        path = change["path"]
        try:
            adapters.write_tags(path, change["patch"], mode=mode)
            applied += 1
        except Exception as exc:
            failed.append({"path": path, "error": f"{type(exc).__name__}: {exc}"})
            if backup_id:
                _restore_one(path, backup_id)

    return {
        "applied": applied,
        "failed": failed,
        "backup": backup_id,
        "mode": mode,
        "skipped": len(changes) - len(normalized),
    }


def _restore_one(path: str, backup_id: str) -> None:
    data = _load_backup(backup_id)
    for entry in data.get("files", []):
        if entry["path"] != path:
            continue
        patch = {key: entry["fields"].get(key) for key in FIELD_KEYS if key != "cover"}
        cover = _cover_from_json(entry.get("cover"))
        patch["cover"] = cover if cover else "remove"
        try:
            adapters.write_tags(path, patch, mode="overwrite")
        except Exception:
            pass
        return


def undo(backup_id: str) -> dict:
    data = _load_backup(backup_id)
    restored = 0
    failed = []
    for entry in data.get("files", []):
        path = entry["path"]
        if not os.path.isfile(path):
            failed.append({"path": path, "error": "文件已不存在"})
            continue
        patch = {key: entry["fields"].get(key) for key in FIELD_KEYS if key != "cover"}
        cover = _cover_from_json(entry.get("cover"))
        patch["cover"] = cover if cover else "remove"
        try:
            adapters.write_tags(path, patch, mode="overwrite")
            restored += 1
        except Exception as exc:
            failed.append({"path": path, "error": f"{type(exc).__name__}: {exc}"})
    return {"restored": restored, "failed": failed, "backup": backup_id}


# --------------------------------------------------------------------------
# 从文件名 / 目录结构生成标签
# --------------------------------------------------------------------------


def preview_from_names(
    items: list[dict], template_raw: str, root: str, use_path: bool, overwrite: bool
) -> dict:
    """dry-run：只计算，不写盘。返回每个文件的解析结果与冲突提示。"""
    template = naming.parse_template(template_raw)
    rows = []
    for item in items:
        path = item["path"]
        target = (
            naming.relative_path_text(path, root)
            if use_path
            else naming.relative_stem(path, root)
        )
        parsed = naming.parse(template, target)
        existing = item.get("fields") or {}
        changes = {}
        conflicts = []
        for key, value in parsed.items():
            if not overwrite and existing.get(key):
                continue
            if existing.get(key) == value:
                continue
            changes[key] = value
        for key in parsed:
            if not parsed.get(key):
                conflicts.append(f"{key} 未解析出内容")
        rows.append(
            {
                "path": path,
                "target": target,
                "parsed": parsed,
                "changes": changes,
                "conflicts": conflicts,
                "matched": bool(parsed),
            }
        )
    return {
        "template": template_raw,
        "fields": template.fields,
        "rows": rows,
        "matched": sum(1 for r in rows if r["matched"]),
        "total": len(rows),
    }


# --------------------------------------------------------------------------
# 标签 -> 文件名（重命名）
# --------------------------------------------------------------------------


def plan_rename(
    items: list[dict], template_raw: str, root: str, keep_dir: bool = True
) -> dict:
    template = naming.parse_template(template_raw)
    base = Path(root)
    planned = []
    seen: dict[str, str] = {}

    for item in items:
        path = item["path"]
        values = dict(item.get("fields") or {})
        values.update(naming.render_values(path))
        rendered = naming.render(template, values).strip()
        if not rendered:
            planned.append({"path": path, "target": None, "error": "模板渲染为空"})
            continue

        parts = [p for p in rendered.replace("\\", "/").split("/") if p not in ("", ".", "..")]
        if not parts:
            planned.append({"path": path, "target": None, "error": "目标路径非法"})
            continue
        leaf = parts[-1] + Path(path).suffix.lower()
        if keep_dir:
            # 保留目录结构：文件留在原文件夹，只替换文件名
            target = Path(path).parent / leaf
        else:
            # 模板里的 "/" 会生成子目录
            target = base.joinpath(*parts[:-1], leaf) if len(parts) > 1 else base / leaf
        rel = target.relative_to(base).as_posix()

        error = None
        if str(target).replace("\\", "/") == path.replace("\\", "/"):
            error = None  # 名称未变，视为无操作
        elif target.exists():
            error = "目标已存在"
        elif rel.lower() in seen:
            error = "与另一个文件目标冲突"
        if not error:
            seen[rel.lower()] = path

        planned.append(
            {
                "path": path,
                "target": str(target).replace("\\", "/"),
                "rel": rel,
                "error": error,
                "unchanged": error is None
                and str(target).replace("\\", "/") == path.replace("\\", "/"),
            }
        )
    return {"template": template_raw, "rows": planned}


def apply_rename(rows: list[dict]) -> dict:
    moved = 0
    failed = []
    for row in rows:
        target = row.get("target")
        if not target or row.get("error") or row.get("unchanged"):
            continue
        try:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            os.rename(row["path"], target)
            moved += 1
        except Exception as exc:
            failed.append({"path": row["path"], "error": f"{type(exc).__name__}: {exc}"})
    return {"moved": moved, "failed": failed}
