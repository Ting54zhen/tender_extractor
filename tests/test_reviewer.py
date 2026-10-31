"""第三方模型复核的单元测试。

用假客户端模拟复核模型的三种结论，验证安全边界：
support 只加注、reject 默认只标记（可配置丢弃）、replace 必须通过校验和回链。
"""

from tender_extractor.document import Document
from tender_extractor.llm_common import LLMHealth
from tender_extractor.models import Block, Candidate, Evidence
from tender_extractor.reviewer import (
    ReviewStats,
    parse_verdicts,
    resolve_settings,
    review_candidates,
)

FIELD_DEFS = {
    "project_name": {
        "key": "project_name",
        "label": "项目名称",
        "type": "text",
        "aliases": ["项目名称"],
    },
    "budget_amount": {
        "key": "budget_amount",
        "label": "预算金额",
        "type": "money",
        "aliases": ["预算金额", "采购预算"],
        "precision": 2,
    },
}


def make_doc() -> Document:
    return Document(
        path="需求报告.docx",
        title="采购需求",
        blocks=[
            Block(
                id="B1",
                kind="paragraph",
                text="项目名称：测试项目（一期）",
                raw="项目名称：测试项目（一期）",
                index=0,
                section="一、项目概况",
            ),
            Block(
                id="B2",
                kind="paragraph",
                text="采购预算：200,000.00元",
                raw="采购预算：200,000.00元",
                index=1,
                section="一、项目概况",
            ),
        ],
    )


def make_candidate(value, source="llm", block="B1", quote=""):
    return Candidate(
        value=value,
        confidence=0.8,
        source=source,
        evidence=[Evidence(block_id=block, quote=quote or value, section="一、项目概况")],
    )


class FakeClient:
    provider = "fake"
    model = "judge-model"

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.last_error = ""

    def health(self):
        return LLMHealth(True, "", [self.model], True)

    def chat_json(self, system, user, schema=None):
        self.calls += 1
        return self.payload


def run_review(candidates, payload, settings=None):
    stats = ReviewStats(enabled=True)
    settings = {"scope": "llm", "max_fields": 8, "reject_action": "flag", **(settings or {})}
    client = FakeClient(payload)
    updated, items = review_candidates(client, make_doc(), FIELD_DEFS, candidates, settings, stats)
    return updated, items, stats


def test_support_adds_note_and_bumps_confidence():
    candidates = {"project_name": make_candidate("测试项目（一期）")}
    payload = {
        "verdicts": [
            {"key": "project_name", "verdict": "support", "reason": "原文明确写出"}
        ]
    }

    updated, items, stats = run_review(candidates, payload)

    assert updated["project_name"].value == "测试项目（一期）"
    assert updated["project_name"].confidence > 0.8
    assert "复核通过" in updated["project_name"].note
    assert items == []
    assert stats.supported == 1


def test_reject_flags_model_value_by_default():
    candidates = {"project_name": make_candidate("编造的项目")}
    payload = {
        "verdicts": [{"key": "project_name", "verdict": "reject", "reason": "原文没有该值"}]
    }

    updated, items, stats = run_review(candidates, payload)

    assert "project_name" in updated
    assert "存疑" in updated["project_name"].note
    assert items and items[0]["priority"] == "high"
    assert "丢弃" not in items[0]["issue"]
    assert stats.rejected == 1 and stats.flagged == 1


def test_reject_drops_model_value_when_configured():
    candidates = {"project_name": make_candidate("编造的项目")}
    payload = {
        "verdicts": [{"key": "project_name", "verdict": "reject", "reason": "原文没有该值"}]
    }

    updated, items, stats = run_review(candidates, payload, {"reject_action": "drop"})

    assert "project_name" not in updated
    assert items and "已丢弃" in items[0]["issue"]
    assert stats.rejected == 1 and stats.flagged == 0


def test_rule_backed_value_is_never_dropped():
    candidates = {"project_name": make_candidate("测试项目（一期）", source="rule+llm")}
    payload = {
        "verdicts": [{"key": "project_name", "verdict": "reject", "reason": "证据不足"}]
    }

    updated, items, _ = run_review(candidates, payload, {"reject_action": "drop"})

    assert updated["project_name"].value == "测试项目（一期）"
    assert items and items[0]["priority"] == "medium"


def test_replace_accepted_when_value_is_grounded():
    candidates = {"budget_amount": make_candidate("100,000.00元", block="B2")}
    payload = {
        "verdicts": [
            {
                "key": "budget_amount",
                "verdict": "replace",
                "value": "200,000.00元",
                "evidence": "采购预算：200,000.00元",
                "reason": "原文预算为 200,000.00 元",
            }
        ]
    }

    updated, items, stats = run_review(candidates, payload)

    assert "200,000" in str(updated["budget_amount"].value)
    assert updated["budget_amount"].source == "llm-reviewed"
    assert updated["budget_amount"].evidence[0].block_id == "B2"
    assert items == []
    assert stats.replaced == 1


def test_replace_ignored_when_value_not_in_source():
    candidates = {"budget_amount": make_candidate("100,000.00元", block="B2")}
    payload = {
        "verdicts": [
            {
                "key": "budget_amount",
                "verdict": "replace",
                "value": "999,999.00元",
                "evidence": "采购预算：999,999.00元",
                "reason": "原文写的是 999,999.00 元",
            }
        ]
    }

    updated, items, stats = run_review(candidates, payload)

    assert str(updated["budget_amount"].value) == "100,000.00元"
    assert "未采用" in updated["budget_amount"].note
    assert items and items[0]["priority"] == "medium"
    assert stats.ignored == 1


def test_parse_verdicts_drops_unknown_keys_and_duplicates():
    targets = ["project_name", "budget_amount"]
    data = {
        "verdicts": [
            {"key": "project_name", "verdict": "support", "reason": "ok"},
            {"key": "project_name", "verdict": "reject", "reason": "重复，忽略"},
            {"key": "budget_amount", "verdict": "maybe", "reason": "非法结论"},
            {"key": "unknown_field", "verdict": "support", "reason": "不在范围"},
        ]
    }

    verdicts = parse_verdicts(data, targets)

    assert list(verdicts) == ["project_name"]
    assert verdicts["project_name"]["verdict"] == "support"


def test_resolve_settings_falls_back_to_main_model():
    effective = resolve_settings(
        {"provider": "ollama", "model": "", "base_url": ""},
        {"model": "qwen2.5:3b-instruct", "base_url": "http://127.0.0.1:11434"},
    )
    assert effective["model"] == "qwen2.5:3b-instruct"
    assert effective["base_url"] == "http://127.0.0.1:11434"


def test_resolve_settings_keeps_explicit_model():
    effective = resolve_settings(
        {"provider": "ollama", "model": "qwen2.5:1.5b-instruct"},
        {"model": "qwen2.5:7b-instruct"},
    )
    assert effective["model"] == "qwen2.5:1.5b-instruct"
