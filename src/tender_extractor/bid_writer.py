"""投标书生成器：招标文件 + 企业资源 → 投标书初稿。

链路：
    招标文件 --解析--> 招标要求（资格条件 / 评分点 / 废标条款 / 项目信息）
    企业档案 ---------> 资质、业绩、人员、承诺模板、证明材料索引
                              ↓
                    逐条映射 + LLM 撰写技术方案
                              ↓
                    投标书 Markdown（含缺口清单）

与解析组件一致的原则：每条响应都标注「招标原文」与「证明材料」，
材料缺失的条目单独汇总成缺口清单供人工补齐——不虚构企业能力。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .cleaning import clean_text
from .company_profile import (
    CompanyProfile,
    check_coverage,
    map_requirement_evidence,
)
from .document import read_document
from .llm_client import LLMClient, LLMError
from .pipeline import Extractor
from .relation_extract import RelationItem, RelationResult, extract_relations

SECTION_SYSTEM = """你是投标文件撰写专家。只输出 JSON，格式为 {"content": "正文"}。
要求：
1. 紧扣评分点的每一条要求，逐项回应，不要遗漏；
2. 引用投标人真实持有的资质、业绩、人员，不得编造；
3. 语言正式、具体，避免「我公司将努力」这类空话；
4. 分点作答，控制在 300-400 字。"""

SECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"content": {"type": "string"}},
    "required": ["content"],
    "additionalProperties": False,
}


@dataclass
class BidGenerationResult:
    markdown: str
    responses: list[dict[str, Any]] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "markdown": self.markdown,
            "responses": self.responses,
            "sections": self.sections,
            "gaps": self.gaps,
            "summary": self.summary,
            "meta": self.meta,
        }


def _field_value(fields: dict[str, Any], key: str) -> str:
    item = fields.get(key)
    if not isinstance(item, dict):
        return ""
    value = item.get("value")
    if value is None or value == "":
        return ""
    return str(value)


def _clip(text: Any, limit: int = 120) -> str:
    value = str(text or "").replace("\n", " ").strip()
    return value[:limit] + ("…" if len(value) > limit else "")


def build_section_prompt(
    project_name: str,
    criterion: RelationItem,
    profile: CompanyProfile,
    performances: list[Any],
) -> str:
    """为单个评分点构造技术方案撰写提示词。"""
    qual_lines = [
        "  - %s（证书号：%s，有效期至 %s）" % (q.name, q.certificate_no, q.valid_until)
        for q in profile.qualifications[:6]
    ]
    perf_lines = [
        "  - %s（%s，%s，合同金额 %s）"
        % (p.project_name, p.client, p.year, p.amount)
        for p in performances
    ]
    staff_lines = [
        "  - %s：%s，%s，持 %s，%s 年经验"
        % (p.name, p.role, p.title, p.certificate, p.years)
        for p in profile.personnel[:5]
    ]
    return (
        f"项目名称：{project_name}\n"
        f"评分点：{criterion.name}（{criterion.value or '未标注'} 分）\n"
        f"招标原文要求：{_clip(criterion.evidence, 400)}\n\n"
        f"投标人：{profile.name}\n"
        f"经营范围：{_clip(profile.business_scope, 200)}\n\n"
        f"持有资质：\n" + ("\n".join(qual_lines) or "  - 无") + "\n\n"
        f"类似业绩：\n" + ("\n".join(perf_lines) or "  - 无") + "\n\n"
        f"拟投入人员：\n" + ("\n".join(staff_lines) or "  - 无") + "\n\n"
        "请撰写该评分点的响应方案。"
    )


def generate_tech_section(
    client: LLMClient | None,
    project_name: str,
    criterion: RelationItem,
    profile: CompanyProfile,
    performances: list[Any],
) -> str:
    """生成单个评分点的技术方案正文。无 LLM 时返回可编辑的占位骨架。"""
    if client is None:
        return (
            "（待补充）请围绕「%s」撰写响应方案，建议覆盖：\n\n"
            "1. 总体思路与理解；\n"
            "2. 具体实施措施；\n"
            "3. 资源保障（人员、设备、资质）；\n"
            "4. 针对本项目的针对性说明。\n" % criterion.name
        )
    prompt = build_section_prompt(project_name, criterion, profile, performances)
    try:
        data = client.chat_json(SECTION_SYSTEM, prompt, SECTION_SCHEMA)
        text = str(data.get("content") or "")
    except LLMError as exc:
        return f"（生成失败：{exc}）"
    return _strip_code_fence(clean_text(text))


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def _render_scope(profile: CompanyProfile) -> str:
    return profile.business_scope or "（未填写经营范围）"


def render_bid_markdown(
    project_name: str,
    project_no: str,
    purchaser: str,
    budget: str,
    profile: CompanyProfile,
    responses: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    disqualifications: list[RelationItem],
) -> str:
    """组装完整投标书 Markdown。"""
    lines: list[str] = []
    lines.append("# 投标文件")
    lines.append("")
    lines.append(f"- **项目名称**：{project_name}")
    if project_no:
        lines.append(f"- **项目编号**：{project_no}")
    if purchaser:
        lines.append(f"- **采购人**：{purchaser}")
    if budget:
        lines.append(f"- **采购预算**：{budget}")
    lines.append(f"- **投标人**：{profile.name}")
    lines.append(f"- **统一社会信用代码**：{profile.credit_code}")
    lines.append(f"- **法定代表人**：{profile.legal_person}")
    lines.append(f"- **联系人**：{profile.contact_name}　{profile.contact_phone}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 一、投标函
    lines.append("## 一、投标函")
    lines.append("")
    lines.append(f"致：{purchaser or '（采购人）'}")
    lines.append("")
    lines.append(
        f"我方收到贵方「{project_name}」的招标文件，经详细研究，决定参加本项目的投标，"
        f"并郑重承诺如下："
    )
    lines.append("")
    lines.append("1. 我方完全理解并接受招标文件的全部条款与评审办法；")
    lines.append("2. 我方提交的投标文件真实、有效，如有虚假愿承担相应责任；")
    lines.append("3. 我方投标文件有效期为投标截止之日起 90 天；")
    lines.append("4. 若中标，我方将严格按照合同约定履行义务。")
    lines.append("")
    lines.append(f"投标人（盖章）：{profile.name}")
    lines.append("")
    lines.append(f"法定代表人或授权代表（签字）：{profile.legal_person}")
    lines.append("")

    # 二、资格证明文件
    lines.append("## 二、资格证明文件（逐条响应）")
    lines.append("")
    lines.append("| # | 招标资格要求 | 响应情况 | 证明材料 | 状态 |")
    lines.append("|---|-------------|---------|---------|------|")
    for index, item in enumerate(responses, 1):
        status = "已有" if item["covered"] else "待补充"
        evidence = item.get("evidence_file") or item["evidence_label"]
        lines.append(
            f"| {index} | {_clip(item['requirement'], 50)} | 满足 | "
            f"{_clip(evidence, 40)} | **{status}** |"
        )
    lines.append("")
    for index, item in enumerate(responses, 1):
        lines.append(f"### 2.{index} {item['requirement']}")
        lines.append("")
        evidence_text = item.get("evidence_file") or item["evidence_label"]
        lines.append(
            f"响应：针对「{_clip(item['requirement'], 60)}」这一要求，"
            f"我公司已具备相应条件，证明材料见「{_clip(evidence_text, 60)}」。"
        )
        lines.append("")
        lines.append(f"> 招标原文：{_clip(item.get('source'), 160)}")
        lines.append("")

    # 三、技术方案
    lines.append("## 三、技术方案（按评分点）")
    lines.append("")
    if not sections:
        lines.append("（未抽取到评分点，请对照招标文件补充技术方案章节。）")
        lines.append("")
    for index, section in enumerate(sections, 1):
        score = section.get("score") or "未标注"
        lines.append(f"### 3.{index} {section['criterion']}（{score} 分）")
        lines.append("")
        lines.append(f"> 招标原文：{_clip(section.get('requirement'), 200)}")
        lines.append("")
        body = section.get("content") or ""
        lines.append(body if body else "（待补充）")
        lines.append("")

    # 四、商务响应
    lines.append("## 四、商务响应")
    lines.append("")
    lines.append(f"- **投标人**：{profile.name}")
    lines.append(f"- **注册地址**：{profile.address}")
    lines.append(f"- **注册资本**：{profile.registered_capital}")
    lines.append(f"- **成立时间**：{profile.founded}")
    lines.append(f"- **经营范围**：{_render_scope(profile)}")
    lines.append("")
    lines.append("### 4.1 同类项目业绩")
    lines.append("")
    if profile.performances:
        lines.append("| # | 项目名称 | 采购人 | 年份 | 金额 |")
        lines.append("|---|---------|--------|------|------|")
        for index, perf in enumerate(profile.performances, 1):
            lines.append(
                f"| {index} | {perf.project_name} | {perf.client} | {perf.year} | {perf.amount} |"
            )
    else:
        lines.append("（暂无业绩记录，请补充。）")
    lines.append("")

    lines.append("### 4.2 拟投入人员")
    lines.append("")
    if profile.personnel:
        lines.append("| 姓名 | 角色 | 职称 | 证书 | 经验 |")
        lines.append("|------|------|------|------|------|")
        for staff in profile.personnel:
            lines.append(
                f"| {staff.name} | {staff.role} | {staff.title} | {staff.certificate} | {staff.years} 年 |"
            )
    else:
        lines.append("（暂无人员记录，请补充。）")
    lines.append("")

    # 五、服务承诺
    lines.append("## 五、服务承诺与声明")
    lines.append("")
    if profile.statements:
        for key, text in profile.statements.items():
            lines.append(f"- {text}")
    else:
        lines.append("（请补充无重大违法记录声明、中小企业声明等承诺文件。）")
    lines.append("")

    # 六、废标风险自查
    if disqualifications:
        lines.append("## 六、废标风险自查")
        lines.append("")
        lines.append("以下情形可能导致废标，请在提交前逐条确认：")
        lines.append("")
        for item in disqualifications:
            lines.append(f"- [ ] {_clip(item.name, 90)}")
        lines.append("")

    # 七、缺口清单
    lines.append("## 七、提交前需补充的材料")
    lines.append("")
    if gaps:
        lines.append("| # | 缺口项 | 需要的材料 |")
        lines.append("|---|--------|-----------|")
        for index, gap in enumerate(gaps, 1):
            lines.append(f"| {index} | {_clip(gap['requirement'], 60)} | {gap['need']} |")
    else:
        lines.append("全部资格要求均有对应材料，可直接提交。")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*本文档由 TenderGraph 根据招标文件与企业资源档案自动生成，请人工复核后使用。*")
    return "\n".join(lines)


def generate_bid_document(
    tender_path: str | Path,
    profile_path: str | Path,
    config_dir: str | Path | None = None,
    use_llm: bool = True,
    settings_override: dict[str, Any] | None = None,
    max_sections: int = 4,
) -> BidGenerationResult:
    """生成投标书初稿。

    - 资格响应：逐条映射证明材料，标出缺口；
    - 技术方案：按评分点调用模型撰写（``use_llm=False`` 时给占位骨架）；
    - 废标自查：把废标条款转成提交前检查清单。
    """
    extractor = Extractor(config_dir=config_dir, settings_override=settings_override)
    extracted = extractor.extract(tender_path, use_llm=use_llm)
    fields = extracted.to_dict().get("fields", {})

    profile = CompanyProfile.load(profile_path)
    project_name = _field_value(fields, "project_name") or "本项目"
    project_no = _field_value(fields, "project_number")
    purchaser = _field_value(fields, "purchaser")
    budget = _field_value(fields, "budget_amount")

    client: LLMClient | None = None
    relations = RelationResult()
    meta: dict[str, Any] = {}
    if use_llm:
        try:
            from .llm_client import build_llm_client

            doc = read_document(Path(tender_path))
            client = build_llm_client(extractor.settings.get("llm", {}))
            relations = extract_relations(client, doc, extractor.settings)
            meta["relations"] = relations.meta
        except Exception as exc:  # noqa: BLE001 - 生成失败降级为占位骨架
            meta["error"] = f"{type(exc).__name__}: {exc}"

    responses: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for requirement in relations.qualifications:
        mapping = map_requirement_evidence(requirement.name)
        covered = check_coverage(profile, mapping["key"])
        evidence_file = profile.evidence_index.get(mapping["key"], "")
        responses.append(
            {
                "requirement": requirement.name,
                "evidence_label": mapping["label"],
                "evidence_file": evidence_file,
                "covered": covered,
                "source": requirement.evidence,
            }
        )
        if not covered:
            gaps.append(
                {
                    "type": "qualification",
                    "requirement": requirement.name,
                    "need": mapping["label"],
                }
            )

    sections: list[dict[str, Any]] = []
    for criterion in relations.scoring_items[:max_sections]:
        performances = profile.find_performances(project_name, limit=3)
        content = generate_tech_section(client, project_name, criterion, profile, performances)
        sections.append(
            {
                "criterion": criterion.name,
                "score": criterion.value,
                "requirement": criterion.evidence,
                "content": content,
            }
        )

    markdown = render_bid_markdown(
        project_name=project_name,
        project_no=project_no,
        purchaser=purchaser,
        budget=budget,
        profile=profile,
        responses=responses,
        sections=sections,
        gaps=gaps,
        disqualifications=relations.disqualifications,
    )

    return BidGenerationResult(
        markdown=markdown,
        responses=responses,
        sections=sections,
        gaps=gaps,
        summary={
            "project_name": project_name,
            "qualifications": len(responses),
            "covered": sum(1 for item in responses if item["covered"]),
            "gaps": len(gaps),
            "sections": len(sections),
            "disqualification_checks": len(relations.disqualifications),
        },
        meta=meta,
    )
