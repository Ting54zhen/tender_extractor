"""本地大模型抽取与“校验失败回喂”修复。

模型只做两件事：
1. 在规则候选之外补缺（尤其是需要理解上下文才能确定的字段）；
2. 在字段校验失败时，带着错误原因和原文片段重新回答。

提示词和输出 schema 都固定，避免小模型自由发挥。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from .cleaning import clean_text, normalize_for_match
from .document import Document
from .llm_client import LLMClient
from .models import Block, Candidate, Evidence


LLM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["key", "value", "evidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["fields"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """你是政府采购招标文件信息抽取引擎。你只输出 JSON，不输出解释。
必须遵守：
1. 只抽取原文中明确出现的信息。找不到就返回空字符串，禁止推测、补全或编造。
2. value 必须来自原文；evidence 必须是与该值对应的原文连续片段，不能改写。
3. 金额、期限、电话号码保留原文写法；不要自己计算，除非字段说明要求。
4. 每个字段最多返回一个值。多个候选时选最明确、最完整、最正式的那个。
5. 不要输出字段列表以外的 key。"""

GROUP_SECTIONS = {
    "basic": ["项目概况", "采购内容", "封面", "采购标的"],
    "commercial": ["项目概况", "采购标的", "商务", "资格", "售后", "交付", "付款", "款项支付"],
    "scoring": ["评审", "评分", "评标", "分值"],
    "contract": ["付款", "款项支付", "交付", "采购标的", "合同", "售后", "资格"],
}

GROUP_TITLES = {
    "basic": "项目基本信息",
    "commercial": "商务与政策",
    "scoring": "评审办法",
    "contract": "合同条款",
}


@dataclass
class LLMStats:
    enabled: bool = False
    available: bool = False
    provider: str = ""
    model: str = ""
    calls: int = 0
    error: str = ""
    groups: dict[str, int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.groups is None:
            self.groups = {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "available": self.available,
            "provider": self.provider,
            "model": self.model,
            "calls": self.calls,
            "error": self.error,
            "groups": self.groups,
        }


def _render_block(block: Block, max_chars: int = 1500) -> str:
    if block.kind != "table" or not block.rows:
        text = block.text[:max_chars]
        if len(block.text) > max_chars:
            text += "…"
        return f"[{block.id}|{block.section}] {text}"

    rows = block.rows
    header = " | ".join(rows[0])
    normalized_header = [normalize_for_match(h) for h in rows[0]]
    is_item_table = any("数量" in h for h in normalized_header) and any(
        "金额" in h or "总价" in h for h in normalized_header
    )
    lines: list[str] = [f"[{block.id}|表|{block.section}] {header}"]
    if is_item_table:
        # 采购标的表可能很长，只保留序号/名称/数量/单价/金额，避免挤爆上下文。
        for row in rows[1:13]:
            if not any(row):
                continue
            short = [cell[:80] for cell in row]
            lines.append(" | ".join(short))
        if len(rows) > 13:
            lines.append("…（中间行省略）")
    else:
        for row in rows[1:8]:
            lines.append(" | ".join(cell[:160] for cell in row))
        if len(rows) > 8:
            lines.append("…（其余行省略）")
    return "\n".join(lines)


def _field_prompt_lines(fields: Iterable[dict[str, Any]]) -> str:
    lines: list[str] = []
    for field in fields:
        aliases = "、".join(field.get("aliases") or [])
        examples = "；".join(field.get("examples") or [])
        parts = [f"- {field['key']}（{field['label']}，类型 {field.get('type', 'text')}）"]
        if field.get("description"):
            parts.append(f"说明：{field['description']}")
        if aliases:
            parts.append(f"原文可能写作：{aliases}")
        if examples:
            parts.append(f"示例：{examples}")
        if field.get("enum"):
            parts.append(f"允许值：{'/'.join(field['enum'])}")
        lines.append("；".join(parts))
    return "\n".join(lines)


def select_context(
    doc: Document,
    group: str,
    fields: list[dict[str, Any]],
    char_budget: int = 7000,
) -> list[Block]:
    sections = GROUP_SECTIONS.get(group, [])
    aliases: list[str] = []
    for field in fields:
        aliases.extend(field.get("aliases") or [])

    scored: list[tuple[float, Block]] = []
    for block in doc.blocks:
        score = 0.0
        if any(section in block.section or section in " ".join(block.section_path) for section in sections):
            score += 3.0
        if any(normalize_for_match(alias) and normalize_for_match(alias) in normalize_for_match(block.text) for alias in aliases):
            score += 2.0
        if block.kind == "table":
            score += 1.0
        if block.index < 4:
            score += 0.5
        if score > 0:
            scored.append((score, block))

    if not scored:
        return []

    scored.sort(key=lambda item: (-item[0], item[1].index))
    selected: list[Block] = []
    used = 0
    for _, block in scored:
        rendered_len = min(len(block.text), 1500) + 40
        if selected and used + rendered_len > char_budget:
            continue
        selected.append(block)
        used += rendered_len
    selected.sort(key=lambda b: b.index)
    return selected


def build_messages(
    doc: Document,
    group: str,
    fields: list[dict[str, Any]],
    char_budget: int,
) -> tuple[str, str]:
    blocks = select_context(doc, group, fields, char_budget)
    context = "\n\n".join(_render_block(block) for block in blocks)
    user = f"""请从下面的招标需求报告中抽取“{GROUP_TITLES.get(group, group)}”字段。

