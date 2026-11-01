"""符合性比对：把「招标要求」和「投标响应」逐条对齐，输出偏离表。

L2 智能投标的核心：对招标的每条资格要求，判断投标文件是否满足
（satisfied / partial / missing）。判断结果强制回链投标文件原文证据
（复用 ``_locate_value``），判定站不住就降级为 missing——延续「模型只建议、
证据一票否决」的可信原则。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .bid_extract import _RESPONSE_KEYWORDS, extract_bid_responses
from .cleaning import clean_text
from .document import Document
from .llm_client import LLMClient, LLMError
from .llm_extract import _locate_value
from .relation_extract import RelationItem, extract_relations

MATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["satisfied", "partial", "missing"]},
                    "matched": {"type": "string"},
                    "evidence": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["index", "verdict", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["results"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """你是招投标符合性审查员。你只输出 JSON，不输出解释。
只依据给出的原文片段判断，禁止使用外部知识、常识或推测。
投标响应中明确满足该要求 → satisfied；部分满足或需补充 → partial；未响应或不满足 → missing。"""

_VERDICT_LABELS = {
    "satisfied": "满足",
    "partial": "部分满足",
    "missing": "未响应",
}


@dataclass
class ComplianceResult:
    """一条资格要求的比对结论。"""

    requirement: str = ""
    req_evidence: str = ""
    verdict: str = "missing"          # satisfied | partial | missing
    matched_response: str = ""
    resp_evidence: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement": self.requirement,
            "req_evidence": self.req_evidence,
            "verdict": self.verdict,
            "matched_response": self.matched_response,
            "resp_evidence": self.resp_evidence,
            "note": self.note,
        }


def _clip(text: Any, limit: int = 200) -> str:
    value = str(text or "").replace("\n", " ").strip()
    return value[:limit] + ("…" if len(value) > limit else "")


def build_match_messages(
    requirements: list[RelationItem],
    responses: list[RelationItem],
) -> tuple[str, str]:
    req_lines = [
        f"{i}. {r.name}（证据：{_clip(r.evidence, 200)}）" for i, r in enumerate(requirements)
    ]
    resp_lines = [
        f"{j}. {s.name}（证据：{_clip(s.evidence, 200)}）" for j, s in enumerate(responses)
    ]
    user = f"""下面是招标文件的资格要求，和投标文件的响应条目。请对每一条资格要求判断投标文件是否满足。

【资格要求】
{chr(10).join(req_lines) if req_lines else "（无）"}

【投标响应】
{chr(10).join(resp_lines) if resp_lines else "（无）"}

判断规则：
- satisfied：投标响应中明确满足该要求；
- partial：部分满足或需要补充材料；
- missing：未响应或不满足。

