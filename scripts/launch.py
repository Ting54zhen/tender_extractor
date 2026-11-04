"""tender-extractor 一键启动器。

职责：准备 .venv 与依赖、检查模型服务、提供交互菜单，并把参数原样透传给 CLI。

用法（项目根目录）：
    start.bat                          进入交互菜单
    start.bat extract "文件.docx"      直接抽取，参数透传给 CLI
    start.bat setup [--pdf] [--dev]    只准备环境
    start.bat test                     运行测试
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV_DIR = ROOT / ".venv"
VENV_PY = VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
STAMP = VENV_DIR / ".launcher-ready"
DATA_DIR = ROOT / "data"
OUTPUT_DIR = DATA_DIR / "结果审核报告"
SAMPLE_DIR = DATA_DIR / "采购需求样本"
DOC_SUFFIXES = {".docx", ".pdf", ".txt"}
MIN_PYTHON = (3, 10)

USAGE = """招标需求解析组件 · 快速启动

  start.bat                            进入交互菜单
  start.bat extract "文件.docx"        抽取（完整链路）
  start.bat extract "文件.docx" --no-llm
  start.bat generate "文件.docx"       生成完整招标文件（.docx）
  start.bat health                     检查模型服务
  start.bat inspect "文件.docx"        只看解析结构
  start.bat setup [--pdf] [--dev]      只准备环境
  start.bat web                        打开网页界面（默认 127.0.0.1:8765）
  start.bat test                       运行测试
