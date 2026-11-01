"""投标文件响应抽取：抽取「投标方能提供什么」（资质/能力/业绩/承诺）。

与 ``relation_extract`` 对称：招标文件抽「要求什么」，投标文件抽「能提供
什么」。复用 ``llm_extract`` 的证据核验（``_locate_value``），保证响应条目
同样可回链原文、不编造——这是 L2 符合性比对的第一环。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .cleaning import clean_text, normalize_for_match
from .document import Document
from .llm_client import LLMClient, LLMError
from .llm_extract import _locate_value, _render_block
from .relation_extract import RelationItem

BID_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "responses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "category": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["name", "evidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["responses"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """你是投标文件的响应信息抽取引擎。你只输出 JSON，不输出解释。
任务：从投标文件中抽取「投标方能提供的资质、能力、业绩、承诺」等响应条目。
必须遵守：
1. 只抽取原文中明确出现的条目，找不到就返回空数组，禁止推测、补全或编造。
2. name 必须是原文原词（或最简完整表述）；evidence 必须是与该 name 对应的原文连续片段，不能改写、拼接或加块编号。
3. category 只填：资质 / 能力 / 业绩 / 承诺 之一，不确定就填空字符串。
4. 每个条目只代表一个独立对象，不要把多个对象合并。"""

# 投标响应类别关键词：用于「短值定位保护」和「上下文选择」。
_RESPONSE_KEYWORDS = [
    "资质", "资格", "证书", "业绩", "承诺", "能力", "人员", "经验",
    "荣誉", "授权", "履约", "案例", "合同", "专利", "软件著作权",
]


@dataclass
class BidResult:
    """投标响应抽取结果。"""

    responses: list[RelationItem]
    meta: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "responses": [item.to_dict() for item in self.responses],
            "meta": self.meta,
        }


def _select_context(doc: Document, char_budget: int = 7000) -> list[Any]:
    """挑出含投标响应关键词的块，控制总预算。"""
    scored: list[tuple[int, Any]] = []
    for block in doc.blocks:
        text = normalize_for_match(block.text)
        hits = sum(1 for kw in _RESPONSE_KEYWORDS if normalize_for_match(kw) in text)
        if hits > 0:
            scored.append((hits, block))
    if not scored:
        return []
    scored.sort(key=lambda item: (-item[0], item[1].index))
    selected: list[Any] = []
    used = 0
    for _, block in scored:
        size = min(len(block.text), 1500) + 40
        if selected and used + size > char_budget:
            continue
        selected.append(block)
        used += size
    selected.sort(key=lambda b: b.index)
    return selected


def build_bid_messages(doc: Document, char_budget: int = 7000) -> tuple[str, str]:
    context = "\n\n".join(_render_block(block) for block in _select_context(doc, char_budget))
    if not context:
        context = "（文档中未找到资质、能力、业绩或承诺相关章节）"
    user = f"""请从下面的投标文件中抽取「投标方能提供的响应条目」。

条目定义：投标方在文件中声明的资质、能力、业绩、承诺等。
- name：条目原文原词；
- category：资质 / 能力 / 业绩 / 承诺 之一，不确定填空；
- evidence：对应的原文连续片段。

文档片段（每段前的 [B数字|章节] 是片段编号，evidence 请直接引用片段中的原文）：
{context}

输出格式（只输出这个 JSON）：
{{"responses":[{{"name":"原文条目","category":"资质/能力/业绩/承诺","evidence":"原文连续片段"}}]}}
没有条目时返回空数组，不要编造。"""
    return SYSTEM_PROMPT, user


def _ground_response(doc: Document, name: str, category: str) -> RelationItem | None:
    """证据核验：name 必须能在原文定位，否则丢弃。"""
    name = clean_text(name)
    if not name:
        return None
    located = _locate_value(doc, name, _RESPONSE_KEYWORDS)
    if located is None:
        return None
    block, quote = located
    return RelationItem(
        kind="response",
        name=name,
        value=clean_text(category),
        evidence=quote,
        block_id=block.id,
        section=block.section,
        confidence=0.7,
        note="证据核验通过（未独立复核）",
    )


def parse_responses(doc: Document, data: dict[str, Any]) -> list[RelationItem]:
    responses: list[RelationItem] = []
    for raw in data.get("responses", []) or []:
        if not isinstance(raw, dict):
            continue
        item = _ground_response(doc, raw.get("name", ""), raw.get("category", ""))
        if item is not None:
            responses.append(item)
    return responses


def extract_bid_responses(
    client: LLMClient | None,
    doc: Document,
    settings: dict[str, Any] | None = None,
) -> BidResult:
    """抽取投标响应条目。任何异常都降级为「空结果 + meta.error」。"""
    settings = settings or {}
    llm_settings = settings.get("llm", {}) or {}
    meta: dict[str, Any] = {}
    responses: list[RelationItem] = []

    if client is None or not bool(llm_settings.get("enabled", True)):
        meta = {"enabled": False, "error": "LLM 未启用或客户端不可用"}
        return BidResult(responses=[], meta=meta)

    try:
        system, user = build_bid_messages(doc, int(llm_settings.get("context_char_budget", 7000)))
        data = client.chat_json(system, user, BID_SCHEMA)
        meta["calls"] = client.calls
        responses = parse_responses(doc, data)
    except LLMError as exc:
        meta["error"] = str(exc)

    meta["count"] = len(responses)
    return BidResult(responses=responses, meta=meta)
