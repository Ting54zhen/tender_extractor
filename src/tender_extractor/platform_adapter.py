"""把组件抽取结果转换成科瑞招标平台 AI 初始化草稿结构。

平台契约（2026-10-28 实测）：

    {
      "version": 0, "status": "DRAFT",
      "fields":   [{"field_code", "field_name", "value", "confidence", "evidence"}],
      "packages": [{"temporary_code", "package_name", "budget_amount", "confidence", "evidence"}],
      "subjects": [{"temporary_code", "package_temporary_code", "subject_name",
                    "attributes": [{"name", "value", "confidence", "evidence"}],
                    "confidence", "evidence"}],
      "warnings": [], "draft_uuid": "...", "project_id": null,
      "file_name": "...", "file_size": 123
    }

这里只做“抽取结果 -> 草稿 JSON”的适配，不负责草稿存储和 HTTP 接口。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from .cleaning import normalize_phone, parse_money, quantize_money
from .models import ExtractionResult, FieldValue
from .pipeline import Extractor

# 平台前端实际支持回填的 9 个字段码；顺序与页面一致。
FIELD_SPECS: tuple[tuple[str, str, str], ...] = (
    ("project_name", "project_name", "项目名称"),
    ("purchaser", "procurement_unit", "采购单位"),
    ("supervision_unit", "supervision_unit", "监督单位"),
    ("budget_amount", "budget_amount", "预算总金额"),
    ("procurement_method", "purchase_mode", "采购方式"),
    ("tender_org_form", "tender_org_form", "招标组织形式"),
    ("purchaser_contact_person", "contact_person", "采购单位联系人"),
    ("purchaser_contact", "contact_phone", "采购单位联系电话"),
    ("purchaser_address", "link_address", "联系地址"),
)

PURCHASE_MODE_CODES = {
    "公开招标": "PUBLIC_TENDER",
    "竞争性磋商": "COMPETITIVE_CONSULTATION",
    "竞争性谈判": "COMPETITIVE_NEGOTIATION",
    "单一来源采购": "SINGLE_SOURCE",
    "单一来源": "SINGLE_SOURCE",
    "询价": "INQUIRY",
}

TENDER_ORG_CODES = {"自主招标": "SELF", "委托招标": "ENTRUSTED"}

# 平台用两位小数、纯数字字符串（无千分位、无“元”）；组件内部精度配置暂不联动。
PLATFORM_MONEY_PRECISION = 2

SUCCESS_CODE = "00000000"


class PlatformRecognitionError(RuntimeError):
    """识别失败。code 供平台映射业务提示，message 直接给用户看。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _plain_money(value: Any, precision: int = PLATFORM_MONEY_PRECISION) -> str | None:
    amount = parse_money(value)
    if amount is None:
        return None
    quantized = quantize_money(amount, precision)
    if quantized is None:
        return None
    return f"{quantized:.{precision}f}"


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _evidence(field: FieldValue) -> list[dict[str, Any]]:
    return [
        {"quote": item.quote, "page_no": None, "section": item.section}
        for item in field.evidence
        if item.quote
    ]


def _platform_value(key: str, field: FieldValue | None) -> tuple[str | None, str | None]:
    """返回 (平台取值, 警告)。取值已按平台枚举/格式转换。"""
    if field is None or field.value in (None, ""):
        return None, None
    value = field.value

    if key == "budget_amount":
        return _plain_money(value), None

    if key == "procurement_method":
        code = PURCHASE_MODE_CODES.get(str(value).strip())
        if code is None:
            return None, f"采购方式“{value}”平台暂不支持，未回填，请人工选择"
        return code, None

    if key == "tender_org_form":
        code = TENDER_ORG_CODES.get(str(value).strip())
        if code is None:
            return None, f"招标组织形式“{value}”无法映射，未回填"
        return code, None

    if key == "purchaser_contact":
        phone = normalize_phone(value)
        return (phone, None) if phone else (None, f"采购人联系方式“{value}”不是有效电话，未回填")

    return _text(value), None


def _field_entry(
    key: str,
    code: str,
    name: str,
    field: FieldValue | None,
) -> tuple[dict[str, Any] | None, list[str]]:
    value, warning = _platform_value(key, field)
    warnings = [warning] if warning else []
    if value is None or field is None:
        return None, warnings
    return (
        {
            "field_code": code,
            "field_name": name,
            "value": value,
            "confidence": round(float(field.confidence or 0.0), 4),
            "evidence": _evidence(field),
        },
        warnings,
    )


