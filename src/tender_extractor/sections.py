"""章节切分。

招标需求报告通常不使用 Word 标题样式，而是用“一、”“（一）”“1.”这类中文编号。
这里按文本模式识别，不依赖样式，兼容绝大多数平台导出的 docx。
"""

from __future__ import annotations

import re

from .models import Block

_TOP_RE = re.compile(r"^(第[一二三四五六七八九十百]+[章节部分]|[一二三四五六七八九十]+[、.．])")
_SUB_RE = re.compile(r"^[（(][一二三四五六七八九十]+[）)]")
_NUM_RE = re.compile(r"^\d+[.．、]\s*\S")


def is_top_heading(text: str) -> bool:
    text = (text or "").strip()
    if not text or len(text) > 50:
        return False
    return bool(_TOP_RE.match(text))


def is_sub_heading(text: str) -> bool:
    text = (text or "").strip()
    if not text or len(text) > 40:
        return False
    if _SUB_RE.match(text):
        return True
    # “1.主要商务要求”是标题；“1.投标人应具备……条件，提供下列材料：”不是。
    if _NUM_RE.match(text) and not text.endswith(("：", ":", "。", "；", ";", "，", ",")):
        return True
    return False


def assign_sections(blocks: list[Block]) -> list[dict]:
    """就地写入 block.section / section_path，并返回章节大纲。"""
    current_top = "封面"
    current_sub: str | None = None
    outline: list[dict] = []
    for block in blocks:
        first_line = (block.text or "").split("\n", 1)[0].strip()
        if block.kind == "paragraph":
            if is_top_heading(first_line):
                current_top = first_line
                current_sub = None
                outline.append({"section": current_top, "start_index": block.index, "blocks": 0})
            elif is_sub_heading(first_line):
                current_sub = first_line

        block.section = current_top
        block.section_path = [current_top] + ([current_sub] if current_sub else [])

        if outline and block.kind == "table":
            outline[-1]["blocks"] += 1
        elif outline:
            outline[-1]["blocks"] += 1

    return outline
