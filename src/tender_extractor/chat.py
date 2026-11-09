"""对话式入口的意图识别层。

设计原则：

- **规则优先**：中文指令关键词正则命中即返回，零延迟、可测试、不依赖模型；
- **模型只做兜底分类**：规则判不出且文本是「短句、不像需求正文」时才调用
  本地模型，输出被约束在白名单意图内。模型在这里**不产出任何事实值**——
  它只回答「用户想做什么」，越界或非法输出一律丢弃，回落到启发式或
  unknown，由界面给出能力说明。证据回链（``block_id`` + ``quote``）等硬
  约束只作用于抽取链路，不受本模块影响。
- **内容与指令的分流**：粘贴进来的采购需求正文（长文本、带采购栏目词）
  默认走 ``extract``，保持「输入框 → 解析任务」的原有主路径。不再用
  「指令提示词」当模型兜底的门槛——口语指令常常一个提示词都没有
  （「把它弄成招标文件吧」），词表只会自我设限；约束改为「短句 + 不像
  正文」。离线或模型异常时仍按原启发式，行为不变。

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

__all__ = [
    "INTENTS",
    "MAX_TEXT_CHARS",
    "ChatIntent",
    "classify_intent",
    "detect_local_path",
    "resolve_policy",
    "resolve_target",
]


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
    """一次意图识别的结果；matched_by 记录判定来源，便于排查误判。

    ``target`` 是动作对象槽位：``{"type": "path"|"text", "value": ...}``。
    只对有副作用的 ``extract`` 填充；识别不出对象时为 ``None``，由界面反问，
    不把整句指令当正文硬执行——这正是「路径句被当成 55 字需求」的修法。
    """

    intent: str
    matched_by: str  # rule（关键词）/ llm（模型兜底）/ fallback（启发式）
    keyword: str = ""
    target: dict[str, str] | None = None
    policy: dict[str, str] | None = None

    def __post_init__(self) -> None:
        # 所有构造出口（含降级、异常兜底）都要有策略，不能只有正常路径有。
        if self.policy is None:
            self.policy = resolve_policy(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "matched_by": self.matched_by,
            "keyword": self.keyword,
            "target": self.target,
            "policy": self.policy,
        }


# 顺序即优先级：更具体的意图放前面，避免「查看解析结果」被 extract 抢走。
_PATTERNS: tuple[tuple[str, str], ...] = (
    ("help", r"帮助|使用说明|你能(做|干)(什么|啥|哪些)|你会(什么|啥|哪些)|怎么(用|玩)|功能介绍"),
    ("list_jobs", r"(解析|需求|任务).{0,6}(列表|清单|有哪些)|(有哪些|列出|列一下)(所有)?(解析|需求|任务|记录)|解析记录|任务列表|历史(解析|记录)"),
    ("delete", r"删除|删掉|删了|清掉|移除(任务|记录)"),
    ("generate", r"生成.{0,8}(招标文件|docx|文档|成稿)|制作.{0,6}(招标文件|标书|文档)|起草.{0,4}(招标文件|标书)|做(一份|一个|个)?(招标文件|招标文档|标书)|导出.{0,6}docx|把.{0,16}(生成|做).{0,6}招标"),
    ("download", r"下载|导出.{0,12}(json|报告|草稿)|审核报告|平台草稿"),
    ("missing", r"缺(什么|了什么|哪些|失)|缺失字段|待复核|复核清单|还差(什么|哪些)|漏了什么"),
    ("result", r"(查看|看看|看一下|了解|介绍).{0,6}(结果|字段|证据|解析)|结果(如何|怎么样|呢)|抽取结果|解析结果|字段结果"),
    ("extract", r"(开始|新建|创建|跑)(一?次|一轮)?解析|解析(一下|这份|这个|该)|抽取(字段|一下)|帮我(解析|抽取)|新解析"),
)

# 超过这个长度按需求正文处理：不参与指令匹配，也不送模型分类。
_COMMAND_MAX_CHARS = 200

# 口语化动作：短句里常见、又缺正式动词（出/弄/整/搞/来），只在短文本生效，
# 避免长正文里「供应商应出具一份标书」这类陈述被当成指令。顺序与正式规则
# 一致：先具体动作（generate / missing / result），extract 最后。
_SPOKEN_MAX_CHARS = 40
_SPOKEN_PATTERNS: tuple[tuple[str, str], ...] = (
    ("generate", r"(?:出|弄|整|搞|写|来|做)(?:一份|一个|个|一)?(?:招标文件|招标文档|标书|成稿)"),
    ("generate", r"把.{0,16}(?:生成|做|弄|整|搞|出).{0,6}(?:招标文件|标书|成稿)"),
    ("generate", r"把.{0,10}(?:招标文件|标书|成稿).{0,6}(?:做|弄|整|搞|出|生成)"),
    ("missing", r"(?:还差|缺|少)(?:什么|啥|哪些)"),
    ("result", r"(?:上次|刚才|之前|那份|这份|这).{0,10}(?:报告|任务|文件|需求).{0,6}(?:结果|怎么样|如何)"),
    ("extract", r"(?:再|重新)(?:解析|抽取)(?:一次|一遍|一下)?"),
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


# 本地文件路径：与前端 use_chat.js 的 detectLocalPath 同一口径，扩展名白名单
# 与 /api/extract 上传一致。这里只识别，不读取、不校验存在性（那是接口层的事）。
_LOCAL_PATH_RE = re.compile(
    r"[A-Za-z]:[\\/][^\n\"'，。；：、<>|?*]*?\.(?:docx|pdf|txt)", re.IGNORECASE
)
_MIN_TEXT_TARGET_CHARS = 30


def detect_local_path(text: str) -> str | None:
    """从一句话里挑出首个本地文件路径，供本机服务代读。"""
    match = _LOCAL_PATH_RE.search(text or "")
    return match.group(0) if match else None


def resolve_target(text: str, intent: str) -> dict[str, str] | None:
    """给有副作用的 extract 意图补动作对象：本地路径优先，其次确认过的正文。

    路径必须先判：文件名里常带「采购需求」字样，若先跑正文判定，整句指令
    会被当成需求正文打包成几十字的假文档（「文档内容过短」跳过的由来）。
    """
    if intent != "extract":
        return None
    path = detect_local_path(text)
    if path:
        return {"type": "path", "value": path}
    stripped = (text or "").strip()
    if len(stripped) >= _MIN_TEXT_TARGET_CHARS and _looks_like_content(stripped):
        return {"type": "text", "value": stripped}
    return None


#: 意图的固有风险：只读动作最低，创建任务居中，不可逆/对外动作最高。
#: 这张表是「该笨」的落点——风险不交给模型判断，避免它把删除说成低风险。
_RISK_BY_INTENT = {
    "help": "low",
    "list_jobs": "low",
    "result": "low",
    "missing": "low",
    "download": "low",
    "extract": "medium",
    "generate": "high",
    "delete": "high",
    "unknown": "none",
}

_POLICY_REASONS = {
    "low": "只读动作，直接执行",
    "medium": "对象明确，直接解析",
    "high": "不可逆或对外动作，建议先确认",
    "none": "越界或无法确认",
}


def resolve_policy(intent: ChatIntent) -> dict[str, str]:
    """把「该笨/该聪明」落成执行策略：直接执行、反问或拒答。

    - 只读动作不啰嗦，直接执行；
    - ``extract`` 是唯一「可以聪明但必须先说清对象」的动作：模型可以理解
      模糊表述，但说不出文件或正文时只反问，绝不硬造内容；
    - ``generate`` / ``delete`` 标高风险，由界面决定是否二次确认；
    - ``unknown`` 一律拒答，不猜、不代填。
    """
    risk = _RISK_BY_INTENT.get(intent.intent, "none")
    if risk == "none":
        return {"risk": "none", "action": "refuse", "reason": _POLICY_REASONS["none"]}
    if intent.intent == "extract" and not intent.target:
        return {"risk": "medium", "action": "ask", "reason": "缺少要解析的文件或正文"}
    return {"risk": risk, "action": "execute", "reason": _POLICY_REASONS[risk]}


def _rule_match(text: str) -> ChatIntent | None:
    """按关键词正则识别指令；命中即返回，不调用模型。

    正式规则先跑；口语规则只在短句上兜一层，长文本不参与，避免陈述句
    被动作词抢走。
    """
    for intent, pattern in _PATTERNS:
        match = re.search(pattern, text)
        if not match:
            continue
        # 长文本按需求正文处理，不拿正文里的「删除」「生成招标文件」当指令；
        # 与 _looks_like_command 的「≤200 字才算指令」保持同一口径。
        if len(text) > _COMMAND_MAX_CHARS and intent != "extract":
            continue
        return ChatIntent(intent=intent, matched_by="rule", keyword=match.group(0))
    if len(text) <= _SPOKEN_MAX_CHARS:
        for intent, pattern in _SPOKEN_PATTERNS:
            match = re.search(pattern, text)
            if match:
                return ChatIntent(intent=intent, matched_by="rule", keyword=match.group(0))
    return None


def _looks_like_command(text: str) -> bool:
    """短文本 + 指令语气才视为指令；长正文始终按采购需求内容处理。

    这是模型不可用时的启发式门槛，不再决定「要不要问模型」。
    """
    return len(text) <= _COMMAND_MAX_CHARS and any(hint in text for hint in _COMMAND_HINTS)


def _worth_model(text: str) -> bool:
    """短句且不像需求正文，就值得交给模型理解一次。

    口语指令经常没有任何「指令提示词」（「把它弄成招标文件吧」），用词表
    当门票等于把该聪明的场景挡在门外；反过来也不能把整篇正文送进模型。
    """
    return len(text) <= _COMMAND_MAX_CHARS and not _looks_like_content(text)


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
    """识别一条输入的意图，并补齐动作对象槽位（target）。

    优先级：输入边界校验 → 关键词规则 → 模型兜底（传入 client 且文本像
    指令时）→ 保守回落（仅可确认的需求正文）→ unknown。target 由
    ``resolve_target`` 统一补，缺对象时留 None 让界面反问，不硬执行。
    """
    result = _classify(text, llm_client)
    if isinstance(text, str):
        result.target = resolve_target(text.strip(), result.intent)
    result.policy = resolve_policy(result)
    return result


def _classify(text: str, llm_client: Any = None) -> ChatIntent:
    """只判意图、不碰动作对象；槽位由 classify_intent 统一补。"""
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
    # 模型可用时放宽触发：短句且不像正文就交给模型理解，不再先过提示词表。
    # 模型异常或离线时继续走下面的启发式，行为与原来一致。
    if llm_client is not None and _worth_model(text):
        matched = _classify_with_client(text, llm_client)
        if matched:
            return matched
    if not _looks_like_command(text):
        # 无指令语气的内容（通常是粘贴的需求正文）保持原有解析主路径。
        return ChatIntent(intent="extract", matched_by="rule", keyword="需求正文")
    if len(text) >= 30 and _looks_like_content(text):
        # 只有能确认像需求正文的长文本才允许兜底创建任务；其余一律明确拒答。
        return ChatIntent(intent="extract", matched_by="fallback", keyword="需求正文兜底")
    return ChatIntent(intent="unknown", matched_by="fallback", keyword="越界或无法确认")
