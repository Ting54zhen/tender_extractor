"""字段归一化、校验、证据回链和跨字段一致性检查。

这一层是“数据清洗 → 喂回模型”的判定中心：
- 先把模型/规则给的值归一化成平台可入库的格式；
- 再做格式校验和跨字段勾稽；
- 校验失败的原因会原样进入 repair prompt，让模型带着错误信息重答。
"""

from __future__ import annotations

import difflib
import re
from decimal import Decimal
from typing import Any

from .cleaning import (
    clean_text,
    format_money,
    is_placeholder,
    money_quantum,
    normalize_enum,
    normalize_for_match,
    normalize_phone,
    parse_decimal,
    parse_integer,
    parse_money,
    parse_number,
    parse_percent,
    split_phones,
)
from .models import Document, FieldValue


def _field_precision(field_def: dict[str, Any] | None, default: int = 2) -> int:
    """读取字段配置里的小数位，缺省 2 位。"""
    try:
        return max(0, int((field_def or {}).get("precision", default)))
    except (TypeError, ValueError):
        return default


def normalize_value(field_def: dict[str, Any], raw: Any) -> tuple[Any, list[str]]:
    """按字段类型清洗。返回 (新值, 归一化说明)。"""
    notes: list[str] = []
    if raw is None:
        return None, notes
    if isinstance(raw, (int, float, Decimal)):
        raw_text = str(raw)
    else:
        raw_text = clean_text(raw)
    if not raw_text or is_placeholder(raw_text):
        return None, notes

    field_type = field_def.get("type", "text")
    key = field_def.get("key", "")

    if field_type == "money":
        amount = parse_money(raw_text)
        if amount is not None:
            precision = _field_precision(field_def)
            formatted = format_money(amount, precision=precision)
            if formatted != raw_text:
                notes.append(f"金额归一化：{raw_text} -> {formatted}")
            return formatted, notes
        return raw_text, notes

    if field_type == "integer":
        number = parse_integer(raw_text)
        if number is not None:
            if str(number) != raw_text:
                notes.append(f"整数归一化：{raw_text} -> {number}")
            return number, notes
        return raw_text, notes

    if field_type == "score":
        number = parse_number(raw_text)
        if number is not None:
            value: Any = int(number) if number == number.to_integral_value() else float(number)
            if str(value) != raw_text:
                notes.append(f"分值归一化：{raw_text} -> {value}")
            return value, notes
        return raw_text, notes

    if field_type == "ratio":
        ratio = parse_percent(raw_text)
        if ratio is not None:
            formatted = f"{ratio * 100:.2f}%"
            if formatted != raw_text:
                notes.append(f"比例归一化：{raw_text} -> {formatted}")
            return formatted, notes
        return raw_text, notes

    if field_type == "enum":
        if key == "consortium_allowed":
            value = normalize_enum(raw_text, "consortium")
        elif key == "subcontracting_allowed":
            value = normalize_enum(raw_text, "subcontracting")
        else:
            # 其他枚举（如招标组织形式）只做“允许值命中”，不能套用接受/允许的否定逻辑。
            allowed = field_def.get("enum") or []
            value = next(
                (option for option in allowed if option == raw_text or option in raw_text),
                raw_text,
            )
        if value and value != raw_text:
            notes.append(f"枚举归一化：{raw_text} -> {value}")
        return value or raw_text, notes

    if field_type == "phone":
        phones = split_phones(raw_text)
        if not phones:
            return raw_text, notes
        phone_text = "、".join(phones)
        if phone_text != raw_text:
            notes.append(f"电话归一化：{raw_text} -> {phone_text}")
        return phone_text, notes

    # text
    text = raw_text.strip()
    if len(text) > 20000:
        text = text[:20000]
        notes.append("文本超长，已截断到 20000 字")
    return text, notes


