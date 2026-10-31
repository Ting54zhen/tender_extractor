"""端到端测试：只用规则层，不依赖 Ollama。"""

from docx import Document as DocxDocument

from tender_extractor import Extractor, extract_document
from tender_extractor.models import FieldValue
from tender_extractor.validation import cross_validate


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
