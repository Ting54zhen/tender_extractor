"""文本清洗与值归一化。

设计原则：
1. 清洗只做“不改变语义”的处理，保留中文标点和原文措辞，便于回链证据。
2. 匹配时另做 normalize_for_match，避免全角/空格影响命中。
3. 金额、日期、百分比等在这里统一成平台可直接入库的格式。
"""

from __future__ import annotations

import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any


_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f​-‏﻿]")
_MULTI_SPACE_RE = re.compile(r"[ \t 　]+")
_MONEY_RE = re.compile(
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?P<unit>亿元|万元|亿|万|元)?"
)
_CN_DIGITS = {
    "零": 0, "〇": 0, "壹": 1, "一": 1, "贰": 2, "二": 2, "两": 2,
    "叁": 3, "三": 3, "肆": 4, "四": 4, "伍": 5, "五": 5,
    "陆": 6, "六": 6, "柒": 7, "七": 7, "捌": 8, "八": 8, "玖": 9, "九": 9,
}
_CN_SMALL_UNITS = {"拾": 10, "十": 10, "佰": 100, "百": 100, "仟": 1000, "千": 1000}
_CN_SECTIONS = {"万": 10_000, "亿": 100_000_000}


def clean_text(value: Any) -> str:
    """保留中文标点的基础清洗：去控制字符、统一空白、去空行。"""
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = _CTRL_RE.sub("", text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def clean_cell(value: Any) -> str:
    """表格单元格清洗：单元格内换行压成空格，避免破坏按行渲染。"""
    return clean_text(value).replace("\n", " ").strip()


def normalize_for_match(value: Any) -> str:
    """仅用于匹配/去重，不用于输出。"""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"\s+", "", text)
    text = (
        text.replace("：", ":")
        .replace("，", ",")
        .replace("（", "(")
        .replace("）", ")")
        .replace("；", ";")
        .replace("、", "、")
    )
    return text.lower()


def strip_numbering(label: str) -> str:
    """去掉“（一）”“1.”“1）”等编号前缀，保留真正的字段名。"""
    text = clean_text(label)
    text = re.sub(r"^[（(]?[一二三四五六七八九十\d]+[）)、.．]\s*", "", text)
    text = re.sub(r"^第[一二三四五六七八九十\d]+[章节部分]\s*", "", text)
    return text.strip(" ：:")


def is_placeholder(value: Any) -> bool:
    """判断模板占位符/空白项。注意“不收取”“无”是有效值，不在此列。"""
    text = clean_text(value)
    if not text:
        return True
    if re.fullmatch(r"\{[^{}]{0,60}\}", text):
        return True
    if re.fullmatch(r"[<《][^<》>]{0,60}[>》]", text):
        return True
    if re.fullmatch(r"[_\-—－\s]{3,}", text):
        return True
    if re.fullmatch(r"[/／\-—]+", text):
        return True
    if re.match(r"^(详见|见第|参见|另行|待定|待明确|以.*为准$)", text):
        return True
    return False


def parse_decimal(value: Any) -> Decimal | None:
    """把任意值转成 Decimal；None、空串、非法输入统一返回 None。

    千分位逗号在这里去掉。全项目只有这一个「字符串 → Decimal」入口，
    不要在别处再写 `Decimal(str(x).replace(",", ""))`。
    """
    if value is None or isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _cn_integer_to_int(text: str) -> int:
    """中文大写整数转 int，支持 亿/万/仟/佰/拾。"""
    total = 0
    section = 0
    number = 0
    for ch in text:
        if ch in _CN_DIGITS:
            number = _CN_DIGITS[ch]
        elif ch in _CN_SMALL_UNITS:
            section += (number or 1) * _CN_SMALL_UNITS[ch]
            number = 0
        elif ch in _CN_SECTIONS:
            section = (section + number) * _CN_SECTIONS[ch]
            total += section
            section = 0
            number = 0
        elif ch in {"元", "圆", "整", "正"}:
            break
    return total + section + number


def parse_cny_upper(value: Any) -> Decimal | None:
    """解析“壹佰叁拾陆万陆仟捌佰元整”这类大写金额。"""
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text or not any(ch in _CN_DIGITS for ch in text):
        return None
    match = re.search(r"[零〇壹一贰二两叁三肆四伍五陆六柒七捌八玖九拾十佰百仟千万亿]+", text)
    if not match:
        return None
    integer = Decimal(_cn_integer_to_int(match.group(0)))

    jiao = re.search(r"([零〇壹一贰二两叁三肆四伍五陆六柒七捌八玖九])角", text)
    fen = re.search(r"([零〇壹一贰二两叁三肆四伍五陆六柒七捌八玖九])分", text)
    if jiao:
        integer += Decimal(_CN_DIGITS[jiao.group(1)]) / Decimal(10)
    if fen:
        integer += Decimal(_CN_DIGITS[fen.group(1)]) / Decimal(100)
    return integer


