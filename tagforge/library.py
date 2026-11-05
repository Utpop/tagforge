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
from collections import Counter
from pathlib import Path

from . import adapters, naming
from .adapters import Cover, is_audio
from .fields import FIELD_KEYS, clean_patch

BACKUP_DIR = Path.home() / ".tagforge" / "backups"

MAX_SCAN = 20000
MAX_BACKUPS = 50          # 备份保留条数，超出按时间裁剪


# --------------------------------------------------------------------------
# 扫描
# --------------------------------------------------------------------------


def scan(root: str, recursive: bool = True, limit: int = MAX_SCAN) -> dict:
    base = Path(root).resolve()
    if not base.is_dir():
        raise NotADirectoryError(str(base))

    # 故意多收 1 个：只有真的还有第 limit+1 个文件，才算被截断。
    # 用 `len == limit` 判断会把"正好 limit 个"误报成截断（或漏报）。
    files: list[Path] = []
    if recursive:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(
                d for d in dirnames if not d.startswith(".") and d != "System Volume Information"
            )
            for name in sorted(filenames):
                if is_audio(name):
                    files.append(Path(dirpath) / name)
                    if len(files) > limit:
                        break
            if len(files) > limit:
                break
    else:
        for entry in sorted(base.iterdir()):
            if entry.is_file() and is_audio(entry.name):
                files.append(entry)
                if len(files) > limit:
                    break

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
    # stat 失败（扫描过程中被删/被占用）不应让整次扫描崩溃
    try:
        stat = path.stat()
        size, mtime = stat.st_size, int(stat.st_mtime)
    except OSError:
        size, mtime = 0, 0
    record = {
        "path": str(path).replace("\\", "/"),
        "name": path.name,
        "rel": path.relative_to(base).as_posix(),
        "dir": path.parent.relative_to(base).as_posix() if path.parent != base else "",
        "ext": path.suffix.lower(),
        "size": size,
        "mtime": mtime,
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
            # 多值字段额外存原始列表：字符串会被 "; " 拼接，撤销时无法无损拆回
            lists = adapters.read_multi(path)
            cover = adapters.read_cover(path)
            entries.append(
                {
                    "path": path,
                    "fields": fields,
                    "lists": lists,
                    "cover": _cover_to_json(cover),
                }
            )
        except Exception as exc:
            # 读取失败：标记 error，undo 时跳过。绝不能存"空字段"——
            # 那会让撤销把该文件的所有标签当成"应当删除"。
            entries.append(
                {"path": path, "fields": {}, "lists": {}, "cover": None, "error": str(exc)}
            )

    backup_id = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    payload = {
        "id": backup_id,
        "created": time.time(),
        "label": label,
        "files": entries,
    }
    target = BACKUP_DIR / f"{backup_id}.json"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    _prune_backups()
    return backup_id


def _prune_backups(keep: int = MAX_BACKUPS) -> None:
    """只保留最近 keep 条备份（含封面 base64，不裁剪会无限膨胀）。"""
    files = sorted(BACKUP_DIR.glob("*.json"), reverse=True)
    for old in files[keep:]:
        for suffix in (".json", ".used"):
            try:
                (BACKUP_DIR / f"{old.stem}{suffix}").unlink()
            except OSError:
                pass


def _mark_consumed(backup_id: str) -> None:
    """标记备份已撤销过，避免连点撤销反复回退同一批。"""
    try:
        (BACKUP_DIR / f"{backup_id}.used").write_text("", encoding="utf-8")
    except OSError:
        pass


def _is_consumed(backup_id: str) -> bool:
    return (BACKUP_DIR / f"{backup_id}.used").is_file()


def list_backups(limit: int = 20) -> list[dict]:
    if not BACKUP_DIR.is_dir():
        return []
    items = []
    for file in sorted(BACKUP_DIR.glob("*.json"), reverse=True):
        if len(items) >= limit:
            break
        if _is_consumed(file.stem):
            continue
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
    out, _ = _split_changes(changes)
    return out


def _split_changes(changes: list[dict]) -> tuple[list[dict], list[str]]:
    """拆成"待写入"与"文件已不存在"两拨。

    原来把不存在的文件直接丢掉，保存完还报告"已保存 N 个"，
    用户的改动会无声消失（文件被重命名/删除时很常见）。
    """
    out: list[dict] = []
    missing: list[str] = []
    for change in changes:
        path = change.get("path")
        if not path:
            continue
        if not os.path.isfile(path):
            missing.append(path)
            continue
        patch = clean_patch(change.get("patch") or {})
        if "cover" in patch:
            patch["cover"] = _coerce_cover(patch["cover"])
            if patch["cover"] is None:
                patch.pop("cover")
        if patch:
            out.append({"path": path, "patch": patch})
    return out, missing


def apply_changes(
    changes: list[dict],
    mode: str = "overwrite",
    label: str = "编辑标签",
    backup: bool = True,
) -> dict:
    """批量写入。返回逐文件结果，任何单点失败都不会中断整批。"""
    normalized, missing = _split_changes(changes)
    if not normalized:
        return {
            "applied": 0,
            "failed": [],
            "missing": missing,
            "backup": None,
            "skipped": len(changes) - len(missing),
        }

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
        "missing": missing,
        "backup": backup_id,
        "mode": mode,
        "skipped": len(changes) - len(missing) - len(normalized),
    }


