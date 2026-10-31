"""第三方模型复核：对模型产出的字段做第二次独立判断。

主模型（默认本机 Ollama）给出字段值后，把「字段定义 + 值 + 原文片段」交给
另一个模型（配置在 ``settings.json`` 的 ``review`` 段，默认走 OpenAI 兼容接口）
判断该值是否成立，结论只有三种：

- ``support``：原文确实支持该值 → 加注复核通过，置信度小幅上调；
- ``reject``：原文没有该值 → 模型独有的值被丢弃，规则值只加“存疑”提示；
- ``replace``：原文写的是别的值 → 新值必须通过字段校验、能在原文定位且证据能
  支撑，否则保留原值并记入复核清单。

安全边界：复核模型只做“减法和修正”，不能凭空新增字段；规则层产出且与模型
一致的值不会被复核模型单方面改写。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .cleaning import clean_text
from .document import Document
from .llm_client import LLMClient
from .llm_extract import _locate_value, evidence_supports_value
from .models import Candidate, Evidence
from .validation import normalize_value, validate_field

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["support", "reject", "replace"]},
                    "value": {"type": "string"},
                    "evidence": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["key", "verdict", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """你是招标需求抽取结果的独立复核员。你只输出 JSON，不输出解释。
你的任务：判断给定字段值能否被提供的原文片段直接支撑。
必须遵守：
1. 只依据给出的原文片段判断，禁止使用外部知识、常识或推测补全。
2. 原文没有明确写出该值 → verdict = "reject"。
3. 原文写的是另一个值 → verdict = "replace"，并在 value/evidence 里给出原文中的正确值及其连续片段。
4. 原文确实支持该值 → verdict = "support"，reason 用一句话指出依据。
5. evidence 必须是原文里的连续片段，不得改写、拼接或加入块编号。
6. 没有把握时用 "reject"，不要猜。"""

_VALID_VERDICTS = {"support", "reject", "replace"}
_MODEL_ONLY_SOURCES = {"llm", "llm-repair", "llm-reviewed"}


@dataclass
class ReviewStats:
    enabled: bool = False
    available: bool = False
    provider: str = ""
    model: str = ""
    calls: int = 0
    checked: int = 0
    supported: int = 0
    rejected: int = 0
    replaced: int = 0
    ignored: int = 0
    flagged: int = 0
    error: str = ""
    decisions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "available": self.available,
            "provider": self.provider,
            "model": self.model,
            "calls": self.calls,
            "checked": self.checked,
            "supported": self.supported,
            "rejected": self.rejected,
            "replaced": self.replaced,
            "ignored": self.ignored,
            "flagged": self.flagged,
            "error": self.error,
            "decisions": self.decisions[:20],
        }


def _append_note(note: str, extra: str) -> str:
    return f"{note}；{extra}".strip("；") if note else extra


def resolve_settings(
    review_settings: dict[str, Any] | None,
    llm_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """复核配置缺省时回退到主模型，保证本机只装一个模型也能直接用。

    显式配置了 review.model / review.base_url 时以显式值为准；走本地 Ollama
    且没填模型时，沿用主模型，避免开箱即报“模型不存在”。
    """
    effective = dict(review_settings or {})
    provider = str(effective.get("provider") or "ollama").strip().lower()
    if provider == "ollama":
        llm = llm_settings or {}
        if not effective.get("base_url"):
            effective["base_url"] = llm.get("base_url") or "http://127.0.0.1:11434"
        if not effective.get("model"):
            effective["model"] = llm.get("model") or "qwen2.5:3b-instruct"
        for key in ("num_ctx", "num_predict", "timeout_seconds"):
            if key in llm and key not in effective:
                effective[key] = llm[key]
    return effective


def _select_targets(
    candidates: dict[str, Candidate],
    scope: str,
) -> list[str]:
    targets: list[str] = []
    for key, candidate in candidates.items():
        if candidate.value in (None, ""):
            continue
        if scope == "all":
            targets.append(key)
        elif candidate.source in _MODEL_ONLY_SOURCES or candidate.source == "rule+llm":
            targets.append(key)
    return sorted(targets)


def _block_context(doc: Document, candidate: Candidate, max_chars: int = 600) -> str:
    parts: list[str] = []
    for evidence in candidate.evidence[:2]:
        block = doc.block_by_id(evidence.block_id)
        if block is not None:
            text = block.text
            if len(text) > max_chars:
                text = text[:max_chars] + "…"
            parts.append(f"[{block.id}|{block.section}] {text}")
        elif evidence.quote:
            parts.append(evidence.quote[:max_chars])
    return "\n".join(parts)


def build_review_messages(
    doc: Document,
    field_defs: dict[str, dict[str, Any]],
    candidates: dict[str, Candidate],
    targets: list[str],
    char_budget: int = 6000,
) -> tuple[str, str]:
    lines: list[str] = []
    used = 0
    for key in targets:
        field_def = field_defs.get(key, {"key": key})
        candidate = candidates[key]
        allowed = "/".join(field_def.get("enum") or []) or "无特殊限制"
        lines.append(
            f"- {key}（{field_def.get('label', key)}，类型 {field_def.get('type', 'text')}）："
            f"模型值={candidate.value!r}；允许值={allowed}；"
            f"字段说明={field_def.get('description', '') or '无'}"
        )
        context = _block_context(doc, candidate)
        if not context:
            context = "（模型未提供原文片段）"
        if used + len(context) > char_budget:
            context = context[: max(0, char_budget - used)] + "…"
        used += len(context)
        lines.append(f"  原文片段：{context.replace(chr(10), ' / ')}")

    user = f"""下面是从招标需求报告中抽取出的字段，请逐项复核。

