"""格式适配器：把 canonical 字段读写到四种标签体系。

四类写入引擎覆盖了绝大多数音频格式：
    id3      MP3 / WAV / AIFF / DSF       -> ID3v2.3 / ID3v2.4
    vorbis   FLAC / OGG / OPUS            -> Vorbis Comments (+ FLAC Picture)
    mp4      M4A / M4B / MP4              -> iTunes atoms
    apev2    APE / WV / MPC               -> APEv2

新增一种格式时只需要：1) 声明扩展名归属哪类引擎；2) 如有特殊键名则补映射。
"""

from __future__ import annotations

import base64
import os
import shutil
from pathlib import Path
from typing import Any

from mutagen.apev2 import APEv2, APEValue
from mutagen.flac import FLAC, Picture
from mutagen.id3 import (
    APIC,
    COMM,
    TALB,
    TCOM,
    TCON,
    TDRC,
    TIT2,
    TPE1,
    TPE2,
    TPOS,
    TRCK,
    ID3,
    ID3NoHeaderError,
)
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

from .fields import MULTI_VALUE_FIELDS, VALUE_SEPARATOR, normalize_year, split_values

# --------------------------------------------------------------------------
# 扩展名 -> 引擎
# --------------------------------------------------------------------------

ENGINE_BY_EXT: dict[str, str] = {
    ".mp3": "id3",
    ".wav": "id3",
    ".aiff": "id3",
    ".aif": "id3",
    ".aifc": "id3",
    ".dsf": "id3",
    ".flac": "vorbis",
    ".ogg": "vorbis",
    ".oga": "vorbis",
    ".opus": "vorbis",
    ".m4a": "mp4",
    ".m4b": "mp4",
    ".m4v": "mp4",
    ".mp4": "mp4",
    ".ape": "apev2",
    ".wv": "apev2",
    ".mpc": "apev2",
}

AUDIO_EXTS: frozenset[str] = frozenset(ENGINE_BY_EXT)

MIME_BY_EXT: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
}


def engine_for(path: str | os.PathLike) -> str | None:
    return ENGINE_BY_EXT.get(Path(path).suffix.lower())


def is_audio(path: str | os.PathLike) -> bool:
    return engine_for(path) is not None


class TagError(Exception):
    pass


# --------------------------------------------------------------------------
# 封面数据
# --------------------------------------------------------------------------


class Cover:
    """一帧封面。data 为原始图片字节。"""

    __slots__ = ("data", "mime")

    def __init__(self, data: bytes, mime: str = "image/jpeg"):
        self.data = data
        self.mime = mime or "image/jpeg"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Cover {self.mime} {len(self.data)}B>"


def sniff_mime(data: bytes, fallback: str = "image/jpeg") -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:2] == b"BM":
        return "image/bmp"
    return fallback


def _as_single(value) -> str:
    """单值字段收到列表时取第一个（撤销路径可能传来列表）。"""
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else ""
    return "" if value is None else str(value)


def _as_list(value) -> list[str]:
    """多值字段统一成列表。"""
    return split_values(value)


# --------------------------------------------------------------------------
# ID3 引擎
# --------------------------------------------------------------------------

ID3_TEXT_FRAMES = {
    "title": "TIT2",
    "artist": "TPE1",
    "album": "TALB",
    "albumartist": "TPE2",
    "genre": "TCON",
    "composer": "TCOM",
}


def _id3_open(path: str, create: bool = False) -> ID3 | None:
    try:
        return ID3(path)
    except ID3NoHeaderError:
        if not create:
            return None
        tag = ID3()
        return tag