def _restore_patch(entry: dict) -> dict:
    """把一条快照还原成 patch：多值用原始列表，缺失字段写 None（删除）。"""
    fields = entry.get("fields") or {}
    lists = entry.get("lists") or {}
    patch: dict = {}
    for key in FIELD_KEYS:
        if key == "cover":
            continue
        values = lists.get(key)
        if values:
            patch[key] = list(values)      # 原样还原多值，避免 "A; B" 被再拆一次
        else:
            patch[key] = fields.get(key)
    cover = _cover_from_json(entry.get("cover"))
    patch["cover"] = cover if cover else "remove"
    return patch


def _restore_one(path: str, backup_id: str) -> None:
    data = _load_backup(backup_id)
    for entry in data.get("files", []):
        if entry["path"] != path or entry.get("error"):
            continue
        try:
            adapters.write_tags(path, _restore_patch(entry), mode="overwrite")
        except Exception:
            pass
        return


def undo(backup_id: str, snapshot_before: bool = True) -> dict:
    """按备份回退。快照读取失败的文件会被跳过，绝不会被清空。"""
    data = _load_backup(backup_id)
    entries = data.get("files", [])

    # 撤销本身也要可反悔：先给"撤销前"的状态拍一张
    if snapshot_before:
        targets = [
            e["path"] for e in entries
            if not e.get("error") and os.path.isfile(e["path"])
        ]
        if targets:
            snapshot(targets, f"撤销前 · {data.get('label', '')}".strip(" ·"))

    restored, failed, skipped = 0, [], []
    for entry in entries:
        path = entry["path"]
        if not os.path.isfile(path):
            failed.append({"path": path, "error": "文件已不存在"})
            continue
        if entry.get("error"):
            # 快照没读到 → 无从还原；跳过是唯一安全的选择
            skipped.append({"path": path, "error": "快照不可用，已跳过"})
            continue
        try:
            adapters.write_tags(path, _restore_patch(entry), mode="overwrite")
            restored += 1
        except Exception as exc:
            failed.append({"path": path, "error": f"{type(exc).__name__}: {exc}"})

    _mark_consumed(backup_id)
    return {
        "restored": restored,
        "failed": failed,
        "skipped": skipped,
        "backup": backup_id,
    }


# --------------------------------------------------------------------------
# 从文件名 / 目录结构生成标签
# --------------------------------------------------------------------------


