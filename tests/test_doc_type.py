"""文档类型前置校验测试：非招标文件应跳过抽取并给出提示。"""

import json

from docx import Document as DocxDocument

from tender_extractor import Extractor
from tender_extractor.cli import main
from tender_extractor.doc_type import classify_document
from tender_extractor.document import read_document
from tender_extractor.report import render_markdown


def _build_tender_docx(path):
    doc = DocxDocument()
    doc.add_paragraph("某某单位采购需求报告")

    doc.add_paragraph("一、项目概况")
    doc.add_paragraph("项目名称：测试项目（一期）")
    doc.add_paragraph("采购方式：公开招标")

    doc.add_paragraph("二、采购标的")
    items = doc.add_table(rows=2, cols=7)
    header = ["序号", "服务项目名称", "服务项目要求", "数量", "单位", "单价", "金额"]
    for idx, value in enumerate(header):
        items.cell(0, idx).text = value
    for idx, value in enumerate(
        ["1", "平台运维", "★7×24小时响应", "2", "年", "50,000", "100,000"]
    ):
        items.cell(1, idx).text = value

    doc.add_paragraph("三、主要商务要求")
    doc.add_paragraph("付款方式：合同签订后支付预付款 30%。")
    doc.add_paragraph("四、合同条款")
    doc.add_paragraph("知识产权归采购人所有。")
    doc.save(str(path))


def _build_resume_docx(path):
    doc = DocxDocument()
    doc.add_paragraph("个人简历")
    doc.add_paragraph("姓名：张三")
    doc.add_paragraph("求职意向：后端开发工程师")
    doc.add_paragraph("教育经历：某某大学计算机科学与技术专业，本科，2016-2020。")
    doc.add_paragraph("工作经历：某科技公司软件工程师，负责内部系统开发与维护。")
    doc.add_paragraph("专业技能：Python、Java、数据库、Linux 运维。")
    doc.add_paragraph("自我评价：工作认真负责，具备良好的团队协作和沟通能力。")
    doc.save(str(path))


def test_tender_document_is_accepted(tmp_path):
    path = tmp_path / "需求报告.docx"
    _build_tender_docx(path)

    doc_type = classify_document(read_document(path))
    assert doc_type.is_tender is True
    assert doc_type.score >= doc_type.threshold
    assert "项目概况" in doc_type.signals["sections"]

    extractor = Extractor(settings_override={"llm": {"enabled": False}})
    data = extractor.extract(path, use_llm=False).to_dict()
    assert data["meta"]["extraction_skipped"] is False
    assert data["document_type"]["is_tender"] is True
    assert data["fields"]["project_name"]["value"] == "测试项目（一期）"


def test_resume_is_rejected_without_extraction(tmp_path):
    path = tmp_path / "个人简历.docx"
    _build_resume_docx(path)

    extractor = Extractor(settings_override={"llm": {"enabled": False}})
    result = extractor.extract(path, use_llm=False)
    data = result.to_dict()

    assert data["meta"]["extraction_skipped"] is True
    assert data["document_type"]["is_tender"] is False
    assert data["fields"] == {}
    assert data["template_values"] == {}
    assert data["validation"]["ok"] is False
    assert data["review"][0]["key"] == "document_type"
    assert data["review"][0]["priority"] == "high"
    assert "简历" in data["meta"]["reject_reason"]
    assert "未执行抽取" in render_markdown(result)


def test_allow_non_tender_bypasses_gate(tmp_path):
    path = tmp_path / "个人简历.docx"
    _build_resume_docx(path)

    extractor = Extractor(settings_override={"llm": {"enabled": False}})
    data = extractor.extract(path, use_llm=False, allow_non_tender=True).to_dict()

    assert data["meta"]["extraction_skipped"] is False
    assert data["document_type"]["is_tender"] is False
    assert len(data["fields"]) > 0  # 仍然产出字段结构，只是全部缺失


def test_cli_returns_exit_code_3_for_non_tender(tmp_path):
    path = tmp_path / "个人简历.docx"
    _build_resume_docx(path)
    output = tmp_path / "结果.json"

    code = main(["extract", str(path), "--no-llm", "-o", str(output)])
    assert code == 3

    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["meta"]["extraction_skipped"] is True
    assert data["fields"] == {}