def validate_field(field_def: dict[str, Any], value: Any) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    key = field_def.get("key", "")
    label = field_def.get("label", key)
    field_type = field_def.get("type", "text")

    if value in (None, ""):
        if field_def.get("required"):
            errors.append(f"{label}为必填项，但原文和模型都没有给出值")
        return errors, warnings

    if isinstance(value, str) and is_placeholder(value):
        errors.append(f"{label}仍是占位符/空白，不是有效值")
        return errors, warnings

    if field_type == "money":
        amount = parse_money(value)
        if amount is None:
            errors.append(f"{label}无法解析为金额：{value}")
        elif amount <= 0:
            errors.append(f"{label}必须大于 0：{value}")
        elif amount > Decimal("1000000000000"):
            warnings.append(f"{label}金额异常大，请复核：{value}")
    elif field_type == "integer":
        if not isinstance(value, int):
            errors.append(f"{label}必须是整数：{value}")
        elif value < 0:
            errors.append(f"{label}不能为负数：{value}")
    elif field_type == "score":
        number = parse_number(value)
        if number is None:
            errors.append(f"{label}无法解析为分值：{value}")
        elif number < 0 or number > 100:
            errors.append(f"{label}分值应在 0-100 之间：{value}")
    elif field_type == "ratio":
        ratio = parse_percent(value)
        if ratio is None:
            errors.append(f"{label}无法解析为比例：{value}")
        elif ratio < 0 or ratio > 1:
            errors.append(f"{label}比例应在 0%-100% 之间：{value}")
    elif field_type == "enum":
        allowed = field_def.get("enum") or []
        if allowed and value not in allowed:
            errors.append(f"{label}不在允许值 {allowed} 中：{value}")
    elif field_type == "phone":
        if not split_phones(value):
            errors.append(f"{label}不是有效电话：{value}")

    if key == "purchaser":
        text = str(value).strip()
        org_marker = re.search(
            r"公司|集团|中心|局|厅|委员会|政府|办公室|管理处|办事处|医院|学校|大学|学院|"
            r"研究院|研究所|银行|协会|联合会|事务所|单位|院|所|站|馆|厂|社|队",
            text,
        )
        address_marker = re.search(
            r"省|市|区|县|镇|乡|街道|路|街|巷|号|村|附近|小区|花园|大厦|广场|安置房",
            text,
        )
        chapter_marker = re.fullmatch(r"第[一二三四五六七八九十百零0-9]+[章节篇部]", text)
        if chapter_marker:
            errors.append("采购人疑似章节标题，不是单位名称")
        elif address_marker and not org_marker:
            errors.append("采购人疑似地址而非单位名称，请复核")
    if key == "project_name":
        if len(str(value)) < 4:
            errors.append("项目名称过短")
        if "采购需求报告" in str(value):
            warnings.append("项目名称疑似包含了报告标题，请复核")
    if key == "contract_period":
        if not re.search(r"(年|月|日|天|合同|服务)", str(value)):
            warnings.append("合同履行期限缺少时间单位，请复核")
    if key == "budget_amount":
        if parse_money(value) is None:
            errors.append("预算金额无法解析")

    return errors, warnings


def check_grounding(
    value: Any,
    evidence: list[Any],
    doc: Document,
    fuzzy_threshold: float = 0.86,
) -> tuple[bool, str]:
    """验证值和证据是否能回链到原文。返回 (是否命中, 命中的原文片段)。

    多行值（如采购标的清单）按行分别回链：每行都能在证据块中定位才算命中，
    避免重排格式（加序号/汇总括号）被误判为改写。
    """
    needle = normalize_for_match(value)
    if not needle:
        return False, ""

    raw_text = str(value or "")
    lines = [line for line in raw_text.splitlines() if normalize_for_match(line)]
    multi_line = len(lines) >= 2

    for item in evidence:
        block = doc.block_by_id(getattr(item, "block_id", ""))
        if block is None:
            continue
        haystack = normalize_for_match(block.text)
        if not haystack:
            continue
        if needle in haystack:
            return True, getattr(item, "quote", "")

        quote = normalize_for_match(getattr(item, "quote", ""))
        if quote and (quote in haystack or needle in quote):
            return True, getattr(item, "quote", "")

        if multi_line:
            # 逐行匹配：每行取「名称部分」（去掉序号/★▲标记与括号/冒号后的
            # 重排内容），能在证据块原文中定位即视为有据——重排格式不算改写。
            def _strip_prefix(line: str) -> str:
                part = re.sub(r"^\s*[\[（(]?\s*[★▲]\s*[\]）)]?\s*", "", line)
                part = re.sub(r"^\s*\d+[.、)）]\s*", "", part)
                return part

            def _line_token(line: str) -> str:
                name_part = re.split(r"[（(：:]", _strip_prefix(line), 1)[0].strip()
                return normalize_for_match(name_part)

            def _grounded_line(line: str) -> bool:
                token = _line_token(line)
                if not token or len(token) < 4:
                    return True  # 过短或纯数字的行不作为否决依据
                return token in haystack or token in quote

            if all(_grounded_line(line) for line in lines):
                return True, getattr(item, "quote", "")

        if multi_line:
            # 兜底：多行清单常跨多个原文块（资格章节十几块），evidence 只挂了
            # 首块；改用全文匹配逐行核对（去除序号后取行首语义段）。
            full_haystack = normalize_for_match(doc.full_text())

            if all(
                (lambda token: not token or len(token) < 4 or token in full_haystack)(
                    _line_token(line)
                )
                for line in lines
            ):
                return True, getattr(item, "quote", "")

        # 长文本/OCR 场景下允许一定模糊匹配，按句比较，避免整块 SequenceMatcher 过慢。
        if len(needle) >= 6:
            for sentence in re.split(r"[。；;\n]", block.text):
                candidate = normalize_for_match(sentence)
                if not candidate:
                    continue
                # 先做长度剪枝，再算相似度
                if abs(len(candidate) - len(needle)) > max(30, len(needle) * 0.8):
                    continue
                if difflib.SequenceMatcher(None, needle, candidate).ratio() >= fuzzy_threshold:
                    return True, sentence
    return False, ""


