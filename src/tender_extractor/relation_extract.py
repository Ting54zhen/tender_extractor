"""列表型实体/关系抽取：资格条件、评分点、废标条款。

按类别**分别调用模型**（三次独立请求），让 3B 小模型每轮只专注一类实体。
三条在真实招标文件上验证过的经验：

1. schema 的顶层 key 必须**语义化**（``qualifications`` 而非通用的 ``items``），
   否则小模型在约束解码下容易直接返回空数组；
2. system prompt 要**宽松**，过度强调「不能写成泛称」会让模型判定「没有条目」；
3. 评分表是表格结构，必须在提示词里说明「每行一个评分项、分值在括号里」，
   否则模型读不懂表格。

每个条目同样复用 ``llm_extract`` 的证据核验：值必须真实出现在原文，短值要求
类别关键词上下文，定位不到就丢弃——保证图谱节点不是模型编造的。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .cleaning import clean_text, normalize_for_match, parse_number
from .document import Document
from .llm_client import LLMClient, LLMError
from .llm_extract import _locate_value, _render_block

ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "value": {"type": "string"},
        "evidence": {"type": "string"},
    },
    "required": ["name", "evidence"],
    "additionalProperties": False,
}


def category_schema(kind: str) -> dict[str, Any]:
    """按类别生成语义化 schema（顶层 key 用真实类别名）。"""
    return {
        "type": "object",
        "properties": {kind: {"type": "array", "items": ITEM_SCHEMA}},
        "required": [kind],
        "additionalProperties": False,
    }


SYSTEM_PROMPT = """你是政府采购招标文件的信息抽取引擎。你只输出 JSON，不输出解释。
只抽取原文中明确出现的信息，找不到就返回空数组，禁止推测、补全或编造。
evidence 必须是原文中的连续片段。"""

# 三类实体的元信息：标签、提示词、章节关键词、短值定位别名。
CATEGORY_META: dict[str, dict[str, Any]] = {
    "qualifications": {
        "label": "资格条件",
        "prompt": (
            "供应商必须具备的资质、资格、证书、业绩、信用等要求。"
            "name 填资格要求的原文表述（如「具有独立承担民事责任的能力」），value 可留空。"
        ),
        "section_hints": ["资格", "资质"],
        "aliases": ["资格", "资质", "证书", "业绩", "信用", "供应商要求"],
    },
    "scoring_items": {
        "label": "评分点",
        "prompt": (
            "评审办法里的评分项。表格中每一行是一个评分项：名称在中间列"
            "（如「服务工作目标及管理方案1」），分值写在名称后的括号里（如「(2.0分)」）。"
            "name 填评分项名称，value 填分值数字。"
        ),
        "section_hints": ["评标", "评审", "评分", "分值"],
        "aliases": ["评审", "评分", "评标", "分值", "得分", "评分标准"],
    },
    "disqualifications": {
        "label": "废标条款",
        "prompt": "会导致废标或投标无效的情形。name 填条款原文，value 填后果（如废标）。",
        "section_hints": ["废标", "无效", "否决", "取消"],
        "aliases": ["废标", "无效投标", "否决", "取消投标", "中标无效", "终止"],
    },
}


# 类别核心词：正文命中时给高权重，避免「开标/评标」这类宽泛章节把噪声带进来。
CORE_HINTS: dict[str, list[str]] = {
    "qualifications": ["资格要求", "资格条件", "供应商资格", "投标人资格"],
    "scoring_items": ["评审因素", "评分标准", "评审标准", "分值构成"],
    "disqualifications": [
        "废标", "无效投标", "否决投标", "投标无效",
        # 具体废标情形往往不含「废标」二字，补上才能覆盖条款主体。
        "不足三家", "违法违规", "超过采购预算", "采购任务取消",
    ],
}

# 单类上下文的块数上限：3B 模型在过多片段里会「顾此失彼」，宁可少而准。
MAX_CONTEXT_BLOCKS = 20


@dataclass
class RelationItem:
    """一条抽取出的实体（图谱中的一个节点）。"""

    kind: str                       # qualifications | scoring_items | disqualifications
    name: str                       # 主文本：资质名 / 评分项名 / 废标条款内容
    value: str = ""                 # 附加值：具体要求 / 分值 / 触发后果
    evidence: str = ""              # 原文连续片段
    block_id: str = ""
    section: str = ""
    confidence: float = 0.0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "value": self.value,
            "evidence": self.evidence,
            "block_id": self.block_id,
            "section": self.section,
            "confidence": round(float(self.confidence), 4),
            "note": self.note,
        }


@dataclass
class RelationResult:
    """三类列表实体的抽取结果。"""

    qualifications: list[RelationItem] = field(default_factory=list)
    scoring_items: list[RelationItem] = field(default_factory=list)
    disqualifications: list[RelationItem] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "qualifications": [item.to_dict() for item in self.qualifications],
            "scoring_items": [item.to_dict() for item in self.scoring_items],
            "disqualifications": [item.to_dict() for item in self.disqualifications],
            "meta": self.meta,
        }


def _densest_span(blocks: list[Any], core_blocks: list[Any]) -> list[Any]:
    """定位条款区：表格块只取自身；段落块取核心词最密集的连续段。

    教训：
    - 评分表是独立表格，整表喂入效果最好，扩展到后续正文反而稀释；
    - 废标条款的标题含「废标」，具体情形（如「不足三家」）不含关键词，
      需要用「间隔容忍」把相邻段落连成一段，再取最长段；
    - 若直接取「首个命中块到章节末尾」，会把前面的评标流程噪声带进来。
    """
    if not core_blocks:
        return []
    tables = [b for b in core_blocks if b.kind == "table"]
    if tables:
        return [tables[0]]

    indices = sorted(b.index for b in core_blocks)
    runs: list[list[int]] = [[indices[0]]]
    for idx in indices[1:]:
        if idx - runs[-1][-1] <= 3:
            runs[-1].append(idx)
        else:
            runs.append([idx])
    best = max(runs, key=len)
    low, high = best[0], best[-1]
    return [b for b in blocks if low <= b.index <= high]


def _select_category_context(doc: Document, kind: str, char_budget: int = 6000) -> list[Any]:
    """按章节分组选上下文：整章优先，超大章节退化为「核心词连续段」。

    实测教训：
    - 按关键词逐块打分会让同一章节的条款被零散块挤掉（废标条款尤其明显）；
    - 把整章无脑塞进去又会让 3B 模型「顾此失彼」，反而抽不出来。
    所以先保证章节完整，超出预算时只取核心词所在的连续区段。
    """
    meta = CATEGORY_META[kind]
    hints = meta["section_hints"]
    aliases = meta["aliases"]
    core_hints = CORE_HINTS[kind]

    groups: dict[str, list[Any]] = {}
    for block in doc.blocks:
        groups.setdefault(block.section, []).append(block)

    candidates: list[tuple[float, list[Any]]] = []
    for section, blocks in groups.items():
        section_text = normalize_for_match(section + " " + " ".join(blocks[0].section_path))
        section_hit = any(normalize_for_match(hint) in section_text for hint in hints)
        core_blocks = [
            b for b in blocks
            if any(normalize_for_match(hint) in normalize_for_match(b.text) for hint in core_hints)
        ]
        alias_blocks = [
            b for b in blocks
            if any(normalize_for_match(alias) in normalize_for_match(b.text) for alias in aliases)
        ]

        if section_hit and len(blocks) <= 15:
            # 小章节（如「投标人的资格要求」）整章喂入，召回最完整。
            candidates.append((10.0, blocks))
        elif core_blocks:
            # 大章节（评标、开标定标）只取核心词最密集的连续段，避免噪声稀释。
            span = _densest_span(blocks, core_blocks)
            base = 12.0 if section_hit else 8.0
            if any(b.kind == "table" for b in span):
                # 评分表是结构化表格，能一次覆盖多个评分项，优先级更高。
                base += 4.0
            candidates.append((base + len(core_blocks), span))
        elif section_hit:
            candidates.append((6.0, blocks[:MAX_CONTEXT_BLOCKS]))
        elif alias_blocks:
            candidates.append((1.0, alias_blocks))

    if not candidates:
        return []

    candidates.sort(key=lambda item: (-item[0], item[1][0].index))
    # 只取最高分的组：多章节混合会把噪声带进上下文，3B 模型反而抽不出来。
    selected: list[Any] = list(candidates[0][1])
    if len(selected) < 3 and len(candidates) > 1:
        selected.extend(candidates[1][1])
    selected.sort(key=lambda b: b.index)
    return selected[:MAX_CONTEXT_BLOCKS]


def build_category_messages(
    doc: Document,
    kind: str,
    char_budget: int = 3500,
) -> tuple[str, str]:
    meta = CATEGORY_META[kind]
    context = "\n\n".join(
        _render_block(block) for block in _select_category_context(doc, kind, char_budget)
    )
    if not context:
        context = f"（文档中未找到与「{meta['label']}」相关的章节）"
    user = (
        f"请从下面的招标文件中抽取「{meta['label']}」。\n\n"
        f"定义：{meta['prompt']}\n\n"
        f"文档片段（每段前的 [B数字|章节] 是片段编号，evidence 请直接引用片段中的原文）：\n"
        f"{context}\n\n"
        f"输出格式（只输出这个 JSON）：\n"
        f'{{"{kind}":[{{"name":"原文条目","value":"附加值","evidence":"原文连续片段"}}]}}'
    )
    return SYSTEM_PROMPT, user


def _coerce_value(kind: str, raw_value: Any) -> str:
    """附加值归一化：评分点分值转纯数字，其余保留原文。"""
    value = clean_text(raw_value)
    if not value:
        return ""
    if kind == "scoring_items":
        number = parse_number(value)
        if number is not None:
            return str(number)
    return value


def _ground_item(doc: Document, kind: str, name: str, value: str) -> RelationItem | None:
    """证据核验：name 必须能在原文定位，否则丢弃。

    复用 ``llm_extract._locate_value``：值必须原样出现在原文；短值（<4 字）
    还要求类别关键词出现在附近，避免把「1」「允许」这类词误认成实体。
    """
    name = clean_text(name)
    if not name:
        return None
    located = _locate_value(doc, name, CATEGORY_META[kind]["aliases"])
    if located is None:
        return None
    block, quote = located
    value_text = _coerce_value(kind, value)
    if kind == "scoring_items" and not value_text:
        # 模型常漏填分值：从原文证据的括号里补（如「(2.0分)」）。
        match = re.search(r"[（(]\s*(\d+(?:\.\d+)?)\s*分", quote or block.text)
        if match:
            value_text = match.group(1)
    return RelationItem(
        kind=kind,
        name=name,
        value=value_text,
        evidence=quote,
        block_id=block.id,
        section=block.section,
        confidence=0.7,
        note="证据核验通过（未独立复核）",
    )


def parse_category(doc: Document, kind: str, data: dict[str, Any]) -> list[RelationItem]:
    items: list[RelationItem] = []
    for raw in data.get(kind, []) or []:
        if not isinstance(raw, dict):
            continue
        item = _ground_item(doc, kind, raw.get("name", ""), raw.get("value", ""))
        if item is not None:
            items.append(item)
    return items


def extract_relations(
    client: LLMClient | None,
    doc: Document,
    settings: dict[str, Any] | None = None,
) -> RelationResult:
    """分三类抽取。任何异常都降级为「空结果 + meta.error」，不抛给上层。"""
    result = RelationResult()
    settings = settings or {}
    llm_settings = settings.get("llm", {}) or {}
    if client is None or not bool(llm_settings.get("enabled", True)):
        result.meta = {"enabled": False, "error": "LLM 未启用或客户端不可用"}
        return result

    budget = int(llm_settings.get("context_char_budget", 7000))
    per_category = max(4000, budget)
    calls_before = client.calls
    errors: list[str] = []

    for kind, target in (
        ("qualifications", result.qualifications),
        ("scoring_items", result.scoring_items),
        ("disqualifications", result.disqualifications),
    ):
        try:
            system, user = build_category_messages(doc, kind, per_category)
            data = client.chat_json(system, user, category_schema(kind))
            items = parse_category(doc, kind, data)
            # 小模型偶发返回空数组：补一次重试，避免演示时「什么都没抽到」。
            if not items:
                data = client.chat_json(system, user, category_schema(kind))
                items = parse_category(doc, kind, data)
                result.meta.setdefault("retried", []).append(kind)
            target.extend(items)
        except LLMError as exc:
            errors.append(f"{kind}: {exc}")

    result.meta["calls"] = client.calls - calls_before
    if errors:
        result.meta["error"] = "；".join(errors)
    result.meta["counts"] = {
        "qualifications": len(result.qualifications),
        "scoring_items": len(result.scoring_items),
        "disqualifications": len(result.disqualifications),
    }
    return result
