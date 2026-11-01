"""招标文件生成器：抽取结果 + 标准模板 → 完整招标文件（.docx）。

设计原则：

1. 模板占位符与 ``fields.json`` 的 ``template`` 属性一一对应；
2. 缺失字段不编造，默认以「【待补充：字段名】」显式标记，便于人工补齐
   （也可切换为留空）；
3. 基于 docxtpl（jinja2）渲染，compiled 模板由
   ``scripts/prepare_template.py`` 从 ``templates/`` 源模板生成。

典型用法::

    from tender_extractor import extract_document
    from tender_extractor.doc_generator import generate_tender_document

    result = extract_document("采购需求.docx")
    gen = generate_tender_document(result)
    print(gen.output_path, gen.fill_rate)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import docx
from docxtpl import DocxTemplate

from .models import ExtractionResult

PLACEHOLDER = re.compile(r'\{\{\s*d\["([^"]+)"\]\s*\}\}')
DEFAULT_TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "templates"
    / "compiled"
    / "广东省政府采购公开招标标准模板.docx"
)
MISSING_TEMPLATE = "【待补充：{name}】"

#: 模板中无法直接从抽取字段取值的占位符 → 派生来源字段（按优先级）
DERIVED_KEYS: dict[str, tuple[str, ...]] = {
    "采购需求": ("procurement_item", "procurement_target", "project_summary"),
}


@dataclass
class GenerationResult:
    """一次招标文件生成的结果摘要。"""

    output_path: Path
    template_path: Path
    total: int
    filled: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def fill_rate(self) -> float:
        """占位符填充率（0-1）。"""
        return len(self.filled) / self.total if self.total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_path": str(self.output_path),
            "template_path": str(self.template_path),
            "total": self.total,
            "filled": len(self.filled),
            "fill_rate": round(self.fill_rate, 4),
            "missing": self.missing,
        }


def _stringify(value: Any) -> str:
    """把字段值转成模板可用的字符串。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (list, tuple)):
        return "、".join(_stringify(item) for item in value if _stringify(item))
    return str(value).strip()


def _iter_paragraphs(document):
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


def template_names(template_path: Path) -> set[str]:
    """从 compiled 模板中提取全部占位符名称。"""
    document = docx.Document(str(template_path))
    names: set[str] = set()
    for para in _iter_paragraphs(document):
        names.update(PLACEHOLDER.findall(para.text))
    return names


def _resolve_output_path(result: ExtractionResult, output_path: Path | None) -> Path:
    if output_path is not None:
        return Path(output_path)
    source_name = str(result.source.get("name") or result.source.get("path") or "")
    stem = Path(source_name).stem if source_name else "招标文件"
    return Path("data") / "结果审核报告" / f"{stem}-招标文件.docx"


def generate_tender_document(
    result: ExtractionResult,
    template_path: Path | str | None = None,
    output_path: Path | str | None = None,
    missing_mode: str = "mark",
    overrides: dict[str, Any] | None = None,
) -> GenerationResult:
    """把抽取结果渲染成完整招标文件。

    Args:
        result: 抽取结果。
        template_path: compiled 模板路径，默认使用内置广东省标准模板。
        output_path: 输出 .docx 路径，默认写入 ``data/结果审核报告/``。
        missing_mode: 缺失字段的呈现方式，``mark``（默认，标记待补充）
            或 ``empty``（留空）。
        overrides: 用户人工确认/修改后的值，键为模板占位符名（如「项目名称」）。
            优先级高于抽取结果，用于「AI 建议 + 人工拍板」工作流。

    Returns:
        GenerationResult：输出路径、填充清单、缺失清单与填充率。

    Raises:
        FileNotFoundError: 模板文件不存在。
        ValueError: missing_mode 取值非法。
    """
    if missing_mode not in {"mark", "empty"}:
        raise ValueError(f"missing_mode 必须是 mark 或 empty，收到: {missing_mode!r}")

    template = Path(template_path) if template_path is not None else DEFAULT_TEMPLATE
    if not template.exists():
        raise FileNotFoundError(
            f"模板不存在: {template}（请先运行 scripts/prepare_template.py 生成 compiled 模板）"
        )

    names = template_names(template)
    values = {
        str(key).strip(): _stringify(value)
        for key, value in result.template_values().items()
    }
    if overrides:
        for name, value in overrides.items():
            key = str(name).strip()
            if key:
                values[key] = _stringify(value)

    for name, source_keys in DERIVED_KEYS.items():
        if name in names and not values.get(name):
            for key in source_keys:
                field_value = result.fields.get(key)
                text = _stringify(field_value.value) if field_value else ""
                if text:
                    values[name] = text
                    break

    missing = sorted(name for name in names if not values.get(name))
    context = {
        name: values.get(name)
        or ("" if missing_mode == "empty" else MISSING_TEMPLATE.format(name=name))
        for name in names
    }

    tpl = DocxTemplate(str(template))
    tpl.render({"d": context})

    target = _resolve_output_path(result, Path(output_path) if output_path else None)
    target.parent.mkdir(parents=True, exist_ok=True)
    tpl.save(str(target))

    return GenerationResult(
        output_path=target,
        template_path=template,
        total=len(names),
        filled=sorted(name for name in names if values.get(name)),
        missing=missing,
    )
