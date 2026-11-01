"""把单花括号占位符模板转换为 docxtpl（jinja2）可渲染模板。

转换规则：
    {占位符}  ->  {{ d["占位符"] }}

跨 run 占位符：合并段落全文后整体重写，保留段落首个 run 的格式。
产物写入 templates/compiled/，源模板保持不动。

用法：
    python scripts/prepare_template.py
"""

import re
import sys
from pathlib import Path

import docx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE = PROJECT_ROOT / "templates" / "广东省政府采购公开招标标准模板.docx"
TARGET_DIR = PROJECT_ROOT / "templates" / "compiled"
TARGET = TARGET_DIR / SOURCE.name
PLACEHOLDER = re.compile(r"(?<!\{)\{([^{}]{1,30})\}(?!\})")


def to_jinja(match: re.Match[str]) -> str:
    """把 {占位符} 转为 Jinja2 dict 取值表达式（中文字符串键安全）。"""
    name = match.group(1).strip()
    return '{{ d["%s"] }}' % name


def iter_paragraphs(document):
    """遍历正文、表格单元格、页眉页脚中的所有段落。"""
    for para in document.paragraphs:
        yield para
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    yield para
    for section in document.sections:
        for container in (section.header, section.footer):
            for para in container.paragraphs:
                yield para


def rewrite_paragraph(para) -> bool:
    """重写包含占位符的段落；返回是否发生改写。"""
    full_text = para.text
    if not PLACEHOLDER.search(full_text):
        return False
    if not para.runs:
        print(f"[跳过] 段落无 run，无法写入: {full_text[:40]!r}")
        return False
    new_text = PLACEHOLDER.sub(to_jinja, full_text)
    para.runs[0].text = new_text
    for run in para.runs[1:]:
        run.text = ""
    return True


def main() -> int:
    if not SOURCE.exists():
        print(f"NOT_FOUND: {SOURCE}")
        return 1
    document = docx.Document(str(SOURCE))
    rewritten = sum(1 for para in iter_paragraphs(document) if rewrite_paragraph(para))
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    document.save(str(TARGET))
    print(f"改写段落数: {rewritten}")
    print(f"已生成: {TARGET}")
    remaining = [
        para.text for para in iter_paragraphs(document) if PLACEHOLDER.search(para.text)
    ]
    print(f"剩余未转换占位符段落: {len(remaining)}")
    for text in remaining[:5]:
        print(f"  ! {text[:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
