"""生成前预览：章节切分、片段类型、合并单元格、补充条款。

预览是纯结构解析，不需要 Word/LibreOffice，测试用现场构造的 docx 即可。
"""

from __future__ import annotations

from pathlib import Path

import docx
import pytest

from tender_extractor.preview import build_preview


def _build(path: Path) -> Path:
    document = docx.Document()
    document.add_paragraph("某测试项目招标文件")
    document.add_paragraph("第一章 投标邀请")
    document.add_paragraph('项目名称：{{ d["项目名称"] }}')
    document.add_paragraph('采购人：{{ d["采购人"] }}')
    document.add_paragraph("")

    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "合并列"
    table.cell(1, 0).text = "第二行"
    table.cell(0, 1).merge(table.cell(1, 1))  # 右列纵向合并

    document.add_paragraph("第二章 采购需求")
    document.add_paragraph("普通正文段落")
    document.save(str(path))
    return path


@pytest.fixture()
def template(tmp_path: Path) -> Path:
    return _build(tmp_path / "模板.docx")


def test_sections_and_stats(template: Path) -> None:
    preview = build_preview(
        template, {"项目名称": "某单位安保服务项目", "采购人": "某单位"}
    )

    titles = [section["title"] for section in preview["sections"]]
    assert titles == ["封面", "第一章 投标邀请", "第二章 采购需求"]
    assert preview["stats"] == {
        "total_placeholders": 2,
        "filled": 2,
        "missing": 0,
    }
    assert preview["missing"] == []


def test_segments_split_value_and_missing(template: Path) -> None:
    preview = build_preview(template, {"项目名称": "某项目"})
    blocks = preview["sections"][1]["blocks"]
    target = blocks[1]  # 「项目名称：…」

    assert target["segments"] == [
        {"kind": "text", "text": "项目名称："},
        {"kind": "value", "text": "某项目", "name": "项目名称"},
    ]
    # 未填字段渲染成待补充标记
    missing_block = blocks[2]
    assert missing_block["segments"][-1]["kind"] == "missing"
    assert missing_block["segments"][-1]["text"] == "【待补充：采购人】"
    assert preview["missing"] == ["采购人"]
    assert preview["stats"]["filled"] == 1


def test_missing_mode_empty_blanks_out_value(template: Path) -> None:
    preview = build_preview(template, {}, missing_mode="empty")
    block = preview["sections"][1]["blocks"][1]

    assert block["segments"][-1] == {"kind": "missing", "text": "", "name": "项目名称"}


def test_empty_paragraph_is_kept(template: Path) -> None:
    preview = build_preview(template, {})
    blocks = preview["sections"][1]["blocks"]

    assert any(block["segments"] == [] for block in blocks), "空行有版面意义，不能丢"


def test_table_merge_cells(template: Path) -> None:
    preview = build_preview(template, {})
    table = next(
        block
        for block in preview["sections"][1]["blocks"]
        if block["type"] == "table"
    )
    rows = table["rows"]

    # 右列纵向合并：首行 rowspan=2，第二行该列跳过
    assert rows[0][1]["rowspan"] == 2
    assert rows[1][1] == {"skip": True}
    assert all(cell["colspan"] == 1 for cell in rows[0])


def test_custom_append_becomes_last_section(template: Path) -> None:
    preview = build_preview(
        template,
        {"项目名称": "某项目"},
        custom_fields=[
            {"label": "履约保证金", "value": "合同金额的5%", "target": "append"},
            {"label": "空值字段", "value": "", "target": "append"},
            {"label": "联系人邮箱", "value": "a@b.com", "target": "placeholder",
             "placeholder": "采购人"},
        ],
    )

    last = preview["sections"][-1]
    assert last["title"] == "补充条款"
    rows = last["blocks"][0]["rows"]
    assert len(rows) == 2  # 表头 + 1 条，空值那条被丢弃
    assert rows[1][0]["segments"][0]["text"] == "履约保证金"
    # 绑定占位符的字段并入取值，因此采购人不再是缺失
    assert "采购人" not in preview["missing"]


def test_preview_of_unknown_placeholder_in_template(tmp_path: Path) -> None:
    document = docx.Document()
    document.add_paragraph('邮箱：{{ d["采购人邮箱"] }}')
    path = tmp_path / "未匹配.docx"
    document.save(str(path))

    preview = build_preview(path, {})
    assert preview["missing"] == ["采购人邮箱"]
    assert preview["sections"][0]["blocks"][0]["segments"][-1]["name"] == "采购人邮箱"
