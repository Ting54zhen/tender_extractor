"""文档解析：把 docx（以及可选的 pdf/txt）变成有序的 Block 列表。

重点是保留“段落/表格在正文中的先后顺序”和表格的行列结构，后续规则和 LLM
都依赖这个结构回链证据，而不是把整篇文档拍平成一坨文本。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from .cleaning import clean_cell, clean_text
from .models import Block, Document
from .sections import assign_sections


def _paragraph_text(paragraph: Any) -> str:
    """从 XML 取段落文本，覆盖超链接、域代码等 python-docx 默认可能漏掉的内容。"""
    from docx.oxml.ns import qn

    parts: list[str] = []
    for node in paragraph._p.iter():
        if node.tag == qn("w:t"):
            parts.append(node.text or "")
        elif node.tag == qn("w:tab"):
            parts.append("\t")
        elif node.tag in (qn("w:br"), qn("w:cr")):
            parts.append("\n")
    return "".join(parts)


def _tc_text(tc: Any) -> str:
    """直接读 tc 下的文本节点，保留制表符/换行。"""
    from docx.oxml.ns import qn

    parts: list[str] = []
    for node in tc.iter():
        if node.tag == qn("w:t"):
            parts.append(node.text or "")
        elif node.tag == qn("w:tab"):
            parts.append("\t")
        elif node.tag in (qn("w:br"), qn("w:cr")):
            parts.append("\n")
    return "".join(parts)


def _row_values(row: Any) -> tuple[list[str], list[str]]:
    """按网格列读取一行。

    关键点：合并单元格不能只按 row.cells 去重，否则会丢列位。
    - gridSpan：横向合并，占多列，只保留首列文本，其余补空串；
    - vMerge continue：纵向合并的续行，文本视为空，由上一行承载。
    这样“序号/名称”纵向合并的子项行不会被误判成新的采购标的。
    """
    from docx.oxml.ns import qn

    clean_values: list[str] = []
    raw_values: list[str] = []
    for tc in row._tr.tc_lst:
        span = 1
        is_continuation = False
        tc_pr = tc.tcPr
        if tc_pr is not None:
            span_el = tc_pr.find(qn("w:gridSpan"))
            if span_el is not None:
                try:
                    span = max(1, int(span_el.get(qn("w:val")) or 1))
                except (TypeError, ValueError):
                    span = 1
            merge_el = tc_pr.find(qn("w:vMerge"))
            if merge_el is not None and merge_el.get(qn("w:val")) in (None, "continue"):
                is_continuation = True

        raw_text = "" if is_continuation else _tc_text(tc)
        raw_values.append(raw_text.replace("\n", " ").strip())
        clean_values.append(clean_cell(raw_text))
        if span > 1:
            raw_values.extend([""] * (span - 1))
            clean_values.extend([""] * (span - 1))
    return clean_values, raw_values


def _style_name(paragraph: Any) -> str:
    try:
        return paragraph.style.name or ""
    except Exception:
        return ""


def _guess_title(blocks: list[Block]) -> tuple[str, list[str]]:
    candidates: list[str] = []
    for block in blocks[:12]:
        if block.kind != "paragraph" or not block.text:
            continue
        first = block.text.split("\n", 1)[0].strip()
        if first:
            candidates.append(first)
    for text in candidates:
        if re.search(r"(采购需求报告|招标文件|采购文件|需求报告)", text):
            return text, candidates[:5]
    return (candidates[0] if candidates else ""), candidates[:5]


def read_docx(path: str | Path) -> Document:
    from docx import Document as DocxDocument
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn

    path = Path(path)
    doc = DocxDocument(str(path))

    blocks: list[Block] = []
    table_index = 0
    paragraph_count = 0
    empty_paragraphs = 0
    cell_count = 0
    placeholder_count = 0
    chars_raw = 0
    chars_clean = 0

    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph_count += 1
            paragraph = Paragraph(child, doc)
            raw = _paragraph_text(paragraph)
            text = clean_text(raw)
            if not text:
                empty_paragraphs += 1
                continue
            chars_raw += len(raw)
            chars_clean += len(text)
            placeholder_count += len(re.findall(r"\{[^{}]{1,60}\}", text))
            blocks.append(
                Block(
                    id=f"B{len(blocks)}",
                    kind="paragraph",
                    text=text,
                    raw=raw,
                    index=len(blocks),
                    style=_style_name(paragraph),
                )
            )

        elif child.tag == qn("w:tbl"):
            table = Table(child, doc)
            rows: list[list[str]] = []
            raw_rows: list[list[str]] = []
            for row in table.rows:
                cells, raw_cells = _row_values(row)
                if not cells:
                    continue
                rows.append(cells)
                raw_rows.append(raw_cells)
                cell_count += len(cells)
            if not rows:
                continue
            text = "\n".join(" | ".join(cells) for cells in rows)
            raw = "\n".join(" | ".join(cells) for cells in raw_rows)
            chars_raw += len(raw)
            chars_clean += len(text)
            placeholder_count += len(re.findall(r"\{[^{}]{1,60}\}", text))
            blocks.append(
                Block(
                    id=f"B{len(blocks)}",
                    kind="table",
                    text=text,
                    raw=raw,
                    index=len(blocks),
                    table_index=table_index,
                    rows=rows,
                )
            )
            table_index += 1

    outline = assign_sections(blocks)
    title, title_candidates = _guess_title(blocks)

    core = doc.core_properties
    metadata = {
        "title": core.title or "",
        "author": core.author or "",
        "subject": core.subject or "",
        "created": str(core.created) if core.created else "",
        "modified": str(core.modified) if core.modified else "",
        "title_candidates": title_candidates,
    }

    cleaning = {
        "paragraphs": paragraph_count,
        "empty_paragraphs": empty_paragraphs,
        "tables": table_index,
        "cells": cell_count,
        "placeholders_found": placeholder_count,
        "chars_raw": chars_raw,
        "chars_clean": chars_clean,
    }

    return Document(
        path=str(path),
        title=title,
        blocks=blocks,
        metadata=metadata,
        cleaning=cleaning,
        outline=outline,
    )


def read_pdf(path: str | Path) -> Document:
    """可选 PDF 支持。依赖 pdfplumber（MIT），未安装时给出明确提示。"""
    try:
        import pdfplumber  # type: ignore
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise RuntimeError(
            "读取 PDF 需要安装 pdfplumber：python -m pip install pdfplumber"
        ) from exc

    path = Path(path)
    blocks: list[Block] = []
    table_index = 0
    chars_raw = 0
    chars_clean = 0

    with pdfplumber.open(str(path)) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            for line in text.split("\n"):
                raw = line
                cleaned = clean_text(raw)
                if not cleaned:
                    continue
                chars_raw += len(raw)
                chars_clean += len(cleaned)
                blocks.append(
                    Block(
                        id=f"B{len(blocks)}",
                        kind="paragraph",
                        text=cleaned,
                        raw=raw,
                        index=len(blocks),
                    )
                )
            for table in page.extract_tables() or []:
                rows = [[clean_cell(cell or "") for cell in row] for row in table if row]
                if not rows:
                    continue
                text = "\n".join(" | ".join(cells) for cells in rows)
                chars_raw += len(text)
                chars_clean += len(text)
                blocks.append(
                    Block(
                        id=f"B{len(blocks)}",
                        kind="table",
                        text=text,
                        raw=text,
                        index=len(blocks),
                        table_index=table_index,
                        rows=rows,
                    )
                )
                table_index += 1

    outline = assign_sections(blocks)
    title, title_candidates = _guess_title(blocks)
    cleaning = {
        "paragraphs": sum(1 for b in blocks if b.kind == "paragraph"),
        "tables": table_index,
        "chars_raw": chars_raw,
        "chars_clean": chars_clean,
        "pages": len(pdf.pages) if hasattr(pdf, "pages") else None,
    }
    return Document(
        path=str(path),
        title=title,
        blocks=blocks,
        metadata={"title_candidates": title_candidates, "format": "pdf"},
        cleaning=cleaning,
        outline=outline,
    )


def read_text(path: str | Path) -> Document:
    path = Path(path)
    raw = path.read_text(encoding="utf-8", errors="ignore")
    blocks: list[Block] = []
    for line in raw.splitlines():
        text = clean_text(line)
        if not text:
            continue
        blocks.append(
            Block(id=f"B{len(blocks)}", kind="paragraph", text=text, raw=line, index=len(blocks))
        )
    outline = assign_sections(blocks)
    title, title_candidates = _guess_title(blocks)
    return Document(
        path=str(path),
        title=title,
        blocks=blocks,
        metadata={"title_candidates": title_candidates, "format": "text"},
        cleaning={
            "paragraphs": len(blocks),
            "chars_raw": len(raw),
            "chars_clean": sum(len(b.text) for b in blocks),
        },
        outline=outline,
    )


def read_document(path: str | Path) -> Document:
    suffix = Path(path).suffix.lower()
    if suffix == ".docx":
        return read_docx(path)
    if suffix == ".pdf":
        return read_pdf(path)
    if suffix in {".txt", ".md"}:
        return read_text(path)
    if suffix == ".doc":
        raise ValueError("暂不支持旧版 .doc，请先另存为 .docx")
    raise ValueError(f"不支持的文件格式：{suffix}")


def blocks_in_section(doc: Document, keywords: Iterable[str]) -> list[Block]:
    words = [w for w in keywords if w]
    if not words:
        return []
    return [
        block
        for block in doc.blocks
        if any(word in block.section or word in " ".join(block.section_path) for word in words)
    ]