def _number_text(value: Any) -> str | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _text(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"


def _subject_attributes(item: dict[str, Any]) -> list[dict[str, Any]]:
    attributes: list[dict[str, Any]] = []

    def add(name: str, value: Any, money: bool = False) -> None:
        text = _plain_money(value) if money else _number_text(value) if name == "数量" else _text(value)
        if text is None or text == "":
            return
        attributes.append(
            {"name": name, "value": text, "confidence": 0.9, "evidence": []}
        )

    add("技术参数", item.get("requirement"))
    add("数量", item.get("quantity"))
    add("单位", item.get("unit"))
    add("单价", item.get("unit_price"), money=True)

    amount = item.get("total_amount")
    if amount in (None, 0):
        amount = item.get("amount")
    add("金额", amount, money=True)
    return attributes


def _subjects(items: list[dict[str, Any]], package_code: str) -> list[dict[str, Any]]:
    subjects: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        name = _text(item.get("name"))
        if not name:
            continue
        subjects.append(
            {
                "temporary_code": f"S{index}",
                "package_temporary_code": package_code,
                "subject_name": name,
                "attributes": _subject_attributes(item),
                "confidence": 0.9,
                "evidence": [],
            }
        )
    return subjects


def _package(
    fields: dict[str, FieldValue],
    project_name: str | None,
) -> dict[str, Any]:
    budget_field = fields.get("budget_amount")
    return {
        "temporary_code": "P1",
        "package_name": project_name or "未命名项目包",
        "budget_amount": (_plain_money(budget_field.value) if budget_field else None) or "",
        "confidence": round(float(budget_field.confidence), 4) if budget_field else 0.0,
        "evidence": _evidence(budget_field) if budget_field else [],
    }


def to_platform_draft(
    result: ExtractionResult,
    *,
    file_name: str | None = None,
    file_size: int | None = None,
    draft_uuid: str | None = None,
) -> dict[str, Any]:
    """把 ExtractionResult 转成平台 initialization/drafts 的单条草稿。"""
    fields: list[dict[str, Any]] = []
    warnings: list[str] = []

    for key, code, name in FIELD_SPECS:
        entry, entry_warnings = _field_entry(key, code, name, result.fields.get(key))
        warnings.extend(entry_warnings)
        if entry is not None:
            fields.append(entry)

    project_name = _text(result.fields.get("project_name").value) if result.fields.get("project_name") else None
    budget_value = _text(result.fields.get("budget_amount").value) if result.fields.get("budget_amount") else None

    items = result.structured.get("procurement_items") or []
    package = _package(result.fields, project_name)
    subjects = _subjects(items, package["temporary_code"])

    if not project_name:
        warnings.append("未识别到项目名称")
    if not budget_value:
        warnings.append("未识别到预算金额")
    if not items:
        warnings.append("未识别到采购标的")
    field_codes = {entry["field_code"] for entry in fields}
    if not {"contact_person", "contact_phone"} & field_codes:
        warnings.append("未识别到采购单位联系人/联系电话")

    source = result.source or {}
    return {
        "version": 0,
        "status": "DRAFT",
        "fields": fields,
        "packages": [package],
        "subjects": subjects,
        "warnings": warnings,
        "draft_uuid": draft_uuid or str(uuid.uuid4()),
        "project_id": None,
        "file_name": file_name or source.get("filename") or "",
        "file_size": file_size if file_size is not None else source.get("size_bytes") or 0,
    }


def recognize_to_draft(
    path: str | Path,
    *,
    use_llm: bool = True,
    settings_override: dict[str, Any] | None = None,
    allow_non_tender: bool = False,
    draft_uuid: str | None = None,
) -> dict[str, Any]:
    """识别入口：DOCX -> 平台草稿。非招标文件抛 PlatformRecognitionError。"""
    path = Path(path)
    extractor = Extractor(settings_override=settings_override)
    result = extractor.extract(path, use_llm=use_llm, allow_non_tender=allow_non_tender)
    if result.meta.get("extraction_skipped"):
        reason = result.meta.get("reject_reason") or "文件不是招标/采购需求文档，已跳过识别"
        raise PlatformRecognitionError("NOT_TENDER_DOCUMENT", reason)
    return to_platform_draft(
        result,
        file_name=path.name,
        file_size=path.stat().st_size if path.exists() else None,
        draft_uuid=draft_uuid,
    )


def success_response(draft: dict[str, Any], request_id: str | None = None) -> dict[str, Any]:
    return {
        "code": SUCCESS_CODE,
        "message": "SUCCESS",
        "data": draft,
        "request_id": request_id or str(uuid.uuid4()),
    }


def error_response(code: str, message: str, request_id: str | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "data": None,
        "request_id": request_id or str(uuid.uuid4()),
    }