def parse_money(value: Any, require_unit: bool = False) -> Decimal | None:
    """解析金额。支持 1,366,800.00元 / 136.68万元 / 100000 / 大写金额。"""
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value)).strip()
    if not text:
        return None

    upper = parse_cny_upper(text)
    if upper is not None:
        return upper

    for match in _MONEY_RE.finditer(text):
        unit = match.group("unit")
        if require_unit and not unit:
            continue
        number = parse_decimal(match.group("num"))
        if number is None:
            continue
        if unit in {"亿", "亿元"}:
            return number * Decimal(100_000_000)
        if unit in {"万", "万元"}:
            return number * Decimal(10_000)
        return number
    return None


def money_quantum(precision: int = 2) -> Decimal:
    """返回指定小数位的量化单位，例如 precision=4 -> Decimal("0.0001")。"""
    return Decimal(1).scaleb(-max(0, int(precision)))


def quantize_money(value: Any, precision: int = 2) -> Decimal | None:
    """按指定小数位做四舍五入（ROUND_HALF_UP）。"""
    number = parse_decimal(value)
    if number is None:
        return None
    return number.quantize(money_quantum(precision), rounding=ROUND_HALF_UP)


def format_money(value: Any, suffix: str = "元", precision: int = 2) -> str | None:
    """格式化金额。precision 控制小数位，预算默认由 fields.json 指定为 4 位。"""
    number = quantize_money(value, precision)
    if number is None:
        return None
    return f"{number:,.{max(0, int(precision))}f}{suffix}"


def format_wan(value: Any) -> str | None:
    number = parse_decimal(value)
    if number is None:
        return None
    return f"{number / Decimal(10_000):,.2f}万元"


def parse_number(value: Any) -> Decimal | None:
    """从“30日”“1.00(年)”“5分”中取数字。"""
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return parse_decimal(value)
    text = unicodedata.normalize("NFKC", str(value))
    match = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    if not match:
        return None
    return parse_decimal(match.group(0))


def parse_integer(value: Any) -> int | None:
    number = parse_number(value)
    if number is None:
        return None
    return int(number)


def parse_percent(value: Any) -> float | None:
    """解析 30.73% / 30.73 / 百分之三十。返回 0-1 的小数。"""
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value)).strip()
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if match:
        return float(match.group(1)) / 100.0
    match = re.search(r"百分之\s*(\d+(?:\.\d+)?)", text)
    if match:
        return float(match.group(1)) / 100.0
    number = parse_number(text)
    if number is None:
        return None
    return float(number) if number <= 1 else float(number) / 100.0


def parse_date(value: Any) -> str | None:
    """提取第一个中文日期，输出 ISO 格式。"""
    text = unicodedata.normalize("NFKC", str(value or ""))
    match = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if not match:
        return None
    year, month, day = (int(x) for x in match.groups())
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def normalize_phone(value: Any) -> str | None:
    text = unicodedata.normalize("NFKC", str(value or ""))
    match = re.search(
        r"(?:(?:\+?86[-\s]?)?1[3-9]\d{9}|0\d{2,3}[-\s]?\d{7,8}(?:[-\s]?\d{1,6})?)",
        text,
    )
    if not match:
        return None
    return re.sub(r"\s+", "", match.group(0))


def normalize_enum(value: Any, enum_name: str) -> str | None:
    """把“接受/不接受”“允许/不允许”统一成枚举值。先判否定，再判肯定。"""
    text = normalize_for_match(value)
    if not text:
        return None
    if enum_name == "consortium":
        if any(word in text for word in ("不接受", "不允许", "不组织", "否")):
            return "不接受"
        if any(word in text for word in ("接受", "允许", "是")):
            return "接受"
    elif enum_name == "subcontracting":
        if any(word in text for word in ("不允许", "不接受", "不得", "否")):
            return "不允许"
        if any(word in text for word in ("允许", "接受", "可以", "是")):
            return "允许"
    return clean_text(value)


def split_sentences(text: str) -> list[str]:
    """按中文句号/分号/换行切句，保留标点。"""
    parts = re.split(r"(?<=[。；;！!？?])|\n", text)
    return [p.strip() for p in parts if p and p.strip()]
