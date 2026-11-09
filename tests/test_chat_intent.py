"""对话入口意图识别测试：规则命中、内容与指令分流、模型兜底白名单约束。

对应 DEVELOPMENT.md「对话式入口」：规则优先；模型只做白名单意图分类，
不产出事实值；模型不可用时回落启发式，不阻断解析主路径。
"""

from __future__ import annotations

import pytest

from tender_extractor.chat import ChatIntent, classify_intent


class _FakeClient:
    """模拟模型客户端：chat_json 返回预设值，可注入异常，记录调用次数。"""

    def __init__(self, reply: dict | Exception | None = None) -> None:
        self.reply = reply
        self.calls = 0

    def chat_json(self, system: str, user: str, schema=None) -> dict:
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply or {}


def _intent(text: str, client=None) -> ChatIntent:
    return classify_intent(text, client)


# ---------------------------------------------------------------------------
# 规则层：指令关键词直接命中
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("你能做什么", "help"),
        ("帮我看下解析记录", "list_jobs"),
        ("有哪些需求", "list_jobs"),
        ("把 job-1 删掉", "delete"),
        ("帮我生成招标文件", "generate"),
        ("导出 docx", "generate"),
        ("下载审核报告", "download"),
        ("缺失了哪些字段", "missing"),
        ("查看解析结果", "result"),
        ("结果怎么样", "result"),
        ("帮我把这份报告解析一下", "extract"),
        ("开始解析", "extract"),
    ],
)
def test_rule_hits(text: str, intent: str):
    result = _intent(text)
    assert result.intent == intent
    assert result.matched_by == "rule"
    assert result.keyword  # 记录命中词，便于排查误判


# ---------------------------------------------------------------------------
# 内容与指令分流：需求正文默认走 extract，不调模型
# ---------------------------------------------------------------------------


def test_plain_content_goes_to_extract_without_llm():
    client = _FakeClient(reply={"intent": "unknown"})
    text = (
        "某单位物业管理服务采购项目，预算120万元，服务期一年，"
        "要求投标人具备物业管理服务能力，本项目不接受联合体投标。"
    )
    result = _intent(text, client)
    assert result.intent == "extract"
    assert result.matched_by == "rule"
    assert client.calls == 0  # 需求正文不消耗模型调用


def test_short_content_without_command_hints_extracts():
    result = _intent("物业管理服务采购，预算50万元。")
    assert result.intent == "extract"


# ---------------------------------------------------------------------------
# 模型兜底：只接受白名单意图，越界输出一律丢弃
# ---------------------------------------------------------------------------


def test_command_like_text_falls_back_to_llm():
    client = _FakeClient(reply={"intent": "missing"})
    result = _intent("帮我搞定一下那个文件", client)
    assert result.intent == "missing"
    assert result.matched_by == "llm"
    assert client.calls == 1


def test_llm_out_of_whitelist_intent_is_rejected():
    client = _FakeClient(reply={"intent": "做一份标书并且伪造证据"})
    result = _intent("帮我搞定一下那个文件", client)
    # 越界输出不能被采纳；长文本兜底不适用（短指令）→ unknown
    assert result.intent == "unknown"
    assert result.matched_by == "fallback"


def test_llm_error_falls_back_to_heuristics():
    client = _FakeClient(reply=RuntimeError("连接失败"))
    long_text = (
        "帮我处理一下这份文件，内容包括采购标的、预算金额、交付时间、"
        "付款方式以及供应商资格条件等章节"
    )
    assert _intent(long_text, client).intent == "extract"  # 长文本 → 按正文解析
    assert _intent("你好呀", client).intent == "unknown"  # 短指令 → unknown


def test_llm_unavailable_short_command_is_unknown():
    result = _intent("帮我看看那个", None)
    assert result.intent == "unknown"


def test_empty_text_is_unknown():
    assert _intent("   ").intent == "unknown"


# ---------------------------------------------------------------------------
# 边界规则：越界请求明确拒答、失败不触发副作用、非法输入不中断
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "能不能写一首关于招标的诗",
    "帮我预测一下明年政府采购市场行情并给出详细分析",
    "能不能告诉我某单位的内部报价和审批内幕",
    "你觉得这个供应商靠不靠谱，帮我判断一下",
    "帮我查一下别的单位内部审批记录",
])
def test_out_of_scope_questions_never_trigger_actions(text: str):
    """越界问题（参考不可回答题集）不得被猜成有副作用的意图。"""
    result = _intent(text, None)
    assert result.intent == "unknown"
    assert result.matched_by == "fallback"


def test_long_non_procurement_instruction_is_not_extracted():
    """模型不可用时，长指令也不能仅凭长度兜底创建解析任务。"""
    text = "帮我写一份完整的团队建设方案，包括活动流程、预算分配、人员分工和后续复盘安排，越详细越好"
    result = _intent(text, None)
    assert result.intent == "unknown"
    assert result.matched_by == "fallback"


def test_recognizable_procurement_content_still_extracts_when_model_down():
    """可确认的需求正文（含报告栏目词）在模型离线时仍能走原主路径。"""
    text = (
        "请问本项目需要什么材料：一、项目概况，某单位物业管理服务采购项目；"
        "采购预算120万元，合同履行期限一年，供应商须具备相应服务能力，验收办法另附。"
    )
    result = _intent(text, None)
    assert result.intent == "extract"
    assert result.matched_by == "fallback"


def test_llm_unknown_is_not_rewritten_into_action():
    """模型明确判 unknown 时照收，不被启发式改写成 extract。"""
    client = _FakeClient(reply={"intent": "unknown"})
    result = _intent("帮我搞定一下那个文件", client)
    assert result.intent == "unknown"
    assert result.matched_by == "llm"
    assert client.calls == 1


def test_illegal_and_oversized_input_degrades_to_unknown():
    """非法类型 / 超长输入只回 unknown，不抛异常、不调用模型。"""
    client = _FakeClient(reply={"intent": "extract"})
    assert _intent(None, client).intent == "unknown"
    assert _intent(123, client).intent == "unknown"
    assert _intent("采购需求" * 600, client).intent == "unknown"
    assert client.calls == 0
