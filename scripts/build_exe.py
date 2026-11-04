# -*- coding: utf-8 -*-
"""PyInstaller 打包脚本：把组件打成单文件 exe，双击即跑。

产物内嵌：抽取配置（config）、前端页面（frontend -> 包内 static）、
compiled 招标模板（templates/compiled）。数据（SQLite/结果/设置覆盖）
运行时写在 exe 同目录 data/，便携可拷贝。

用法（在项目根目录，用 .venv 运行）：
    .venv/Scripts/python scripts/build_exe.py
产物：dist/招标需求解析.exe
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
APP_NAME = "招标需求解析"
ENTRY = ROOT / "run_web.py"


def _fail(message):
    print("[build] " + message)
    raise SystemExit(1)


def ensure_pyinstaller():
    try:
        import PyInstaller  # noqa: F401
        return
    except ImportError:
        pass
    print("[build] 未安装 PyInstaller，正在安装…")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller>=6.0"])


def collect_data():
    "--add-data 参数：源路径<分隔符>包内路径；Windows 用 `;`、macOS/Linux 用 `:`"
    pairs = [
        (SRC / "tender_extractor" / "config", "tender_extractor/config"),
        (ROOT / "frontend", "tender_extractor/static"),
        (ROOT / "templates" / "compiled", "templates/compiled"),
    ]
    args = []
    for src, dest in pairs:
        if not src.is_dir():
            _fail("缺少打包资源目录：" + str(src))
        args += ["--add-data", src.__str__() + os.pathsep + dest]
    return args


def hidden_imports():
    "函数内延迟 import 的模块，显式声明避免漏包。"
    mods = [
        "tender_extractor.bid_extract",
        "tender_extractor.bid_writer",
        "tender_extractor.compliance_matcher",
        "tender_extractor.company_profile",
        "tender_extractor.graph_builder",
        "tender_extractor.graph_queries",
        "tender_extractor.relation_extract",
        "docxtpl",
        "docx",
    ]
    try:
        import pdfplumber  # noqa: F401
        mods.append("pdfplumber")
    except ImportError:
        print("[build] 未安装 pdfplumber，exe 将不支持 PDF")
    args = []
    for mod in mods:
        args += ["--hidden-import", mod]
    return args


def main():
    if not ENTRY.is_file():
        _fail("缺少入口文件：" + str(ENTRY))
    ensure_pyinstaller()

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile", "--console",
        "--name", APP_NAME,
        *collect_data(),
        *hidden_imports(),
        "--specpath", str(ROOT),
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        str(ENTRY),
    ]
    print("[build] 运行 PyInstaller…")
    subprocess.check_call(cmd)
    product = ROOT / "dist" / (APP_NAME + ".exe")
    if not product.is_file():
        _fail("打包结束但未找到产物，请检查上方日志")
    print("[build] 完成：" + str(product))
    print("[build] 使用说明：把 exe 拷到任意目录双击运行；数据落在同目录 data/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
