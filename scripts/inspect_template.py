"""检查标准招标模板结构与项目 git 状态的只读工具。

用法：
    python scripts/inspect_template.py
"""

import subprocess
import sys
from pathlib import Path

import docx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = Path(r"D:\微软浏览器下载文件\标准模版.docx")


def inspect_template(path: Path) -> None:
    """打印模板的段落/表格结构摘要，用于了解标准招标模板的形态。"""
    document = docx.Document(str(path))
    paras = document.paragraphs
    tables = document.tables
    print(f"paragraphs: {len(paras)}, tables: {len(tables)}")
    shown = 0
    for i, para in enumerate(paras):
        text = para.text.strip()
        if not text:
            continue
        if shown < 70:
            style = para.style.name if para.style else ""
            print(f"[{i}][{style}] {text[:55]}")
        shown += 1
    print(f"nonempty: {shown}")
    for i, table in enumerate(tables[:6]):
        try:
            head = [c.text.strip()[:10] for c in table.rows[0].cells] if table.rows else []
        except Exception:
            head = []
        print(f"table{i}: {len(table.rows)}x{len(table.columns)} {head}")


def check_git() -> None:
    """检查项目根目录是否已初始化 git 仓库。"""
    print("---GIT---")
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    output = (result.stdout + result.stderr).strip()
    print(output if output else "no git output")


def main() -> int:
    if not TEMPLATE.exists():
        print(f"NOT_FOUND: {TEMPLATE}")
        return 1
    inspect_template(TEMPLATE)
    check_git()
    return 0


if __name__ == "__main__":
    sys.exit(main())