def preview_from_names(
    items: list[dict], template_raw: str, root: str, use_path: bool, overwrite: bool
) -> dict:
    """dry-run：只计算，不写盘。返回每个文件的解析结果与失败原因。"""
    template = naming.parse_template(template_raw)
    has_dir = "/" in template_raw or "\\" in template_raw
    rows = []

    for item in items:
        path = item["path"]
        target = (
            naming.relative_path_text(path, root)
            if use_path
            else naming.relative_stem(path, root)
        )
        existing = item.get("fields") or {}
        reason = None
        parsed: dict = {}

        if use_path and not has_dir and "/" in target:
            # 模板没有目录段却拿整条相对路径去匹配，正则会贪婪吞掉目录前缀，
            # 把 "Artist/Album/01" 当成音轨号 —— 拒绝比写坏更安全。
            reason = "模板不含目录分隔符，无法按目录结构匹配"
        else:
            raw = naming.parse(template, target)
            parsed = {k: v for k, v in raw.items() if k not in naming.VIRTUAL_FIELDS}
            if not parsed and raw:
                reason = "模板只含虚拟字段，没有可写入的标签"
            for key in ("track", "disc"):
                if key in parsed and "/" in str(parsed[key]):
                    reason = f"{key} 解析出非法值（含 '/'）"
                    break

        changes = {}
        for key, value in parsed.items():
            if not isinstance(value, str) or not value:
                continue
            if not overwrite and existing.get(key):
                continue
            if existing.get(key) == value:
                continue
            changes[key] = value

        rows.append(
            {
                "path": path,
                "target": target,
                "parsed": parsed,
                "changes": changes,
                "matched": bool(parsed) and reason is None,
                "reason": reason,
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
    items: list[dict],
    template_raw: str,
    root: str,
    keep_dir: bool = True,
    skip_exist: bool = True,
) -> dict:
    """计算重命名计划。

    两遍：先算出所有目标，再判定冲突。第二遍才知道"目标路径上那个文件
    是不是也要改名"，从而支持整批互换（01↔02）而不必先手动挪一个。
    """
    template = naming.parse_template(template_raw)
    base = Path(root)

    def norm(text) -> str:
        """统一成正斜杠：Windows 上 str(Path) 是反斜杠，直接比较会永远不等。"""
        return str(text).replace("\\", "/")

    planned: list[dict] = []

    for item in items:
        path = item["path"]
        values = dict(item.get("fields") or {})
        values.update(naming.render_values(path))
        rendered = naming.render(template, values).strip()
        if not rendered:
            planned.append({"path": path, "target": None, "error": "模板渲染为空", "unchanged": False})
            continue

        parts = [p for p in rendered.replace("\\", "/").split("/") if p not in ("", ".", "..")]
        if not parts:
            planned.append({"path": path, "target": None, "error": "目标路径非法", "unchanged": False})
            continue

        leaf = parts[-1] + Path(path).suffix.lower()
        if keep_dir:
            target = Path(path).parent / leaf
        else:
            target = base.joinpath(*parts[:-1], leaf) if len(parts) > 1 else base / leaf

        try:
            rel = target.relative_to(base).as_posix()
        except ValueError:
            planned.append({"path": path, "target": None, "error": "目标不在库目录内", "unchanged": False})
            continue

        same = norm(target) == norm(path)
        planned.append(
            {
                "path": path,
                "target": str(target).replace("\\", "/"),
                "rel": rel,
                "error": None,
                "unchanged": same,
                "via_temp": False,
                "overwrite": False,
            }
        )

    # 第二遍：冲突判定
    counter = Counter(
        row["rel"].lower() for row in planned if row.get("target") and not row.get("unchanged")
    )
    for row in planned:
        if row.get("error") or row.get("unchanged") or not row.get("target"):
            continue
        target = Path(row["target"])
        if counter[row["rel"].lower()] > 1:
            row["error"] = "与另一个文件目标冲突"
            continue
        if not target.exists():
            continue
        # 目标被本批次里另一个"即将改名"的文件占着 → 允许，执行时走两阶段
        holder = next((r for r in planned if norm(r["path"]) == norm(target)), None)
        if holder is not None and not holder.get("unchanged") and not holder.get("error"):
            row["via_temp"] = True
        elif skip_exist:
            row["error"] = "目标已存在"
        else:
            row["overwrite"] = True
            row["via_temp"] = True

    return {"template": template_raw, "rows": planned}


def apply_rename(rows: list[dict]) -> dict:
    """执行重命名。存在"目标被占"的行时先全部改成临时名，再落到终名。"""
    todo = [
        r for r in rows
        if r.get("target") and not r.get("error") and not r.get("unchanged")
    ]
    moved, failed = 0, []

    if any(r.get("via_temp") for r in todo):
        staged: list[tuple[dict, Path]] = []
        for index, row in enumerate(todo):
            src = Path(row["path"])
            tmp = src.with_name(f".tagforge-tmp-{os.getpid()}-{index}{src.suffix}")
            try:
                os.replace(src, tmp)
                staged.append((row, tmp))
            except Exception as exc:
                failed.append({"path": row["path"], "error": f"{type(exc).__name__}: {exc}"})
        for row, tmp in staged:
            try:
                Path(row["target"]).parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp, row["target"])
                moved += 1
            except Exception as exc:
                failed.append({"path": row["path"], "error": f"{type(exc).__name__}: {exc}"})
                try:
                    os.replace(tmp, row["path"])      # 尽量退回原名，避免文件卡在临时名
                except Exception:
                    pass
        return {"moved": moved, "failed": failed}

    for row in todo:
        try:
            Path(row["target"]).parent.mkdir(parents=True, exist_ok=True)
            os.replace(row["path"], row["target"])
            moved += 1
        except Exception as exc:
            failed.append({"path": row["path"], "error": f"{type(exc).__name__}: {exc}"})
    return {"moved": moved, "failed": failed}
