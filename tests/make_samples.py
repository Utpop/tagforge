"""生成测试音频（需要 ffmpeg）。用于验证标签读写与批量操作。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"

TRACKS = [
    ("Music/Daft Punk/Random Access Memories/01 - Give Life Back to Music.mp3", "mp3", 440),
    ("Music/Daft Punk/Random Access Memories/02 - The Game of Love.mp3", "mp3", 494),
    ("Music/Daft Punk/Random Access Memories/03 - Giorgio by Moroder.mp3", "mp3", 523),
    ("Music/Radiohead/In Rainbows/01 - 15 Step.flac", "flac", 587),
    ("Music/Radiohead/In Rainbows/02 - Bodysnatchers.flac", "flac", 659),
    ("Music/Radiohead/In Rainbows/03 - Nude.m4a", "m4a", 698),
    ("Music/Boards of Canada/Music Has the Right/01 - Wildlife Analysis.ogg", "ogg", 784),
    ("Music/Boards of Canada/Music Has the Right/02 - An Eagle in Your Mind.wav", "wav", 880),
]


def make(path: Path, fmt: str, freq: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    codec = {
        "mp3": ["-c:a", "libmp3lame", "-q:a", "6", "-id3v2_version", "3"],
        "flac": ["-c:a", "flac"],
        "m4a": ["-c:a", "aac", "-b:a", "96k"],
        "ogg": ["-c:a", "libvorbis", "-q:a", "2"],
        "wav": ["-c:a", "pcm_s16le"],
    }[fmt]
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=2",
        *codec, str(path),
    ]
    subprocess.run(cmd, check=True)


def main() -> int:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    for rel, fmt, freq in TRACKS:
        target = SAMPLES / rel
        if target.exists():
            target.unlink()
        make(target, fmt, freq)
        print("created", target.relative_to(ROOT))
    print(f"\n共 {len(TRACKS)} 个测试文件 -> {SAMPLES}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
