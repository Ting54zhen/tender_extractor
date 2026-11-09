# -*- coding: utf-8 -*-
"""PyInstaller 打包脚本：把组件打成单文件 exe，双击即跑。

产物内嵌：抽取配置（config）、前端产物（frontend/dist -> 包内 static）、
compiled 招标模板（templates/compiled）。数据（SQLite/结果/设置覆盖）
运行时写在 exe 同目录 data/，便携可拷贝。

准备：开发机先在 frontend 执行 npm ci；本脚本自动构建并校验前端。
用法（在项目根目录，用 .venv 运行）：
    .venv/Scripts/python scripts/build_exe.py
产物：dist/招标需求解析.exe
"""

from __future__ import annotations

import os
import shutil
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


def build_frontend() -> None:
    """每次打包先重新构建，避免装入旧页面；依赖安装由开发者显式执行。"""
    frontend = ROOT / "frontend"
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm or not (frontend / "node_modules" / "vite").is_dir():
        _fail("请安装 Node.js，并先在 frontend 执行 npm ci；打包脚本不会自动联网安装前端依赖。")
    subprocess.check_call([npm, "run", "build"], cwd=frontend)
    subprocess.check_call([npm, "run", "check"], cwd=frontend)


def collect_data() -> list[str]:
    """只收集运行资源；Windows 分隔符为分号，其他系统为冒号。"""
    # 仅打包 dist，禁止把 npm 依赖、SFC 源码或开发配置装入运行时。
    pages = ("index", "fill", "templates", "projects", "settings", "company", "compare")
    resources = (
        "assets/VUE-LICENSE.txt", "assets/VITE-LICENSE.txt", "assets/THIRD-PARTY-NOTICES.txt",
        *(f"{page}.html" for page in pages),
    )
    for name in resources:
        if not (ROOT / "frontend" / "dist" / name).is_file():
            _fail("缺少前端构建产物：" + name + "；请先执行 npm run build。")
    pairs = [
        (SRC / "tender_extractor" / "config", "tender_extractor/config"),
        (ROOT / "frontend" / "dist", "tender_extractor/static"),
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
    build_frontend()
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
