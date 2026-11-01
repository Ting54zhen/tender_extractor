"""验证生成的招标文件：占位符是否全部渲染、待补充标记是否生效。

用法：
    python scripts/verify_generated.py [输出文件路径]
"""

import sys
from pathlib import Path

import docx

DEFAULT = Path("data") / "结果审核报告" / "E2E-招标文件.docx"


def iter_paragraphs(document):
    for para in document.paragraphs:
        yield para
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    yield para


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    if not target.exists():
        print(f"NOT_FOUND: {target}")
        return 1
    document = docx.Document(str(target))
    paragraphs = [p for p in iter_paragraphs(document) if p.text.strip()]

    leftover = [p.text for p in paragraphs if "{{" in p.text or "}}" in p.text]
    missing = [p.text for p in paragraphs if "【待补充" in p.text]
    tables = len(document.tables)

    print(f"文件: {target}")
    print(f"非空段落: {len(paragraphs)}，表格: {tables}")
    print(f"未渲染占位符: {len(leftover)}")
    for text in leftover[:3]:
        print(f"  ! {text[:70]}")
    print(f"待补充标记: {len(missing)}")

    print("\n--- 前 12 个非空段落 ---")
    for para in paragraphs[:12]:
        print(f"  {para.text[:70]}")

    print("\n--- 表格首行抽样 ---")
    for index, table in enumerate(document.tables[:4]):
        if not table.rows:
            continue
        head = [cell.text.strip()[:14] for cell in table.rows[0].cells[:5]]
        print(f"  表{index}: {len(table.rows)}x{len(table.columns)} {head}")
    return 0 if not leftover else 2


if __name__ == "__main__":
    sys.exit(main())
