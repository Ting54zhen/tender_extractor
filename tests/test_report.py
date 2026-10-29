"""审核报告渲染测试：过程信息里的回喂记录可能是 dict 而不是字段键。"""

from tender_extractor.models import ExtractionResult, FieldValue
from tender_extractor.report import _cell, _quote, render_markdown


def _result() -> ExtractionResult:
    field = FieldValue(
        key="purchaser",
        label="采购人",
        template="采购人",
        value="某某公司",
        confidence=0.8,
        status="ok",
        source="llm",
    )
    return ExtractionResult(
        source={"filename": "需求报告.docx", "title": "采购需求", "sha256": "abc", "size_bytes": 1},
        meta={
            "use_llm": True,
            "llm_available": True,
            "llm_provider": "ollama",
            "model": "qwen2.5:3b-instruct",
            "duration_ms": 12,
            "template_coverage": {"filled": 1, "total": 2, "rate": 0.5},
            "confidence_summary": {"fields_with_value": 1, "average_confidence": 0.8},
        },
        fields={"purchaser": field},
        structured={},
        validation={"ok": True, "errors": [], "warnings": [], "normalizations": []},
        cleaning={},
        review=[],
        audit={
            "repair_rounds": [
                {
                    "round": 1,
                    "requested": ["purchaser"],
                    "accepted": [],
                    "rejected": [
                        {"field": "purchaser", "reason": ["采购人疑似地址而非单位名称，请复核"]}
                    ],
                }
            ]
        },
        document_type={"is_tender": True, "score": 5, "threshold": 4, "confidence": 0.9},
    )


def test_report_renders_rejected_repair_round_with_reason():
    text = render_markdown(_result())
    assert "审计与过程信息" in text
    assert "采购人（采购人疑似地址而非单位名称，请复核）" in text


def test_cell_escapes_pipe_but_quote_does_not():
    """表格单元格必须转义竖线，引用正文不转义——这是 _cell/_quote 唯一的行为差异。"""
    assert _cell("甲|乙") == "甲\\|乙"
    assert _quote("甲|乙") == "甲|乙"


def test_cell_and_quote_share_truncation():
    assert _cell("x" * 10, 5) == "xxxx…"
    assert _quote("x" * 10, 5) == "xxxx…"
    assert _cell("x" * 5, 5) == "xxxxx"
    assert _quote("x" * 5, 5) == "xxxxx"


def test_cell_and_quote_flatten_newlines_and_default_dash():
    assert _cell("一\n二\r三") == "一 二 三"
    assert _quote("一\n二\r三") == "一 二 三"
    assert _cell(None) == "—"
    assert _quote("") == "—"
    assert _cell("  ") == "—"
    assert _quote("  ") == "—"
