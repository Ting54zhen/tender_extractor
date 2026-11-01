"""诊断指定字段的原文上下文（只读工具）。

在源文档中搜索关键词，打印所在段落/表格行及其上下文，
用于排查字段抽取错误。

用法：
    python scripts/debug_fields.py "84215123" "28866118" "项目联系" "质疑"
"""

import sys
from pathlib import Path

import docx

DEFAULT_DOC = (
    Path("data")
    / "采购需求样本"
    / "飞书20261028-2113162026年安全服务项目（保安、宿管）招标文件（2026051103）.docx"
)


def iter_paragraphs(document):
    """遍历正文、表格单元格、页眉页脚中的所有段落。"""
    for index, para in enumerate(document.paragraphs):
        yield f"正文[{index}]", para.text
    for t_index, table in enumerate(document.tables):
        for r_index, row in enumerate(table.rows):
            cells = [cell.text.strip() for cell in row.cells]
            yield f"表{t_index}[行{r_index}]", " | ".join(cells)
    for s_index, section in enumerate(document.sections):
        for container, name in ((section.header, "页眉"), (section.footer, "页脚")):
            for para in container.paragraphs:
                yield f"{name}[{s_index}]", para.text


def main() -> int:
    keywords = sys.argv[1:] or ["项目联系", "质疑"]
    doc_path = DEFAULT_DOC
    if not doc_path.exists():
        print(f"NOT_FOUND: {doc_path}")
        return 1
    document = docx.Document(str(doc_path))
    hits = 0
    for location, text in iter_paragraphs(document):
        if not text:
            continue
        matched = [kw for kw in keywords if kw in text]
        if not matched:
            continue
        hits += 1
        print(f"--- {location} 命中 {matched}")
        print(f"    {text[:260]}")
        if hits >= 40:
            print("（命中过多，已截断）")
            break
    print(f"\n总命中: {hits}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
