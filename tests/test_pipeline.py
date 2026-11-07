"""端到端测试：只用规则层，不依赖 Ollama。"""
from __future__ import annotations

from pathlib import Path

from docx import Document as DocxDocument

from tender_extractor import Extractor, extract_document
from tender_extractor.models import FieldValue
from tender_extractor.validation import cross_validate


def test_progress_follows_real_pipeline_stages(tmp_path: Path) -> None:
    """规则与证据处理应驱动进度；引擎不报告代表 Web 已落盘的 100%。"""
    path = tmp_path / "需求报告.docx"
    _build_requirement_docx(path)
    events: list[tuple[int, str]] = []
    extractor = Extractor(settings_override={"llm": {"enabled": False}, "review": {"enabled": False}})
    result = extractor.extract(path, use_llm=False, on_progress=lambda percent, stage: events.append((percent, stage)))
    assert result.fields["project_name"].value == "测试项目（一期）"
    assert [percent for percent, _ in events] == [5, 15, 25, 35, 65, 75, 85, 90]
    assert all(stage for _, stage in events)


def test_invalid_document_stops_before_rule_progress(tmp_path: Path) -> None:
    """跳过非采购文本时不能显示模型或证据处理阶段已执行。"""
    path = tmp_path / "无关文本.txt"
    path.write_text("这是一份个人简历，个人爱好是读书。", encoding="utf-8")
    events: list[int] = []
    result = Extractor().extract(path, use_llm=False, on_progress=lambda percent, stage: events.append(percent))
    assert result.meta["extraction_skipped"]
    assert events == [5, 15, 90]


def _build_requirement_docx(path):
    doc = DocxDocument()
    doc.add_paragraph("某某单位采购需求报告")

    doc.add_paragraph("一、项目概况")
    doc.add_paragraph("项目名称：测试项目（一期）")
    doc.add_paragraph("采购方式：公开招标")
    project = doc.add_table(rows=2, cols=3)
    for idx, value in enumerate(["采购内容", "采购预算", "合同履行期限"]):
        project.cell(0, idx).text = value
    for idx, value in enumerate(
        ["测试项目（一期）", "100,000.00元", "2026年1月1日至2026年12月31日"]
    ):
        project.cell(1, idx).text = value

    doc.add_paragraph("二、采购标的")
    items = doc.add_table(rows=3, cols=7)
    header = ["序号", "服务项目名称", "服务项目要求", "数量", "单位", "单价", "金额"]
    for idx, value in enumerate(header):
        items.cell(0, idx).text = value
    for idx, value in enumerate(
        ["1", "平台运维", "★7×24小时响应；▲提供月度报告", "2", "年", "50,000", "100,000"]
    ):
        items.cell(1, idx).text = value
    for idx, value in enumerate(["合计", "", "", "", "", "", "100,000.00"]):
        items.cell(2, idx).text = value

    doc.add_paragraph("三、主要商务要求")
    business = doc.add_table(rows=6, cols=2)
    rows = [
        ["商务要求", "内容"],
        ["标的提供的时间", "2026年1月1日"],
        ["标的提供的地点", "某某市某某路1号"],
        ["付款方式", "1期：支付预付款30,000元；2期：支付尾款70,000元。"],
        ["验收要求", "按合同约定验收"],
        ["履约保证金", "不收取"],
    ]
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            business.cell(r, c).text = value

    doc.add_paragraph("四、款项支付")
    doc.add_paragraph("合同签订后按进度支付。")

    doc.save(str(path))


