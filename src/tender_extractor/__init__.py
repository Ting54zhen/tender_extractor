"""招标需求报告关键信息抽取组件。

典型用法：
    from tender_extractor import extract_document
    result = extract_document("需求报告.docx")
    data = result.to_dict()
    template_values = data["template_values"]  # {占位符名: 值}

设计目标是作为招标平台的一个后端部件：输入需求报告，输出结构化字段，
由平台自己的表单/模板渲染层完成填充。

知识图谱能力（可选）：
    from tender_extractor import analyze_tender_document
    output = analyze_tender_document("需求报告.docx")
    print(output["turtle"])                 # 标准 Turtle，可存 .ttl 用 Protégé 打开
    print(output["analysis"]["risk_checklist"])
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .bid_extract import BidResult, extract_bid_responses
from .bid_writer import BidGenerationResult, generate_bid_document
from .company_profile import CompanyProfile, map_requirement_evidence
from .compliance_matcher import (
    ComplianceResult,
    compare_bid_compliance,
    match_qualifications,
    render_compliance_report,
)
from .graph_builder import GraphData, build_graph_from_result, to_turtle
from .graph_queries import (
    analyze_graph,
    compliance_checklist,
    render_knowledge_report,
    risk_checklist,
    scoring_material_map,
)
from .models import ExtractionResult
from .pipeline import Extractor
from .platform_adapter import (
    PlatformRecognitionError,
    recognize_to_draft,
    to_platform_draft,
)
from .relation_extract import RelationItem, RelationResult, extract_relations

__version__ = "0.2.0"

__all__ = [
    "BidGenerationResult",
    "BidResult",
    "CompanyProfile",
    "ComplianceResult",
    "Extractor",
    "ExtractionResult",
    "GraphData",
    "PlatformRecognitionError",
    "RelationItem",
    "RelationResult",
    "analyze_graph",
    "analyze_tender_document",
    "build_graph_from_result",
    "compare_bid_compliance",
    "compliance_checklist",
    "extract_bid_responses",
    "extract_document",
    "extract_relations",
    "generate_bid_document",
    "map_requirement_evidence",
    "match_qualifications",
    "recognize_to_draft",
    "render_compliance_report",
    "render_knowledge_report",
    "risk_checklist",
    "scoring_material_map",
    "to_platform_draft",
    "to_turtle",
    "__version__",
]


def extract_document(
    path: str | Path,
    config_dir: str | Path | None = None,
    use_llm: bool = True,
    settings_override: dict[str, Any] | None = None,
    allow_non_tender: bool = False,
) -> ExtractionResult:
    """一行式入口，适合平台在请求处理里直接调用。"""
    extractor = Extractor(config_dir=config_dir, settings_override=settings_override)
    return extractor.extract(
        path,
        use_llm=use_llm,
        allow_non_tender=allow_non_tender,
    )


def analyze_tender_document(
    path: str | Path,
    config_dir: str | Path | None = None,
    use_llm: bool = True,
    settings_override: dict[str, Any] | None = None,
    allow_non_tender: bool = False,
) -> dict[str, Any]:
    """完整知识图谱分析：字段抽取 + 实体/关系抽取 + 建图 + 图查询。

    返回 dict，包含：
    - ``result``：字段抽取结果（同 ``extract_document`` 的 ``to_dict()``）；
    - ``relations``：列表型实体（资格/评分点/废标条款）抽取结果；
    - ``graph``：图的节点/边；
    - ``turtle``：标准 Turtle 序列化（可存 .ttl 用 Protégé 打开）；
    - ``analysis``：三类分析（自查表 / 风险清单 / 评分映射）。

    关系抽取失败不影响字段结果：``relations`` 会带 ``meta.error``，
    ``graph`` 退化为只含字段映射出的节点。
    """
    from .document import read_document
    from .llm_client import build_llm_client

    extractor = Extractor(config_dir=config_dir, settings_override=settings_override)
    result = extractor.extract(path, use_llm=use_llm, allow_non_tender=allow_non_tender)
    result_dict = result.to_dict()

    relations: dict[str, Any] = {
        "qualifications": [],
        "scoring_items": [],
        "disqualifications": [],
        "meta": {},
    }
    if use_llm and not result_dict["meta"].get("extraction_skipped"):
        try:
            doc = read_document(Path(path))
            client = build_llm_client(extractor.settings.get("llm", {}))
            relations = extract_relations(client, doc, extractor.settings).to_dict()
        except Exception as exc:  # noqa: BLE001 - 图谱失败不影响字段结果
            relations["meta"] = {"error": str(exc)}

    graph = build_graph_from_result(result_dict, relations)
    return {
        "result": result_dict,
        "relations": relations,
        "graph": graph.to_dict(),
        "turtle": to_turtle(graph),
        "analysis": analyze_graph(graph),
    }
