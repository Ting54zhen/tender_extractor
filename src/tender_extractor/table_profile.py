"""表格画像：按表形态与列值特征推断表型和列角色。

为什么需要这一层：规则层此前把「表头必须含某些词」当成准入条件，换一种写法
就整表丢弃，而且丢弃是静默的（审核报告仍显示校验通过）。这里把列名降级成
加权提示，主判据换成可观测的结构特征——列数、是否有表头、每列的值类型分布。
推断不出的表返回 unknown 并保留原始列名，由上层放进未映射候选清单，让人看得见、
能补录，而不是悄悄丢掉。

本层只判断类型，不解析金额数值、不产出字段值，因此不参与证据链；真正产出
字段值的规则仍必须带 block_id 与原文引文。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .cleaning import clean_cell, normalize_for_match
from .models import Block, Document

KIND_KV = "kv"
KIND_ITEMS = "items"
KIND_CLAUSES = "clauses"
KIND_UNKNOWN = "unknown"

ROLE_LABEL = "label"
ROLE_VALUE = "value"
ROLE_SEQ = "seq"
ROLE_NAME = "name"
ROLE_CATEGORY = "category"
ROLE_QUANTITY = "quantity"
ROLE_UNIT = "unit"
ROLE_UNIT_PRICE = "unit_price"
ROLE_AMOUNT = "amount"
ROLE_MARKER = "marker"
ROLE_REQUIREMENT = "requirement"
ROLE_FLAG = "flag"
ROLE_UNKNOWN = "unknown"

#: 通用计量单位词，描述「值长什么样」，不含任何项目或品目的专有名词。
_UNIT_WORDS = frozenset(
    {
        "台", "套", "辆", "个", "项", "件", "批", "年", "月", "日", "次",
        "人", "名", "组", "块", "张", "只", "支", "箱", "吨", "公斤",
        "米", "平方米", "立方米", "升", "小时", "天", "学期", "学年", "期",
        "家", "处", "点位", "工位",
    }
)
_MARKERS = ("★", "▲")
_BOOL_MARKS = ("☑", "□")
_NUM_ONLY_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_MONEY_UNIT_RE = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:亿元|万元|万|元)")
_LONG_PUNCT = "。；，：,;:"
_HEADER_MAX_LEN = 12

#: 列名只作为加权提示：命中词素加分，未命中仍可靠值特征推断。
#: 这里放的是通用语义词素，不是「必须出现的表头白名单」。
_HEADER_HINTS: dict[str, tuple[str, ...]] = {
    ROLE_SEQ: ("序号", "序", "编号"),
    # 不放「产品」这类泛词：它会命中「是否核心产品」等标记列，抢走真正的名称列。
    ROLE_NAME: ("名称", "标的", "品目", "设备", "货物", "服务"),
    ROLE_CATEGORY: ("类别", "分类", "品目"),
    ROLE_QUANTITY: ("数量", "数目"),
    ROLE_UNIT: ("单位", "计量"),
    ROLE_UNIT_PRICE: ("单价",),
    ROLE_AMOUNT: ("金额", "总价", "预算", "合价"),
    ROLE_MARKER: ("符号", "性质", "标记"),
    ROLE_REQUIREMENT: ("要求", "参数", "规格", "指标", "内容", "说明"),
    ROLE_LABEL: ("条款", "项目", "名称"),
    ROLE_VALUE: ("要求", "内容", "说明", "值"),
}

_ROLES_BY_KIND: dict[str, tuple[str, ...]] = {
    KIND_KV: (ROLE_LABEL, ROLE_VALUE),
    KIND_ITEMS: (
        ROLE_SEQ, ROLE_NAME, ROLE_CATEGORY, ROLE_QUANTITY, ROLE_UNIT,
        ROLE_UNIT_PRICE, ROLE_AMOUNT, ROLE_REQUIREMENT, ROLE_FLAG,
    ),
    KIND_CLAUSES: (ROLE_SEQ, ROLE_MARKER, ROLE_REQUIREMENT, ROLE_NAME),
    KIND_UNKNOWN: (),
}


@dataclass(frozen=True)
class _ColumnStats:
    """一列的值类型分布，用于替代「看列名」做语义判断。"""

    numeric_ratio: float = 0.0
    money_ratio: float = 0.0
    marker_ratio: float = 0.0
    bool_ratio: float = 0.0
    unit_ratio: float = 0.0
    short_label_ratio: float = 0.0
    avg_len: float = 0.0


@dataclass(frozen=True)
class ColumnProfile:
    index: int
    header: str
    role: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "header": self.header,
            "role": self.role,
            "confidence": round(self.confidence, 2),
        }


@dataclass
class TableProfile:
    block: Block
    kind: str
    headers: list[str]
    header_row: int | None
    columns: list[ColumnProfile]
    confidence: float
    unmapped_headers: list[str] = field(default_factory=list)

    def role_index(self, role: str) -> int | None:
        """返回某角色所在的列号；没有该角色时返回 None（表示原文未提供）。"""
        for col in self.columns:
            if col.role == role:
                return col.index
        return None

    def data_rows(self) -> list[list[str]]:
        rows = self.block.rows or []
        start = self.header_row + 1 if self.header_row is not None else 0
        return rows[start:]

    def to_dict(self) -> dict[str, Any]:
        """供 structured 与审核报告回显，block_id 可回链到原表格。"""
        return {
            "block_id": self.block.id,
            "section": self.block.section,
            "kind": self.kind,
            "confidence": round(self.confidence, 2),
            "headers": self.headers,
            "columns": [col.to_dict() for col in self.columns],
            "unmapped_headers": self.unmapped_headers,
        }


def _column_stats(rows: list[list[str]], index: int) -> _ColumnStats:
    values = [clean_cell(row[index]) for row in rows if index < len(row)]
    values = [value for value in values if value]
    if not values:
        return _ColumnStats()
    total = len(values)
    return _ColumnStats(
        numeric_ratio=sum(bool(_NUM_ONLY_RE.fullmatch(v)) for v in values) / total,
        money_ratio=sum(bool(_MONEY_UNIT_RE.search(v)) for v in values) / total,
        marker_ratio=sum(any(m in v for m in _MARKERS) for v in values) / total,
        bool_ratio=sum(any(m in v for m in _BOOL_MARKS) for v in values) / total,
        unit_ratio=sum(v in _UNIT_WORDS for v in values) / total,
        short_label_ratio=sum(
            len(v) <= _HEADER_MAX_LEN and not any(p in v for p in _LONG_PUNCT)
            for v in values
        ) / total,
        avg_len=sum(len(v) for v in values) / total,
    )


def _find_header_row(rows: list[list[str]]) -> int | None:
    """首行全部是短标签才当作表头；否则整张表都是数据行（如商务要求表）。"""
    if not rows or not rows[0]:
        return None
    first = rows[0]
    if all(
        0 < len(clean_cell(cell)) <= _HEADER_MAX_LEN
        and not any(p in cell for p in _LONG_PUNCT)
        for cell in first
    ):
        return 0
    return None


def _hint(header: str, role: str) -> float:
    normalized = normalize_for_match(header)
    words = _HEADER_HINTS.get(role, ())
    return 0.35 if any(normalize_for_match(word) in normalized for word in words) else 0.0


def _role_score(
    role: str, header: str, stats: _ColumnStats, index: int, total_cols: int
) -> float:
    """列角色得分 = 列名提示 + 值特征；提示缺失时值特征仍可独立判定。"""
    hint = _hint(header, role)
    if role == ROLE_SEQ:
        value = stats.numeric_ratio * 0.3 + (0.2 if index == 0 else 0.0)
    elif role == ROLE_NAME:
        # 名称列通常在表格左侧；同分时靠这个通用先验避免被右侧标记列抢走。
        left_bias = 0.1 * (1.0 - index / max(1, total_cols - 1))
        value = (
            (1.0 - stats.numeric_ratio) * 0.3
            + stats.short_label_ratio * 0.2
            + left_bias
        )
    elif role == ROLE_CATEGORY:
        value = stats.short_label_ratio * 0.25
    elif role == ROLE_QUANTITY:
        value = stats.numeric_ratio * 0.55
    elif role == ROLE_UNIT:
        value = stats.unit_ratio * 0.6
    elif role == ROLE_UNIT_PRICE:
        value = stats.money_ratio * 0.5
    elif role == ROLE_AMOUNT:
        value = stats.money_ratio * 0.5
    elif role == ROLE_MARKER:
        value = stats.marker_ratio * 0.6
    elif role == ROLE_FLAG:
        value = stats.bool_ratio * 0.6
    elif role == ROLE_REQUIREMENT:
        value = min(stats.avg_len / 40.0, 1.0) * 0.5
    elif role == ROLE_LABEL:
        value = stats.short_label_ratio * 0.5 + (0.15 if index == 0 else 0.0)
    elif role == ROLE_VALUE:
        value = min(stats.avg_len / 30.0, 1.0) * 0.5 + (
            0.15 if index == total_cols - 1 else 0.0
        )
    else:
        value = 0.0
    return hint + value


def _assign_roles(
    headers: list[str], stats: list[_ColumnStats], roles: tuple[str, ...]
) -> list[ColumnProfile]:
    """按得分贪心分配角色，一列一个角色、一个角色只占一列。"""
    scored = sorted(
        (
            (_role_score(role, headers[i], stats[i], i, len(headers)), role, i)
            for role in roles
            for i in range(len(headers))
        ),
        reverse=True,
    )
    used_cols: set[int] = set()
    used_roles: set[str] = set()
    assigned: dict[int, tuple[str, float]] = {}
    for score, role, index in scored:
        if index in used_cols or role in used_roles or score < 0.15:
            continue
        assigned[index] = (role, score)
        used_cols.add(index)
        used_roles.add(role)
    return [
        ColumnProfile(
            index=i,
            header=headers[i],
            role=assigned.get(i, (ROLE_UNKNOWN, 0.0))[0],
            confidence=min(1.0, assigned.get(i, (ROLE_UNKNOWN, 0.0))[1]),
        )
        for i in range(len(headers))
    ]


def _classify(
    headers: list[str], data: list[list[str]], stats: list[_ColumnStats]
) -> tuple[str, float]:
    total = len(headers)
    if total < 2:
        return KIND_UNKNOWN, 0.2
    # 条款表要有独立的符号列（值通常就是「★」「▲」或空）。长文本里偶尔带 ★
    # 的键值表（如售后条款）不能算，否则非技术条款会混进技术参数附表。
    has_marker_column = any(s.marker_ratio > 0.1 and s.avg_len <= 3 for s in stats)
    # 阈值取 15 而非 30：短条款表（每条十几字）同样是条款表，不能因为句子短
    # 就退回 unknown，否则 ★▲ 条款会再次被静默丢掉。
    if has_marker_column and any(s.avg_len > 15 for s in stats):
        return KIND_CLAUSES, 0.85
    if (
        total == 2
        and stats[0].short_label_ratio >= 0.6
        and stats[1].avg_len > stats[0].avg_len + 2
    ):
        return KIND_KV, 0.8
    looks_named = any(
        _role_score(ROLE_NAME, headers[i], stats[i], i, total) >= 0.5
        for i in range(total)
    )
    has_quantity = any(s.numeric_ratio >= 0.6 for s in stats)
    if looks_named and has_quantity and len(data) >= 2:
        return KIND_ITEMS, 0.75
    return KIND_UNKNOWN, 0.2


def profile_table(block: Block) -> TableProfile:
    """给一张表做画像：判表型、判列角色，并把判不出的列名原样留下。"""
    rows = block.rows or []
    if len(rows) < 2:
        return TableProfile(block, KIND_UNKNOWN, [], None, [], 0.0)

    header_row = _find_header_row(rows)
    if header_row is not None:
        headers = [clean_cell(cell) for cell in rows[header_row]]
    else:
        width = max(len(row) for row in rows)
        headers = [f"列{i + 1}" for i in range(width)]
    data = rows[header_row + 1:] if header_row is not None else rows
    stats = [_column_stats(data, i) for i in range(len(headers))]
    kind, confidence = _classify(headers, data, stats)
    columns = _assign_roles(headers, stats, _ROLES_BY_KIND[kind])
    unmapped = (
        [col.header for col in columns if col.role == ROLE_UNKNOWN]
        if header_row is not None
        else []
    )
    return TableProfile(
        block=block,
        kind=kind,
        headers=headers,
        header_row=header_row,
        columns=columns,
        confidence=confidence,
        unmapped_headers=unmapped,
    )


def profile_document(doc: Document) -> list[TableProfile]:
    """按正文顺序给所有表格画像，供规则层与审核报告共用。"""
    return [profile_table(block) for block in doc.tables]
