"""自定义字段落到生成的文档里：绑定占位符 / 追加补充条款。

这条链路最容易出的错是「页面上填了、文档里没有」——所以断言一律落在
生成后的 docx 文本上，而不是参数是否传进去。
"""

from __future__ import annotations

from pathlib import Path

import docx
import pytest

from tender_extractor.doc_generator import DEFAULT_TEMPLATE, generate_tender_document
from tender_extractor.models import ExtractionResult, FieldValue

requires_template = pytest.mark.skipif(
    not DEFAULT_TEMPLATE.exists(),
    reason="compiled 模板不存在，请先运行 scripts/prepare_template.py",
)


def _make_result(**templates: str) -> ExtractionResult:
    fields = {
        name: FieldValue(key=name, label=name, template=name, value=value)
        for name, value in templates.items()
    }
    return ExtractionResult(
        source={"name": "测试需求.docx"},
        meta={},
        fields=fields,
        structured={},
        validation={},
        cleaning={},
        review=[],
        audit={},
    )


def _document_text(path: Path) -> str:
    document = docx.Document(str(path))
    parts = [para.text for para in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.extend(para.text for para in cell.paragraphs)
    return "\n".join(parts)


@requires_template
def test_placeholder_field_fills_template(tmp_path: Path) -> None:
    generated = generate_tender_document(
        _make_result(),
        output_path=tmp_path / "占位符.docx",
        custom_fields=[
            {
                "key": "cf_1",
                "label": "采购人邮箱",
                "value": "a@b.com",
                "target": "placeholder",
                "placeholder": "采购人",
            }
        ],
    )

    text = _document_text(generated.output_path)
    assert "a@b.com" in text
    assert "采购人" not in generated.missing


@requires_template
def test_append_field_adds_section(tmp_path: Path) -> None:
    generated = generate_tender_document(
        _make_result(项目名称="某测试项目"),
        output_path=tmp_path / "补充条款.docx",
        custom_fields=[
            {"key": "cf_1", "label": "履约保证金", "value": "合同金额的5%", "target": "append"},
            {"key": "cf_2", "label": "空字段", "value": "   ", "target": "append"},
        ],
    )

    text = _document_text(generated.output_path)
    assert "补充条款" in text
    assert "履约保证金" in text and "合同金额的5%" in text
    assert "空字段" not in text, "空值行不应写进成稿"


@requires_template
def test_no_custom_fields_no_extra_section(tmp_path: Path) -> None:
    """模板正文自带「补充条款」字样，所以看文末而不是全文。"""
    generated = generate_tender_document(
        _make_result(项目名称="某测试项目"),
        output_path=tmp_path / "无补充.docx",
    )
    document = docx.Document(str(generated.output_path))
    paragraphs = [para.text.strip() for para in document.paragraphs if para.text.strip()]
    assert paragraphs[-1] != "补充条款"


@requires_template
def test_placeholder_field_does_not_override_manual_value(tmp_path: Path) -> None:
    generated = generate_tender_document(
        _make_result(项目名称="人工确认的项目名"),
        output_path=tmp_path / "优先级.docx",
        custom_fields=[
            {
                "key": "cf_1",
                "label": "重复项",
                "value": "自定义覆盖值",
                "target": "placeholder",
                "placeholder": "项目名称",
            }
        ],
    )

    text = _document_text(generated.output_path)
    assert "人工确认的项目名" in text
    assert "自定义覆盖值" not in text
