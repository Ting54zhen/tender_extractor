"""把单花括号占位符模板转换为 docxtpl（jinja2）可渲染模板。

转换规则：
    {占位符}  ->  {{ d["占位符"] }}

批量处理 templates/ 下的全部源模板（货物类、竞争性磋商等派生模板同样
走这里），新增模板不用再改脚本。转换逻辑本身在
``tender_extractor.template_tools``——模板库上传走的是同一份实现，两处
各写一份必然漂移。

产物写入 templates/compiled/，源模板保持不动。

用法：
    python scripts/prepare_template.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = PROJECT_ROOT / "templates"
TARGET_DIR = TEMPLATE_DIR / "compiled"

sys.path.insert(0, str(PROJECT_ROOT / "src"))

import docx  # noqa: E402

from tender_extractor.template_tools import (  # noqa: E402
    SOURCE_PLACEHOLDER,
    iter_paragraphs,
    to_jinja,
)


def convert(source: Path) -> int:
    """转换单个源模板，返回剩余未转换的占位符段落数。"""
    document = docx.Document(str(source))
    rewritten = to_jinja(document)
    target = TARGET_DIR / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(target))

    remaining = [
        para.text
        for para in iter_paragraphs(document)
        if SOURCE_PLACEHOLDER.search(para.text)
    ]
    print(f"[{source.name}] 改写段落 {rewritten} -> compiled/{target.name}")
    if remaining:
        print(f"  ! 剩余未转换占位符段落: {len(remaining)}")
        for text in remaining[:5]:
            print(f"    {text[:60]}")
    return len(remaining)


def main() -> int:
    sources = sorted(path for path in TEMPLATE_DIR.glob("*.docx") if path.is_file())
    if not sources:
        print(f"NOT_FOUND: {TEMPLATE_DIR}/*.docx")
        return 1
    failed = 0
    for source in sources:
        failed += convert(source)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
