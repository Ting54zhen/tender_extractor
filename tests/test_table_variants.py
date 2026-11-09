"""表格形态变体的规则层回归测试。

为什么单独一个文件：这些用例验证的是「输入侧泛化」——同一语义的表格换列名、
换列组合后仍应被识别，而不是针对某一份具体样本。样本本身不进测试，避免把
项目名、单位名等具体事实写死进代码。
"""

from __future__ import annotations

from typing import Any

import docx
import pytest

from tender_extractor.doc_generator import DEFAULT_TEMPLATE, generate_tender_document
from tender_extractor.models import Block, Document, ExtractionResult
from tender_extractor.report import render_markdown
from tender_extractor.rules import (
    extract_business_requirements,
    extract_rules,
    find_items_table,
)
from tender_extractor.table_profile import KIND_KV, KIND_UNKNOWN, profile_table


def _doc(rows: list[list[str]], section: str = "") -> Document:
    """构造只含一张表的文档，保留表型与行列结构供规则层判定。"""
    text = "\n".join(" | ".join(row) for row in rows)
    block = Block(
        id="T0",
        kind="table",
        text=text,
        raw=text,
        index=0,
        table_index=0,
        section=section,
        section_path=[section] if section else [],
        rows=rows,
    )
    return Document(path="脱敏样本.docx", title="某单位设备采购项目", blocks=[block])


def test_items_table_without_money_columns_is_recognized() -> None:
    """标的信息表只给数量与单位、不给单价金额时，仍应逐项识别且金额留空。

    原文没有金额时不得用预算倒推或填 0，否则成稿会出现编造的分项价格。
    """
    rows: list[list[str]] = [
        ["序号", "名称", "数量", "计量单位", "是否核心产品"],
        ["一", "隧道式洗笼机", "1", "台", "□是 ☑否"],
        ["二", "自动化翻转系统", "1", "套", "☑是 □否"],
        ["三", "转运车", "100", "辆", "□是 ☑否"],
    ]

    found: Any = find_items_table(_doc(rows, "二、标的信息表"))

    assert found is not None, "名称/数量齐全的表不应因缺金额列被整表丢弃"
    items, declared_total, _block = found
    assert [item["name"] for item in items] == [
        "隧道式洗笼机",
        "自动化翻转系统",
        "转运车",
    ]
    assert items[2]["quantity"] == 100.0
    assert items[2]["unit"] == "辆"
    assert all(item["amount"] is None for item in items)
    assert all(item["unit_price"] is None for item in items)
    assert declared_total is None


def test_two_column_clause_table_yields_business_requirements() -> None:
    """「条款 | 要求」两列 KV 表应被识别，且标签归一化到招标模板的键。

    生成层的商务要求表按固定键取值（标的提供的时间/地点…），所以规则层必须
    把「交付时间」「到货验收」这类同义写法映射到这些键，而不是丢弃整表。
    """
    rows: list[list[str]] = [
        ["条款", "要求"],
        ["交付时间", "☑ 合同签订后90日内交付"],
        ["交付地点", "采购人指定地点（上海市内）"],
        ["安装调试", "☑ 供应商负责安装调试"],
        ["到货验收", "☑ 功能验收（测试技术指标是否达标）"],
        ["第三方检测报告", "□ 需要　☑ 不需要"],
    ]

    business, _block = extract_business_requirements(_doc(rows, "二、交货与安装要求"))

    assert business, "两列 KV 表的标签不在白名单时不应静默丢弃"
    assert "合同签订后90日内" in business.get("标的提供的时间", "")
    assert "上海市内" in business.get("标的提供的地点", "")
    assert "功能验收" in business.get("验收要求", "")


_CLAUSE_ROWS: list[list[str]] = [
    ["序号", "符号", "指标分类/名称", "指标要求"],
    ["1", "▲", "业绩", "提供已正式运行的系统，合同与使用方现场照片。"],
    ["2", "★", "系统主体构成", "系统需由处理设备与转运设备配套构成"],
    ["", "", "某设备模块", ""],
    ["3", "", "", "设备总长度需≤8米，提供实物测量照片证明。"],
]


def test_star_clauses_are_extracted_without_an_items_table() -> None:
    """技术参数表独立于标的清单：即使标的解析失败，★▲ 条款也不该一起清零。"""
    output = extract_rules(_doc(_CLAUSE_ROWS, "三、技术参数表"), {})

    clauses = output.structured["star_clauses"]
    assert len(clauses) >= 2
    assert {"★", "▲"} <= {str(clause["type"]) for clause in clauses}
    assert all(clause.get("block_id") == "T0" and clause.get("quote") for clause in clauses)


def test_general_tech_clauses_are_kept_and_grouped() -> None:
    """一般参数（无 ★▲）也要保留，并按表内分组标题归到对应标的附表。"""
    output = extract_rules(_doc(_CLAUSE_ROWS, "三、技术参数表"), {})

    tech = output.structured["tech_clauses"]
    assert len(tech) == 3
    assert any(str(clause["type"]) == "" for clause in tech)
    assert any(clause.get("item_name") == "某设备模块" for clause in tech)
    assert all(clause.get("block_id") == "T0" and clause.get("quote") for clause in tech)