字段定义：
{_field_prompt_lines(fields)}

文档片段（每段前的 [B数字|章节] 是片段编号，evidence 请直接引用片段中的原文）：
{context}

输出格式（只输出这个 JSON）：
{{"fields":[{{"key":"字段key","value":"原文值","evidence":"原文连续片段"}}]}}
如果某个字段在原文中不存在，value 填空字符串，不要猜。"""
    return SYSTEM_PROMPT, user


_EVIDENCE_MARKER_RE = re.compile(r"\[B\d+[^\]]*\]\s*")


def _strip_evidence_marker(evidence: str) -> str:
    """小模型常把上下文渲染的 [B12|章节] 前缀一起抄进 evidence，这里剥掉。"""
    return clean_text(_EVIDENCE_MARKER_RE.sub(" ", evidence or ""))


def _find_block_for_evidence(doc: Document, evidence: str) -> Block | None:
    needle = normalize_for_match(evidence)
    if not needle:
        return None
    for block in doc.blocks:
        if needle in normalize_for_match(block.text):
            return block
    return None


def _locate_value(
    doc: Document,
    value: str,
    aliases: Iterable[str],
) -> tuple[Block, str] | None:
    """模型证据不可靠时，用“值 + 字段别名”在原文里重新定位。

    这一步只做机器可验证的定位：值必须原样出现在原文里。短值还要求别名
    出现在附近，避免把“1”“允许”这类到处都有的词误认成字段值。
    """
    needle = clean_text(value)
    if not needle:
        return None
    short = len(normalize_for_match(needle)) < 4
    alias_list = [alias for alias in aliases if alias and alias != needle]

    for block in doc.blocks:
        text = block.text
        pos = text.find(needle)
        if pos < 0:
            continue
        if not short:
            start = max(0, pos - 80)
            end = min(len(text), pos + len(needle) + 80)
            return block, text[start:end]

        distances: list[int] = []
        for alias in alias_list:
            alias_pos = text.find(alias)
            if alias_pos >= 0:
                distances.append(abs(alias_pos - pos))
        if distances and min(distances) <= 150:
            start = max(0, pos - 60)
            end = min(len(text), pos + len(needle) + 60)
            return block, text[start:end]
    return None


def evidence_supports_value(
    value: str,
    evidence: str,
    aliases: Iterable[str],
) -> tuple[bool, str]:
    """检查 evidence 是否真的能支撑 value。

    仅“证据片段能在原文找到”不够：小模型经常把无关段落当证据，或者用“1”
    这种到处都有的短值蒙混过关。这里要求：
    - value 必须出现在 evidence 片段里；
    - 短值（<4 字）必须同时出现字段别名，且片段不能过长，避免断章取义。
    """
    value_norm = normalize_for_match(value)
    evidence_norm = normalize_for_match(evidence)
    if not value_norm or not evidence_norm:
        return False, "模型未给出有效证据"
    if value_norm not in evidence_norm:
        return False, "证据片段不包含该值"

    alias_hit = any(
        normalize_for_match(alias) and normalize_for_match(alias) in evidence_norm
        for alias in aliases
    )
    if len(value_norm) < 4:
        if not alias_hit:
            return False, "短值缺少字段名上下文，无法确认对应关系"
        if len(evidence_norm) > 200:
            return False, "短值证据片段过长，无法确认对应关系"
    return True, ""


def parse_llm_fields(
    doc: Document,
    data: dict[str, Any],
    fields: list[dict[str, Any]],
) -> dict[str, Candidate]:
    allowed = {field["key"]: field for field in fields}
    candidates: dict[str, Candidate] = {}
    for item in data.get("fields", []) or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", "")).strip()
        if key not in allowed or key in candidates:
            continue
        value = item.get("value", "")
        evidence = item.get("evidence", "")
        if value is None:
            continue
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        value = clean_text(value)
        if not value:
            continue
        evidence = _strip_evidence_marker(clean_text(evidence))
        if not evidence:
            continue
        aliases = [key, *(allowed[key].get("aliases") or [])]
        block = _find_block_for_evidence(doc, evidence)
        if block is not None:
            ok, _ = evidence_supports_value(value, evidence, aliases)
            if ok:
                candidates[key] = Candidate(
                    value=value,
                    confidence=0.8,
                    source="llm",
                    evidence=[Evidence(block_id=block.id, quote=evidence, section=block.section)],
                    note="",
                )
                continue

        # 模型证据不准确（抄了上下文前缀、引错段落等）时，重新按原文定位。
        located = _locate_value(doc, value, aliases)
        if located is None:
            continue
        located_block, quote = located
        candidates[key] = Candidate(
            value=value,
            confidence=0.68,
            source="llm",
            evidence=[Evidence(block_id=located_block.id, quote=quote, section=located_block.section)],
            note="模型证据不准确，已按原文重新定位",
        )
    return candidates


def extract_group(
    client: LLMClient,
    doc: Document,
    group: str,
    fields: list[dict[str, Any]],
    char_budget: int = 7000,
) -> tuple[dict[str, Candidate], str]:
    system, user = build_messages(doc, group, fields, char_budget)
    data = client.chat_json(system, user, LLM_SCHEMA)
    return parse_llm_fields(doc, data, fields), ""


def build_repair_messages(
    doc: Document,
    fields: list[dict[str, Any]],
    current: dict[str, Any],
    errors: dict[str, list[str]],
    char_budget: int = 6000,
) -> tuple[str, str]:
    aliases: list[str] = []
    sections: list[str] = []
    for field in fields:
        aliases.extend(field.get("aliases") or [])
        sections.extend(field.get("sections") or [])

    # 修复时优先给字段别名命中的片段，以及这些字段所在章节。
    scored: list[tuple[float, Block]] = []
    for block in doc.blocks:
        score = 0.0
        if any(section in block.section or section in " ".join(block.section_path) for section in sections):
            score += 3.0
        if any(normalize_for_match(alias) in normalize_for_match(block.text) for alias in aliases):
            score += 2.0
        if block.kind == "table":
            score += 0.5
        if score > 0:
            scored.append((score, block))
    scored.sort(key=lambda item: (-item[0], item[1].index))
    selected: list[Block] = []
    used = 0
    for _, block in scored:
        size = min(len(block.text), 1500) + 40
        if selected and used + size > char_budget:
            continue
        selected.append(block)
        used += size
    selected.sort(key=lambda b: b.index)
    if not selected:
        selected = doc.blocks[:8]
    context = "\n\n".join(_render_block(block) for block in selected)

    issue_lines: list[str] = []
    for field in fields:
        key = field["key"]
        value = current.get(key)
        problems = errors.get(key) or []
        issue_lines.append(
            f"- {key}（{field['label']}）：当前值={value!r}；问题={'；'.join(problems) or '置信度低，请复核'}"
            f"；字段说明={field.get('description', '')}"
            f"；允许值={'/'.join(field.get('enum') or []) or '无特殊限制'}"
        )

    user = f"""上一轮抽取结果未通过校验。请只针对下面这些字段，重新从原文中抽取或修正。

待修复字段：
{chr(10).join(issue_lines)}

文档片段（evidence 必须引用片段中的连续原文）：
{context}

要求：
1. 只返回这些字段的 key；无法确认的字段返回空字符串，不要编造。
2. 金额保留原文单位；日期、期限保留原文写法；枚举字段返回允许值之一。
3. 输出格式：{{"fields":[{{"key":"字段key","value":"原文值","evidence":"原文连续片段"}}]}}"""
    return SYSTEM_PROMPT, user


def repair_fields(
    client: LLMClient,
    doc: Document,
    fields: list[dict[str, Any]],
    current: dict[str, Any],
    errors: dict[str, list[str]],
    char_budget: int = 6000,
) -> dict[str, Candidate]:
    system, user = build_repair_messages(doc, fields, current, errors, char_budget)
    data = client.chat_json(system, user, LLM_SCHEMA)
    return parse_llm_fields(doc, data, fields)
