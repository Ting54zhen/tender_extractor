"""基于 GraphData 的纯 Python 图查询，生成产品级输出。

不依赖 SPARQL 引擎：直接遍历节点做查询，等价于预定义 SPARQL。
输出：投标符合性自查表 / 废标风险预警清单 / 评分点-证明材料映射。
"""

from __future__ import annotations

from typing import Any

from .graph_builder import GraphData


def _cell(text: Any, limit: int = 80) -> str:
    """表格单元格安全化：去竖线、压空白、截断。"""
    value = str(text or "").replace("|", "\\|").replace("\n", " ").strip()
    if len(value) > limit:
        return value[:limit] + "…"
    return value


def compliance_checklist(graph: GraphData) -> str:
    """投标符合性自查表：逐条资质 + 原文证据。"""
    quals = graph.nodes_of_type("Qualification")
    if not quals:
        return "（未抽取到资格条件实体，请检查原文或确认 LLM 可用）"
    lines = ["## 投标符合性自查表", "", "| # | 资格条件 | 原文证据 | 状态 |", "|---|---------|---------|------|"]
    for i, q in enumerate(quals, 1):
        lines.append(
            f"| {i} | {_cell(q.label, 60)} | {_cell(q.properties.get('evidence'), 80)} | 待核对 |"
        )
    return "\n".join(lines)


def risk_checklist(graph: GraphData) -> str:
    """废标风险预警清单：逐条废标条款 + 触发后果 + 原文证据。"""
    clauses = graph.nodes_of_type("DisqualificationClause")
    if not clauses:
        return "（未抽取到废标条款实体）"
    lines = [
        "## 废标风险预警清单",
        "",
        "| # | 废标条款 | 触发后果 | 原文证据 |",
        "|---|---------|---------|---------|",
    ]
    for i, c in enumerate(clauses, 1):
        lines.append(
            f"| {i} | {_cell(c.label, 60)} | {_cell(c.properties.get('value'), 20)} | {_cell(c.properties.get('evidence'), 80)} |"
        )
    lines.append("")
    lines.append("> 以上条款若未响应可能导致废标，请优先人工核对。")
    return "\n".join(lines)


def scoring_material_map(graph: GraphData) -> str:
    """评分点-证明材料映射：每个评分项 + 分值 + 原文证据。"""
    items = graph.nodes_of_type("ScoringCriterion")
    if not items:
        return "（未抽取到评分点实体）"
    lines = [
        "## 评分点-证明材料映射",
        "",
        "| # | 评分项 | 分值 | 原文证据 | 需准备材料 |",
        "|---|-------|-----|---------|-----------|",
    ]
    for i, s in enumerate(items, 1):
        lines.append(
            f"| {i} | {_cell(s.label, 60)} | {_cell(s.properties.get('score'), 10)} | "
            f"{_cell(s.properties.get('evidence'), 80)} | 待补充 |"
        )
    return "\n".join(lines)


def analyze_graph(graph: GraphData) -> dict[str, str]:
    """一次性返回三类分析结果。"""
    return {
        "compliance_checklist": compliance_checklist(graph),
        "risk_checklist": risk_checklist(graph),
        "scoring_material_map": scoring_material_map(graph),
    }


def render_knowledge_report(graph: GraphData) -> str:
    """把三类分析合并成一份完整 Markdown 报告。"""
    parts = [
        f"# 知识图谱分析报告：{graph.project_name}",
        "",
        "> 由 TenderGraph 基于「字段抽取 + 实体/关系抽取」自动生成，所有节点均带原文证据回链。",
        "",
        compliance_checklist(graph),
        "",
        risk_checklist(graph),
        "",
        scoring_material_map(graph),
        "",
        "---",
        f"*节点数：{len(graph.nodes)}；关系数：{len(graph.edges)}*",
    ]
    return "\n".join(parts)