def _id3_read(path: str) -> dict:
    tag = _id3_open(path)
    rec: dict = {}
    if tag is None:
        return rec

    for key, frame_id in ID3_TEXT_FRAMES.items():
        frame = tag.get(frame_id)
        if frame is not None and frame.text:
            rec[key] = VALUE_SEPARATOR.join(str(t) for t in frame.text)

    tdrc = tag.get("TDRC")
    if tdrc is not None and tdrc.text:
        rec["year"] = normalize_year(str(tdrc.text[0]))

    trck = tag.get("TRCK")
    if trck is not None and trck.text:
        num, _, total = str(trck.text[0]).partition("/")
        rec["track"] = num.strip()
        if total:
            rec["tracktotal"] = total.strip()

    tpos = tag.get("TPOS")
    if tpos is not None and tpos.text:
        num, _, total = str(tpos.text[0]).partition("/")
        rec["disc"] = num.strip()
        if total:
            rec["disctotal"] = total.strip()

    for frame in tag.getall("COMM"):
        if frame.desc in ("", None) or frame.desc == "Comment":
            if frame.text:
                rec["comment"] = str(frame.text[0])
            break

    rec = {k: v for k, v in rec.items() if v}
    rec["_cover"] = bool(tag.getall("APIC"))
    return rec


def _id3_cover(path: str) -> Cover | None:
    tag = _id3_open(path)
    if tag is None:
        return None
    pics = tag.getall("APIC")
    if not pics:
        return None
    front = [p for p in pics if getattr(p, "type", 0) == 3]
    chosen = front[0] if front else pics[0]
    return Cover(bytes(chosen.data), chosen.mime or sniff_mime(bytes(chosen.data)))


def _drop_front_apic(tag: ID3) -> None:
    """只移除正面封面（type=3），保留封底 / 艺人照 / 其它图片帧。

    原来的 delall("APIC") 会把用户精心保存的所有图片一起删掉。
    """
    keep = [p for p in tag.getall("APIC") if getattr(p, "type", 0) != 3]
    tag.delall("APIC")
    for frame in keep:
        tag.add(frame)


def _drop_comment_slot(tag: ID3) -> None:
    """只移除"英文 + 无描述"那一槽备注，保留其它语言与带描述的备注。"""
    for frame in list(tag.getall("COMM")):
        if (frame.desc or "") == "" and frame.lang == "eng":
            del tag[frame.HashKey]


def _id3_write(path: str, patch: dict, mode: str) -> None:
    had_tag = True
    try:
        tag = ID3(path)
    except ID3NoHeaderError:
        had_tag = False
        tag = ID3()
    # 保留文件原有的 ID3v2 版本，避免把 v2.3 曲库悄悄升级成 v2.4
    v2_version = 3 if (had_tag and tag.version[1] == 3) else 4

    def existing(frame_id: str) -> bool:
        frame = tag.get(frame_id)
        return bool(frame is not None and getattr(frame, "text", None))

    for key, frame_id in ID3_TEXT_FRAMES.items():
        if key not in patch:
            continue
        value = patch[key]
        if value is None or (mode == "fill" and existing(frame_id)):
            if mode == "overwrite" and value is None:
                tag.delall(frame_id)
            continue
        tag.delall(frame_id)
        frame_cls = {
            "TIT2": TIT2, "TPE1": TPE1, "TALB": TALB,
            "TPE2": TPE2, "TCON": TCON, "TCOM": TCOM,
        }[frame_id]
        values = split_values(value) or [""]
        # ID3v2.3 用 "/" 表示多值，写多个值会把 "A; B" 变成 "A/B" 且回读成单值。
        # v2.3 文件一律合并成一个值，保住用户输入的语义。
        if v2_version == 3 and len(values) > 1:
            values = [VALUE_SEPARATOR.join(values)]
        tag.add(frame_cls(encoding=3, text=values))

    if "year" in patch:
        value = patch["year"]
        if value is None:
            if mode == "overwrite":
                tag.delall("TDRC")
        elif not (mode == "fill" and existing("TDRC")):
            tag.delall("TDRC")
            tag.add(TDRC(encoding=3, text=[normalize_year(value)]))

    # 音轨 / 碟号：TRCK / TPOS 是 "n/total" 形式，需要合并写
    for num_key, total_key, frame_id, frame_cls in (
        ("track", "tracktotal", "TRCK", TRCK),
        ("disc", "disctotal", "TPOS", TPOS),
    ):
        if num_key not in patch and total_key not in patch:
            continue
        if mode == "fill" and existing(frame_id):
            continue
        old = tag.get(frame_id)
        old_num, old_total = "", ""
        if old is not None and old.text:
            old_num, _, old_total = str(old.text[0]).partition("/")
        num = patch.get(num_key, old_num) if num_key in patch else old_num
        total = patch.get(total_key, old_total) if total_key in patch else old_total
        num = _as_single(num).strip()
        total = _as_single(total).strip()
        tag.delall(frame_id)
        # 只有 total 时写 "/12"：既保住总数，也不会伪造一个音轨号 0
        if num or total:
            text = f"{num}/{total}" if total else num
            tag.add(frame_cls(encoding=3, text=[text]))

    if "comment" in patch:
        value = patch["comment"]
        has_comment = any(
            (f.desc or "") == "" and f.lang == "eng" for f in tag.getall("COMM")
        )
        if value is None:
            if mode == "overwrite":
                _drop_comment_slot(tag)
        elif not (mode == "fill" and has_comment):
            _drop_comment_slot(tag)
            tag.add(COMM(encoding=3, lang="eng", desc="", text=[_as_single(value)]))

    if "cover" in patch:
        cover = patch["cover"]
        if cover == "remove":
            _drop_front_apic(tag)
        elif isinstance(cover, Cover) and not (mode == "fill" and tag.getall("APIC")):
            _drop_front_apic(tag)
            tag.add(
                APIC(encoding=3, mime=cover.mime, type=3, desc="", data=cover.data)
            )

    tag.save(path, v2_version=v2_version)


