"""对话式入口的意图识别层。

设计原则：

- **规则优先**：中文指令关键词正则命中即返回，零延迟、可测试、不依赖模型；
- **模型只做兜底分类**：规则判不出且文本像指令时才调用本地模型，输出被
  约束在白名单意图内。模型在这里**不产出任何事实值**——它只回答「用户想
  做什么」，越界或非法输出一律丢弃，回落到启发式或 unknown，由界面给出
  能力说明。证据回链（``block_id`` + ``quote``）等硬约束只作用于抽取链路，
  不受本模块影响。
- **内容与指令的分流**：粘贴进来的采购需求正文（长文本、无指令词）默认
  走 ``extract``，保持「输入框 → 解析任务」的原有主路径；只有短文本且带
  指令语气（帮我/能不能/怎么/哪些…）才进入模型兜底，避免需求正文被误判
  成提问。

边界规则（分流判定与回答边界分开写死，避免靠模型自觉）：

- **允许**：模型只输出白名单意图；动作由界面调用既有 ``/api/*`` 执行；
  界面只展示接口返回的事实数据，删除等有副作用动作必须二次确认。
- **不允许**：模型产出字段值、证据或结论；模型直接触发任何动作；把无依据
  的话当事实；用兜底猜测去「补齐」用户没表达清楚的指令。
- **超出范围**：规则与模型都判不出时回 ``unknown``，由界面用固定文案明确
  告知能力范围并说明「未执行任何操作」，不猜测、不代填、不编造来源。
- **失败降级**：模型异常、输出越界、输入非法都不得中断链路，也不得因此
  执行有副作用的动作；一律降级到最保守的判定（unknown 或可确认的正文）。
  证据回链（``block_id`` + ``quote``）仍只由抽取链路负责。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = ["INTENTS", "MAX_TEXT_CHARS", "ChatIntent", "classify_intent"]


# 输入边界：分类接口只处理一条指令/一小段文字，不接收整篇文档。
MAX_TEXT_CHARS = 2000

# 意图白名单：模型只能从中选择，前端按意图调用既有 /api/* 接口执行。
INTENTS = (
    "extract",  # 解析采购需求（文字或报告）
    "list_jobs",  # 列出解析记录
    "result",  # 查看某次解析的字段结果
    "missing",  # 查看缺失字段与待人工复核项
    "generate",  # 生成招标文件 .docx
    "download",  # 下载结果 JSON / 审核报告 / 平台草稿
    "delete",  # 删除解析记录（前端二次确认后执行）
    "help",  # 说明能力边界
    "unknown",  # 无法识别（含模型兜底失败）
)


@dataclass
class ChatIntent:
    """一次意图识别的结果；matched_by 记录判定来源，便于排查误判。"""

    intent: str
    matched_by: str  # rule（关键词）/ llm（模型兜底）/ fallback（启发式）
    keyword: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "intent": self.intent,
            "matched_by": self.matched_by,
            "keyword": self.keyword,
        }


# 顺序即优先级：更具体的意图放前面，避免「查看解析结果」被 extract 抢走。
_PATTERNS: tuple[tuple[str, str], ...] = (
    ("help", r"帮助|使用说明|你能(做|干)(什么|啥|哪些)|你会(什么|啥|哪些)|怎么(用|玩)|功能介绍"),
    ("list_jobs", r"(解析|需求|任务).{0,6}(列表|清单|有哪些)|(有哪些|列出|列一下)(所有)?(解析|需求|任务|记录)|解析记录|任务列表|历史(解析|记录)"),
    ("delete", r"删除|删掉|删了|清掉|移除(任务|记录)"),
    ("generate", r"生成.{0,8}(招标文件|docx|文档|成稿)|导出.{0,6}docx|把.{0,16}生成.{0,6}招标"),
    ("download", r"下载|导出.{0,12}(json|报告|草稿)|审核报告|平台草稿"),
    ("missing", r"缺(什么|了什么|哪些|失)|缺失字段|待复核|复核清单|还差(什么|哪些)|漏了什么"),
    ("result", r"(查看|看看|看一下|了解|介绍).{0,6}(结果|字段|证据|解析)|结果(如何|怎么样|呢)|抽取结果|解析结果|字段结果"),
    ("extract", r"(开始|新建|创建|跑)(一?次|一轮)?解析|解析(一下|这份|这个|该)|抽取(字段|一下)|帮我(解析|抽取)|新解析"),
)

# 指令语气提示词：出现这些词且规则未命中时，才值得花一次模型调用去分类。
_COMMAND_HINTS = (
    "帮我", "请", "能不能", "可以", "怎么", "如何", "什么", "哪些", "哪有",
    "查看", "看看", "看一下", "列出", "下载", "导出", "生成", "删除",
    "解析", "抽取", "帮助", "有没有", "是否", "你好", "？", "?",
)

# 兜底时才用：只有明确带采购语义、或像需求报告栏目的长文本，才允许在
# 模型不可用时按「需求正文」创建解析任务（该动作有副作用，不能靠猜）。
_CONTENT_HINTS = (
    "采购", "招标", "投标", "需求", "标的", "预算", "供应商", "合同",
    "交付", "资格", "验收", "报价", "采购人", "服务期", "履约",
)
_DOC_MARKERS = (
    "项目概况", "采购需求", "采购预算", "合同履行期限", "标的清单",
    "资格要求", "验收办法", "技术要求", "付款方式",
)
_STRONG_COMMAND_HINTS = (
    "帮我", "请", "能不能", "怎么", "如何", "查看", "看看", "列出",
    "下载", "导出", "生成", "删除", "解析", "抽取", "帮助", "你好",
)


def _looks_like_content(text: str) -> bool:
    """判断文本是否像采购需求正文，供模型不可用时的保守兜底。"""
    if any(marker in text for marker in _DOC_MARKERS):
        return True
    if sum(1 for word in _CONTENT_HINTS if word in text) >= 2:
        return True
    return len(text) >= 80 and not any(hint in text for hint in _STRONG_COMMAND_HINTS)


def _rule_match(text: str) -> ChatIntent | None:
    """按关键词正则识别指令；命中即返回，不调用模型。"""
    for intent, pattern in _PATTERNS:
        match = re.search(pattern, text)
        if match:
            return ChatIntent(intent=intent, matched_by="rule", keyword=match.group(0))
    return None


def _looks_like_command(text: str) -> bool:
    """短文本 + 指令语气才视为指令；长正文始终按采购需求内容处理。"""
    return len(text) <= 200 and any(hint in text for hint in _COMMAND_HINTS)


def _llm_prompt(text: str) -> tuple[str, str]:
    """构造意图分类的系统与用户提示词；只要求返回白名单内的一个词。"""
    system = (
        "你是政府采购招标助手的意图分类器。只输出一个 JSON 对象，"
        "不要输出任何解释。"
    )
    user = (
        "从下面的意图白名单中选择最匹配用户输入的一项：\n"
        "- extract：用户想解析采购需求（文字或报告），或输入本身就是采购需求内容\n"
        "- list_jobs：查看解析记录列表\n"
        "- result：查看某次解析的字段结果\n"
        "- missing：查看缺失字段与待人工复核项\n"
        "- generate：生成招标文件\n"
        "- download：下载结果文件\n"
        "- delete：删除解析记录\n"
        "- help：询问助手能做什么\n"
        "- unknown：以上都不是\n"
        "注意：如果输入是采购需求/报告的内容本身而不是指令，必须选 extract。\n"
        '输出格式：{"intent": "白名单中的词"}\n\n'
        "用户输入：" + text[:500]
    )
    return system, user


def _classify_with_client(text: str, client: Any) -> ChatIntent | None:
    """模型兜底：返回值越界、解析失败或调用异常时返回 None。"""
    try:
        system, user = _llm_prompt(text)
        data = client.chat_json(system, user, {
            "type": "object",
            "properties": {"intent": {"type": "string"}},
            "required": ["intent"],
        })
        intent = str(data.get("intent") or "").strip()
        # unknown 也照收：模型明确判「听不懂」时，不再被启发式改写成
        # 有副作用的 extract；越界值仍一律丢弃。
        if intent in INTENTS:
            return ChatIntent(intent=intent, matched_by="llm")
    except Exception:  # noqa: BLE001 - 模型不可用属预期路径，回落启发式
        return None
    return None


def classify_intent(text: str, llm_client: Any = None) -> ChatIntent:
    """识别一条输入的意图。

    优先级：输入边界校验 → 关键词规则 → 模型兜底（传入 client 且文本像
    指令时）→ 保守回落（仅可确认的需求正文）→ unknown。
    """
    if not isinstance(text, str):
        return ChatIntent(intent="unknown", matched_by="fallback", keyword="非法输入")
    text = text.strip()
    if not text:
        return ChatIntent(intent="unknown", matched_by="fallback", keyword="空输入")
    if len(text) > MAX_TEXT_CHARS:
        return ChatIntent(intent="unknown", matched_by="fallback", keyword="超长输入")

    matched = _rule_match(text)
    if matched:
        return matched
    if not _looks_like_command(text):
        # 无指令语气的内容（通常是粘贴的需求正文）保持原有解析主路径。
        return ChatIntent(intent="extract", matched_by="rule", keyword="需求正文")
    if llm_client is not None:
        matched = _classify_with_client(text, llm_client)
        if matched:
            return matched
    if len(text) >= 30 and _looks_like_content(text):
        # 只有能确认像需求正文的长文本才允许兜底创建任务；其余一律明确拒答。
        return ChatIntent(intent="extract", matched_by="fallback", keyword="需求正文兜底")
    return ChatIntent(intent="unknown", matched_by="fallback", keyword="越界或无法确认")
