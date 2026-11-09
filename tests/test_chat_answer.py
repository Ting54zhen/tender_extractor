# -*- coding: utf-8 -*-
"""对话表达层：只允许基于结果数据做解释性总结，越界一律回落固定话术。"""
from __future__ import annotations

from typing import Any

from tender_extractor.chat_answer import (
    build_prompt,
    check_faithful,
    dump_context,
    summarize,
)
from tender_extractor.llm_common import LLMError

CONTEXT = {
    "filename": "需求.docx",
    "status": "done",
    "missing": 3,
    "elapsed": 8.2,
    "summary": {"fields": 42, "review": 5},
}


class FakeClient:
    """只实现 chat_json 的替身，避免测试依赖真实模型。"""

    def __init__(self, payload: Any = None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.model = "fake-model"
        self.calls = 0

    def chat_json(self, system: str, user: str, schema: Any = None) -> dict[str, Any]:
        self.calls += 1
        if self.error:
            raise self.error
        return self.payload


def test_accepts_faithful_summary() -> None:
    client = FakeClient({"answer": "需求.docx 已解析完成，共 42 个字段，其中 5 项需要人工复核。"})
    result = summarize(client, intent="result", question="结果怎么样", context=CONTEXT, fallback="固定")
    assert result.matched_by == "llm"
    assert "42" in result.answer
    assert result.model == "fake-model"
    assert client.calls == 1


def test_rejects_fabricated_number() -> None:
    """结果数据里没有 120，模型却说了 120 万 → 必须回落固定话术。"""
    client = FakeClient({"answer": "需求.docx 已解析完成，预算 120 万元，共 42 个字段。"})
    result = summarize(client, intent="result", question="", context=CONTEXT, fallback="固定话术")
    assert result.matched_by == "fallback"
    assert result.answer == "固定话术"
    assert "120" in result.reason


def test_rejects_promise_phrase() -> None:
    client = FakeClient({"answer": "这份需求保证可以中标，共 42 个字段。"})
    result = summarize(client, intent="result", question="", context=CONTEXT, fallback="固定话术")
    assert result.matched_by == "fallback"
    assert "保证" in result.reason


def test_rejects_chinese_numerals() -> None:
    """中文数字无法与结果数据核对，保守拦截，避免编造数量。"""
    client = FakeClient({"answer": "需求.docx 已解析完成，有三项需要人工复核。"})
    result = summarize(client, intent="missing", question="", context=CONTEXT, fallback="固定话术")
    assert result.matched_by == "fallback"


def test_rejects_empty_and_too_long() -> None:
    context_text = dump_context(CONTEXT)
    assert check_faithful("", context_text) != ""
    long_text = "需求.docx 已解析完成。" * 40
    assert check_faithful(long_text, context_text, max_chars=100) != ""


def test_falls_back_when_client_missing() -> None:
    result = summarize(None, intent="result", question="", context=CONTEXT, fallback="固定话术")
    assert result.matched_by == "fallback"
    assert result.answer == "固定话术"


def test_falls_back_on_llm_error() -> None:
    client = FakeClient(error=LLMError("连接超时"))
    result = summarize(client, intent="result", question="", context=CONTEXT, fallback="固定话术")
    assert result.matched_by == "fallback"
    assert "连接超时" in result.reason


def test_disabled_switch_skips_model() -> None:
    client = FakeClient({"answer": "需求.docx 已解析完成，共 42 个字段。"})
    result = summarize(
        client, intent="result", question="", context=CONTEXT, fallback="固定话术", enabled=False
    )
    assert result.matched_by == "disabled"
    assert client.calls == 0


def test_intent_whitelist_blocks_other_scenes() -> None:
    """帮助、越界、删除确认等场景必须保持固定话术，不交给模型改写。"""
    client = FakeClient({"answer": "随便说点什么"})
    result = summarize(client, intent="delete", question="", context=CONTEXT, fallback="固定话术")
    assert result.matched_by == "fallback"
    assert client.calls == 0


def test_context_is_truncated() -> None:
    text = dump_context({"blob": "x" * 10000}, 50)
    assert len(text) == 51
    assert text.endswith("…")


def test_prompt_states_hard_rules() -> None:
    prompt = build_prompt(200)
    assert "200" in prompt
    assert "不得新增" in prompt
    assert '{"answer"' in prompt
    assert "中文数字" in prompt
