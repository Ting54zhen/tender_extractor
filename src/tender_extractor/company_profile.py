"""企业资源档案：投标书生成的企业侧素材库。

投标书生成 = 招标要求（解析结果）+ 企业资源（本模块）→ 逐条响应。

档案覆盖企业基本信息、资质证书、历史业绩、人员、承诺模板与证明材料索引。
生成器据此判断「哪些要求已有材料可用、哪些需要补充」，并把已有材料直接
写进投标书对应章节，避免人工从头复制粘贴。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .cleaning import normalize_for_match


@dataclass
class Qualification:
    """企业持有的资质/证书。"""

    name: str
    certificate_no: str = ""
    issuer: str = ""
    valid_until: str = ""
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "certificate_no": self.certificate_no,
            "issuer": self.issuer,
            "valid_until": self.valid_until,
            "evidence": self.evidence,
        }


@dataclass
class Performance:
    """同类项目业绩。"""

    project_name: str
    amount: str = ""
    year: str = ""
    client: str = ""
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_name": self.project_name,
            "amount": self.amount,
            "year": self.year,
            "client": self.client,
            "evidence": self.evidence,
        }


@dataclass
class Personnel:
    """拟投入人员。"""

    name: str
    role: str = ""
    title: str = ""
    certificate: str = ""
    years: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "title": self.title,
            "certificate": self.certificate,
            "years": self.years,
        }


@dataclass
class CompanyProfile:
    """企业资源档案。"""

    name: str
    credit_code: str = ""
    registered_capital: str = ""
    founded: str = ""
    address: str = ""
    legal_person: str = ""
    contact_name: str = ""
    contact_phone: str = ""
    business_scope: str = ""
    qualifications: list[Qualification] = field(default_factory=list)
    performances: list[Performance] = field(default_factory=list)
    personnel: list[Personnel] = field(default_factory=list)
    statements: dict[str, str] = field(default_factory=dict)
    evidence_index: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # 加载与查询
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> "CompanyProfile":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        company = data.get("company") or {}
        return cls(
            name=company.get("name", ""),
            credit_code=company.get("credit_code", ""),
            registered_capital=company.get("registered_capital", ""),
            founded=company.get("founded", ""),
            address=company.get("address", ""),
            legal_person=company.get("legal_person", ""),
            contact_name=company.get("contact_name", ""),
            contact_phone=company.get("contact_phone", ""),
            business_scope=company.get("business_scope", ""),
            qualifications=[
                Qualification(**{k: v for k, v in item.items() if k in Qualification.__annotations__})
                for item in data.get("qualifications", []) or []
            ],
            performances=[
                Performance(**{k: v for k, v in item.items() if k in Performance.__annotations__})
                for item in data.get("performances", []) or []
            ],
            personnel=[
                Personnel(**{k: v for k, v in item.items() if k in Personnel.__annotations__})
                for item in data.get("personnel", []) or []
            ],
            statements=data.get("statements", {}) or {},
            evidence_index=data.get("evidence_index", {}) or {},
        )

    def find_qualification(self, keyword: str) -> Qualification | None:
        """按关键词模糊匹配企业资质。"""
        needle = normalize_for_match(keyword)
        if not needle:
            return None
        for item in self.qualifications:
            if needle in normalize_for_match(item.name) or normalize_for_match(item.name) in needle:
                return item
        return None

    def find_performances(self, keyword: str, limit: int = 3) -> list[Performance]:
        """挑出与项目类型相关的业绩（按关键词命中排序）。"""
        needle = normalize_for_match(keyword)
        scored: list[tuple[int, Performance]] = []
        for item in self.performances:
            text = normalize_for_match(item.project_name + " " + item.client)
            hits = sum(1 for token in needle.split() if token and token in text)
            scored.append((hits, item))
        scored.sort(key=lambda pair: -pair[0])
        return [item for _, item in scored[:limit]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "company": {
                "name": self.name,
                "credit_code": self.credit_code,
                "registered_capital": self.registered_capital,
                "founded": self.founded,
                "address": self.address,
                "legal_person": self.legal_person,
                "contact_name": self.contact_name,
                "contact_phone": self.contact_phone,
                "business_scope": self.business_scope,
            },
            "qualifications": [item.to_dict() for item in self.qualifications],
            "performances": [item.to_dict() for item in self.performances],
            "personnel": [item.to_dict() for item in self.personnel],
            "statements": self.statements,
            "evidence_index": self.evidence_index,
        }


# 资格要求关键词 -> 应提供的证明材料（key 对应 evidence_index）。
# 顺序即优先级，命中即停。
EVIDENCE_RULES: list[tuple[list[str], str, str]] = [
    # 顺序即优先级：先匹配语义最强的条目，避免「失信」被「税收」抢先命中。
    (["失信", "信用中国", "严重违法失信"], "“信用中国”网站查询记录截图", "credit_check"),
    (["联合体"], "非联合体投标声明", "no_consortium"),
    (["中小企业", "中小微", "小微企业", "政府采购政策"], "中小企业声明函", "sme"),
    (["独立承担民事责任", "法人资格", "营业执照"], "营业执照副本", "business_license"),
    (["社会保障", "社保"], "近 6 个月社会保险缴纳证明", "social_insurance"),
    (["缴纳税收", "纳税", "税收"], "近 6 个月依法缴纳税收证明", "tax_record"),
    (["商业信誉", "财务会计制度", "财务状况"], "近一年度财务审计报告", "financial_report"),
    (["设备和专业技术能力", "履约能力", "技术能力"], "主要设备清单及技术能力说明", "equipment"),
    (["重大违法记录", "违法记录"], "参加采购活动前 3 年内无重大违法记录声明函", "no_violation"),
    (["其他条件", "法律、行政法规"], "投标函及承诺函", "bid_letter"),
    (["保安服务许可", "保安服务"], "保安服务许可证", "security_license"),
]


def map_requirement_evidence(requirement: str) -> dict[str, str]:
    """把一条资格要求映射到「应提供的证明材料」。"""
    text = normalize_for_match(requirement)
    for keywords, label, key in EVIDENCE_RULES:
        if any(normalize_for_match(word) in text for word in keywords):
            return {"label": label, "key": key}
    return {"label": "待补充的证明材料", "key": ""}


def check_coverage(profile: CompanyProfile, key: str) -> bool:
    """企业档案里是否已有对应证明材料。"""
    if not key:
        return False
    return bool(profile.evidence_index.get(key))
