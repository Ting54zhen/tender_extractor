"""清洗与归一化单元测试。"""

from decimal import Decimal

from tender_extractor.cleaning import (
    clean_cell,
    clean_text,
    format_money,
    is_placeholder,
    normalize_enum,
    normalize_for_match,
    normalize_phone,
    parse_cny_upper,
    parse_date,
    parse_money,
    parse_percent,
    quantize_money,
    strip_numbering,
)
from tender_extractor.validation import normalize_value


def test_clean_text_keeps_chinese_punctuation_and_drops_control_chars():
    raw = "  项目名称：测试\u0000项目，　金额 100 元 \n\n"
    assert clean_text(raw) == "项目名称：测试项目， 金额 100 元"


def test_clean_cell_flattens_newlines():
    assert clean_cell("第一行\n第二行\t附注") == "第一行 第二行 附注"


def test_normalize_for_match_is_only_for_matching():
    assert normalize_for_match("项目名称： 测试（一期）") == "项目名称:测试(一期)"


def test_strip_numbering():
    assert strip_numbering("（一）项目概况") == "项目概况"
    assert strip_numbering("1. 采购预算") == "采购预算"
    assert strip_numbering("第一章 总则") == "总则"


def test_is_placeholder():
    assert is_placeholder("{项目名称}")
    assert is_placeholder("____")
    assert is_placeholder("详见第三章")
    assert not is_placeholder("不收取")
    assert not is_placeholder("0")


def test_parse_money_variants():
    assert parse_money("1,366,800.00元") == Decimal("1366800.00")
    assert parse_money("136.68万元") == Decimal("1366800.00")
    assert parse_money("100000") == Decimal("100000")
    assert parse_money("壹佰叁拾陆万陆仟捌佰元整") == Decimal("1366800")


def test_format_money_precision():
    assert format_money(Decimal("1366800"), precision=4) == "1,366,800.0000元"
    assert format_money(Decimal("1366800.12346"), precision=4) == "1,366,800.1235元"
    assert format_money(Decimal("1366800.123"), precision=4) == "1,366,800.1230元"
    assert format_money(Decimal("1366800.123"), precision=2) == "1,366,800.12元"
    assert format_money(Decimal("1.00005"), precision=4) == "1.0001元"  # 四舍五入，非银行家舍入
    assert quantize_money("136.68万元", precision=4) is None  # 不是纯数字，交给 parse_money


def test_budget_normalization_uses_four_decimals():
    budget_def = {"key": "budget_amount", "type": "money", "precision": 4}
    value, notes = normalize_value(budget_def, "136.68万元")
    assert value == "1,366,800.0000元"
    assert notes

    other_money_def = {"key": "other_money", "type": "money"}
    assert normalize_value(other_money_def, "136.68万元")[0] == "1,366,800.00元"


def test_parse_cny_upper_with_jiao_fen():
    assert parse_cny_upper("叁万元伍角陆分") == Decimal("30000.56")


def test_parse_percent():
    assert parse_percent("30.73%") == 0.3073
    assert parse_percent("百分之30") == 0.3
    assert parse_percent("0.5") == 0.5


def test_parse_date_and_phone():
    assert parse_date("自2025年1月1日起") == "2025-01-01"
    assert parse_date("2025年13月1日") is None
    assert normalize_phone("电话：0755-12345678 转 800") == "0755-12345678"


def test_normalize_enum_checks_negation_first():
    assert normalize_enum("不接受联合体投标", "consortium") == "不接受"
    assert normalize_enum("允许联合体投标", "consortium") == "接受"
    assert normalize_enum("不允许合同分包", "subcontracting") == "不允许"
    assert normalize_enum("允许分包", "subcontracting") == "允许"
