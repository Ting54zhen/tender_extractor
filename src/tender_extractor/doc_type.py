"""提取前文档类型识别。

目的不是做通用文本分类，而是拦住明显无关的文档（简历、论文、小说、说明书、
财务报表等），避免规则和模型对无关内容硬抽字段、编造结果。

判断完全基于文档结构和关键词，不依赖模型，因此 ``--no-llm`` 下同样生效。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .cleaning import normalize_for_match
from .models import Document

DEFAULT_THRESHOLD = 4.0

# 高精度标题词：出现就基本可以确定是招标/采购类文件。
_TITLE_STRONG = (
    "采购需求报告",
    "招标需求报告",
    "招标文件",
    "采购文件",
    "招标书",
    "投标邀请",
    "竞争性磋商",
    "竞争性谈判",
    "询价公告",
    "询价通知书",
    "单一来源",
    "资格预审文件",
    "招标公告",
    "采购公告",
)

# 中等强度：单独出现不足以判定，需要正文配合。
_TITLE_MEDIUM = (
    "采购需求",
    "需求报告",
    "招标需求",
    "项目需求",
    "采购项目",
)

# 弱信号：很多无关文档也会出现，只加分不判定。
_TITLE_WEAK = (
    "招标",
    "采购",
    "投标",
    "中标",
    "供应商",
    "磋商",
    "询价",
)

_SECTION_TERMS = (
    "项目概况",
    "采购需求",
    "采购内容",
    "采购标的",
    "技术要求",
    "商务要求",
    "资格要求",
    "投标人资格",
    "供应商资格",
    "评审办法",
    "评标办法",
    "评分标准",
    "评分办法",
    "合同条款",
    "投标人须知",
    "付款方式",
    "履约保证金",
    "验收要求",
    "投标文件格式",
    "采购方式",
    "预算金额",
    "最高限价",
)

_ENTITY_TERMS = (
    "采购人",
    "招标人",
    "采购代理机构",
    "代理机构",
    "投标人",
    "供应商",
    "中标",
    "开标",
    "评标",
    "评审",
    "报价",
    "预算金额",
    "最高限价",
    "采购方式",
    "合同",
    "履约",
    "保证金",
    "验收",
    "采购包",
    "标的",
)

_GOV_TERMS = (
    "政府采购",
    "公开招标",
    "邀请招标",
    "竞争性磋商",
    "竞争性谈判",
    "单一来源",
    "中标候选人",
    "评标委员会",
    "投标保证金",
    "采购代理机构",
)

_MONEY_TERMS = (
    "采购预算",
    "预算金额",
    "最高限价",
    "控制价",
    "项目预算",
    "采购金额",
)

# 出现这些词通常说明不是招标需求文件；只做扣分，避免误伤“采购用户手册”这类文档。
_NEGATIVE_TERMS = (
    "个人简历",
    "求职简历",
    "简历",
    "小说",
    "散文",
    "诗歌",
    "剧本",
    "读后感",
    "毕业论文",
    "学位论文",
    "开题报告",
    "参考文献",
    "致谢",
    "实验报告",
    "课程论文",
    "试卷",
    "试题",
    "答案",
    "产品说明书",
    "用户手册",
    "操作手册",
    "使用说明书",
    "财务报表",
    "审计报告",
    "会议纪要",
    "请假条",
    "工作总结",
    "述职报告",
    "报销单",
    "请购单",
)

# 标题里出现这些词且没有招标类标题特征时，直接判定为非招标文件。
_TITLE_NEGATIVE = (
    "简历",
    "小说",
    "散文",
    "诗歌",
    "剧本",
    "论文",
    "开题报告",
    "实验报告",
    "试卷",
    "试题",
    "说明书",
    "用户手册",
    "操作手册",
    "财务报表",
    "审计报告",
    "会议纪要",
    "请假",
    "工作总结",
    "述职",
    "报销",
    "请购",
    # 投标/响应文件、合同是采购流程里的其他文档，不是“招标需求报告”。
    "投标文件",
    "响应文件",
    "投标书",
    "合同",
)


@dataclass
class DocTypeResult:
    is_tender: bool
    score: float
    threshold: float
    confidence: float
    reason: str
    signals: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_tender": self.is_tender,
            "score": round(self.score, 2),
            "threshold": self.threshold,
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
            **self.signals,
        }


def _distinct_hits(text: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if normalize_for_match(term) in text]


def _table_signal(doc: Document) -> bool:
    """采购标的表：表头同时有商务列和采购类词汇。"""
    business_cols = ("序号", "名称", "数量", "单价", "金额", "单位", "规格", "品牌")
    tender_cols = ("采购", "标的", "服务项目", "货物", "技术要求", "报价", "合同")
    for block in doc.tables:
        if not block.rows:
            continue
        header = normalize_for_match(" ".join(block.rows[0]))
        columns = sum(1 for col in business_cols if col in header)
        if columns >= 2 and any(col in header for col in tender_cols):
            return True
    return False


def _confidence(score: float, threshold: float) -> float:
    if score >= threshold:
        return min(0.99, 0.6 + (score - threshold) * 0.05)
    return min(0.99, 0.6 + (threshold - score) * 0.1)


def classify_document(
    doc: Document,
    threshold: float = DEFAULT_THRESHOLD,
) -> DocTypeResult:
    """判断文档是否属于招标/采购需求类文件。"""
    filename = Path(doc.path).name if doc.path else ""
    title = normalize_for_match(f"{doc.title} {filename}")
    full = normalize_for_match(doc.full_text())
    chars = len(full)

    title_strong = _distinct_hits(title, _TITLE_STRONG)
    title_medium = _distinct_hits(title, _TITLE_MEDIUM)
    title_weak = _distinct_hits(title, _TITLE_WEAK)
    section_hits = _distinct_hits(full, _SECTION_TERMS)
    entity_hits = _distinct_hits(full, _ENTITY_TERMS)
    gov_hits = _distinct_hits(full, _GOV_TERMS)
    money_hits = _distinct_hits(full, _MONEY_TERMS)
    negative_hits = _distinct_hits(full, _NEGATIVE_TERMS)
    title_negative = _distinct_hits(title, _TITLE_NEGATIVE)
    table_signal = _table_signal(doc)

    score = 0.0
    if title_strong:
        score += 4.0 + 0.5 * (len(title_strong) - 1)
    elif title_medium:
        score += 2.5 + 0.5 * (len(title_medium) - 1)
    score += min(2.0, 1.0 * len(title_weak))
    score += min(3.5, 0.35 * len(section_hits))
    score += min(2.5, 0.25 * len(entity_hits))
    score += min(2.25, 0.75 * len(gov_hits))
    if money_hits:
        score += 1.5
    if table_signal:
        score += 2.0
    score -= min(6.0, 2.0 * len(negative_hits))

    content_signal = bool(
        len(section_hits) >= 2
        or len(entity_hits) >= 3
        or table_signal
        or (money_hits and (section_hits or entity_hits))
    )

    signals = {
        "title_strong": title_strong,
        "title_medium": title_medium,
        "title_weak": title_weak,
        "sections": section_hits,
        "entities": entity_hits,
        "government_terms": gov_hits,
        "money_terms": money_hits,
        "negative": negative_hits,
        "table_signal": table_signal,
        "chars": chars,
    }

    if chars < 60 and not title_strong:
        return DocTypeResult(
            False,
            score,
            threshold,
            _confidence(score, threshold),
            f"文档内容过短（{chars} 字），无法识别为招标/采购需求文件，已跳过抽取。",
            signals,
        )

    if title_negative and not (title_strong or title_medium):
        return DocTypeResult(
            False,
            score,
            threshold,
            _confidence(score, threshold),
            f"标题或文件名疑似为“{title_negative[0]}”，不是招标/采购需求文件，已跳过抽取。",
            signals,
        )

    is_tender = score >= threshold and content_signal
    if is_tender:
        reason = f"识别为招标/采购需求类文件（匹配得分 {score:.2f} ≥ 阈值 {threshold:.2f}）。"
    elif not content_signal:
        reason = (
            f"未发现足够的招标/采购结构特征（匹配得分 {score:.2f}，阈值 {threshold:.2f}），"
            "已跳过抽取。"
        )
        if negative_hits:
            reason = (
                f"疑似不是招标/采购需求文件（命中非招标特征：{'、'.join(negative_hits[:5])}），"
                "已跳过抽取。"
            )
    else:
        reason = (
            f"未识别出足够的招标/采购特征（匹配得分 {score:.2f} < 阈值 {threshold:.2f}），"
            "已跳过抽取。"
        )
    return DocTypeResult(
        is_tender,
        score,
        threshold,
        _confidence(score, threshold),
        reason,
        signals,
    )