def test_star_inside_long_text_does_not_make_a_clause_table() -> None:
    """长文本里偶尔带 ★ 的键值表不能被误判成技术参数表。

    否则售后条款会混进技术参数附表，成稿出现不属于技术要求的行。
    """
    rows: list[list[str]] = [
        ["条款", "要求"],
        ["质保期", "5年（自验收合格之日起）"],
        ["驻场服务", "至少1名驻场，时间≥5年（★）"],
        ["服务响应", "接到通知1小时内答复，24小时内给出解决方案"],
    ]

    profile = profile_table(_doc(rows, "三、质保期与售后服务").tables[0])

    assert profile.kind == KIND_KV


def test_unknown_table_is_reported_instead_of_silently_dropped() -> None:
    """判不出表型的表格必须带着原始列名出现在结果里，而不是静默消失。"""
    rows: list[list[str]] = [
        ["甲栏", "乙栏", "丙栏"],
        ["这是一段较长的说明文字用于判型测试", "另一段较长的说明文字", "第三段说明文字"],
        ["继续一段较长的说明文字用于判型测试", "再一段较长的说明文字", "第四段说明文字"],
    ]
    output = extract_rules(_doc(rows, "五、其它补充事宜"), {})

    profiles = output.structured["table_profiles"]
    assert len(profiles) == 1
    assert profiles[0]["kind"] == KIND_UNKNOWN
    assert profiles[0]["block_id"] == "T0"
    assert profiles[0]["unmapped_headers"] == ["甲栏", "乙栏", "丙栏"]
    assert output.structured["unmapped_tables"]


requires_template = pytest.mark.skipif(
    not DEFAULT_TEMPLATE.exists(),
    reason="compiled 模板不存在，请先运行 scripts/prepare_template.py",
)


def _docx_text(path: Any) -> str:
    document = docx.Document(str(path))
    parts = [para.text for para in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


@requires_template
def test_generated_tech_tables_include_general_clauses(tmp_path: Any) -> None:
    """技术参数附表要写入全部参数（含无 ★▲ 的一般参数），并按标的拆附表。

    只渲染 ★▲ 会让大多数技术要求在成稿里凭空消失；标的清单缺失时也不能
    因为「没有 items」就放弃整张条款表。
    """
    clauses = [
        {"type": "★", "text": "实质性条款须满足", "item_name": "某处理设备",
         "item_seq": "", "block_id": "T0", "quote": "★ 实质性条款须满足"},
        {"type": "", "text": "一般参数也要进附表", "item_name": "某处理设备",
         "item_seq": "", "block_id": "T0", "quote": "一般参数也要进附表"},
        {"type": "▲", "text": "重要参数供评分", "item_name": "某转运设备",
         "item_seq": "", "block_id": "T0", "quote": "▲ 重要参数供评分"},
    ]
    result = ExtractionResult(
        source={"name": "脱敏样本.docx"},
        meta={},
        fields={},
        structured={
            "business_requirements": {"标的提供的时间": "合同签订后90日内"},
            "procurement_items": [
                {"seq": "1", "name": "某处理设备", "unit": "台", "quantity": 1.0,
                 "unit_price": None, "amount": None, "category": "", "requirement": ""},
                {"seq": "2", "name": "某转运设备", "unit": "辆", "quantity": 10.0,
                 "unit_price": None, "amount": None, "category": "", "requirement": ""},
            ],
            "tech_clauses": clauses,
            "star_clauses": [clause for clause in clauses if clause["type"]],
        },
        validation={},
        cleaning={},
        review=[],
        audit={},
    )

    generated = generate_tender_document(result, output_path=tmp_path / "成稿.docx")
    text = _docx_text(generated.output_path)

    assert "合同签订后90日内" in text
    assert "一般参数也要进附表" in text
    assert "附表二" in text


def test_report_shows_table_profiles_and_unmapped_tables() -> None:
    """判不出表型的表格要在审核报告里可见，而不是静默消失。"""
    profile = {
        "block_id": "T0",
        "section": "五、其它补充事宜",
        "kind": "unknown",
        "confidence": 0.2,
        "headers": ["甲栏", "乙栏", "丙栏"],
        "columns": [
            {"index": 0, "header": "甲栏", "role": "unknown", "confidence": 0.0},
            {"index": 1, "header": "乙栏", "role": "unknown", "confidence": 0.0},
            {"index": 2, "header": "丙栏", "role": "unknown", "confidence": 0.0},
        ],
        "unmapped_headers": ["甲栏", "乙栏", "丙栏"],
    }
    result = ExtractionResult(
        source={"name": "脱敏样本.docx"},
        meta={},
        fields={},
        structured={"table_profiles": [profile], "unmapped_tables": [profile]},
        validation={},
        cleaning={},
        review=[],
        audit={},
    )

    markdown = render_markdown(result)

    assert "表格识别结果" in markdown
    assert "T0" in markdown
    assert "甲栏" in markdown
    assert "未自动判型" in markdown
