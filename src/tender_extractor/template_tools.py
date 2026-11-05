"""模板文件工具：占位符识别、跨 run 改写、单花括号 → jinja2 转换。

为什么单独成模块：同一套逻辑有三个调用方——离线批量转换
（``scripts/prepare_template.py``）、模板库上传（在线转换）、模板体检与
预览（识别占位符）。放在 ``scripts/`` 下 web 层 import 不到，各写一份则
必然漂移，所以收进包内。

两个正则对应两种文件形态，不要混用：
- ``SOURCE_PLACEHOLDER``：源模板里的 ``{项目名称}``；
- ``JINJA_PLACEHOLDER``：compiled 模板里的 ``{{ d["项目名称"] }}``。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

import docx
from docx.text.paragraph import Paragraph

#: 源模板（单花括号）。前后紧邻花括号的不算，避免误伤已转换的 ``{{ }}``。
SOURCE_PLACEHOLDER = re.compile(r"(?<!\{)\{([^{}]{1,30})\}(?!\})")
#: compiled 模板（docxtpl 取值表达式）
JINJA_PLACEHOLDER = re.compile(r'\{\{\s*d\["([^"]+)"\]\s*\}\}')


def iter_paragraphs(document: Any) -> list[Paragraph]:
    """正文、表格、页眉页脚的段落；按 XML 元素去重。

    合并单元格会被 ``table.rows[].cells`` 重复枚举，不去重会让改写计数
    虚高，也会让同一段被反复处理。
    """
    result: list[Paragraph] = []
    seen: set[int] = set()

    def add(para: Paragraph) -> None:
        key = id(para._p)
        if key in seen:
            return
        seen.add(key)
        result.append(para)

    for para in document.paragraphs:
        add(para)
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    add(para)
    for section in document.sections:
        for container in (section.header, section.footer):
            for para in container.paragraphs:
                add(para)
    return result


def rewrite_paragraph(para: Paragraph, transform: Callable[[str], str]) -> bool:
    """按 ``transform`` 改写段落文本，返回是否改动。

    优先逐 run 改写，保留段内混排格式（加粗、上标不会被抹平）；只有当
    目标词被 Word 拆到多个 run（跨 run 占位符很常见）时才合并重写，此时
    只保留首个 run 的格式。
    """
    original = para.text
    if not original:
        return False
    expected = transform(original)
    if expected == original:
        return False

    per_run = [transform(run.text) for run in para.runs]
    if per_run and "".join(per_run) == expected:
        for run, text in zip(para.runs, per_run):
            run.text = text
        return True
    if not para.runs:
        return False
    para.runs[0].text = expected
    for run in para.runs[1:]:
        run.text = ""
    return True


def to_jinja(document: Any) -> int:
    """把占位符改成 docxtpl 语法，返回改写段落数。"""

    def transform(text: str) -> str:
        return SOURCE_PLACEHOLDER.sub(lambda m: '{{ d["%s"] }}' % m.group(1).strip(), text)

    return sum(1 for para in iter_paragraphs(document) if rewrite_paragraph(para, transform))


def normalize_placeholders(document: Any) -> int:
    """``{ 字段名 }`` → ``{字段名}``，返回改写段落数。

    带空格的占位符名与 fields.json 的键对不上，会让字段永远显示「待补充」。
    """

    def transform(text: str) -> str:
        return SOURCE_PLACEHOLDER.sub(lambda m: "{" + m.group(1).strip() + "}", text)

    return sum(1 for para in iter_paragraphs(document) if rewrite_paragraph(para, transform))


def source_placeholder_names(path: Path | str) -> set[str]:
    """源模板里的占位符名（已去空格）。"""
    document = docx.Document(str(path))
    return {
        name.strip()
        for para in iter_paragraphs(document)
        for name in SOURCE_PLACEHOLDER.findall(para.text)
    }


def compiled_placeholder_names(path: Path | str) -> set[str]:
    """compiled 模板里的占位符名。"""
    document = docx.Document(str(path))
    return {
        name
        for para in iter_paragraphs(document)
        for name in JINJA_PLACEHOLDER.findall(para.text)
    }


def compile_template(source: Path, target: Path) -> int:
    """源模板 → compiled，返回改写段落数。"""
    document = docx.Document(str(source))
    count = to_jinja(document)
    target.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(target))
    return count


def field_template_map() -> dict[str, str]:
    """占位符名 → fields.json 的字段 key。

    fields.json 是字段契约的唯一来源；模板体检与「未匹配占位符」都以此为准。
    """
    path = Path(__file__).resolve().parent / "config" / "fields.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    mapping: dict[str, str] = {}
    for item in config.get("fields") or []:
        template = str(item.get("template") or "").strip()
        if template:
            mapping[template] = str(item.get("key") or template)
    return mapping
