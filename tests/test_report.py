"""审核报告渲染测试：过程信息里的回喂记录可能是 dict 而不是字段键。"""

from tender_extractor.models import ExtractionResult, FieldValue
from tender_extractor.report import render_markdown


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
