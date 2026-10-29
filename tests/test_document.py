"""DOCX 解析测试：顺序、章节、合并单元格。"""

from docx import Document as DocxDocument

from tender_extractor.document import read_document


def _build_docx(path):
    doc = DocxDocument()
    doc.add_paragraph("某某单位采购需求报告")
    doc.add_paragraph("一、项目概况")
    doc.add_paragraph("项目名称：社区服务教学资源库项目")
    table = doc.add_table(rows=3, cols=3)
    table.cell(0, 0).text = "序号"
    table.cell(0, 1).text = "服务项目名称"
    table.cell(0, 2).text = "金额"
    table.cell(1, 0).text = "1"
    table.cell(1, 1).text = "主项目"
    table.cell(1, 2).text = "60,000.00"
    table.cell(2, 0).text = ""
    table.cell(2, 1).text = "子项目"
    table.cell(2, 2).text = "40,000.00"
    # 序号列纵向合并：第二行应视为合并续行，文本为空
    table.cell(1, 0).merge(table.cell(2, 0))
    doc.add_paragraph("二、采购标的")
    doc.add_paragraph("本项目不接受联合体投标。")
    doc.save(str(path))


def test_read_document_preserves_block_order_and_sections(tmp_path):
    path = tmp_path / "需求报告.docx"
    _build_docx(path)

    doc = read_document(path)

    assert doc.title == "某某单位采购需求报告"
    assert [b.kind for b in doc.blocks] == ["paragraph", "paragraph", "paragraph", "table", "paragraph", "paragraph"]
    assert doc.blocks[0].section == "封面"
    assert doc.blocks[2].section == "一、项目概况"
    assert doc.blocks[3].section == "一、项目概况"
    assert doc.blocks[4].section == "二、采购标的"
    assert doc.cleaning["tables"] == 1


def test_vertical_merge_continuation_cell_is_blank(tmp_path):
    path = tmp_path / "需求报告.docx"
    _build_docx(path)

    doc = read_document(path)
    table = doc.tables[0]
    assert table.rows is not None
    assert table.rows[1][0] == "1"
    assert table.rows[2][0] == ""
    assert table.rows[2][1] == "子项目"
