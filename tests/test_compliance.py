"""合规审查规则层测试：命中、否定语境、证据回链。

对应 DEVELOPMENT.md 第十一节「合规审查 v1」：只做确定性规则，不调模型。
"""

from __future__ import annotations

from tender_extractor.compliance import analyze_compliance
from tender_extractor.models import Block, Document


def _document(*texts: str) -> Document:
    """构造最小文档：每个字符串一段，block_id 依次为 b0、b1…。"""
    blocks = [
        Block(
            id=f"b{index}",
            kind="paragraph",
            text=text,
            raw=text,
            index=index,
            section="资格要求",
        )
        for index, text in enumerate(texts)
    ]
    return Document(path="sample.docx", title="样本", blocks=blocks)


def _rule_ids(report) -> set[str]:
    return {finding.rule_id for finding in report.findings}


def test_detects_brand_restriction():
    report = analyze_compliance(_document("投标产品必须为华为品牌。"))

    assert "brand_restriction" in _rule_ids(report)


def test_detects_regional_performance():
    report = analyze_compliance(_document("投标人须具有本市同类项目业绩。"))

    assert "regional_performance" in _rule_ids(report)


def test_detects_capital_threshold():
    report = analyze_compliance(_document("投标人注册资本不低于1000万元。"))

    assert "scale_condition" in _rule_ids(report)


def test_detects_local_service_requirement():
    report = analyze_compliance(_document("投标人须在本市设有售后服务机构。"))

    assert "local_service" in _rule_ids(report)


def test_detects_manufacturer_authorization():
    report = analyze_compliance(_document("投标人须提供生产厂家针对本项目的授权书。"))

    assert "manufacturer_authorization" in _rule_ids(report)


def test_ignores_negated_clause():
    report = analyze_compliance(_document("采购人不得限定品牌、商标或供应商。"))

    assert report.findings == []


def test_findings_carry_evidence():
    doc = _document("投标产品必须为华为品牌。")
    report = analyze_compliance(doc)

    finding = report.findings[0]
    assert finding.block_id == "b0"
    assert finding.quote
    assert finding.quote in doc.blocks[0].text


def test_counts_by_severity():
    report = analyze_compliance(
        _document("投标产品必须为华为品牌。", "投标人注册资本不低于1000万元。")
    )

    assert report.counts["high"] >= 1
    assert report.counts["medium"] >= 1
    assert report.to_dict()["findings"]
