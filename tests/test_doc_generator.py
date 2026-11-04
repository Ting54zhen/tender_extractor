"""招标文件生成器单元测试：渲染、缺失标记、参数校验。"""

import docx
import pytest

from tender_extractor.doc_generator import (
    DEFAULT_TEMPLATE,
    generate_tender_document,
)
from tender_extractor.models import ExtractionResult, FieldValue

requires_template = pytest.mark.skipif(
    not DEFAULT_TEMPLATE.exists(),
    reason="compiled 模板不存在，请先运行 scripts/prepare_template.py",
)


def _make_result(**templates: str) -> ExtractionResult:
    """构造只含指定占位符值的抽取结果。"""
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


def _make_result_with_structured(structured: dict) -> ExtractionResult:
    """构造带结构化数据（采购标的/商务要求/★▲条款）的抽取结果。"""
    result = _make_result(项目名称="某单位信息化运维服务项目")
    result.structured = structured
    return result


def _document_text(path) -> str:
    document = docx.Document(str(path))
    parts = [para.text for para in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.extend(para.text for para in cell.paragraphs)
    return "\n".join(parts)


@requires_template
def test_generate_fills_values_and_marks_missing(tmp_path):
    result = _make_result(项目名称="某单位安保服务采购项目")
    generated = generate_tender_document(
        result, output_path=tmp_path / "招标文件.docx"
    )

    assert generated.output_path.exists()
    assert generated.total > 0
    assert "项目名称" in generated.filled
    assert "采购人" in generated.missing
    assert 0 < generated.fill_rate < 1

    text = _document_text(generated.output_path)
    assert "某单位安保服务采购项目" in text
    assert "【待补充：采购人】" in text
    assert "{{" not in text and "}}" not in text


@requires_template
def test_generate_empty_mode_leaves_blank(tmp_path):
    result = _make_result(项目名称="某单位安保服务采购项目")
    generated = generate_tender_document(
        result, output_path=tmp_path / "空缺版.docx", missing_mode="empty"
    )

    text = _document_text(generated.output_path)
    assert "某单位安保服务采购项目" in text
    assert "【待补充" not in text


@requires_template
def test_generate_overrides_win_over_ai_values(tmp_path):
    result = _make_result(项目名称="AI 抽取的项目名")
    generated = generate_tender_document(
        result,
        output_path=tmp_path / "覆盖版.docx",
        overrides={"项目名称": "人工确认的项目名", "采购人": "某人工填写单位"},
    )

    text = _document_text(generated.output_path)
    assert "人工确认的项目名" in text
    assert "AI 抽取的项目名" not in text
    assert "某人工填写单位" in text
    assert "采购人" in generated.filled
    assert "采购人" not in generated.missing


@requires_template
def test_generate_overrides_can_clear_value(tmp_path):
    result = _make_result(项目名称="AI 值")
    generated = generate_tender_document(
        result,
        output_path=tmp_path / "清空版.docx",
        overrides={"项目名称": ""},
    )

    text = _document_text(generated.output_path)
    assert "AI 值" not in text
    assert "【待补充：项目名称】" in text
    assert "项目名称" in generated.missing


@requires_template
def test_generate_rejects_invalid_missing_mode(tmp_path):
    result = _make_result(项目名称="某项目")
    with pytest.raises(ValueError):
        generate_tender_document(
            result, output_path=tmp_path / "x.docx", missing_mode="unknown"
        )


def test_generate_raises_when_template_missing(tmp_path):
    result = _make_result(项目名称="某项目")
    with pytest.raises(FileNotFoundError):
        generate_tender_document(
            result,
            template_path=tmp_path / "不存在.docx",
            output_path=tmp_path / "y.docx",
        )


@requires_template
def test_generate_fills_structured_tables(tmp_path):
    """第二章采购需求的商务要求表、标的清单与多附表由 structured 回填。"""
    result = _make_result_with_structured(
        {
            "business_requirements": {
                "标的提供的时间": "自合同签订之日起12个月",
                "标的提供的地点": "某单位院内",
                "付款方式": "一次性支付",
            },
            "procurement_items": [
                {"seq": "1", "name": "平台运维", "unit": "年", "quantity": 1.0,
                 "unit_price": "50000", "amount": "50000", "requirement": "7×24小时值守"},
                {"seq": "2", "name": "数据治理", "unit": "年", "quantity": 1.0,
                 "unit_price": "50000", "amount": "50000", "requirement": "提供数据质量周报"},
                {"seq": "3", "name": "安全服务", "unit": "年", "quantity": 1.0,
                 "unit_price": "50000", "amount": "50000", "requirement": "★漏洞扫描"},
            ],
            "star_clauses": [
                {"type": "★", "item_seq": "3", "item_name": "安全服务", "text": "须提供漏洞扫描报告"},
            ],
        }
    )
    generated = generate_tender_document(result, output_path=tmp_path / "结构化.docx")

    text = _document_text(generated.output_path)
    # 商务要求表
    assert "自合同签订之日起12个月" in text
    # 标的清单
    assert "平台运维" in text and "数据治理" in text and "安全服务" in text
    # 多附表标题
    assert "附表二：数据治理" in text and "附表三：安全服务" in text
    # ★ 条款进技术参数附表
    assert "须提供漏洞扫描报告" in text


@requires_template
def test_generate_applies_default_values(tmp_path):
    """编制规范必然字段（有效供应商家数等）用默认值填充，不再「待补充」。"""
    from tender_extractor.doc_generator import DEFAULT_TEMPLATE_VALUES

    result = _make_result(项目名称="某项目")
    generated = generate_tender_document(result, output_path=tmp_path / "默认值.docx")

    for name in DEFAULT_TEMPLATE_VALUES:
        assert name not in generated.missing

    text = _document_text(generated.output_path)
    assert "本采购包不接受联合体投标" in text
    assert "采购包1：总价" in text
    assert "采购包1：3家" in text