"""


def say(text: str = "") -> None:
    print(text, flush=True)


def fail(message: str, code: int = 1) -> None:
    say(f"[错误] {message}")
    raise SystemExit(code)


def confirm(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    try:
        raw = input(f"{prompt} [{hint}] ").strip().lower()
    except EOFError:
        return default
    if not raw:
        return default
    return raw in {"y", "yes", "是", "1"}


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run(args: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=str(ROOT),
        env=_env(),
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def check_python() -> None:
    if sys.version_info < MIN_PYTHON:
        fail(
            f"需要 Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 或更高版本，"
            f"当前为 {sys.version.split()[0]}"
        )


def _has_module(name: str) -> bool:
    return _run([str(VENV_PY), "-c", f"import {name}"], capture=True).returncode == 0


def _touch_stamp() -> None:
    STAMP.parent.mkdir(parents=True, exist_ok=True)
    STAMP.write_text("tender-extractor env ready\n", encoding="utf-8")


def venv_ok() -> bool:
    if not VENV_PY.is_file() or not STAMP.is_file():
        return False
    manifest = ROOT / "pyproject.toml"
    if manifest.is_file() and manifest.stat().st_mtime > STAMP.stat().st_mtime:
        return False
    return _run([str(VENV_PY), "-c", "import docx"], capture=True).returncode == 0


def ensure_env(with_pdf: bool = False, with_dev: bool = False) -> bool:
    if not VENV_PY.is_file():
        say("[环境] 首次运行，正在创建虚拟环境 .venv ...")
        if _run([sys.executable, "-m", "venv", str(VENV_DIR)]).returncode != 0:
            say("[错误] 创建虚拟环境失败，请确认 Python 安装完整")
            return False

    extras = []
    if with_pdf and not _has_module("pdfplumber"):
        extras.append("pdf")
    if with_dev and not _has_module("pytest"):
        extras.append("dev")
    if venv_ok() and not extras:
        return True

    target = ".[" + ",".join(extras) + "]" if extras else "."
    say(f"[环境] 安装依赖 {target}（首次需要下载 python-docx，请耐心等待）")
    code = _run(
        [
            str(VENV_PY),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-e",
            target,
        ]
    ).returncode
    if code != 0:
        say("[错误] 依赖安装失败，请检查网络或 pip 源配置后重试")
        return False
    _touch_stamp()
    return True


def _health_detail(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "无返回内容"
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text.splitlines()[0]
    provider = data.get("provider") or "ollama"
    model = data.get("model") or ""
    if data.get("available"):
        return f"{provider} / {model} 可用"
    reason = data.get("reason") or "服务不可达"
    return f"{provider} / {model} 不可用（{reason}）"


def check_llm() -> tuple[bool, str]:
    proc = _run([str(VENV_PY), "-m", "tender_extractor", "health"], capture=True)
    return proc.returncode == 0, _health_detail(proc.stdout or "")


def pick_document() -> Path | None:
    files: list[Path] = []
    if SAMPLE_DIR.is_dir():
        files = sorted(p for p in SAMPLE_DIR.iterdir() if p.suffix.lower() in DOC_SUFFIXES)
    if files:
        say("\n样本文件：")
        for idx, item in enumerate(files, 1):
            say(f"  {idx}) {item.name}")
    say("  0) 手动输入路径（可以直接把文件拖进本窗口）")

    raw = ask("\n请选择编号或输入文件路径: ").strip().strip('"').strip("'")
    if not raw:
        return None

    if raw.isdigit():
        idx = int(raw)
        if idx == 0:
            raw = ask("文件路径: ").strip().strip('"').strip("'")
            if not raw:
                return None
        elif 1 <= idx <= len(files):
            return files[idx - 1]
        else:
            say("[提示] 编号超出范围")
            return None

    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_file():
        say(f"[提示] 文件不存在：{path}")
        return None
    return path


def run_cli(args: list[str], report_output: bool = True) -> int:
    say("")
    code = _run([str(VENV_PY), "-m", "tender_extractor", *args]).returncode
    if code == 0:
        if report_output:
            say(f"\n[完成] 结果目录：{OUTPUT_DIR}")
    elif code == 2:
        say(f"\n[完成] 存在高风险复核项，请先看审核报告：{OUTPUT_DIR}")
    elif code == 3:
        say("\n[跳过] 输入不是招标/采购需求文件；如确需强制抽取，加 --allow-non-tender")
    else:
        say(f"\n[失败] 退出码 {code}")
    return code


def do_extract(use_llm: bool = True) -> int:
    path = pick_document()
    if path is None:
        return 1
    args = ["extract", str(path), "--output-dir", str(OUTPUT_DIR)]
    if not use_llm:
        args.append("--no-llm")
    else:
        ok, detail = check_llm()
        say(f"[模型] {detail}")
        if not ok and confirm("模型不可用，改用纯规则抽取？", default=True):
            args.append("--no-llm")
    return run_cli(args)


def do_inspect() -> int:
    path = pick_document()
    if path is None:
        return 1
    say("")
    return _run([str(VENV_PY), "-m", "tender_extractor", "inspect", str(path)]).returncode


def do_health() -> int:
    say("")
    return _run([str(VENV_PY), "-m", "tender_extractor", "health"]).returncode


def do_install_pdf() -> bool:
    if _has_module("pdfplumber"):
        say("[环境] PDF 支持已就绪，无需重复安装")
        return True
    say("[环境] 安装 PDF 依赖 pdfplumber ...")
    ok = ensure_env(with_pdf=True)
    say("[环境] PDF 支持已就绪" if ok else "[错误] PDF 依赖安装失败")
    return ok


def do_tests() -> int:
    if not ensure_env(with_dev=True):
        return 1
    say("")
    return _run([str(VENV_PY), "-m", "pytest"]).returncode


def do_web(extra: list[str] | None = None) -> int:
    """前台启动网页界面，Ctrl+C 停止后回到菜单。"""
    say("")
    say("[网页] 正在启动本地界面，浏览器会自动打开")
    say("[网页] 按 Ctrl+C 停止并返回菜单")
    proc = subprocess.Popen(
        [str(VENV_PY), "-m", "tender_extractor.web", *(extra or [])],
        cwd=str(ROOT),
        env=_env(),
    )
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        say("\n[网页] 已停止")
        return 0


def do_exe() -> int:
    """启动已打包的免 Python 版本（dist/招标需求解析.exe）。"""
    target = ROOT / "dist" / "招标需求解析.exe"
    if not target.is_file():
        say("")
        say("[提示] 还没有打包产物，先运行：.venv\\Scripts\\python scripts\\build_exe.py")
        return 1
    say("")
    say(f"[exe] 启动 {target.name}，浏览器会自动打开")
    proc = subprocess.Popen([str(target)], cwd=str(target.parent), env=_env())
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        say("\n[exe] 已停止")
        return 0


def interactive() -> int:
    say("")
    say("招标需求解析组件 · 快速启动")
    if not ensure_env():
        return 1
    _, detail = check_llm()
    say(f"[模型] {detail}")

    menu = (
        "\n请选择操作：\n"
        "  1) 抽取需求报告（规则 + 模型，完整链路）\n"
        "  2) 抽取需求报告（只跑规则，不调用模型）\n"
        "  3) 检查模型服务（Ollama / API）\n"
        "  4) 查看文档解析结构\n"
        "  5) 安装 PDF 支持\n"
        "  6) 运行测试\n"
        "  7) 打开网页界面（本机浏览器）\n"
        "  8) 启动打包版 exe（免 Python 演示）\n"
        "  0) 退出"
    )
    actions = {
        "1": lambda: do_extract(use_llm=True),
        "2": lambda: do_extract(use_llm=False),
        "3": do_health,
        "4": do_inspect,
        "5": do_install_pdf,
        "6": do_tests,
        "7": do_web,
        "8": do_exe,
    }

    while True:
        say(menu)
        choice = ask("\n请输入编号: ").strip()
        if choice in {"0", "q", "quit", "exit"}:
            say("已退出。")
            return 0
        action = actions.get(choice)
        if action is None:
            say("[提示] 请输入 0-8 之间的编号")
            continue
        action()
        ask("\n按回车返回菜单...")


def main(argv: list[str]) -> int:
    os.chdir(ROOT)
    check_python()

    if not argv:
        return interactive()

    command, rest = argv[0], argv[1:]
    if command in {"help", "-h", "--help"}:
        say(USAGE)
        return 0
    if command == "menu":
        return interactive()
    if command == "setup":
        ok = ensure_env(with_pdf="--pdf" in rest, with_dev="--dev" in rest)
        if ok:
            _, detail = check_llm()
            say(f"[模型] {detail}")
        return 0 if ok else 1
    if command == "test":
        return do_tests()
    if command == "web":
        if not ensure_env():
            return 1
        return do_web(rest)
    if command in {"extract", "inspect", "health", "generate"}:
        if not ensure_env():
            return 1
        return run_cli(argv, report_output=command == "extract")

    say(USAGE)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
