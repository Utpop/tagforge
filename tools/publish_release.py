"""创建 GitHub Release 并上传 exe。

    python tools/publish_release.py

凭据复用 Git Credential Manager（不落盘）：git credential fill 取 token。
"""

from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "dist" / "TagForge.exe"
TAG = "v1.0.0"
REPO = "Utpop/tagforge"

BODY = """## TagForge 1.0.0

Mp3tag 风格的多格式音频标签管理器，Windows 桌面应用（Python + Tkinter）。
免安装单文件，双击即用。

### 下载

| 文件 | 说明 |
|---|---|
| `TagForge.exe` | 单文件免安装版，Windows 10 / 11 64 位 |

首次运行：双击 → 点左上角「扫描」选择音乐目录。也可把目录拖到 exe 上，或 `TagForge.exe "D:\\Music"`。

> TagForge 不建库、不索引，只处理你当场选择的目录，所以首次打开列表为空是正常的。

### 功能

- **表格内联编辑**：点单元格直接改，`Enter` 提交、`Tab` 连编、`Esc` 取消；改动先进暂存，`Ctrl+S` 才写盘
- **批量编辑**：勾选多个文件统一专辑 / 专辑艺术家 / 年份 / 流派 / 封面，支持「覆盖」与「仅补空」
- **从文件名生成标签**：`%albumartist%/%album%/%track:2% - %title%`，先预览后写入
- **按标签重命名**：保留目录结构、冲突检测、整批互换编号（01↔02）
- **增量写入**：只写填了内容的字段；换封面只动正面封面，封底与艺人照保留；换备注只动英文槽位
- **快照与撤销**：写盘前自动快照，单文件失败即回滚，整批可撤销，撤销本身也可撤销
- **高 DPI**：100%–200% 下版式等比缩放，不会字大框小

### 支持格式

ID3v2.3 / v2.4（mp3 / wav / aiff / dsf）、Vorbis Comments（flac / ogg / opus）、
iTunes atoms（m4a / m4b / mp4）、APEv2（ape / wv / mpc）

### 本版变更

- 新增应用图标：标题栏、任务栏、exe 文件图标统一；界面左上角 logo 同步替换
- 补上 `Tab` / `Shift+Tab` 连续编辑（此前文档写了但未实现）
- 从源码运行与打包版共用同一套资源路径解析
- README 重写

### 校验

```
SHA256: {sha}
大小:   {size} 字节（{mb:.1f} MB）
```

> 未签名。Windows SmartScreen 可能提示"未知发布者"，选择"更多信息 → 仍要运行"即可。
"""


def token() -> str:
    out = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True,
        env=dict(os.environ, GIT_TERMINAL_PROMPT="0"),
    ).stdout
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    raise SystemExit("未取到 GitHub 凭据")


def api(url: str, data: bytes | None = None, method: str = "GET",
        content_type: str = "application/json", auth: str = "") -> dict:
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {auth}")
    req.add_header("Accept", "application/vnd.github+json")
    if data is not None:
        req.add_header("Content-Type", content_type)
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def main() -> int:
    if not EXE.is_file():
        print(f"找不到 {EXE}，先执行打包")
        return 1

    import hashlib
    raw = EXE.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    body = BODY.format(sha=sha, size=len(raw), mb=len(raw) / 1048576)
    auth = token()

    # 已存在同名 release 就复用，避免重复创建
    try:
        rel = api(f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}", auth=auth)
        print("复用已有 release:", rel["html_url"])
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        payload = json.dumps({
            "tag_name": TAG,
            "name": "TagForge 1.0.0",
            "body": body,
            "draft": False,
            "prerelease": False,
        }).encode()
        rel = api(f"https://api.github.com/repos/{REPO}/releases", payload, "POST", auth=auth)
        print("已创建 release:", rel["html_url"])

    name = EXE.name
    existing = {a["name"]: a for a in rel.get("assets", [])}
    if name in existing:
        api(f"https://api.github.com/repos/{REPO}/releases/assets/{existing[name]['id']}",
            method="DELETE", auth=auth)
        print("已删除同名旧附件")

    upload = rel["upload_url"].split("{")[0] + f"?name={name}"
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    asset = api(upload, raw, "POST", content_type=mime, auth=auth)
    print("已上传:", asset["name"], f"{asset['size'] / 1048576:.1f} MB")
    print("下载地址:", asset["browser_download_url"])
    print("release:", rel["html_url"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