# --------------------------------------------------------------------------
# Vorbis 引擎（FLAC / OGG / OPUS）
# --------------------------------------------------------------------------

VORBIS_KEYS = {
    "title": "TITLE",
    "artist": "ARTIST",
    "album": "ALBUM",
    "albumartist": "ALBUMARTIST",
    "year": "DATE",
    "genre": "GENRE",
    "composer": "COMPOSER",
    "comment": "COMMENT",
    "track": "TRACKNUMBER",
    "tracktotal": "TRACKTOTAL",
    "disc": "DISCNUMBER",
    "disctotal": "DISCTOTAL",
}

VORBIS_ALIASES = {
    "tracktotal": ("TRACKTOTAL", "TOTALTRACKS"),
    "disctotal": ("DISCTOTAL", "TOTALDISCS"),
    "albumartist": ("ALBUMARTIST", "ALBUM ARTIST"),
}


def _vorbis_open(path: str):
    ext = Path(path).suffix.lower()
    if ext == ".flac":
        return FLAC(path)
    if ext == ".opus":
        return OggOpus(path)
    return OggVorbis(path)


def _vorbis_read(path: str) -> dict:
    audio = _vorbis_open(path)
    tags = audio.tags or {}
    rec: dict = {}
    for key, vkey in VORBIS_KEYS.items():
        candidates = VORBIS_ALIASES.get(key, (vkey,))
        for candidate in candidates:
            values = tags.get(candidate) if hasattr(tags, "get") else None
            if values:
                text = VALUE_SEPARATOR.join(str(v) for v in values)
                rec[key] = normalize_year(text) if key == "year" else text
                break
    rec = {k: v for k, v in rec.items() if v}
    if Path(path).suffix.lower() == ".flac":
        rec["_cover"] = bool(FLAC(path).pictures)
    else:
        rec["_cover"] = bool((audio.tags or {}).get("metadata_block_picture"))
    return rec


def _vorbis_cover(path: str) -> Cover | None:
    ext = Path(path).suffix.lower()
    if ext == ".flac":
        audio = FLAC(path)
        if not audio.pictures:
            return None
        front = [p for p in audio.pictures if p.type == 3]
        chosen = front[0] if front else audio.pictures[0]
        return Cover(bytes(chosen.data), chosen.mime or sniff_mime(bytes(chosen.data)))

    audio = _vorbis_open(path)
    tags = audio.tags or {}
    raw = tags.get("metadata_block_picture")
    if not raw:
        return None
    try:
        pic = Picture(base64.b64decode(raw[0]))
    except Exception:
        return None
    return Cover(bytes(pic.data), pic.mime or sniff_mime(bytes(pic.data)))


