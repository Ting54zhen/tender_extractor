"""对话回答的表达层：把已经算好的结构化结果改写成自然中文。

边界与 ``chat.py`` 一致：**模型不产出任何事实值**。
这里只允许模型做「基于结果数据的解释性总结」，所有数字、状态、结论都必须
能在输入的结果数据里找到；任何越界（新增数字、承诺性措辞、空回答、超长）
都会判定失败，由调用方回落到固定话术，而不是把不可靠的回答展示给用户。

模型只被要求输出 ``{"answer": "..."}`` 一个字段，便于校验与截断；
校验通过才返回 ``matched_by="llm"``，否则 ``matched_by="fallback"``。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .llm_common import LLMClient, LLMError

# 展示型场景才允许生成；其它场景（帮助、越界、删除确认）必须保持固定话术
ALLOWED_INTENTS: frozenset[str] = frozenset({"result", "missing", "jobs"})

DEFAULT_MAX_CHARS = 320
DEFAULT_MAX_CONTEXT_CHARS = 6000

_PROMPT_TEMPLATE = """你是「招标需求解析」助手的表达层。输入是程序已经算好的结构化结果，
你的唯一任务是用自然中文把它总结给用户。

硬性规则（违反任何一条都算失败）：
1. 只能使用【结果数据】里出现的字段名、状态、数字和结论；不得新增任何金额、比例、期限、条款、机构名或来源。
2. 不得判断「能否中标」「是否合规」「供应商好坏」，不得预测结果，不得给出法律意见。
3. 数据里标为缺失、待补充、unknown 的内容，只能说「待补充」或「需人工复核」，不得猜测具体值。
4. 禁止「保证」「一定」「绝对」「肯定」「必然」「包过」等承诺性措辞。
5. 用 2~4 句中文，最多 __MAX_CHARS__ 字；不要 Markdown 表格、不要代码块、不要列清单符号。
6. 不要使用中文数字（一二三四五六七八九十），需要说数量时只复述结果数据里的阿拉伯数字。
7. 先总结结果数据里已有的内容（文件名、状态、字段数、待复核数、耗时等）；只有用户问的具体项在数据里确实没有时，才补一句「该项以原文证据为准」。不要因为有缺项就整段拒答。

只输出 JSON：{"answer": "..."}"""

_NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?")
_CHINESE_NUMERAL = re.compile(r"[一二三四五六七八九十百千万亿两]")
_FORBIDDEN_PHRASES: tuple[str, ...] = (
    "保证", "一定", "绝对", "肯定", "必然", "百分百", "稳赚", "包过", "中标概率",
)


@dataclass
class AnswerResult:
    """表达层结果；matched_by 为 llm / fallback / disabled。"""

    answer: str
    matched_by: str = "fallback"
    model: str = ""
    reason: str = ""


def build_prompt(max_chars: int = DEFAULT_MAX_CHARS) -> str:
    return _PROMPT_TEMPLATE.replace("__MAX_CHARS__", str(max_chars))


def dump_context(context: Any, max_chars: int = DEFAULT_MAX_CONTEXT_CHARS) -> str:
    """把结果数据序列化成稳定文本；超长截断，避免把整份结果塞进提示词。"""
    try:
        text = json.dumps(context, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = str(context)
    if len(text) > max_chars:
        text = text[:max_chars] + "…"
    return text


def allowed_numbers(context_text: str) -> set[str]:
    """结果数据里出现过的数字集合，作为「不得新增数字」的白名单。"""
    return {match.group(0) for match in _NUMBER_PATTERN.finditer(context_text)}


def check_faithful(answer: str, context_text: str, *, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """返回失败原因；空字符串表示通过。宁可回落话术，也不展示不可核对的结论。"""
    text = (answer or "").strip()
    if not text:
        return "模型返回空内容"
    if len(text) > max_chars * 2:
        return f"回答过长（{len(text)} 字）"
    for phrase in _FORBIDDEN_PHRASES:
        if phrase in text:
            return f"出现承诺性措辞：{phrase}"
    permitted = allowed_numbers(context_text)
    for number in _NUMBER_PATTERN.findall(text):
        if number not in permitted:
            return f"出现结果数据里没有的数字：{number}"
    if _CHINESE_NUMERAL.search(text):
        return "出现中文数字，无法核对是否来自结果数据"
    return ""


def summarize(
    client: LLMClient | None,
    *,
    intent: str,
    question: str,
    context: Any,
    fallback: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    enabled: bool = True,
) -> AnswerResult:
    """生成解释性总结；任何异常/越界都回落到调用方给的固定话术。"""
    if not enabled:
        return AnswerResult(fallback, "disabled", reason="chat_summary 已关闭")
    if intent not in ALLOWED_INTENTS:
        return AnswerResult(fallback, "fallback", reason=f"场景 {intent} 不允许生成")
    if client is None:
        return AnswerResult(fallback, "fallback", reason="模型不可用")

    context_text = dump_context(context, max_context_chars)
    user = f"用户原话：{question or '（无）'}\n场景：{intent}\n结果数据：{context_text}"
    try:
        payload = client.chat_json(build_prompt(max_chars), user, {"answer": "string"})
    except LLMError as exc:
        return AnswerResult(fallback, "fallback", reason=str(exc))

    answer = str(payload.get("answer") or "").strip()
    problem = check_faithful(answer, context_text, max_chars=max_chars)
    if problem:
        return AnswerResult(fallback, "fallback", model=getattr(client, "model", ""), reason=problem)
    return AnswerResult(answer, "llm", model=getattr(client, "model", ""))
