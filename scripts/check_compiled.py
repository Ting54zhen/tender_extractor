"""诊断 compiled 模板中的占位符转换结果（只读工具）。"""

import sys
from pathlib import Path

import docx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE = PROJECT_ROOT / "templates" / "广东省政府采购公开招标标准模板.docx"
TARGET = PROJECT_ROOT / "templates" / "compiled" / "广东省政府采购公开招标标准模板.docx"


def iter_paragraphs(document):
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


def main() -> int:
    for label, path in (("SOURCE", SOURCE), ("TARGET", TARGET)):
        if not path.exists():
            print(f"{label}: MISSING {path}")
            continue
        document = docx.Document(str(path))
        hits = 0
        print(f"=== {label} ===")
        for para in iter_paragraphs(document):
            if "商务分值" in para.text:
                hits += 1
                if hits <= 2:
                    print(f"FULL: {para.text!r}")
                    print(f"RUNS: {[r.text for r in para.runs]}")
            if "{%" in para.text or "%}" in para.text:
                print(f"JINJA_STMT: {para.text[:100]!r}")
        print(f"商务分值 段落数: {hits}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