def _drop_front_flac_picture(audio: FLAC) -> None:
    """只移除 type=3 的正面封面，保留其它图片。"""
    keep = [p for p in audio.pictures if p.type != 3]
    audio.clear_pictures()
    for pic in keep:
        audio.add_picture(pic)


def _vorbis_write(path: str, patch: dict, mode: str) -> None:
    audio = _vorbis_open(path)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags

    def current(key: str) -> list[str]:
        for candidate in VORBIS_ALIASES.get(key, (VORBIS_KEYS[key],)):
            if tags.get(candidate):
                return [str(v) for v in tags[candidate]]
        return []

    for key, vkey in VORBIS_KEYS.items():
        if key not in patch:
            continue
        existing = current(key)  # 必须在任何删除动作之前读取
        if mode == "fill" and existing:
            continue
        for candidate in VORBIS_ALIASES.get(key, (vkey,)):
            if candidate in tags:
                del tags[candidate]
        value = patch[key]
        if value is None:
            continue
        if key in MULTI_VALUE_FIELDS:
            values = split_values(value)
        else:
            values = [normalize_year(_as_single(value)) if key == "year" else _as_single(value)]
        tags[vkey] = values or [""]

    if "cover" in patch:
        cover = patch["cover"]
        is_flac = isinstance(audio, FLAC)
        if cover == "remove":
            if is_flac:
                _drop_front_flac_picture(audio)
            else:
                if tags.get("metadata_block_picture"):
                    del tags["metadata_block_picture"]
        elif isinstance(cover, Cover):
            has = bool(audio.pictures) if is_flac else bool(tags.get("metadata_block_picture"))
            if not (mode == "fill" and has):
                pic = Picture()
                pic.type = 3
                pic.mime = cover.mime
                pic.desc = ""
                pic.data = cover.data
                if is_flac:
                    _drop_front_flac_picture(audio)
                    audio.add_picture(pic)
                else:
                    tags["metadata_block_picture"] = [
                        base64.b64encode(pic.write()).decode("ascii")
                    ]

    audio.save()


# --------------------------------------------------------------------------
# MP4 引擎
# --------------------------------------------------------------------------

MP4_ATOMS = {
    "title": "\xa9nam",
    "artist": "\xa9ART",
    "album": "\xa9alb",
    "albumartist": "aART",
    "year": "\xa9day",
    "genre": "\xa9gen",
    "composer": "\xa9wrt",
    "comment": "\xa9cmt",
}


def _mp4_open(path: str) -> MP4:
    return MP4(path)


def _mp4_read(path: str) -> dict:
    audio = _mp4_open(path)
    tags = audio.tags or {}
    rec: dict = {}
    for key, atom in MP4_ATOMS.items():
        values = tags.get(atom)
        if values:
            text = VALUE_SEPARATOR.join(str(v) for v in values)
            rec[key] = normalize_year(text) if key == "year" else text
    trkn = tags.get("trkn")
    if trkn:
        num, total = trkn[0]
        if num:
            rec["track"] = str(num)
        if total:
            rec["tracktotal"] = str(total)
    disk = tags.get("disk")
    if disk:
        num, total = disk[0]
        if num:
            rec["disc"] = str(num)
        if total:
            rec["disctotal"] = str(total)
    rec = {k: v for k, v in rec.items() if v}
    rec["_cover"] = bool(tags.get("covr"))
    return rec


def _mp4_cover(path: str) -> Cover | None:
    audio = _mp4_open(path)
    covers = (audio.tags or {}).get("covr")
    if not covers:
        return None
    chosen = covers[0]
    data = bytes(chosen)
    mime = "image/png" if chosen.imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
    return Cover(data, sniff_mime(data, mime))


