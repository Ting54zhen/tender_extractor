"""平台草稿适配层测试：字段码、枚举、项目包、标的结构。"""

from decimal import Decimal

from docx import Document as DocxDocument

from tender_extractor import Extractor
from tender_extractor.platform_adapter import recognize_to_draft, to_platform_draft


def _build_sample(path, method="公开招标"):
    doc = DocxDocument()
    doc.add_paragraph("某某职业技术学院2026年实训室设备采购需求报告")
    doc.add_paragraph("一、项目概况")
    doc.add_paragraph("项目名称：2026年智能制造实训室设备采购项目")
    doc.add_paragraph("采购单位：某某职业技术学院")
    doc.add_paragraph("监督单位：某某市财政局")
    doc.add_paragraph("招标组织形式：委托招标")
    doc.add_paragraph("采购单位联系人：张三")
    doc.add_paragraph("采购单位联系方式：020-88886666")
    doc.add_paragraph(f"采购方式：{method}")
    doc.add_paragraph("预算金额：人民币 1,366,800.00 元（大写：壹佰叁拾陆万陆仟捌佰元整）")
    doc.add_paragraph("合同履行期限：自合同签订之日起 60 日内完成供货、安装及调试。")

    doc.add_paragraph("二、采购标的")
    table = doc.add_table(rows=1, cols=6)
    header = ["序号", "设备名称", "技术参数", "数量", "单位", "金额（元）"]
    for i, text in enumerate(header):
        table.rows[0].cells[i].text = text
    for row in [
        ["1", "工业机器人实训台", "六轴，负载≥5kg", "2", "套", "600,000.00"],
        ["2", "机器视觉检测平台", "分辨率≥500万像素", "2", "套", "466,800.00"],
        ["3", "数字化实训管理系统", "支持不少于 60 个并发终端", "1", "套", "300,000.00"],
    ]:
        cells = table.add_row().cells
        for i, text in enumerate(row):
            cells[i].text = text

    doc.add_paragraph("三、主要商务要求")
    business = doc.add_table(rows=4, cols=2)
    rows = [
        ("商务要求", "内容"),
        ("标的提供的时间", "合同签订后 60 日内"),
        ("标的提供的地点", "某某市某某区学院路 1 号实训楼"),
        ("付款方式", "合同签订后支付预付款 30%（410,040.00 元），验收合格后支付尾款 70%（956,760.00 元）。"),
    ]
    for r, (label, value) in enumerate(rows):
        business.cell(r, 0).text = label
        business.cell(r, 1).text = value
    doc.save(str(path))


def _draft_for(path, method="公开招标"):
    _build_sample(path, method)
    extractor = Extractor(settings_override={"llm": {"enabled": False}})
    return to_platform_draft(extractor.extract(path, use_llm=False))


def test_platform_draft_matches_field_codes_and_structure(tmp_path):
    draft = _draft_for(tmp_path / "需求报告.docx")

    fields = {item["field_code"]: item for item in draft["fields"]}
    assert fields["project_name"]["value"] == "2026年智能制造实训室设备采购项目"
    assert fields["procurement_unit"]["value"] == "某某职业技术学院"
    assert fields["supervision_unit"]["value"] == "某某市财政局"
    assert fields["budget_amount"]["value"] == "1366800.00"  # 平台要求纯数字两位小数
    assert fields["purchase_mode"]["value"] == "PUBLIC_TENDER"
    assert fields["tender_org_form"]["value"] == "ENTRUSTED"
    assert fields["contact_person"]["value"] == "张三"
    assert fields["contact_phone"]["value"] == "020-88886666"
    assert "1,366,800.00" in fields["budget_amount"]["evidence"][0]["quote"]

    assert draft["status"] == "DRAFT"
    assert draft["project_id"] is None
    assert draft["packages"][0]["temporary_code"] == "P1"
    assert draft["packages"][0]["budget_amount"] == "1366800.00"

    subjects = draft["subjects"]
    assert [s["subject_name"] for s in subjects] == [
        "工业机器人实训台", "机器视觉检测平台", "数字化实训管理系统",
    ]
    first = {attr["name"]: attr["value"] for attr in subjects[0]["attributes"]}
    assert first == {"技术参数": "六轴，负载≥5kg", "数量": "2", "单位": "套", "金额": "600000.00"}
    assert subjects[0]["package_temporary_code"] == "P1"


def test_address_is_not_filled_with_purchaser_name(tmp_path):
    draft = _draft_for(tmp_path / "需求报告.docx")
    fields = {item["field_code"]: item for item in draft["fields"]}
    # 原文没有地址/交付地点时，不能把“采购单位”错当成地址。
    assert "link_address" not in fields or fields["link_address"]["value"] != "某某职业技术学院"


def test_unsupported_purchase_mode_becomes_warning(tmp_path):
    draft = _draft_for(tmp_path / "需求报告.docx", method="邀请招标")
    fields = {item["field_code"] for item in draft["fields"]}
    assert "purchase_mode" not in fields
    assert any("邀请招标" in warning for warning in draft["warnings"])


def test_payment_with_ratio_and_parenthesized_amount(tmp_path):
    path = tmp_path / "需求报告.docx"
    _build_sample(path)
    extractor = Extractor(settings_override={"llm": {"enabled": False}})
    result = extractor.extract(path, use_llm=False)
    payment = result.structured["payment"]

    assert payment["prepay_amount"] == Decimal("410040.00")
    assert payment["balance_amount"] == Decimal("956760.00")
    assert abs(float(payment["prepay_ratio"]) - 0.3) < 1e-9


def test_recognize_to_draft_rejects_non_tender(tmp_path):
    doc = DocxDocument()
    doc.add_paragraph("个人简历")
    doc.add_paragraph("姓名：张三")
    doc.add_paragraph("教育经历：某某大学计算机专业")
    doc.add_paragraph("工作经历：某某公司软件工程师，负责后端开发。")
    path = tmp_path / "简历.docx"
    doc.save(str(path))

    try:
        recognize_to_draft(path, use_llm=False)
    except Exception as exc:  # noqa: BLE001
        assert getattr(exc, "code", "") == "NOT_TENDER_DOCUMENT"
    else:  # pragma: no cover
        raise AssertionError("简历不应通过识别")
