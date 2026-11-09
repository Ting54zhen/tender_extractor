"""对话入口意图识别测试：规则命中、内容与指令分流、模型兜底白名单约束。

对应 DEVELOPMENT.md「对话式入口」：规则优先；模型只做白名单意图分类，
不产出事实值；模型不可用时回落启发式，不阻断解析主路径。
"""

from __future__ import annotations

import pytest

from tender_extractor.chat import (
    ChatIntent,
    classify_intent,
    detect_local_path,
    resolve_policy,
    resolve_target,
)


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
        ("我要做招标文件", "generate"),
        ("帮我做一份标书", "generate"),
        ("做招标文档", "generate"),
        ("起草招标文件", "generate"),
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
# 口语指令：短句不必带正式动词，也不该被当成需求正文
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("出一份标书", "generate"),
        ("弄个招标文件", "generate"),
        ("整一份招标文件", "generate"),
        ("把它弄成招标文件吧", "generate"),
        ("把标书做出来", "generate"),
        ("帮我搞个标书", "generate"),
        ("还差啥", "missing"),
        ("缺啥", "missing"),
        ("上次那份报告的结果", "result"),
        ("再解析一次", "extract"),
    ],
)
def test_spoken_commands_are_understood_without_llm(text: str, intent: str):
    """口语动作靠短句规则命中，不消耗模型调用；该聪明的地方不依赖在线。"""
    client = _FakeClient(reply={"intent": "unknown"})
    result = _intent(text, client)
    assert result.intent == intent
    assert result.matched_by == "rule"
    assert client.calls == 0


def test_spoken_rules_do_not_hijack_long_content():
    """长正文里的「出具一份标书」是陈述，不是指令。"""
    text = (
        "本项目要求中标供应商在合同签订后三十日内出具一份标书作为履约文件，"
        "具体格式由采购人另行确定。"
    )
    result = _intent(text)
    assert result.intent == "extract"
    assert result.matched_by == "rule"
    assert result.keyword == "需求正文"


def test_long_content_with_action_words_is_not_a_command():
    """长正文里的「删除 / 生成招标文件」是陈述，不能触发动作。"""
    text = (
        "一、项目概况：某单位物业管理服务采购项目，服务期一年，预算120万元。"
        "二、采购需求：详见标的清单，供应商须具备相应服务能力，不接受联合体投标。"
        "三、其他要求：中标供应商在合同期内不得擅自删除服务记录，并按采购人要求"
        "生成招标文件的过程档案留存备查，同时下载并保存每季度的考核报告。"
        "四、验收办法：按季度考核，年度汇总，考核结果与合同款支付挂钩。"
        "五、付款方式：按季度支付，需提供发票与考核报告。六、联系方式：某单位采购办公室。"
    )
    assert len(text) > 200  # 防止样本变短后测试失效
    result = _intent(text, None)
    assert result.intent == "extract"
    assert result.matched_by == "rule"
    assert result.keyword == "需求正文"


def test_spoken_reextract_without_target_asks_for_object():
    """「再解析一次」听懂是解析，但对象不明仍走反问，不硬造正文。"""
    result = _intent("再解析一次")
    assert result.intent == "extract"
    assert result.target is None
    assert result.policy["action"] == "ask"


def test_short_sentence_without_command_hints_goes_to_llm_when_available():
    """在线时，短句不再因为没有指令提示词就被当成正文。"""
    client = _FakeClient(reply={"intent": "result"})
    result = _intent("你看下这个咋样", client)
    assert result.intent == "result"
    assert result.matched_by == "llm"
    assert client.calls == 1


def test_same_short_sentence_stays_conservative_when_model_down():
    """离线时保持原启发式：无提示词的短句按正文路径，行为不变。"""
    result = _intent("你看下这个咋样", None)
    assert result.intent == "extract"
    assert result.matched_by == "rule"
    assert result.keyword == "需求正文"


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


# ---------------------------------------------------------------------------
# 动作对象槽位：路径优先、缺对象留空、指令句不冒充正文
# ---------------------------------------------------------------------------


def test_detect_local_path_picks_first_supported_file():
    assert detect_local_path('我要抽取"D:\\目录\\需求 (1).docx"的字段') == "D:\\目录\\需求 (1).docx"
    assert detect_local_path("把 D:/data/report.PDF 解析一下") == "D:/data/report.PDF"
    assert detect_local_path("帮我解析那个文件") is None


def test_resolve_target_prefers_path_over_content():
    """文件名里带「采购需求」时仍是 path，不能被正文判定抢走。"""
    path = "D:\\新建文件夹\\01-采购需求.docx"
    assert resolve_target('我要抽取"' + path + '"的结构化字段', "extract") == {
        "type": "path",
        "value": path,
    }


def test_resolve_target_keeps_confirmed_content():
    text = "某单位物业服务采购项目，采购预算120万元，合同履行期限一年，供应商须具备相应服务能力。"
    target = resolve_target(text, "extract")
    assert target is not None and target["type"] == "text"


def test_resolve_target_is_empty_for_short_command_and_other_intents():
    assert resolve_target("帮我解析那个文件", "extract") is None
    assert resolve_target("生成招标文件", "generate") is None


def test_classify_intent_attaches_target():
    result = _intent("开始解析 D:\\a\\需求.docx")
    assert result.intent == "extract"
    assert result.target == {"type": "path", "value": "D:\\a\\需求.docx"}
    assert result.to_dict()["target"] == result.target


# ---------------------------------------------------------------------------
# 执行策略：该笨的地方确定、该聪明的地方只在不明确时出手
# ---------------------------------------------------------------------------


def test_policy_executes_readonly_actions_directly():
    """只读动作不啰嗦：直接执行，风险最低。"""
    for text in ("查看解析结果", "缺失了哪些字段", "帮我看下解析记录", "下载审核报告"):
        policy = resolve_policy(_intent(text))
        assert policy["action"] == "execute"
        assert policy["risk"] == "low"


def test_policy_asks_when_extract_has_no_target():
    """要解析却说不清对象时先反问，绝不硬造一个几十字的「正文」。"""
    policy = resolve_policy(_intent("帮我解析那个文件"))
    assert policy["action"] == "ask"
    assert policy["risk"] == "medium"


def test_policy_executes_extract_with_explicit_target():
    policy = resolve_policy(_intent("开始解析 D:\\a\\需求.docx"))
    assert policy["action"] == "execute"
    assert policy["risk"] == "medium"


def test_policy_marks_irreversible_actions_as_high_risk():
    """生成与删除不能和只读动作同级，界面据此决定是否二次确认。"""
    assert resolve_policy(_intent("帮我生成招标文件"))["risk"] == "high"
    assert resolve_policy(_intent("把 job-1 删掉"))["risk"] == "high"


def test_policy_refuses_out_of_scope():
    policy = resolve_policy(_intent("能不能写一首关于招标的诗"))
    assert policy == {"risk": "none", "action": "refuse", "reason": "越界或无法确认"}


def test_classify_intent_attaches_policy():
    result = _intent("帮我解析那个文件")
    assert result.policy is not None
    assert result.to_dict()["policy"] == result.policy