def _mp4_write(path: str, patch: dict, mode: str) -> None:
    audio = _mp4_open(path)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags

    for key, atom in MP4_ATOMS.items():
        if key not in patch:
            continue
        value = patch[key]
        if value is None:
            if mode == "overwrite":
                tags.pop(atom, None)
            continue
        if mode == "fill" and tags.get(atom):
            continue
        if key in MULTI_VALUE_FIELDS:
            values = split_values(value)
        else:
            values = [normalize_year(_as_single(value)) if key == "year" else _as_single(value)]
        tags[atom] = values or [""]

    for num_key, total_key, atom in (
        ("track", "tracktotal", "trkn"),
        ("disc", "disctotal", "disk"),
    ):
        if num_key not in patch and total_key not in patch:
            continue
        if mode == "fill" and tags.get(atom):
            continue
        old = tags.get(atom)
        old_num, old_total = (old[0] if old else (0, 0))
        num = _as_single(patch.get(num_key, old_num))
        total = _as_single(patch.get(total_key, old_total))
        try:
            num_i = int(str(num).split("/")[0]) if num not in (None, "") else 0
        except ValueError:
            num_i = 0
        try:
            total_i = int(str(total)) if total not in (None, "") else 0
        except ValueError:
            total_i = 0
        if num_i or total_i:
            tags[atom] = [(num_i, total_i)]
        else:
            tags.pop(atom, None)

    if "cover" in patch:
        cover = patch["cover"]
        if cover == "remove":
            tags.pop("covr", None)
        elif isinstance(cover, Cover):
            has = bool(tags.get("covr"))
            if not (mode == "fill" and has):
                fmt = (
                    MP4Cover.FORMAT_PNG
                    if cover.mime == "image/png"
                    else MP4Cover.FORMAT_JPEG
                )
                tags["covr"] = [MP4Cover(cover.data, imageformat=fmt)]

    audio.save()


# --------------------------------------------------------------------------
# APEv2 引擎（APE / WavPack / Musepack）
# --------------------------------------------------------------------------

APEV2_KEYS = {
    "title": "Title",
    "artist": "Artist",
    "album": "Album",
    "albumartist": "Album Artist",
    "year": "Year",
    "genre": "Genre",
    "composer": "Composer",
    "comment": "Comment",
}

# APEv2 惯例把音轨号存成 "n/m"，与 ID3 的 TRCK 一致；单独处理以支持拆写。
APEV2_TRACK = "Track"
APEV2_DISC = "Disc"

APEV2_COVER_KEY = "Cover Art (Front)"


def _apev2_read(path: str) -> dict:
    try:
        tag = APEv2(path)
    except Exception:
        return {}
    rec: dict = {}
    for key, akey in APEV2_KEYS.items():
        if akey in tag:
            text = str(tag[akey])
            # APEv2 的多值用 \0 分隔
            parts = [p for p in text.split("\x00") if p]
            rec[key] = VALUE_SEPARATOR.join(parts) if parts else text
    for num_key, total_key, akey in (
        ("track", "tracktotal", APEV2_TRACK),
        ("disc", "disctotal", APEV2_DISC),
    ):
        if akey not in tag:
            continue
        num, _, total = str(tag[akey]).partition("/")
        if num.strip():
            rec[num_key] = num.strip()
        if total.strip():
            rec[total_key] = total.strip()
    if "year" in rec:
        rec["year"] = normalize_year(rec["year"])
    rec = {k: v for k, v in rec.items() if v}
    rec["_cover"] = APEV2_COVER_KEY in tag
    return rec


def _apev2_cover(path: str) -> Cover | None:
    try:
        tag = APEv2(path)
    except Exception:
        return None
    if APEV2_COVER_KEY not in tag:
        return None
    raw = bytes(tag[APEV2_COVER_KEY])
    _, _, data = raw.partition(b"\x00")
    return Cover(data, sniff_mime(data)) if data else None