输出格式（只输出这个 JSON）：
{{"results":[{{"index":0,"verdict":"satisfied|partial|missing","matched":"命中的投标响应原文","evidence":"投标文件中支持判定的连续片段","reason":"一句话依据"}}]}}
verdict 为 missing 时 matched/evidence 填空字符串。每条资格要求都必须给出结论。"""
    return SYSTEM_PROMPT, user


def parse_match(
    data: dict[str, Any],
    requirements: list[RelationItem],
    bid_doc: Document,
) -> list[ComplianceResult]:
    results: list[ComplianceResult] = []
    for item in data.get("results", []) or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index", -1))
        except (TypeError, ValueError):
            continue
        if index < 0 or index >= len(requirements):
            continue
        req = requirements[index]

        verdict = str(item.get("verdict", "")).strip().lower()
        if verdict not in {"satisfied", "partial", "missing"}:
            verdict = "missing"
        matched = clean_text(item.get("matched") or "")
        evidence = clean_text(item.get("evidence") or "")
        reason = clean_text(item.get("reason") or "")

        # 证据核验：非 missing 的判定必须能在投标文件原文定位，否则降级。
        if verdict != "missing":
            target = matched or evidence
            located = _locate_value(bid_doc, target, _RESPONSE_KEYWORDS) if target else None
            if located is None:
                verdict = "missing"
                matched = ""
                evidence = ""
                reason = (reason + "；判定缺少投标原文证据，已降级为未响应").strip("；")
            else:
                _, quote = located
                evidence = quote

        results.append(
            ComplianceResult(
                requirement=req.name,
                req_evidence=req.evidence,
                verdict=verdict,
                matched_response=matched,
                resp_evidence=evidence,
                note=reason,
            )
        )
    return results


def match_qualifications(
    client: LLMClient | None,
    bid_doc: Document,
    requirements: list[RelationItem],
    responses: list[RelationItem],
) -> list[ComplianceResult]:
    """逐条比对。无要求或无客户端时返回空列表。"""
    if client is None or not requirements:
        return []
    system, user = build_match_messages(requirements, responses)
    data = client.chat_json(system, user, MATCH_SCHEMA)
    return parse_match(data, requirements, bid_doc)


def render_compliance_report(results: list[ComplianceResult]) -> str:
    """把比对结果渲染成《符合性比对表》。"""
    if not results:
        return "（未进行符合性比对）"
    counts = {"satisfied": 0, "partial": 0, "missing": 0}
    for r in results:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1

    lines = [
        "## 投标符合性比对表",
        "",
        f"共 {len(results)} 项：满足 {counts['satisfied']} · 部分满足 {counts['partial']} · 未响应 {counts['missing']}",
        "",
        "| # | 招标资格要求 | 判定 | 命中的投标响应 | 依据 |",
        "|---|-------------|------|---------------|------|",
    ]
    for i, r in enumerate(results, 1):
        lines.append(
            f"| {i} | {_clip(r.requirement, 50)} | **{_VERDICT_LABELS[r.verdict]}** | "
            f"{_clip(r.matched_response, 40)} | {_clip(r.note, 60)} |"
        )
    lines.append("")
    lines.append("> 未响应项可能导致废标或丢分，请优先补充材料。")
    return "\n".join(lines)


def compare_bid_compliance(
    tender_path: str,
    bid_path: str,
    config_dir: str | None = None,
    use_llm: bool = True,
    settings_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """一键门面：抽招标要求 → 抽投标响应 → 逐条比对 → 生成报告。

    返回 dict：requirements / responses / results / report。
    """
    from pathlib import Path

    from .document import read_document
    from .llm_client import build_llm_client
    from .pipeline import Extractor

    extractor = Extractor(config_dir=config_dir, settings_override=settings_override)
    llm_settings = extractor.settings.get("llm", {})

    tender_doc = read_document(Path(tender_path))
    bid_doc = read_document(Path(bid_path))

    client: LLMClient | None = None
    if use_llm:
        try:
            client = build_llm_client(llm_settings)
        except Exception:  # noqa: BLE001 - 客户端构建失败按无 LLM 处理
            client = None

    requirements: list[RelationItem] = []
    responses: list[RelationItem] = []
    relations_meta: dict[str, Any] = {}
    bid_meta: dict[str, Any] = {}

    if client is not None:
        try:
            rel = extract_relations(client, tender_doc, extractor.settings)
            requirements = rel.qualifications
            relations_meta = rel.meta
        except Exception as exc:  # noqa: BLE001 - 关系抽取失败不影响比对
            relations_meta = {"error": str(exc)}

        try:
            bid_result = extract_bid_responses(client, bid_doc, extractor.settings)
            responses = bid_result.responses
            bid_meta = bid_result.meta
        except Exception as exc:  # noqa: BLE001 - 响应抽取失败不影响比对
            bid_meta = {"error": str(exc)}

    results: list[ComplianceResult] = []
    if client is not None and requirements:
        try:
            results = match_qualifications(client, bid_doc, requirements, responses)
        except LLMError as exc:
            bid_meta = {**bid_meta, "match_error": str(exc)}

    return {
        "requirements": [r.to_dict() for r in requirements],
        "responses": [r.to_dict() for r in responses],
        "results": [r.to_dict() for r in results],
        "meta": {"relations": relations_meta, "bid": bid_meta},
        "report": render_compliance_report(results),
    }