def test_rule_only_extraction(tmp_path):
    path = tmp_path / "需求报告.docx"
    _build_requirement_docx(path)

    extractor = Extractor(settings_override={"llm": {"enabled": False}})
    result = extractor.extract(path, use_llm=False)
    data = result.to_dict()

    assert data["validation"]["errors"] == []
    assert data["meta"]["llm_available"] is False

    fields = data["fields"]
    assert fields["project_name"]["value"] == "测试项目（一期）"
    assert fields["budget_amount"]["value"] == "100,000.0000元"
    assert fields["contract_period"]["value"] == "2026年1月1日至2026年12月31日"
    assert fields["purchaser"]["value"] == "某某单位"
    assert fields["purchaser"]["status"] == "inferred"

    items = data["structured"]["procurement_items"]
    assert len(items) == 1
    assert items[0]["name"] == "平台运维"
    assert items[0]["quantity"] == 2
    assert items[0]["unit"] == "年"
    assert float(items[0]["total_amount"]) == 100000.0

    payment = data["structured"]["payment"]
    assert float(payment["prepay_amount"]) == 30000.0
    assert float(payment["balance_amount"]) == 70000.0
    assert abs(payment["prepay_ratio"] - 0.3) < 1e-9

    assert len(data["structured"]["star_clauses"]) == 2

    # 模板占位符映射：平台按中文占位符名取值
    template_values = data["template_values"]
    assert template_values["项目名称"] == "测试项目（一期）"
    assert template_values["预算金额"] == "100,000.0000元"

    # 采购标的清单：标的表逐项成清单，且不因重排格式被判「未在原文定位」
    target_list = fields["procurement_target_list"]
    assert target_list["status"] == "ok"
    assert "1. 平台运维" in target_list["value"]
    assert "数量2年" in target_list["value"]
    assert "金额100,000.00元" in target_list["value"]
    assert "7×24小时响应" in target_list["value"]  # 要求摘要保留

    # 实质性条款清单：★▲ 条款从结构化结果承接为可见字段
    star = fields["star_clauses_summary"]
    assert star["status"] == "ok"
    assert "[★]" in star["value"]
    assert "7×24小时响应" in star["value"]
    assert template_values["采购人"] == "某某单位"


def _build_scoring_docx(path):
    doc = DocxDocument()
    doc.add_paragraph("某某单位采购需求报告")
    doc.add_paragraph("项目名称：评分表测试项目")
    doc.add_paragraph("预算金额：100,000.00元")
    doc.add_paragraph("三、评审程序")
    table = doc.add_table(rows=3, cols=3)
    table.cell(0, 0).text = "评审因素"
    table.cell(0, 2).text = "评审标准"
    table.cell(1, 0).text = "分值构成"
    table.cell(1, 2).text = "商务部分12.0分技术部分73.0分综合信用分5.0分报价得分10.0分"
    table.cell(2, 0).text = "技术部分"
    table.cell(2, 2).text = "重要技术指标响应情况 (4.0分)"
    doc.save(str(path))


def test_multi_column_scoring_table_is_read(tmp_path):
    """三列评分表不能被当成“非标签值表”整表跳过。"""
    path = tmp_path / "评分表.docx"
    _build_scoring_docx(path)

    extractor = Extractor(settings_override={"llm": {"enabled": False}})
    fields = extractor.extract(path, use_llm=False).to_dict()["fields"]

    assert float(fields["business_score"]["value"]) == 12.0
    assert float(fields["tech_score"]["value"]) == 73.0
    assert float(fields["credit_score"]["value"]) == 5.0
    assert float(fields["price_score"]["value"]) == 10.0
    assert fields["tech_review_item_1"]["value"] == "重要技术指标响应情况"
    assert float(fields["tech_review_score_1"]["value"]) == 4.0


def _build_notice_table_docx(path):
    doc = DocxDocument()
    doc.add_paragraph("某某单位采购需求报告")
    doc.add_paragraph("项目名称：资料表测试项目")
    doc.add_paragraph("预算金额：100,000.00元")
    doc.add_paragraph("二、须知前附表")
    table = doc.add_table(rows=4, cols=3)
    rows = [
        ["序号", "条款名称", "内容及要求"],
        ["1", "报价形式", "采购包1：折扣价"],
        ["2", "有效供应商家数", "采购包1：3家"],
        ["3", "代理服务费", "向中标人收取"],
    ]
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            table.cell(r, c).text = value
    doc.save(str(path))


def test_notice_table_label_value_columns(tmp_path):
    """“序号 | 条款名称 | 内容及要求”三列资料表要按列取值。"""
    path = tmp_path / "须知前附表.docx"
    _build_notice_table_docx(path)

    extractor = Extractor(settings_override={"llm": {"enabled": False}})
    fields = extractor.extract(path, use_llm=False).to_dict()["fields"]

    assert fields["price_method"]["value"] == "折扣价"
    assert fields["valid_supplier_count"]["value"] == 3
    assert fields["agency_fee"]["value"] == "向中标人收取"