def _apev2_write(path: str, patch: dict, mode: str) -> None:
    try:
        tag = APEv2(path)
    except Exception:
        # 文件里还没有 APEv2 标签：先在内存里建一个，最后一次落盘。
        # 注意不要"先保存空标签再重新打开"——空标签不会写出文件头。
        tag = APEv2()

    for key, akey in APEV2_KEYS.items():
        if key not in patch:
            continue
        value = patch[key]
        if value is None:
            if mode == "overwrite" and akey in tag:
                del tag[akey]
            continue
        if mode == "fill" and akey in tag:
            continue
        if key in MULTI_VALUE_FIELDS:
            # APEv2 多值用 \0 分隔，写成一个字符串，与读取方向对称
            text = "\x00".join(split_values(value))
        elif key == "year":
            text = normalize_year(_as_single(value))
        else:
            text = _as_single(value)
        tag[akey] = text

    # 音轨 / 碟号：合并成 "n/m"，与 ID3 语义一致
    for num_key, total_key, akey in (
        ("track", "tracktotal", APEV2_TRACK),
        ("disc", "disctotal", APEV2_DISC),
    ):
        if num_key not in patch and total_key not in patch:
            continue
        if mode == "fill" and akey in tag:
            continue
        old_num, old_total = "", ""
        if akey in tag:
            old_num, _, old_total = str(tag[akey]).partition("/")
        num = _as_single(patch.get(num_key, old_num)).strip()
        total = _as_single(patch.get(total_key, old_total)).strip()
        if akey in tag:
            del tag[akey]
        if num or total:
            tag[akey] = f"{num}/{total}" if total else num

    if "cover" in patch:
        cover = patch["cover"]
        if cover == "remove":
            if APEV2_COVER_KEY in tag:
                del tag[APEV2_COVER_KEY]
        elif isinstance(cover, Cover) and not (
            mode == "fill" and APEV2_COVER_KEY in tag
        ):
            ext = ".png" if cover.mime == "image/png" else ".jpg"
            tag[APEV2_COVER_KEY] = APEValue(
                b"cover" + ext.encode() + b"\x00" + cover.data, 1
            )

    tag.save(path)


# --------------------------------------------------------------------------
# 统一入口
# --------------------------------------------------------------------------

_READERS = {
    "id3": _id3_read,
    "vorbis": _vorbis_read,
    "mp4": _mp4_read,
    "apev2": _apev2_read,
}
_COVER_READERS = {
    "id3": _id3_cover,
    "vorbis": _vorbis_cover,
    "mp4": _mp4_cover,
    "apev2": _apev2_cover,
}
_WRITERS = {
    "id3": _id3_write,
    "vorbis": _vorbis_write,
    "mp4": _mp4_write,
    "apev2": _apev2_write,
}


def _id3_multi(path: str) -> dict[str, list[str]]:
    tag = _id3_open(path)
    if tag is None:
        return {}
    out: dict[str, list[str]] = {}
    for key, frame_id in ID3_TEXT_FRAMES.items():
        if key not in MULTI_VALUE_FIELDS:
            continue
        frame = tag.get(frame_id)
        if frame is not None and frame.text:
            out[key] = [str(t) for t in frame.text]
    return out


def _vorbis_multi(path: str) -> dict[str, list[str]]:
    audio = _vorbis_open(path)
    tags = audio.tags or {}
    out: dict[str, list[str]] = {}
    for key in MULTI_VALUE_FIELDS:
        for candidate in VORBIS_ALIASES.get(key, (VORBIS_KEYS[key],)):
            values = tags.get(candidate)
            if values:
                out[key] = [str(v) for v in values]
                break
    return out


def _mp4_multi(path: str) -> dict[str, list[str]]:
    tags = _mp4_open(path).tags or {}
    out: dict[str, list[str]] = {}
    for key in MULTI_VALUE_FIELDS:
        values = tags.get(MP4_ATOMS[key])
        if values:
            out[key] = [str(v) for v in values]
    return out


