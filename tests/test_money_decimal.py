"""金额链路输出断言：金额去重改造前后，格式化文案与勾稽结果必须逐字一致。

这三组断言是 P1（金额链路去重）的护栏：先跑绿锁定现状，改完 `_as_decimal` /
`_money_sum` 的解析入口后必须仍然全绿，否则说明输出被悄悄改了。
"""

from decimal import Decimal

from tender_extractor.cleaning import format_money, parse_decimal
from tender_extractor.models import FieldValue
from tender_extractor.report import _money_sum
from tender_extractor.validation import cross_validate


def _budget_fields(value: str) -> dict[str, FieldValue]:
    return {
        "budget_amount": FieldValue(
            key="budget_amount",
            label="采购预算",
            template="采购预算",
            value=value,
            confidence=0.9,
            status="ok",
            source="rule",
        )
    }


def test_format_money_text_is_stable():
    assert format_money(Decimal("1366800")) == "1,366,800.00元"
    assert format_money(Decimal("1366800"), precision=4) == "1,366,800.0000元"
    assert format_money("1,366,800.00") == "1,366,800.00元"
    assert format_money(Decimal("0.005")) == "0.01元"  # ROUND_HALF_UP
    assert format_money("136.68万元") is None  # 带单位不归 quantize 管
    assert format_money(None) is None
    assert format_money("") is None


def test_cross_validate_total_mismatch_message_is_stable():
    structured = {
        "procurement_items": [
            {"seq": 1, "name": "A", "total_amount": Decimal("600.00")},
            {"seq": 2, "name": "B", "total_amount": Decimal("400.00")},
        ]
    }
    errors, warnings = cross_validate(
        _budget_fields("1,366,800.00元"),
        structured,
        {"budget_amount": {"precision": 4}},
    )
    assert not warnings
    assert len(errors) == 1
    assert errors[0]["code"] == "ITEM_TOTAL_MISMATCH"
    assert errors[0]["message"] == "采购标的合计 1,000.00元 与预算 1,366,800.0000元 不一致"


def test_cross_validate_line_amount_message_is_stable():
    structured = {
        "procurement_items": [
            {
                "seq": 1,
                "name": "A",
                "total_amount": Decimal("100.00"),
                "sub_items": [{"seq": 1, "quantity": Decimal("3"), "unit_price": Decimal("40.00"), "amount": Decimal("100.00")}],
            }
        ]
    }
    errors, warnings = cross_validate(_budget_fields("100.00元"), structured)
    assert not errors
    assert [w["code"] for w in warnings] == ["LINE_AMOUNT_MISMATCH"]
    assert warnings[0]["message"] == "第 1 行数量×单价与金额不一致"


def test_cross_validate_payment_message_is_stable():
    structured = {
        "payment": {"prepay_amount": Decimal("100.00"), "balance_amount": Decimal("200.00")},
    }
    errors, warnings = cross_validate(_budget_fields("500.00元"), structured)
    assert not errors
    assert [w["code"] for w in warnings] == ["PAYMENT_TOTAL_MISMATCH"]
    assert warnings[0]["message"] == "预付款 100.00元 + 尾款 200.00元 与预算 500.00元 不一致"


def test_money_sum_keeps_decimal_precision():
    items = [
        {"total_amount": Decimal("1.10")},
        {"amount": Decimal("2.20")},
        {"total_amount": None},
        {},
    ]
    assert _money_sum(items) == "3.30"


def test_money_sum_prefers_total_amount_over_amount():
    assert _money_sum([{"total_amount": Decimal("9.99"), "amount": Decimal("1.00")}]) == "9.99"


def test_money_sum_returns_none_without_values():
    assert _money_sum([{"total_amount": None}, {}]) is None


def test_money_sum_accepts_thousand_separators():
    """改造前 `Decimal(str(x))` 会静默跳过带千分位的字符串，统一入口后能正常计入。"""
    assert _money_sum([{"total_amount": "1,366,800.00"}, {"total_amount": "1.00"}]) == "1,366,801.00"


def test_parse_decimal_input_matrix():
    assert parse_decimal(None) is None
    assert parse_decimal("") is None
    assert parse_decimal("  ") is None
    assert parse_decimal("1,366,800.00") == Decimal("1366800.00")
    assert parse_decimal(Decimal("1.10")) == Decimal("1.10")
    assert parse_decimal(3) == Decimal("3")
    assert parse_decimal(3.7) == Decimal("3.7")
    assert parse_decimal("abc") is None
    assert parse_decimal("136.68万元") is None  # 单位换算归 parse_money 管
