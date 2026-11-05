"""合规审查 v1：用确定性规则识别采购需求里的排他性/倾向性表述。

为什么只做规则层：法规判断需要口径稳定、可解释、可复现。模型容易把
「不得限定品牌」这类禁止性表述误判为违规，也会凭语气编出不存在的条款。
所以这里只匹配明确表述，输出「原文 + 修改建议」，**不改写文件、不阻断生成**，
所有命中项都进人工复核清单，误报可直接忽略。

法规依据：《政府采购法实施条例》第二十条（以不合理条件限制或排斥潜在供应商）、
财库〔2019〕38 号（不得要求提供生产厂家授权、承诺、证明、背书等）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .models import Document

# 句末/分号/换行切句：证据引文以「一句」为最小单位，避免整段进报告
_SENTENCE_SPLIT = re.compile(r"[。；;\n]")

# 否定语境：措施本身合法（如「不得限定品牌」），不能当成违规
_NEGATED = re.compile(r"(不得|禁止|严禁|不应|不得以|不予)")


@dataclass(frozen=True)
class ComplianceRule:
    """一条判定规则：正则 + 分级 + 修改建议。"""

    rule_id: str
    category: str
    severity: str                  # high | medium | low
    pattern: re.Pattern[str]
    suggestion: str


@dataclass
class ComplianceFinding:
    """一条命中记录，必须能回到原文块。"""

    rule_id: str
    category: str
    severity: str
    block_id: str
    quote: str
    matched: str
    suggestion: str
    section: str = ""
    basis: str = "《政府采购法实施条例》第二十条"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "category": self.category,
            "severity": self.severity,
            "block_id": self.block_id,
            "quote": self.quote,
            "matched": self.matched,
            "suggestion": self.suggestion,
            "section": self.section,
            "basis": self.basis,
        }


@dataclass
class ComplianceReport:
    """一次合规审查的结果；counts 由 findings 推导，避免两处计数漂移。"""

    findings: list[ComplianceFinding] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        counts = {"high": 0, "medium": 0, "low": 0}
        for finding in self.findings:
            counts[finding.severity] = counts.get(finding.severity, 0) + 1
        return counts

    @property
    def ok(self) -> bool:
        """没有 high 级命中即视为通过；medium/low 仍进人工复核。"""
        return not any(finding.severity == "high" for finding in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "counts": self.counts,
            "policy": {
                "version": "v1",
                "mode": "rule_only",
                "auto_fix": False,
                "basis": "《政府采购法实施条例》第二十条",
                "note": "只提示不改写，命中项需人工复核，误报可直接忽略。",
            },
            "findings": [finding.to_dict() for finding in self.findings],
        }


RULES: tuple[ComplianceRule, ...] = (
    ComplianceRule(
        rule_id="brand_restriction",
        category="品牌/供应商限定",
        severity="high",
        pattern=re.compile(
            r"(限定|指定|仅限|只接受|必须为|须为|只能使用)"
            r"[^。；;\n]{0,12}(品牌|商标|专利|原产地|生产厂家|供应商)"
        ),
        suggestion="删除品牌/供应商限定，改用功能、性能、技术参数等客观指标描述需求。",
    ),
    ComplianceRule(
        rule_id="regional_performance",
        category="特定区域业绩",
        severity="high",
        pattern=re.compile(
            r"(本市|本地|本省|当地|省内|市内|本区域)"
            r"[^。；;\n]{0,12}(业绩|案例|奖项|经验)"
        ),
        suggestion="删除特定行政区域的业绩/奖项要求，业绩条件应与项目规模、内容相关。",
    ),
    ComplianceRule(
        rule_id="local_service",
        category="本地化服务机构",
        severity="high",
        pattern=re.compile(
            r"(本市|本地|当地|本省|省内)"
            r"[^。；;\n]{0,10}(注册|设立|设有|拥有|具备)"
            r"[^。；;\n]{0,12}(分支机构|服务机构|售后|办公场所|服务团队)"
        ),
        suggestion="删除本地注册/本地服务机构的强制要求，改为中标后按合同约定提供服务的承诺。",
    ),
    ComplianceRule(
        rule_id="scale_condition",
        category="规模条件门槛",
        severity="medium",
        pattern=re.compile(
            r"(注册资本|实收资本|资产总额|营业收入|年营业额|从业人员|员工人数|纳税额)"
            r"[^。；;\n]{0,12}(不少于|不低于|达到|超过|须达到|以上)"
        ),
        suggestion="删除注册资本、营收、人员规模等规模条件，资格要求应与履约能力直接相关。",
    ),
    ComplianceRule(
        rule_id="manufacturer_authorization",
        category="厂家授权/背书",
        severity="medium",
        pattern=re.compile(
            r"(生产厂家|制造商)[^。；;\n]{0,12}(授权|承诺|背书|证明)"
        ),
        suggestion="删除生产厂家授权、承诺、背书等要求；进口货物按财政部规定另行处理。",
    ),
)


def analyze_compliance(document: Document) -> ComplianceReport:
    """扫描全文块，逐句匹配规则；命中项都带 block_id 与原文引文。"""
    findings: list[ComplianceFinding] = []
    seen: set[tuple[str, str, str]] = set()

    for block in document.blocks:
        text = block.text or ""
        if not text.strip():
            continue
        for sentence in _SENTENCE_SPLIT.split(text):
            sentence = sentence.strip()
            if not sentence:
                continue
            for rule in RULES:
                match = rule.pattern.search(sentence)
                if match is None:
                    continue
                # 措施本身合法（如「不得限定品牌」）时跳过，避免把禁令读成违规
                if _NEGATED.search(sentence[: match.start()]):
                    continue
                key = (rule.rule_id, block.id, sentence)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    ComplianceFinding(
                        rule_id=rule.rule_id,
                        category=rule.category,
                        severity=rule.severity,
                        block_id=block.id,
                        quote=sentence,
                        matched=match.group(0),
                        suggestion=rule.suggestion,
                        section=block.section,
                    )
                )

    return ComplianceReport(findings=findings)