待复核字段：
{chr(10).join(lines)}

判断要求：
1. 只认可原文片段中明确写出的值；推断、换算、拼接都不算。
2. 原文没有提到该字段 → reject。
3. 原文写的是别的值 → replace，并给出原文中的正确值和包含它的连续片段。
4. 原文支持该值 → support，reason 指出依据。
5. 金额、日期、期限保持原文写法，不要自行换算。

输出格式（只输出这个 JSON）：
{{"verdicts":[{{"key":"字段key","verdict":"support|reject|replace","value":"","evidence":"","reason":"一句话依据"}}]}}
每个待复核字段都必须给出结论。"""
    return SYSTEM_PROMPT, user


def parse_verdicts(
    data: dict[str, Any],
    targets: list[str],
) -> dict[str, dict[str, Any]]:
    allowed = set(targets)
    verdicts: dict[str, dict[str, Any]] = {}
    for item in data.get("verdicts", []) or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", "")).strip()
        verdict = str(item.get("verdict", "")).strip().lower()
        if key not in allowed or key in verdicts or verdict not in _VALID_VERDICTS:
            continue
        verdicts[key] = {
            "verdict": verdict,
            "value": str(item.get("value") or ""),
            "evidence": str(item.get("evidence") or ""),
            "reason": str(item.get("reason") or "").strip(),
        }
    return verdicts


def _try_replace(
    doc: Document,
    field_def: dict[str, Any],
    verdict: dict[str, Any],
) -> tuple[tuple[Any, Evidence] | None, str]:
    raw = clean_text(verdict.get("value") or "")
    if not raw:
        return None, "复核模型未给出新值"

    normalized, _ = normalize_value(field_def, raw)
    if normalized in (None, ""):
        return None, "新值归一化后为空"
    errors, _ = validate_field(field_def, normalized)
    if errors:
        return None, "；".join(errors)

    key = field_def.get("key", "")
    aliases = [key, *(field_def.get("aliases") or [])]
    located = _locate_value(doc, raw, aliases) or _locate_value(doc, str(normalized), aliases)
    if located is None:
        return None, "新值无法在原文定位"

    block, quote = located
    ok, why = evidence_supports_value(raw, quote, aliases)
    if not ok:
        ok, why = evidence_supports_value(str(normalized), quote, aliases)
    if not ok:
        return None, why

    return (normalized, Evidence(block_id=block.id, quote=quote, section=block.section)), ""


def review_candidates(
    client: LLMClient,
    doc: Document,
    field_defs: dict[str, dict[str, Any]],
    candidates: dict[str, Candidate],
    settings: dict[str, Any],
    stats: ReviewStats,
) -> tuple[dict[str, Candidate], list[dict[str, Any]]]:
    """复核模型产出的值，就地更新 candidates，返回需要人工复核的条目。"""
    scope = str(settings.get("scope") or "llm")
    reject_action = str(settings.get("reject_action") or "flag").strip().lower()
    if reject_action not in {"drop", "flag"}:
        reject_action = "flag"
    max_fields = max(1, int(settings.get("max_fields", 12)))
    targets = _select_targets(candidates, scope)[:max_fields]
    if not targets:
        return candidates, []

    stats.checked = len(targets)
    system, user = build_review_messages(
        doc,
        field_defs,
        candidates,
        targets,
        int(settings.get("context_char_budget", 6000)),
    )
    data = client.chat_json(system, user, REVIEW_SCHEMA)
    stats.calls = client.calls
    verdicts = parse_verdicts(data, targets)

    review_items: list[dict[str, Any]] = []
    for key, verdict in verdicts.items():
        candidate = candidates[key]
        field_def = field_defs.get(key, {"key": key})
        label = field_def.get("label", key)
        action = verdict["verdict"]
        reason = verdict["reason"]
        before = candidate.value
        applied = False

        if action == "support":
            candidate.confidence = min(0.99, round(candidate.confidence + 0.04, 4))
            candidate.note = _append_note(candidate.note, "第三方模型复核通过")
            stats.supported += 1
            applied = True

        elif action == "reject":
            stats.rejected += 1
            model_only = candidate.source in _MODEL_ONLY_SOURCES
            if model_only and reject_action == "drop":
                candidates.pop(key, None)
                applied = True
                review_items.append(
                    {
                        "priority": "high",
                        "key": key,
                        "label": label,
                        "issue": "第三方模型判断该值缺少原文支撑，已丢弃"
                        + (f"（{reason}）" if reason else ""),
                        "value": before,
                        "evidence": [e.quote for e in candidate.evidence][:1],
                    }
                )
            else:
                stats.flagged += 1
                candidate.note = _append_note(
                    candidate.note,
                    "第三方模型判断该值缺少原文支撑，已标记存疑，需人工确认"
                    if model_only
                    else "第三方模型不认可该值，但规则层也有相同结果，已保留",
                )
                review_items.append(
                    {
                        "priority": "high" if model_only else "medium",
                        "key": key,
                        "label": label,
                        "issue": (
                            "第三方模型判断该值缺少原文支撑，已标记存疑"
                            if model_only
                            else "第三方模型不认可该值，但规则层也有相同结果，未自动丢弃"
                        )
                        + (f"（{reason}）" if reason else ""),
                        "value": before,
                        "evidence": [e.quote for e in candidate.evidence][:1],
                    }
                )

        elif action == "replace":
            if candidate.source not in _MODEL_ONLY_SOURCES:
                candidate.note = _append_note(
                    candidate.note, f"第三方模型建议改为 {verdict['value']!r}，规则层不覆盖"
                )
                review_items.append(
                    {
                        "priority": "medium",
                        "key": key,
                        "label": label,
                        "issue": f"第三方模型给出不同值 {verdict['value']!r}，规则层结果未被覆盖",
                        "value": before,
                        "evidence": [e.quote for e in candidate.evidence][:1],
                    }
                )
                stats.ignored += 1
                continue

            replacement, why = _try_replace(doc, field_def, verdict)
            if replacement is None:
                candidate.note = _append_note(
                    candidate.note,
                    f"第三方模型建议改为 {verdict['value']!r}，未采用（{why}）",
                )
                review_items.append(
                    {
                        "priority": "medium",
                        "key": key,
                        "label": label,
                        "issue": f"第三方模型给出不同值 {verdict['value']!r}，但未通过校验：{why}",
                        "value": before,
                        "evidence": [e.quote for e in candidate.evidence][:1],
                    }
                )
                stats.ignored += 1
                continue

            new_value, evidence = replacement
            candidate.value = new_value
            candidate.evidence = [evidence]
            candidate.source = "llm-reviewed"
            candidate.confidence = min(0.99, round(candidate.confidence + 0.02, 4))
            candidate.note = _append_note(
                candidate.note,
                f"第三方模型改为该值" + (f"（{reason}）" if reason else ""),
            )
            stats.replaced += 1
            applied = True

        stats.decisions.append(
            {
                "key": key,
                "label": label,
                "verdict": action,
                "from": before,
                "to": candidate.value if key in candidates else None,
                "reason": reason,
                "applied": applied,
            }
        )

    return candidates, review_items
