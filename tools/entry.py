"""PyInstaller 打包入口。

打包命令见 tools/build_exe.py；这里只负责把项目根目录放进 sys.path 后启动 GUI。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tagforge.gui import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
