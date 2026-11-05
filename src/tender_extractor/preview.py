"""生成前的结构化预览：把 compiled 模板解析成「段落 / 表格 / 片段」。

为什么不走「渲染成 docx 再转 PDF」：本机不一定装了 LibreOffice 或 Word，
而预览是基础功能，不能挂在可选依赖上；转一次要 2–10 秒，也撑不住输入
即刷新。这里只读 docx 结构、毫秒级返回 JSON，由前端套纸张样式渲染。

片段（segment）带占位符名，前端据此实现「点预览里的字段 → 跳回编辑区」。
样式是近似的：段落样式名在 Word 里常被存成 null3 这类丢失名称的形态，
所以「第X章 / 一、 / 1.」的文本特征比样式名更可靠。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from .template_tools import JINJA_PLACEHOLDER, compiled_placeholder_names

#: 缺失占位符的呈现方式，与 doc_generator.MISSING_TEMPLATE 保持一致
MISSING_TEMPLATE = "【待补充：{name}】"

_CHAPTER = re.compile(r"^第[一二三四五六七八九十百\d]+章")
_SECTION = re.compile(r"^[一二三四五六七八九十]+[、.]")
_CLAUSE = re.compile(r"^\d+(\.\d+)*[、.]")
_HEADING = re.compile(r"^(heading|标题)\s*([123])$", re.IGNORECASE)


def build_preview(
    template_path: Path | str,
    values: dict[str, Any] | None = None,
    missing_mode: str = "mark",
    custom_fields: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """把模板渲染成预览结构。

    Args:
        template_path: compiled 模板（含 ``{{ d["字段"] }}``）。
        values: 占位符名 → 当前值，键为模板占位符名。
        missing_mode: ``mark`` 显示「待补充」，``empty`` 留空。
        custom_fields: 自定义字段，``target=placeholder`` 的并入取值，
            ``target=append`` 的在文末生成「补充条款」表格。
    """
    document = docx.Document(str(template_path))
    named_values = _merge_custom_values(values, custom_fields)
    warnings: list[str] = []

    sections: list[dict[str, Any]] = []
    current: dict[str, Any] = {"title": "封面", "blocks": []}

    for kind, element in _iter_blocks(document):
        if kind == "paragraph":
            block = _paragraph_block(element, named_values, missing_mode)
            text = element.text.strip()
            style = _style_of(element, text)
            if style == "h1" and text:
                if current["blocks"]:
                    sections.append(current)
                current = {"title": text, "blocks": []}
            if block is not None:
                current["blocks"].append(block)
        else:
            block = _table_block(element, named_values, missing_mode)
            current["blocks"].append(block)

    if current["blocks"]:
        sections.append(current)

    appended = _custom_section(custom_fields)
    if appended is not None:
        sections.append(appended)

    names = compiled_placeholder_names(template_path)
    filled = sorted(name for name in names if str(named_values.get(name) or "").strip())
    missing = sorted(set(names) - set(filled))
    if not names:
        warnings.append("这套模板没有解析到占位符，预览内容不会随填写变化")

    return {
        "stats": {
            "total_placeholders": len(names),
            "filled": len(filled),
            "missing": len(missing),
        },
        "missing": missing,
        "sections": sections,
        "warnings": warnings,
    }


def _iter_blocks(document: Any):
    """按文档顺序产出段落与表格。

    ``document.paragraphs`` 与 ``document.tables`` 是两个独立列表，拼不回
    原始顺序；招标文件的表格夹在正文中间，顺序错乱会让预览完全读不懂。
    """
    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield "paragraph", Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield "table", Table(child, document)


def _style_of(para: Paragraph, text: str) -> str:
    try:
        name = (para.style.name or "").strip()
    except Exception:  # noqa: BLE001 - 异常样式名按正文处理
        name = ""
    if name.lower() == "title":
        return "title"
    matched = _HEADING.match(name)
    if matched:
        return "h" + matched.group(2)
    if _CHAPTER.match(text):
        return "h1"
    if _SECTION.match(text):
        return "h2"
    if _CLAUSE.match(text) and len(text) <= 40:
        return "h3"
    if para.alignment == WD_ALIGN_PARAGRAPH.CENTER and len(text) <= 30:
        return "title"
    return "body"


def _paragraph_block(
    para: Paragraph, values: dict[str, Any], missing_mode: str
) -> dict[str, Any] | None:
    text = para.text
    if not text.strip():
        # 空行在 Word 里有版面意义，保留但标注清楚
        return {"type": "paragraph", "style": "body", "segments": []}
    return {
        "type": "paragraph",
        "style": _style_of(para, text.strip()),
        "segments": _segments(text, values, missing_mode),
    }


def _segments(text: str, values: dict[str, Any], missing_mode: str) -> list[dict[str, str]]:
    """把一段文本切成「原文 / 有值 / 待补充」片段。"""
    if not text:
        return []
    parts = JINJA_PLACEHOLDER.split(text)
    segments: list[dict[str, str]] = []
    for index, part in enumerate(parts):
        if index % 2 == 1:
            name = part
            value = values.get(name)
            text_value = "" if value is None else str(value)
            if text_value.strip():
                segments.append({"kind": "value", "text": text_value, "name": name})
            elif missing_mode == "empty":
                segments.append({"kind": "missing", "text": "", "name": name})
            else:
                segments.append(
                    {
                        "kind": "missing",
                        "text": MISSING_TEMPLATE.format(name=name),
                        "name": name,
                    }
                )
        elif part:
            segments.append({"kind": "text", "text": part})
    return segments


def _table_block(
    table: Table, values: dict[str, Any], missing_mode: str
) -> dict[str, Any]:
    return {
        "type": "table",
        "rows": _table_rows(table, values, missing_mode),
    }


def _table_rows(
    table: Table, values: dict[str, Any], missing_mode: str
) -> list[list[dict[str, Any]]]:
    """按网格展开表格，合并单元格用 colspan / rowspan / skip 表达。

    不能直接用 ``row.cells``：合并后的单元格会被重复枚举，且跨行判断
    依赖 lxml 代理对象的身份，容易在垃圾回收后失效。直接读 tr/tc 更稳。
    """
    grid = _grid(table)
    rows: list[list[dict[str, Any]]] = []
    for row_index, cells in enumerate(grid):
        row: list[dict[str, Any]] = []
        column = 0
        for tc, span, continued in cells:
            if continued:
                row.append({"skip": True})
                column += span
                continue
            row.append(
                {
                    "colspan": span,
                    "rowspan": _rowspan(grid, row_index, column),
                    "segments": _segments(
                        _tc_text(tc, table), values, missing_mode
                    ),
                }
            )
            column += span
        rows.append(row)
    return rows


def _grid(table: Table) -> list[list[tuple[Any, int, bool]]]:
    """每行的 (tc 元素, 跨列数, 是否纵向合并的延续行)。"""
    rows: list[list[tuple[Any, int, bool]]] = []
    for tr in table._tbl.tr_lst:
        cells: list[tuple[Any, int, bool]] = []
        for tc in tr.tc_lst:
            span = 1
            continued = False
            tc_pr = tc.tcPr
            if tc_pr is not None:
                grid_span = tc_pr.find(qn("w:gridSpan"))
                if grid_span is not None:
                    try:
                        span = int(grid_span.get(qn("w:val")) or 1)
                    except (TypeError, ValueError):
                        span = 1
                vmerge = tc_pr.find(qn("w:vMerge"))
                if vmerge is not None:
                    continued = (vmerge.get(qn("w:val")) or "continue") != "restart"
            cells.append((tc, span, continued))
        rows.append(cells)
    return rows


def _rowspan(grid: list[list[tuple[Any, int, bool]]], row_index: int, column: int) -> int:
    span = 1
    for next_index in range(row_index + 1, len(grid)):
        cell = _cell_at(grid[next_index], column)
        if cell is None or not cell[2]:
            break
        span += 1
    return span


def _cell_at(cells: list[tuple[Any, int, bool]], column: int) -> tuple[Any, int, bool] | None:
    cursor = 0
    for cell in cells:
        width = cell[1]
        if cursor <= column < cursor + width:
            return cell
        cursor += width
    return None


def _tc_text(tc: Any, table: Table) -> str:
    return "\n".join(Paragraph(p, table).text for p in tc.findall(qn("w:p")))


def _merge_custom_values(
    values: dict[str, Any] | None, custom_fields: list[dict[str, Any]] | None
) -> dict[str, Any]:
    """绑定占位符的自定义字段并入取值；values 里已有的优先。"""
    merged: dict[str, Any] = dict(values or {})
    for item in custom_fields or []:
        if not isinstance(item, dict):
            continue
        if (item.get("target") or "append") != "placeholder":
            continue
        placeholder = str(item.get("placeholder") or "").strip()
        if placeholder and placeholder not in merged:
            merged[placeholder] = item.get("value")
    return merged


def _custom_cell(text: str) -> dict[str, Any]:
    return {
        "colspan": 1,
        "rowspan": 1,
        "segments": [{"kind": "text", "text": text}] if text else [],
    }


def _custom_section(custom_fields: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """「追加到文末」的自定义字段 → 补充条款表格，与生成结果保持一致。"""
    items = [
        item
        for item in custom_fields or []
        if isinstance(item, dict)
        and (item.get("target") or "append") == "append"
        and str(item.get("value") or "").strip()
    ]
    if not items:
        return None
    rows = [[_custom_cell("字段"), _custom_cell("内容")]]
    for item in items:
        rows.append(
            [
                _custom_cell(str(item.get("label") or "")),
                _custom_cell(str(item.get("value") or "")),
            ]
        )
    return {
        "title": "补充条款",
        "blocks": [{"type": "table", "rows": rows}],
    }