def test_budget_cross_check_uses_four_decimal_precision():
    field_defs = {
        "budget_amount": {
            "key": "budget_amount",
            "label": "预算金额",
            "template": "预算金额",
            "type": "money",
            "precision": 4,
        }
    }
    fields = {
        "budget_amount": FieldValue(
            key="budget_amount",
            label="预算金额",
            template="预算金额",
            value="100,000.0000元",
        )
    }

    # 差异 0.00005 小于 4 位小数的量化单位，不应报错。
    errors, _ = cross_validate(
        fields,
        {"procurement_items": [{"total_amount": "100000.00005"}]},
        field_defs,
    )
    assert errors == []

    # 差异 0.001 超过 0.0001，应报不一致，并且预算按 4 位小数展示。
    errors, _ = cross_validate(
        fields,
        {"procurement_items": [{"total_amount": "100000.001"}]},
        field_defs,
    )
    assert errors and errors[0]["code"] == "ITEM_TOTAL_MISMATCH"
    assert "100,000.0000元" in errors[0]["message"]


def _build_compliance_docx(path):
    doc = DocxDocument()
    doc.add_paragraph("某某单位采购需求报告")
    doc.add_paragraph("项目名称：合规测试项目")
    doc.add_paragraph("预算金额：100,000.00元")
    doc.add_paragraph("二、资格要求")
    doc.add_paragraph("投标人注册资本不低于1000万元。")
    doc.save(str(path))


def test_extraction_includes_compliance_findings(tmp_path):
    """合规审查随抽取结果一起产出，命中项必须能回链到原文块。"""
    path = tmp_path / "合规.docx"
    _build_compliance_docx(path)

    extractor = Extractor(settings_override={"llm": {"enabled": False}})
    data = extractor.extract(path, use_llm=False).to_dict()

    findings = data["compliance"]["findings"]
    assert findings
    assert findings[0]["rule_id"] == "scale_condition"
    assert findings[0]["block_id"]
    assert findings[0]["quote"] in "投标人注册资本不低于1000万元。"


def test_extract_document_helper(tmp_path):
    path = tmp_path / "需求报告.docx"
    _build_requirement_docx(path)

    result = extract_document(
        path,
        use_llm=False,
        settings_override={"llm": {"enabled": False}},
    )

    assert isinstance(result.template_values(), dict)
    assert "项目名称" in result.template_values()
    assert result.missing_templates()


def _build_delivery_docx(path, delivery_place):
    doc = DocxDocument()
    doc.add_paragraph("某某学院采购需求报告")
    doc.add_paragraph("一、项目概况")
    doc.add_paragraph("项目名称：某项目")
    doc.add_paragraph("采购方式：公开招标")
    doc.add_paragraph("五、采购项目交付或实施的时间和地点")
    doc.add_paragraph(f"自合同签订之日起至2025年12月31日止，于{delivery_place}交付。")
    doc.save(str(path))


def test_purchaser_address_inferred_from_delivery(tmp_path):
    """交付地点明确是采购人单位时，回退推断采购人地址。"""
    path = tmp_path / "交付地址.docx"
    _build_delivery_docx(path, "某某市某某路1号某某学院")

    extractor = Extractor(settings_override={"llm": {"enabled": False}})
    fields = extractor.extract(path, use_llm=False).to_dict()["fields"]

    address = fields["purchaser_address"]
    assert address["value"] == "某某市某某路1号某某学院"
    assert address["source"] == "inferred"


def test_purchaser_address_not_inferred_from_third_party_place(tmp_path):
    """交付地点不含采购人单位/机构词时，不做推断，避免误判第三方实施地点。"""
    path = tmp_path / "第三方地点.docx"
    _build_delivery_docx(path, "某某市某某路1号")

    extractor = Extractor(settings_override={"llm": {"enabled": False}})
    fields = extractor.extract(path, use_llm=False).to_dict()["fields"]

    address = fields["purchaser_address"]
    assert address["value"] in (None, "")
    assert address["status"] == "missing"