def _apev2_multi(path: str) -> dict[str, list[str]]:
    try:
        tag = APEv2(path)
    except Exception:
        return {}
    out: dict[str, list[str]] = {}
    for key in MULTI_VALUE_FIELDS:
        akey = APEV2_KEYS.get(key)
        if akey and akey in tag:
            text = str(tag[akey])
            parts = [p for p in text.split("\x00") if p]
            if parts:
                out[key] = parts
    return out


_MULTI_READERS = {
    "id3": _id3_multi,
    "vorbis": _vorbis_multi,
    "mp4": _mp4_multi,
    "apev2": _apev2_multi,
}


def read_multi(path: str) -> dict[str, list[str]]:
    """读取多值字段的原始列表，仅供快照/撤销使用。

    为什么不复用 read_record 的字符串：`"A; B"` 可能是两个值，也可能是
    一个内部含分号的单值——只有拿到原始列表才能无损还原。
    """
    engine = engine_for(path)
    if engine is None:
        return {}
    try:
        return _MULTI_READERS[engine](path)
    except Exception:
        return {}


def read_tags(path: str) -> dict:
    """读取 canonical 标签（不含封面二进制）。"""
    return read_record(path)[0]


def read_record(path: str) -> tuple[dict, bool]:
    """一次打开标签，同时返回字段与"是否有封面"，避免二次解析。"""
    engine = engine_for(path)
    if engine is None:
        raise TagError(f"不支持的格式：{Path(path).suffix}")
    rec = _READERS[engine](path)
    has_cover = bool(rec.pop("_cover", False))
    return rec, has_cover


def read_cover(path: str) -> Cover | None:
    engine = engine_for(path)
    if engine is None:
        return None
    try:
        return _COVER_READERS[engine](path)
    except Exception:
        return None


def write_tags(path: str, patch: dict, mode: str = "overwrite") -> None:
    """把 patch 写入文件。

    patch 的语义：
        键存在 + 值字符串  -> 覆盖（或补全，取决于 mode）
        键存在 + 值为 None -> 删除该字段
        键不存在           -> 保持原样，绝不触碰
    这是批量编辑安全的基石：只有用户实际填写的字段才会被改写。
    """
    if mode not in ("overwrite", "fill"):
        raise TagError(f"未知的写入模式：{mode!r}（只接受 overwrite / fill）")
    engine = engine_for(path)
    if engine is None:
        raise TagError(f"不支持的格式：{Path(path).suffix}")

    patch = dict(patch)
    # 封面 None / 空串 = 不改动；删除必须显式给 "remove"
    if patch.get("cover") in (None, ""):
        patch.pop("cover", None)
    # 音轨号 / 碟号里的 "/" 会被 ID3 当成 n/total 分隔符，静默改写语义
    for key in ("track", "disc"):
        value = patch.get(key)
        if value not in (None, "") and "/" in _as_single(value):
            raise TagError(f"{key} 不能包含 '/'（请分别填 {key} 与总数）")

    _WRITERS[engine](path, patch, mode)


def probe(path: str) -> dict:
    """音频技术信息，用于详情页展示（时长 / 码率 / 采样率 / 声道）。"""
    from mutagen import File as MutagenFile

    info: dict[str, Any] = {}
    try:
        audio = MutagenFile(path)
    except Exception as exc:  # 损坏文件不应让整个列表崩掉
        return {"error": str(exc)}
    if audio is None:
        return info
    stream = getattr(audio, "info", None)
    if stream is not None:
        info["length"] = round(float(getattr(stream, "length", 0) or 0), 2)
        info["bitrate"] = int(getattr(stream, "bitrate", 0) or 0)
        info["sample_rate"] = int(getattr(stream, "sample_rate", 0) or 0)
        info["channels"] = int(getattr(stream, "channels", 0) or 0)
    info["size"] = os.path.getsize(path)
    info["engine"] = engine_for(path)
    if engine_for(path) == "id3":
        try:
            info["id3_version"] = f"2.{ID3(path).version[1]}"
        except Exception:
            pass
    return info


def copy_file(src: str, dst: str) -> None:
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
