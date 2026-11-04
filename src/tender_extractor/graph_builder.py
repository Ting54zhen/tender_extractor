"""把「字段抽取结果 + 关系抽取结果」构建成知识图谱（RDF 三元组 + Turtle）。

零新增运行时依赖：用纯 Python 生成标准 Turtle 文本，可存成 ``.ttl`` 用
Protégé 打开；图查询见 ``graph_queries``（纯 Python 遍历，等价于预定义
SPARQL）。不依赖 rdflib / Neo4j，学生机即可运行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 字段 key -> 数据属性名（tender: 前缀下的局部名）。
PROJECT_FIELD_MAP: dict[str, str] = {
    "project_name": "projectName",
    "project_number": "projectNumber",
    "budget_amount": "budgetAmount",
    "procurement_method": "procurementMethod",
    "purchaser": "purchaser",
    "agency": "agency",
    "contract_period": "contractPeriod",
    "consortium_allowed": "consortiumAllowed",
    "subcontracting_allowed": "subcontractingAllowed",
    "price_method": "priceMethod",
}

# 关系类别 -> 对象属性名。
KIND_PREDICATE: dict[str, str] = {
    "qualifications": "requiresQualification",
    "scoring_items": "hasScoringCriterion",
    "disqualifications": "hasDisqualificationClause",
}

# 关系类别 -> 节点 RDF 类型。
KIND_TYPE: dict[str, str] = {
    "qualifications": "Qualification",
    "scoring_items": "ScoringCriterion",
    "disqualifications": "DisqualificationClause",
}

# 关系类别 -> 节点 id 前缀。
KIND_PREFIX: dict[str, str] = {
    "qualifications": "qual",
    "scoring_items": "scoring",
    "disqualifications": "clause",
}

PREFIXES: dict[str, str] = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "tender": "http://tendergraph.ai/ontology#",
}

ONTOLOGY_CLASSES = [
    "Project",
    "ProcurementItem",
    "Qualification",
    "ScoringCriterion",
    "DisqualificationClause",
    "PaymentTerm",
]

# 对象属性（连接两个节点）。
OBJECT_PROPERTIES = [
    "hasProcurementItem",
    "requiresQualification",
    "hasScoringCriterion",
    "hasDisqualificationClause",
    "hasPaymentTerm",
]

# 数据属性（连接节点与字面量）。
DATATYPE_PROPERTIES = [
    "projectName",
    "projectNumber",
    "budgetAmount",
    "procurementMethod",
    "purchaser",
    "agency",
    "contractPeriod",
    "consortiumAllowed",
    "subcontractingAllowed",
    "priceMethod",
    "name",
    "value",
    "score",
    "evidence",
    "section",
    "prepayRatio",
    "settlement",
]


@dataclass
class Node:
    """图节点。id 为局部短名（如 ``qual_1``）。"""

    id: str
    rdf_type: str
    label: str
    properties: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.rdf_type,
            "label": self.label,
            "properties": self.properties,
        }


@dataclass
class Edge:
    """图边。predicate 为对象属性局部名。"""

    source: str
    predicate: str
    target: str

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "predicate": self.predicate, "target": self.target}


@dataclass
class GraphData:
    project_name: str = ""
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    # 关系为空时由规则层补的节点来源，如 {"disqualifications": "规则层★条款兜底"}
    fallbacks: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_name": self.project_name,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "fallbacks": dict(self.fallbacks),
        }

    def nodes_of_type(self, rdf_type: str) -> list[Node]:
        return [node for node in self.nodes if node.rdf_type == rdf_type]


def _field_value(fields: dict[str, Any], key: str) -> str:
    """从 ``result["fields"]`` 里取值，空值归一为 ``""``。"""
    field = fields.get(key)
    if not isinstance(field, dict):
        return ""
    value = field.get("value")
    if value is None or value == "":
        return ""
    return str(value)


def _star_clause_fallback(result_dict: dict[str, Any]) -> list[dict[str, str]]:
    """规则层 ★ 条款转废标条款节点。

    需求报告里「★不满足即废标」常写在标的清单表格中，不在「废标/无效投标」
    章节里，模型关系抽取容易整体返回空数组。规则层已按行抽到 ``star_clauses``，
    直接复用，避免图谱风险清单在演示时是空表。
    """
    structured = result_dict.get("structured") or {}
    out: list[dict[str, str]] = []
    for raw in structured.get("star_clauses") or []:
        if not isinstance(raw, dict):
            continue
        if "★" not in str(raw.get("type") or ""):
            continue
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        out.append(
            {
                "name": text,
                "value": "实质性条款（不满足即废标）",
                "evidence": text,
                "section": str(raw.get("item_name") or ""),
            }
        )
    return out


def build_graph_from_result(
    result_dict: dict[str, Any],
    relations_dict: dict[str, Any],
) -> GraphData:
    """把字段结果 + 关系结果组合成一个 GraphData。"""
    fields = result_dict.get("fields", {}) or {}
    nodes: list[Node] = []
    edges: list[Edge] = []
    fallbacks: dict[str, str] = {}
    relations = {kind: list(relations_dict.get(kind) or []) for kind in KIND_PREDICATE}
    if not relations["disqualifications"]:
        star_items = _star_clause_fallback(result_dict)
        if star_items:
            relations["disqualifications"] = star_items
            fallbacks["disqualifications"] = "LLM 未返回废标条款，已用规则层★条款补齐"

    project_label = _field_value(fields, "project_name") or "未命名项目"
    project_props: dict[str, str] = {}
    for key, prop in PROJECT_FIELD_MAP.items():
        value = _field_value(fields, key)
        if value:
            project_props[prop] = value
    nodes.append(Node("project", "Project", project_label, project_props))

    item_name = _field_value(fields, "procurement_target") or _field_value(fields, "procurement_item")
    if item_name:
        nodes.append(Node("item_1", "ProcurementItem", item_name, {"name": item_name}))
        edges.append(Edge("project", "hasProcurementItem", "item_1"))

    for kind in ("qualifications", "scoring_items", "disqualifications"):
        predicate = KIND_PREDICATE[kind]
        rdf_type = KIND_TYPE[kind]
        prefix = KIND_PREFIX[kind]
        for index, raw in enumerate(relations[kind], start=1):
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()
            if not name:
                continue
            props: dict[str, str] = {
                "evidence": str(raw.get("evidence") or ""),
                "section": str(raw.get("section") or ""),
            }
            value = str(raw.get("value") or "").strip()
            if value:
                props["value"] = value
                if kind == "scoring_items":
                    props["score"] = value
            nodes.append(Node(f"{prefix}_{index}", rdf_type, name, props))
            edges.append(Edge("project", predicate, f"{prefix}_{index}"))

    prepay = _field_value(fields, "contract_prepay_ratio")
    settlement = _field_value(fields, "contract_settlement")
    if prepay or settlement:
        props: dict[str, str] = {}
        if prepay:
            props["prepayRatio"] = prepay
        if settlement:
            props["settlement"] = settlement
        nodes.append(Node("payment", "PaymentTerm", settlement or "付款条款", props))
        edges.append(Edge("project", "hasPaymentTerm", "payment"))

    return GraphData(
        project_name=project_label,
        nodes=nodes,
        edges=edges,
        fallbacks=fallbacks,
    )


def _slug(value: str) -> str:
    """把任意文本压成可作 Turtle 局部名的安全片段。"""
    out = []
    for ch in value:
        if ch.isalnum() and ord(ch) < 128:
            out.append(ch)
        else:
            out.append("_")
    text = "".join(out).strip("_")
    return text or "node"


def _ttl_literal(value: Any) -> str:
    """生成 Turtle 字符串字面量，转义引号与换行。"""
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{text}"'


def to_turtle(graph: GraphData) -> str:
    """把 GraphData 序列化成标准 Turtle 文本（可直接存 .ttl 用 Protégé 打开）。"""
    lines: list[str] = []
    for prefix, uri in PREFIXES.items():
        lines.append(f"@prefix {prefix}: <{uri}> .")
    lines.append("")

    lines.append("# --- 本体：类声明 ---")
    for cls in ONTOLOGY_CLASSES:
        lines.append(f"tender:{cls} a owl:Class .")
    lines.append("")
    lines.append("# --- 本体：属性声明 ---")
    for prop in OBJECT_PROPERTIES:
        lines.append(f"tender:{prop} a owl:ObjectProperty .")
    for prop in DATATYPE_PROPERTIES:
        lines.append(f"tender:{prop} a owl:DatatypeProperty .")
    lines.append("")

    lines.append("# --- 实例：节点 ---")
    for node in graph.nodes:
        node_id = _slug(node.id)
        body = [
            f"tender:{node_id} a tender:{node.rdf_type}",
            f"rdfs:label {_ttl_literal(node.label)}",
        ]
        for key, value in node.properties.items():
            body.append(f"tender:{_slug(key)} {_ttl_literal(value)}")
        for i, statement in enumerate(body):
            terminator = " ;" if i < len(body) - 1 else " ."
            lines.append(f"    {statement}{terminator}")
        lines.append("")

    lines.append("# --- 实例：关系边 ---")
    for edge in graph.edges:
        lines.append(
            f"tender:{_slug(edge.source)} tender:{_slug(edge.predicate)} tender:{_slug(edge.target)} ."
        )
    return "\n".join(lines) + "\n"
