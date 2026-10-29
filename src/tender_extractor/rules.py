"""确定性规则抽取。

规则层负责“能从原文直接读到”的字段：项目名称、预算、期限、商务要求、采购标的、
付款分期、资格/售后/交付条款、★/▲条款等。LLM 只在这些结果之上补缺和纠错，
这样即使模型很小或离线，核心字段也不会崩。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable

from .cleaning import (
    clean_cell,
    clean_text,
    format_money,
    is_placeholder,
    normalize_enum,
    normalize_for_match,
    parse_money,
    parse_number,
    parse_percent,
    quantize_money,
    split_sentences,
    strip_numbering,
)
from .document import Document
from .models import Block, Candidate, Evidence


@dataclass
class KV:
    label: str
    value: str
    block: Block
    source: str  # paragraph | table


@dataclass
class RulesOutput:
    candidates: dict[str, Candidate] = field(default_factory=dict)
    structured: dict[str, Any] = field(default_factory=dict)
    kvs: list[KV] = field(default_factory=list)


_PARAGRAPH_KV_RE = re.compile(
    r"^\s*(?:[（(]?[一二三四五六七八九十\d]{1,3}[）)、.．]\s*)?"
    r"([^：:]{1,30}?)\s*[：:]\s*(.*)$"
)


def _evidence(block: Block, quote: str | None = None) -> Evidence:
    text = clean_text(quote) if quote else block.text
    return Evidence(block_id=block.id, quote=text[:800], section=block.section)


def _candidate(
    value: Any,
    confidence: float,
    source: str,
    block: Block | None = None,
    quote: str | None = None,
    note: str = "",
) -> Candidate:
    return Candidate(
        value=value,
        confidence=max(0.0, min(0.99, confidence)),
        source=source,
        evidence=[_evidence(block, quote)] if block else [],
        note=note,
    )


def collect_kvs(doc: Document) -> list[KV]:
    kvs: list[KV] = []
    for block in doc.blocks:
        if block.kind == "paragraph":
            match = _PARAGRAPH_KV_RE.match(block.text)
            if not match:
                continue
            label = strip_numbering(match.group(1))
            value = clean_text(match.group(2))
            if label:
                kvs.append(KV(label=label, value=value, block=block, source="paragraph"))
        elif block.kind == "table" and block.rows:
            for row in block.rows:
                if len(row) != 2:
                    continue
                label = strip_numbering(row[0])
                value = clean_text(row[1])
                if label:
                    kvs.append(KV(label=label, value=value, block=block, source="table"))
    return kvs


# 标签比别名短时，若别名多出来的部分带着这些后缀，说明是另一个字段
# （例如“采购单位” vs “采购单位地址”），不能按前缀相似度匹配。
_STRICT_SUFFIXES = (
    "地址", "联系方式", "联系电话", "电话", "联系人", "编号", "名称", "邮箱", "传真",
)


def _match_score(label: str, alias: str) -> float:
    left = normalize_for_match(strip_numbering(label))
    right = normalize_for_match(alias)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left.startswith(right) and len(left) <= len(right) + 10:
        return 0.92
    if right in left:
        return 0.84
    if left in right and len(left) >= 2:
        extra = right.replace(left, "", 1)
        if any(suffix in extra for suffix in _STRICT_SUFFIXES):
            return 0.0
        return 0.72
    return 0.0


def _best_kvs(
    kvs: Iterable[KV],
    aliases: Iterable[str],
    section_hints: Iterable[str] = (),
    min_score: float = 0.7,
) -> list[tuple[KV, float]]:
    hints = [h for h in section_hints if h]
    scored: list[tuple[KV, float]] = []
    for kv in kvs:
        if is_placeholder(kv.value):
            continue
        score = max((_match_score(kv.label, alias) for alias in aliases), default=0.0)
        if score < min_score:
            continue
        if hints and any(h in kv.block.section or h in " ".join(kv.block.section_path) for h in hints):
            score = min(0.99, score + 0.03)
        scored.append((kv, score))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored


def _candidate_from_kvs(
    kvs: Iterable[KV],
    aliases: Iterable[str],
    section_hints: Iterable[str] = (),
) -> Candidate | None:
    scored = _best_kvs(kvs, aliases, section_hints)
    if not scored:
        return None
    kv, score = scored[0]
    return _candidate(
        value=kv.value,
        confidence=min(0.97, score * 0.94),
        source="table_label" if kv.source == "table" else "label",
        block=kv.block,
        quote=kv.value,
    )


# ---------------------------------------------------------------------------
# 表格定位
# ---------------------------------------------------------------------------


def find_project_table(doc: Document) -> tuple[dict[str, str], Block] | None:
    """项目概况里的“采购内容/采购预算/合同履行期限”三列表。"""
    for block in doc.tables:
        if not block.rows:
            continue
        header = [normalize_for_match(cell) for cell in block.rows[0]]
        if not any("采购内容" in h for h in header):
            continue
        if not any("采购预算" in h or "预算" in h for h in header):
            continue
        row = block.rows[1] if len(block.rows) > 1 else []
        if not row:
            continue
        data: dict[str, str] = {}
        for idx, name in enumerate(header):
            if idx < len(row):
                data[name] = row[idx]
        return data, block
    return None


def find_business_table(doc: Document) -> tuple[dict[str, str], Block] | None:
    known = {
        "标的提供的时间", "标的提供的地点", "付款方式", "验收要求",
        "履约保证金", "响应有效期", "其他", "服务期限", "交付时间", "交付地点",
    }
    for block in doc.tables:
        if not block.rows:
            continue
        pairs: dict[str, str] = {}
        for row in block.rows:
            if len(row) < 2:
                continue
            label = strip_numbering(row[0])
            if label in known:
                pairs[label] = clean_text(row[1])
        if len(pairs) >= 3:
            return pairs, block
    return None


def _header_index(header: list[str], keywords: Iterable[str]) -> int | None:
    """按关键字优先级找列：先匹配靠前的关键字，避免“品目名称”抢走“标的名称”。"""
    normalized = [normalize_for_match(h) for h in header]
    for keyword in keywords:
        target = normalize_for_match(keyword)
        if not target:
            continue
        for idx, cell in enumerate(normalized):
            if target in cell:
                return idx
    return None


def find_items_table(doc: Document) -> tuple[list[dict[str, Any]], Decimal | None, Block] | None:
    """采购标的表：表头含 序号/名称/数量/单价/金额。"""
    for block in doc.tables:
        if not block.rows or len(block.rows) < 2:
            continue
        header = block.rows[0]
        idx_seq = _header_index(header, ["序号"])
        idx_name = _header_index(
            header,
            ["服务项目名称", "标的名称", "设备名称", "货物名称", "产品名称", "服务名称", "采购内容", "品目名称", "名称"],
        )
        idx_category = _header_index(header, ["品目名称", "品目", "类别", "分类"])
        idx_req = _header_index(
            header,
            ["服务项目要求", "技术要求", "技术参数", "规格型号", "技术规格", "服务要求", "参数要求", "规格", "参数"],
        )
        idx_qty = _header_index(header, ["数量"])
        idx_unit = _header_index(header, ["单位"])
        idx_price = _header_index(header, ["单价"])
        idx_amount = _header_index(header, ["金额", "总价", "合计价"])
        # 单价列可以缺省（不少需求报告只给金额），只要名称/数量/金额齐全就能成表。
        if None in (idx_name, idx_qty, idx_amount):
            continue

        def cell(row: list[str], idx: int | None) -> str:
            if idx is None or idx >= len(row):
                return ""
            return clean_text(row[idx])

        items: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        declared_total: Decimal | None = None

        for raw_row in block.rows[1:]:
            row = [clean_cell(c) for c in raw_row]
            if not any(row):
                continue
            seq = cell(row, idx_seq)
            name = cell(row, idx_name)
            category = cell(row, idx_category)
            requirement = cell(row, idx_req)
            qty_raw = cell(row, idx_qty)
            unit = cell(row, idx_unit)
            unit_price_raw = cell(row, idx_price)
            amount_raw = cell(row, idx_amount)

            if "合计" in seq or "合计" in name or "总计" in seq or "总计" in name:
                for value in reversed(row):
                    number = parse_money(value, require_unit=False)
                    if number is not None:
                        declared_total = number
                        break
                continue

            qty_number = parse_number(qty_raw)
            if not unit and qty_raw:
                # 只把“1年”“1.00(年)”里的非数字部分当单位；
                # 纯数字“100”不能被正则回溯拆成 10 + 单位“0”。
                match = re.match(r"^\s*([\d.]+)\s*([^\d.\s].*)$", qty_raw)
                if match:
                    qty_number = parse_number(match.group(1))
                    unit = clean_text(match.group(2))
            quantity = float(qty_number) if qty_number is not None else None
            unit_price = parse_money(unit_price_raw, require_unit=False)
            amount = parse_money(amount_raw, require_unit=False)
            if amount is None and unit_price is not None and quantity is not None:
                amount = unit_price * Decimal(str(quantity))

            sub_item = {
                "seq": seq,
                "name": name,
                "category": category,
                "requirement": requirement,
                "quantity": quantity,
                "unit": unit,
                "unit_price": unit_price,
                "amount": amount,
            }

            if seq and name:
                current = {
                    "seq": seq,
                    "name": name,
                    "category": category,
                    "unit": unit,
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "amount": amount,
                    "sub_items": [sub_item],
                }
                items.append(current)
            elif current is not None:
                current["sub_items"].append(sub_item)
                if amount is not None:
                    current["amount"] = (current.get("amount") or Decimal(0)) + amount
                if requirement:
                    current["requirement"] = (current.get("requirement") or "") + requirement
            else:
                current = {
                    "seq": seq,
                    "name": name,
                    "category": category,
                    "unit": unit,
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "amount": amount,
                    "sub_items": [sub_item],
                }
                items.append(current)

        for item in items:
            requirements = [s["requirement"] for s in item["sub_items"] if s["requirement"]]
            item["requirement"] = "\n".join(requirements)
            item["total_amount"] = sum(
                (s["amount"] for s in item["sub_items"] if s["amount"] is not None),
                Decimal(0),
            )
            if item["total_amount"] == 0 and item.get("amount") is not None:
                item["total_amount"] = item["amount"]
        if items:
            return items, declared_total, block
    return None


# ---------------------------------------------------------------------------
# 结构化条款
# ---------------------------------------------------------------------------


def extract_business_requirements(doc: Document) -> tuple[dict[str, str], Block | None]:
    found = find_business_table(doc)
    if not found:
        return {}, None
    pairs, block = found
    return pairs, block


def extract_payment(business: dict[str, str], doc: Document, budget: Decimal | None) -> dict[str, Any]:
    texts = [business.get("付款方式", "")]
    for block in doc.blocks:
        if any(word in block.section for word in ("付款", "支付", "结算")):
            texts.append(block.text)
    text = "\n".join(t for t in texts if t)

    installments: list[dict[str, Any]] = []
    pattern = re.compile(
        r"(\d+)\s*期[：:，,、]?\s*(?:支付)?\s*"
        r"(预付款|尾款|进度款|验收款|质保金|第一期|第二期|第三期)?\s*"
        r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(亿元|万元|万|元)"
    )
    for match in pattern.finditer(text):
        period = int(match.group(1))
        kind = match.group(2) or ""
        amount = parse_money(f"{match.group(3)}{match.group(4)}")
        sentence = _sentence_around(text, match.start(), match.end())
        installments.append(
            {
                "period": period,
                "type": kind,
                "amount": amount,
                "amount_text": format_money(amount) if amount is not None else "",
                "terms": sentence,
            }
        )

    # 只写比例、不写金额的分期，如“1期：支付比例30%,2期：支付比例50%”。
    if not installments:
        ratio_pattern = re.compile(
            r"(\d+)\s*期[：:，,、]?\s*(?:支付)?\s*(?:比例|款项)?\s*(\d+(?:\.\d+)?)\s*%"
        )
        for match in ratio_pattern.finditer(text):
            period = int(match.group(1))
            ratio = float(match.group(2)) / 100
            installments.append(
                {
                    "period": period,
                    "type": "",
                    "amount": None,
                    "amount_text": "",
                    "ratio": ratio,
                    "terms": _sentence_around(text, match.start(), match.end()),
                }
            )

    # 兜底：不少需求报告不写“1期/2期”，而是“支付预付款 30%（410,040.00 元）”。
    # 按款项类型就近抓“带单位的金额”和百分数，避免把 30% 当成 30 元。
    if not installments:
        for period, kind in enumerate(("预付款", "尾款", "进度款", "验收款", "质保金"), start=1):
            match = re.search(kind, text)
            if not match:
                continue
            window = text[match.start() : match.start() + 80]
            amount_match = re.search(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(亿元|万元|万|元)", window)
            if not amount_match:
                continue
            amount = parse_money(amount_match.group(0))
            ratio_match = re.search(r"(\d+(?:\.\d+)?)\s*%", window)
            ratio = float(ratio_match.group(1)) / 100 if ratio_match else None
            installments.append(
                {
                    "period": period,
                    "type": kind,
                    "amount": amount,
                    "amount_text": format_money(amount) if amount is not None else "",
                    "terms": _sentence_around(text, match.start(), match.start() + len(kind)),
                    "ratio": ratio,
                }
            )

    seen: set[tuple[int, str, Decimal | None]] = set()
    unique: list[dict[str, Any]] = []
    for item in installments:
        amount_key = item["amount"].quantize(Decimal("0.01")) if item["amount"] is not None else None
        key = (item["period"], item["type"], amount_key)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    installments = unique

    prepay_parts = [
        i["amount"] for i in installments if "预付" in i["type"] and i["amount"] is not None
    ]
    balance_parts = [
        i["amount"] for i in installments if "尾款" in i["type"] and i["amount"] is not None
    ]
    prepay = sum(prepay_parts, Decimal(0)) if prepay_parts else None
    balance = sum(balance_parts, Decimal(0)) if balance_parts else None
    total = budget or ((prepay or 0) + (balance or 0) if (prepay or balance) else None)
    ratio = (prepay / total) if (prepay and total) else None
    if ratio is None and "预付" in text:
        # 原文常写成“支付工程预付款合同金额30%”，没有金额，只能取比例。
        match = re.search(r"预付(?:款)?[^。；\n]{0,40}?(\d+(?:\.\d+)?)\s*%", text)
        if match:
            ratio = Decimal(match.group(1)) / Decimal(100)
    methods = sorted({m for m in ("支票", "银行汇票", "电汇", "转账", "网银") if m in text})

    return {
        "text": clean_text(text),
        "installments": installments,
        "prepay_amount": prepay,
        "balance_amount": balance,
        "total_amount": total,
        "prepay_ratio": float(ratio) if ratio is not None else None,
        "methods": methods,
    }


def _sentence_around(text: str, start: int, end: int) -> str:
    left = max(text.rfind("。", 0, start), text.rfind("；", 0, start), text.rfind("\n", 0, start))
    right_candidates = [pos for pos in (text.find("。", end), text.find("；", end), text.find("\n", end)) if pos != -1]
    right = min(right_candidates) if right_candidates else min(len(text), end + 120)
    return clean_text(text[left + 1 : right + 1])


def extract_delivery(doc: Document, business: dict[str, str], contract_period: str | None) -> dict[str, Any]:
    texts: list[str] = []
    detail_block: Block | None = None
    for block in doc.blocks:
        if any(word in block.section for word in ("交付", "实施", "时间和地点")):
            texts.append(block.text)
            if len(block.text) > 20 and detail_block is None:
                detail_block = block
    text = "\n".join(texts)

    official_time = business.get("标的提供的时间") or contract_period or ""
    official_place = business.get("标的提供的地点") or ""

    detail_time = ""
    detail_place = ""
    match = re.search(r"(自[^，。；]{2,40}?至[^，。；]{2,40}?(?:止)?)", text)
    if match:
        detail_time = match.group(1)
    match = re.search(r"于(.+?)(?:交付|实施|完成)", text)
    if match:
        detail_place = match.group(1).strip(" ，。；")

    return {
        "time": clean_text(official_time or detail_time),
        "place": clean_text(official_place or detail_place),
        "detail_time": clean_text(detail_time),
        "detail_place": clean_text(detail_place),
        "text": clean_text(text),
        "block_id": detail_block.id if detail_block else "",
    }


def extract_section_text(doc: Document, keywords: Iterable[str], fallback_keywords: Iterable[str] = ()) -> str:
    words = [w for w in keywords if w]
    blocks = [
        block for block in doc.blocks
        if block.kind == "paragraph"
        and any(word in block.section or word in " ".join(block.section_path) for word in words)
    ]
    if not blocks:
        fallback = [w for w in fallback_keywords if w]
        blocks = [
            block for block in doc.blocks
            if block.kind == "paragraph" and any(word in block.text for word in fallback)
        ]
    return "\n".join(block.text for block in blocks if not _looks_like_heading(block.text))


def _looks_like_heading(text: str) -> bool:
    first = text.split("\n", 1)[0].strip()
    return bool(re.match(r"^[一二三四五六七八九十]+[、.．]", first)) and len(first) <= 50


def extract_sme_policy(doc: Document) -> tuple[str, Block | None]:
    candidates: list[tuple[int, str, Block]] = []
    for block in doc.blocks:
        if block.kind != "paragraph":
            continue
        text = block.text
        if "中小企业" not in text:
            continue
        if not any(word in text for word in ("预留", "专门面向", "分包", "中小微")):
            continue
        # 排除复述政策名称的短句
        if len(text) < 40:
            continue
        score = 0
        if "预留部分采购项目预算" in text:
            score += 4
        if "专门面向" in text:
            score += 3
        if "分包" in text:
            score += 2
        if "资格" in block.section or "供应商" in block.section:
            score += 2
        candidates.append((score, text, block))
    if not candidates:
        return "", None
    candidates.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
    return candidates[0][1], candidates[0][2]


def extract_star_clauses(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clauses: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        for sub in item.get("sub_items", []):
            requirement = sub.get("requirement") or ""
            for sentence in split_sentences(requirement):
                if "★" not in sentence and "▲" not in sentence:
                    continue
                parts = re.split(r"(?=[★▲])", sentence)
                for part in parts:
                    part = part.strip()
                    if not part or part[0] not in "★▲":
                        continue
                    mark = part[0]
                    text = part[1:].strip(" ：:，,、")
                    if len(text) < 4:
                        continue
                    key = normalize_for_match(text)
                    if key in seen:
                        continue
                    seen.add(key)
                    clauses.append(
                        {
                            "type": mark,
                            "item_seq": item.get("seq", ""),
                            "item_name": item.get("name", ""),
                            "text": text[:500],
                        }
                    )
    return clauses


# ---------------------------------------------------------------------------
# 单字段规则
# ---------------------------------------------------------------------------


_CHAPTER_MARKER_RE = re.compile(r"^第[一二三四五六七八九十百零0-9]+[章节篇部]$")
_GENERIC_HEADINGS = {"采购需求", "需求报告", "采购文件", "招标文件", "磋商文件", "谈判文件"}

_PROCUREMENT_METHOD_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"竞争性磋商|磋商文件", "竞争性磋商"),
    (r"竞争性谈判|谈判文件", "竞争性谈判"),
    (r"询价通知书|询价采购", "询价"),
    (r"单一来源", "单一来源采购"),
    (r"公开招标|招标公告", "公开招标"),
)


def _infer_purchaser(doc: Document) -> Candidate | None:
    texts = [doc.title] + [b.text.split("\n", 1)[0] for b in doc.blocks[:8] if b.kind == "paragraph"]
    for text in texts:
        match = re.match(r"^(.{2,40}?)(?:采购需求报告|采购需求|需求报告|招标文件)$", clean_text(text))
        if not match:
            continue
        name = match.group(1).strip()
        # “第二章 采购需求”“一、采购需求”这类章节标题不是采购人。
        if len(name) < 2 or name in _GENERIC_HEADINGS or _CHAPTER_MARKER_RE.match(name):
            continue
        if not re.search(r"[一-鿿A-Za-z]", name):
            continue
        block = next((b for b in doc.blocks if b.text.startswith(text)), None)
        return _candidate(name, 0.78, "inferred", block, quote=text, note="从报告标题推断，需人工确认")
    return None


def _infer_procurement_method(doc: Document) -> Candidate | None:
    filename = doc.path.replace("\\", "/").rsplit("/", 1)[-1]
    heading = f"{filename}\n{doc.title}"
    full_text = doc.full_text()
    for pattern, value in _PROCUREMENT_METHOD_PATTERNS:
        match = re.search(pattern, heading) or re.search(pattern, full_text)
        if not match:
            continue
        block = next((b for b in doc.blocks if match.group(0) in b.text), None)
        confidence = 0.8 if match.group(0) in heading else 0.7
        return _candidate(
            value,
            confidence,
            "inferred",
            block,
            quote=match.group(0),
            note="由文件标题/正文推断采购方式，需人工确认",
        )
    return None


def _enum_from_text(doc: Document, patterns: Iterable[str], enum_name: str) -> Candidate | None:
    full = doc.full_text()
    for pattern in patterns:
        match = re.search(pattern, full)
        if not match:
            continue
        block = next((b for b in doc.blocks if match.group(0) in b.text), None)
        value = normalize_enum(match.group(0), enum_name)
        if value is not None:
            return _candidate(value, 0.82, "label", block, quote=match.group(0))
    return None


def extract_rules(doc: Document, fields: dict[str, dict[str, Any]]) -> RulesOutput:
    kvs = collect_kvs(doc)
    output = RulesOutput(kvs=kvs)

    project_table_found = find_project_table(doc)
    project_table: dict[str, str] = {}
    project_block: Block | None = None
    if project_table_found:
        project_table, project_block = project_table_found

    business, business_block = extract_business_requirements(doc)
    items_found = find_items_table(doc)
    items: list[dict[str, Any]] = []
    declared_total: Decimal | None = None
    items_block: Block | None = None
    if items_found:
        items, declared_total, items_block = items_found

    project_name_candidate = _candidate_from_kvs(kvs, ["项目名称", "采购项目名称"], ["项目概况"])
    if not project_name_candidate and project_block:
        project_name_candidate = _candidate(
            project_table.get("采购内容", ""), 0.8, "table", project_block,
            quote=project_table.get("采购内容", ""), note="取自采购内容表",
        )
    if project_name_candidate:
        output.candidates["project_name"] = project_name_candidate

    project_number = _candidate_from_kvs(kvs, ["项目编号", "采购项目编号", "招标编号"], ["项目概况"])
    if project_number:
        output.candidates["project_number"] = project_number

    plan_number = _candidate_from_kvs(kvs, ["采购计划编号", "计划编号", "采购计划文号"])
    if plan_number:
        output.candidates["procurement_plan_number"] = plan_number

    method = _candidate_from_kvs(kvs, ["采购方式", "招标方式"], ["项目概况"])
    if not method:
        method = _infer_procurement_method(doc)
    if method:
        output.candidates["procurement_method"] = method

    budget_candidates: list[Candidate] = []
    for alias in ("预算金额", "采购预算", "项目预算", "最高限价"):
        candidate = _candidate_from_kvs(kvs, [alias], ["项目概况", "采购内容"])
        if candidate:
            budget_candidates.append(candidate)
    if project_block:
        for key, value in project_table.items():
            if "预算" in key and parse_money(value):
                budget_candidates.append(
                    _candidate(value, 0.9, "table", project_block, quote=value, note="取自项目概况表")
                )
    full_text = doc.full_text()
    for match in re.finditer(r"(?:投入|预算|经费|金额)[^。；\n]{0,20}?([0-9][0-9,]*(?:\.[0-9]+)?\s*(?:亿元|万元|元))", full_text):
        block = next((b for b in doc.blocks if match.group(0) in b.text), None)
        if block:
            budget_candidates.append(
                _candidate(match.group(1), 0.72, "regex", block, quote=match.group(0), note="从正文金额表述提取")
            )
    if budget_candidates:
        budget_candidates.sort(key=lambda c: c.confidence, reverse=True)
        output.candidates["budget_amount"] = budget_candidates[0]

    purchaser = _candidate_from_kvs(kvs, ["采购人", "采购单位", "招标人", "采购人名称"], ["项目概况"])
    if not purchaser:
        purchaser = _infer_purchaser(doc)
    if purchaser:
        output.candidates["purchaser"] = purchaser

    for key, aliases in {
        "purchaser_address": ["采购人地址", "采购单位地址"],
        "purchaser_contact_person": ["采购人联系人", "采购单位联系人", "采购人项目联系人"],
        "purchaser_contact": [
            "采购人联系方式", "采购人联系电话", "采购单位联系方式", "采购单位联系电话",
        ],
        "supervision_unit": ["监督单位", "监督部门"],
        "tender_org_form": ["招标组织形式", "组织形式"],
        "agency": ["采购代理机构", "代理机构", "招标代理机构"],
        "agency_address": ["采购代理机构地址", "代理机构地址"],
        "agency_contact": ["采购代理机构联系方式", "代理机构联系方式"],
        "agency_project_contact": ["采购代理机构项目联系人", "项目联系人"],
        "agency_project_phone": ["采购代理机构项目联系方式", "项目联系电话", "项目联系方式"],
        "procurement_item": ["采购品目", "品目名称"],
        "price_method": ["报价方式", "报价形式"],
        "candidate_count": ["中标候选供应商推荐家数", "推荐家数"],
        "winner_count": ["中标供应商数量", "中标人数量"],
        "valid_supplier_count": ["有效供应商家数", "最低有效供应商家数"],
        "agency_fee": ["代理服务费", "招标代理服务费", "采购代理服务费"],
        "inquiry_contact": ["质疑联系人"],
        "inquiry_phone": ["质疑联系电话", "质疑电话"],
        "inquiry_address": ["质疑联系地址", "质疑地址"],
        "business_score": ["商务部分", "商务分值", "商务评分"],
        "tech_score": ["技术部分", "技术分值", "技术评分"],
        "credit_score": ["综合信用分", "信用分值", "信用评分"],
        "price_score": ["报价得分", "价格分值", "价格分"],
        "tech_review_item_1": ["技术评审项"],
        "tech_review_score_1": ["技术评审项得分"],
        "tech_review_content_1": ["技术评审项内容"],
        "business_review_item_1": ["商务评审项"],
        "business_review_score_1": ["商务评审项得分"],
        "business_review_content_1": ["商务评审项内容"],
        "contract_party_a_rights": ["甲方权利义务", "甲方权利和义务"],
        "contract_party_b_rights": ["乙方权利义务", "乙方权利和义务"],
        "contract_ip": ["知识产权", "知识产权归属"],
        "contract_confidentiality": ["保密", "保密条款"],
        "contract_copies": ["合同份数", "一式几份"],
    }.items():
        candidate = _candidate_from_kvs(kvs, aliases)
        if candidate:
            output.candidates[key] = candidate

    target = _candidate_from_kvs(kvs, ["采购标的", "采购内容", "服务名称", "标的名称"], ["项目概况", "采购标的"])
    if not target and project_block:
        value = project_table.get("采购内容", "")
        if value:
            target = _candidate(value, 0.82, "table", project_block, quote=value)
    if items:
        names = [item.get("name", "") for item in items if item.get("name")]
        # “采购内容：单项预算在…以内的装修改造…”这类正文不是标的名称；
        # 采购标的表里的标的名称更可靠，优先采用。
        if names and (target is None or (target.source == "label" and len(str(target.value)) > 60)):
            target = _candidate(
                "、".join(dict.fromkeys(names[:3])),
                0.88,
                "table",
                items_block,
                quote="、".join(dict.fromkeys(names[:3])),
                note="取自采购标的表的标的名称",
            )
    if target:
        output.candidates["procurement_target"] = target

    industry = _candidate_from_kvs(kvs, ["所属行业", "行业"])
    if not industry:
        match = re.search(r"所属行业[：:]\s*([^\s，。；;、）)]+)", full_text)
        if match:
            block = next((b for b in doc.blocks if match.group(0) in b.text), None)
            industry = _candidate(match.group(1), 0.86, "regex", block, quote=match.group(0))
    if industry:
        output.candidates["industry"] = industry

    # 优先“合同履行期限”本身；项目概况表次之；最后才退回“标的提供的时间”，
    # 后者可能只是交付日期，不是完整合同期限。
    period = _candidate_from_kvs(
        kvs,
        ["合同履行期限", "服务期限", "服务期间", "项目完成期限"],
        ["项目概况", "采购内容", "商务要求"],
    )
    if not period and project_block:
        for key, value in project_table.items():
            if "期限" in key:
                period = _candidate(value, 0.9, "table", project_block, quote=value)
                break
    if not period:
        period = _candidate_from_kvs(kvs, ["标的提供的时间"], ["商务要求"])
    if period:
        output.candidates["contract_period"] = period

    consortium = _enum_from_text(
        doc,
        [
            r"(?:本项目)?(?:接受|不接受|允许|不允许)[^。；\n]{0,8}联合体(?:投标)?",
            r"联合体投标[：:]\s*(?:接受|不接受|允许|不允许)",
        ],
        "consortium",
    )
    if consortium:
        output.candidates["consortium_allowed"] = consortium

    sme_policy, sme_block = extract_sme_policy(doc)
    if sme_policy:
        output.candidates["sme_policy"] = _candidate(
            sme_policy, 0.84, "section", sme_block, quote=sme_policy[:500]
        )

    subcontracting = _enum_from_text(
        doc,
        [
            r"合同分包[：:]\s*(?:允许|不允许)",
            r"本项目[^。；\n]{0,12}(?:允许|不允许)(?:合同)?分包",
            r"(?:允许|不允许)(?:合同)?分包(?!给)",
        ],
        "subcontracting",
    )
    if not subcontracting and sme_policy and "分包" in sme_policy:
        # “必须分包给中小企业”不是合同分包条款，但可以作为“允许分包”的弱证据，
        # 标成 inferred 并降低置信度，交给人工确认。
        subcontracting = _candidate(
            "允许",
            0.55,
            "inferred",
            sme_block,
            quote="分包",
            note="需求报告只给出中小企业预留份额的分包安排，未明确合同分包条款；此值由该政策推断，需人工确认",
        )
    if subcontracting:
        output.candidates["subcontracting_allowed"] = subcontracting

    # 结构化部分
    output.structured["project_table"] = project_table
    output.structured["business_requirements"] = business
    output.structured["procurement_items"] = items
    output.structured["procurement_total_declared"] = declared_total
    output.structured["qualifications"] = extract_section_text(
        doc, ["资格", "供应商资格"], fallback_keywords=["投标人应具备", "资格要求"]
    )
    output.structured["after_sales"] = extract_section_text(
        doc, ["售后", "培训"], fallback_keywords=["质保", "售后服务"]
    )

    budget = None
    budget_precision = int(fields.get("budget_amount", {}).get("precision", 2))
    if "budget_amount" in output.candidates:
        budget = parse_money(output.candidates["budget_amount"].value)
        if budget is not None:
            budget = quantize_money(budget, budget_precision)
    payment = extract_payment(business, doc, budget)
    output.structured["payment"] = payment

    contract_period_value = output.candidates.get("contract_period")
    delivery = extract_delivery(
        doc, business, contract_period_value.value if contract_period_value else None
    )
    output.structured["delivery"] = delivery

    output.structured["star_clauses"] = extract_star_clauses(items)

    # 派生字段
    if items:
        names = [item.get("category") or item.get("name", "") for item in items]
        scope = "、".join(dict.fromkeys(n for n in names if n))
        if scope:
            output.candidates["contract_scope"] = Candidate(
                value=f"本项目服务范围包括：{scope}。",
                confidence=0.72,
                source="computed",
                evidence=[_evidence(items_block, items_block.text[:300])] if items_block else [],
                note="由采购标的名称汇总生成，需人工确认措辞",
            )

    if payment.get("prepay_ratio") is not None:
        ratio = payment["prepay_ratio"]
        value = f"{ratio * 100:.2f}%"
        if payment.get("prepay_amount") is not None:
            value += f"（{format_money(payment['prepay_amount'])}）"
        output.candidates["contract_prepay_ratio"] = Candidate(
            value=value,
            confidence=0.9 if payment.get("prepay_amount") is not None else 0.82,
            source="computed",
            evidence=[_evidence(business_block, business.get("付款方式", "")[:300])] if business_block else [],
            note="按预付款金额/预算金额计算" if payment.get("prepay_amount") is not None else "从原文预付款比例提取",
        )
    settlement_text = business.get("付款方式", "") or payment.get("text", "")
    if settlement_text:
        output.candidates["contract_settlement"] = Candidate(
            value=settlement_text,
            confidence=0.84 if business.get("付款方式") else 0.72,
            source="section" if business.get("付款方式") else "computed",
            evidence=[_evidence(business_block, settlement_text)] if business_block else [],
        )

    # 采购人地址只认原文显式字段；交付地点不等于采购人地址，不做推断。

    # 摘要兜底（LLM 可用时会覆盖）
    if project_name_candidate:
        parts = [f"本项目为{project_name_candidate.value}"]
        if budget is not None:
            parts.append(f"预算{format_money(budget, precision=budget_precision)}")
        if contract_period_value:
            parts.append(f"合同履行期限为{contract_period_value.value}")
        if items:
            parts.append(f"包含{len(items)}项采购标的")
        output.candidates["project_summary"] = Candidate(
            value="，".join(parts) + "。",
            confidence=0.66,
            source="computed",
            note="规则生成的摘要，LLM 可用时会重写",
        )

    return output
