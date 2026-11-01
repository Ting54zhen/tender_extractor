"""电话号码抽取与校验：多号码、无区号固话、多号码校验通过。

回归自真实样本的识别错误：质疑联系电话「28866118、28866163」被判无效。
"""

from tender_extractor.cleaning import normalize_phone, split_phones
from tender_extractor.validation import validate_field


def test_normalize_phone_accepts_local_8_digit():
    """无区号的 8 位本地固话是合法电话（广州 28866118）。"""
    assert normalize_phone("28866118") == "28866118"


def test_normalize_phone_keeps_area_code():
    assert normalize_phone("020-84215123") == "020-84215123"


def test_normalize_phone_accepts_mobile():
    assert normalize_phone("13800138000") == "13800138000"


def test_split_phones_handles_chinese_separator():
    assert split_phones("28866118、28866163") == ["28866118", "28866163"]


def test_split_phones_handles_mixed_separators():
    assert split_phones("020-84215123，13800138000") == ["020-84215123", "13800138000"]


def test_split_phones_returns_empty_on_invalid_part():
    assert split_phones("28866118、非电话") == []


def test_validate_field_accepts_multiple_phones():
    field_def = {"key": "inquiry_phone", "label": "质疑联系电话", "type": "phone"}
    errors, _warnings = validate_field(field_def, "28866118、28866163")
    assert errors == []


def test_validate_field_rejects_non_phone():
    field_def = {"key": "inquiry_phone", "label": "质疑联系电话", "type": "phone"}
    errors, _warnings = validate_field(field_def, "not-a-phone")
    assert errors and "不是有效电话" in errors[0]
