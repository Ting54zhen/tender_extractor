"""分析标准招标模板：占位符清单、跨 run 分布、与 fields.json 的映射覆盖。

只读工具，用于开发招标文件生成器前确认模板结构。

用法：
    python scripts/analyze_template.py
"""

import json
import re
import sys
from pathlib import Path

import docx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = PROJECT_ROOT / "templates" / "广东省政府采购公开招标标准模板.docx"
FIELDS = PROJECT_ROOT / "src" / "tender_extractor" / "config" / "fields.json"
PLACEHOLDER = re.compile(r"\{([^{}]{1,30})\}")


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


def main() -> int:
    if not TEMPLATE.exists():
        print(f"NOT_FOUND: {TEMPLATE}")
        return 1
    document = docx.Document(str(TEMPLATE))
    found: dict[str, int] = {}
    cross_run = 0
    total = 0
    for para in iter_paragraphs(document):
        names = PLACEHOLDER.findall(para.text)
        if not names:
            continue
        run_texts = [run.text for run in para.runs]
        joined = "".join(run_texts)
        for name in names:
            total += 1
            found[name] = found.get(name, 0) + 1
            token = "{" + name + "}"
            if not any(token in rt for rt in run_texts) and token in joined:
                cross_run += 1

    print(f"模板占位符总数: {total}，去重后: {len(found)}")
    print(f"跨 run 占位符: {cross_run}")

    fields = json.loads(FIELDS.read_text(encoding="utf-8"))
    template_fields = {
        f["template"]: f["key"] for f in fields["fields"] if f.get("template")
    }
    print(f"fields.json 定义的模板字段: {len(template_fields)}")

    missing_in_fields = sorted(set(found) - set(template_fields))
    missing_in_template = sorted(set(template_fields) - set(found))
    print(f"\n模板有但 fields.json 未定义 ({len(missing_in_fields)}):")
    for name in missing_in_fields:
        print(f"  - {name}  (出现 {found[name]} 次)")
    print(f"\nfields.json 有但模板中未找到 ({len(missing_in_template)}):")
    for name in missing_in_template:
        print(f"  - {name} (key={template_fields[name]})")

    print("\n全部占位符（按出现次数降序）:")
    for name, count in sorted(found.items(), key=lambda kv: -kv[1]):
        print(f"  {name} x{count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
