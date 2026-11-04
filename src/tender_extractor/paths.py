"""运行时路径解析：兼容源码运行与 PyInstaller 打包（onefile / onedir）。

约定：
- 打包后（``sys.frozen``）：可写数据放在 exe 同目录的 ``data/``，便携式、双击即跑；
- 源码运行：数据放在工程根目录 ``data/``（与 README 的数据目录约定一致）。
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """当前是否运行在 PyInstaller 打包产物中。"""
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """可写数据根目录：打包后为 exe 所在目录，源码运行为工程根目录。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def app_data_dir() -> Path:
    """本地数据目录 ``<root>/data``：SQLite 数据库、结果输出、用户设置都放这里。"""
    d = app_root() / "data"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        # 极端情况（只读目录）退回当前工作目录，保证服务可用
        d = Path.cwd() / "data"
        d.mkdir(parents=True, exist_ok=True)
    return d


def database_path() -> Path:
    """SQLite 数据库文件路径。"""
    return app_data_dir() / "tender.db"