def cross_validate(
    fields: dict[str, FieldValue],
    structured: dict[str, Any],
    field_defs: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """跨字段勾稽。返回 (errors, warnings)。"""
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    budget = parse_money(fields["budget_amount"].value) if "budget_amount" in fields else None
    budget_precision = _field_precision((field_defs or {}).get("budget_amount"))
    budget_tolerance = money_quantum(budget_precision)

    items = structured.get("procurement_items") or []
    item_total = sum(
        (parse_decimal(item.get("total_amount")) or Decimal(0) for item in items),
        Decimal(0),
    )
    declared = parse_decimal(structured.get("procurement_total_declared"))

    if budget and items and item_total > 0 and abs(item_total - budget) > budget_tolerance:
        message = (
            f"采购标的合计 {format_money(item_total)} "
            f"与预算 {format_money(budget, precision=budget_precision)} 不一致"
        )
        (warnings if declared and abs(declared - item_total) < Decimal("0.01") else errors).append(
            {"code": "ITEM_TOTAL_MISMATCH", "message": message, "fields": ["budget_amount"]}
        )

    for item in items:
        for sub in item.get("sub_items", []):
            qty = parse_decimal(sub.get("quantity"))
            price = parse_decimal(sub.get("unit_price"))
            amount = parse_decimal(sub.get("amount"))
            if qty is not None and price is not None and amount is not None:
                if abs(qty * price - amount) > Decimal("0.01"):
                    warnings.append(
                        {
                            "code": "LINE_AMOUNT_MISMATCH",
                            "message": f"第 {sub.get('seq') or item.get('seq')} 行数量×单价与金额不一致",
                            "fields": [],
                        }
                    )
                    break

    payment = structured.get("payment") or {}
    prepay = parse_decimal(payment.get("prepay_amount"))
    balance = parse_decimal(payment.get("balance_amount"))
    if prepay is not None and balance is not None and budget is not None:
        if prepay + balance > 0 and abs(prepay + balance - budget) > budget_tolerance:
            warnings.append(
                {
                    "code": "PAYMENT_TOTAL_MISMATCH",
                    "message": (
                        f"预付款 {format_money(prepay)} + 尾款 {format_money(balance)} "
                        f"与预算 {format_money(budget, precision=budget_precision)} 不一致"
                    ),
                    "fields": ["contract_prepay_ratio", "contract_settlement"],
                }
            )

    if "contract_prepay_ratio" in fields and payment.get("prepay_ratio") is not None:
        field_ratio = parse_percent(fields["contract_prepay_ratio"].value)
        computed = float(payment["prepay_ratio"])
        if field_ratio is not None and abs(field_ratio - computed) > 0.001:
            warnings.append(
                {
                    "code": "PREPAY_RATIO_MISMATCH",
                    "message": f"预付款比例与付款条款计算值不一致：字段 {field_ratio:.2%}，计算 {computed:.2%}",
                    "fields": ["contract_prepay_ratio"],
                }
            )

    # ------------------------------------------------------------------
    # 交叉校验 1：招标组织形式 vs 代理机构信息
    # 自主（自行）招标时不应有代理机构；委托代理招标时应能识别出代理机构。
    # ------------------------------------------------------------------
    org_form = str(fields["tender_org_form"].value or "") if "tender_org_form" in fields else ""
    agency_value = str(fields["agency"].value or "") if "agency" in fields else ""
    is_self_org = any(word in org_form for word in ("自行", "自主", "本单位", "自己"))
    is_agent_org = any(word in org_form for word in ("委托", "代理", "中介"))
    if is_self_org and agency_value.strip() and agency_value.strip() != "无":
        warnings.append(
            {
                "code": "ORG_FORM_AGENCY_CONFLICT",
                "message": f"组织形式为「{org_form}」（自主招标），但识别到代理机构「{agency_value.strip()}」；请核对，自主招标时无需代理机构信息",
                "fields": ["tender_org_form", "agency"],
            }
        )
    if is_agent_org and not agency_value.strip():
        warnings.append(
            {
                "code": "ORG_FORM_AGENCY_MISSING",
                "message": "组织形式为委托代理招标，但未识别到采购代理机构名称；请补充或核对",
                "fields": ["agency"],
            }
        )

    # ------------------------------------------------------------------
    # 交叉校验 2：采购方式 vs 评审（成交）方法
    # 竞争性磋商只允许综合评分法；竞争性谈判/询价只允许最低价成交。
    # ------------------------------------------------------------------
    method = str(fields["procurement_method"].value or "") if "procurement_method" in fields else ""
    price_method = str(fields["price_method"].value or "") if "price_method" in fields else ""
    if method and price_method:
        is_comprehensive = "综合" in price_method
        is_lowest = "最低" in price_method
        if "磋商" in method and is_lowest:
            errors.append(
                {
                    "code": "METHOD_PRICE_CONFLICT",
                    "message": "竞争性磋商依法只能采用综合评分法，与识别到的评审方法「" + price_method + "」矛盾",
                    "fields": ["procurement_method", "price_method"],
                }
            )
        elif ("谈判" in method or "询价" in method) and is_comprehensive:
            errors.append(
                {
                    "code": "METHOD_PRICE_CONFLICT",
                    "message": f"{method}依法采用最低价成交法，与识别到的评审方法「{price_method}」矛盾",
                    "fields": ["procurement_method", "price_method"],
                }
            )

    return errors, warnings


def apply_validation(
    field_values: dict[str, FieldValue],
    field_defs: dict[str, dict[str, Any]],
    structured: dict[str, Any],
    doc: Document,
    min_confidence: float = 0.6,
    fuzzy_threshold: float = 0.86,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """归一化 + 校验所有字段，返回 validation 汇总和清洗记录。"""
    all_errors: list[dict[str, Any]] = []
    all_warnings: list[dict[str, Any]] = []
    normalizations: list[dict[str, Any]] = []

    for key, field in field_values.items():
        field_def = field_defs.get(key, {})
        raw = field.value
        field.raw_value = raw

        normalized, notes = normalize_value(field_def, raw)
        if normalized != raw:
            normalizations.append(
                {"field": key, "label": field.label, "raw": raw, "value": normalized, "notes": notes}
            )
        field.value = normalized

        errors, warnings = validate_field(field_def, normalized)

        if field.value not in (None, "") and field.source not in {"computed", "inferred"}:
            grounded, matched = check_grounding(field.value, field.evidence, doc, fuzzy_threshold)
            if not grounded:
                warnings.append("值未能在原文证据中定位，可能是模型改写或推断")
                field.confidence = max(0.0, field.confidence - 0.25)
            elif matched and not field.evidence:
                field.evidence = []

        if errors:
            field.confidence = max(0.0, field.confidence - 0.3)
        if warnings:
            field.confidence = max(0.0, field.confidence - 0.05)

        field.errors = errors
        field.warnings = warnings

        if field.value in (None, ""):
            field.status = "missing"
        elif errors:
            field.status = "invalid"
        elif field.source == "inferred":
            field.status = "inferred"
        elif warnings or field.confidence < min_confidence:
            field.status = "low_confidence"
        else:
            field.status = "ok"

        for error in errors:
            all_errors.append(
                {"code": "FIELD_INVALID", "field": key, "message": error, "value": field.value}
            )
        for warning in warnings:
            all_warnings.append(
                {"code": "FIELD_WARNING", "field": key, "message": warning, "value": field.value}
            )

    cross_errors, cross_warnings = cross_validate(field_values, structured, field_defs)
    for item in cross_errors:
        all_errors.append(item)
        for key in item.get("fields", []):
            if key in field_values:
                field_values[key].warnings.append(item["message"])
                field_values[key].status = "invalid"
    for item in cross_warnings:
        all_warnings.append(item)
        for key in item.get("fields", []):
            if key in field_values:
                field_values[key].warnings.append(item["message"])

    validation = {
        "ok": not all_errors,
        "errors": all_errors,
        "warnings": all_warnings,
        "normalizations": normalizations,
    }
    return validation, normalizations
