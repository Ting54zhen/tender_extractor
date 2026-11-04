"""数据模型：文档块、候选值、字段结果、抽取结果。

所有跨模块传递的数据都用这里的 dataclass，避免 dict 到处飞导致字段名漂移。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _clean_json_value(value: Any) -> Any:
    """把 Decimal 等不可 JSON 序列化的值转成基础类型。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_clean_json_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _clean_json_value(v) for k, v in value.items()}
    return str(value)


@dataclass
class Evidence:
    """原文证据。block_id 用于回链到解析后的文档块。"""

    block_id: str
    quote: str
    section: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"block_id": self.block_id, "quote": self.quote, "section": self.section}


@dataclass
class Block:
    """文档中的一段正文或一张表，保持原始顺序。"""

    id: str
    kind: str                      # paragraph | table
    text: str                      # 清洗后的文本；表格为按行拼接
    raw: str                       # 清洗前文本
    index: int
    style: str = ""
    section: str = ""              # 最近的一级章节标题
    section_path: list[str] = field(default_factory=list)
    table_index: int | None = None
    rows: list[list[str]] | None = None

    @property
    def is_table(self) -> bool:
        return self.kind == "table"

    def to_dict(self, include_raw: bool = False) -> dict[str, Any]:
        data = {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "index": self.index,
            "style": self.style,
            "section": self.section,
            "section_path": self.section_path,
            "table_index": self.table_index,
        }
        if include_raw:
            data["raw"] = self.raw
        if self.rows is not None:
            data["rows"] = self.rows
        return data


@dataclass
class Document:
    path: str
    title: str
    blocks: list[Block]
    metadata: dict[str, Any] = field(default_factory=dict)
    cleaning: dict[str, Any] = field(default_factory=dict)
    outline: list[dict[str, Any]] = field(default_factory=list)

    @property
    def tables(self) -> list[Block]:
        return [b for b in self.blocks if b.is_table]

    def block_by_id(self, block_id: str) -> Block | None:
        for block in self.blocks:
            if block.id == block_id:
                return block
        return None

    def full_text(self) -> str:
        return "\n".join(b.text for b in self.blocks if b.text)


@dataclass
class Candidate:
    """抽取过程中产生的候选值。规则和 LLM 各自产出 Candidate，再合并。"""

    value: Any
    confidence: float
    source: str                    # label/table/computed/inferred/llm/rule+llm
    evidence: list[Evidence] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": _clean_json_value(self.value),
            "confidence": round(float(self.confidence), 4),
            "source": self.source,
            "evidence": [e.to_dict() for e in self.evidence],
            "note": self.note,
        }


@dataclass
class FieldValue:
    key: str
    label: str
    template: str | None
    value: Any = None
    raw_value: Any = None
    confidence: float = 0.0
    status: str = "missing"        # ok/low_confidence/inferred/missing/invalid/conflict
    source: str = "none"
    evidence: list[Evidence] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    attempts: int = 0
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "template": self.template,
            "value": _clean_json_value(self.value),
            "raw_value": _clean_json_value(self.raw_value),
            "confidence": round(float(self.confidence), 4),
            "status": self.status,
            "source": self.source,
            "evidence": [e.to_dict() for e in self.evidence],
            "errors": self.errors,
            "warnings": self.warnings,
            "attempts": self.attempts,
            "notes": self.notes,
        }


@dataclass
class ExtractionResult:
    source: dict[str, Any]
    meta: dict[str, Any]
    fields: dict[str, FieldValue]
    structured: dict[str, Any]
    validation: dict[str, Any]
    cleaning: dict[str, Any]
    review: list[dict[str, Any]]
    audit: dict[str, Any]
    document_type: dict[str, Any] = field(default_factory=dict)

    def template_values(self) -> dict[str, Any]:
        """只返回模板里真正有值的占位符，方便平台直接绑定。"""
        values: dict[str, Any] = {}
        for fv in self.fields.values():
            if not fv.template or fv.value in (None, ""):
                continue
            name = fv.template.strip()
            values[name] = _clean_json_value(fv.value)
        return values

    def missing_templates(self) -> list[str]:
        return [
            (fv.template or "").strip()
            for fv in self.fields.values()
            if fv.template and fv.value in (None, "")
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "meta": self.meta,
            "document_type": _clean_json_value(self.document_type),
            "template_values": self.template_values(),
            "missing_templates": self.missing_templates(),
            "fields": {k: v.to_dict() for k, v in self.fields.items()},
            "structured": _clean_json_value(self.structured),
            "validation": _clean_json_value(self.validation),
            "cleaning": _clean_json_value(self.cleaning),
            "review": _clean_json_value(self.review),
            "audit": _clean_json_value(self.audit),
        }


def extraction_result_from_dict(data: dict[str, Any]) -> "ExtractionResult":
    """从 to_dict() 输出重建 ExtractionResult（持久化恢复、跨进程传输用）。

    覆盖 doc_generator 依赖的 source / template_values() / fields 全部数据面；
    blocks 级原文不重建（不影响模板回填与字段展示）。
    """
    fields: dict[str, FieldValue] = {}
    for key, item in (data.get("fields") or {}).items():
        evidence = [
            Evidence(
                block_id=str(ev.get("block_id", "")),
                quote=str(ev.get("quote", "")),
                section=str(ev.get("section", "")),
            )
            for ev in (item.get("evidence") or [])
        ]
        fields[key] = FieldValue(
            key=str(item.get("key", key)),
            label=str(item.get("label", key)),
            template=item.get("template"),
            value=item.get("value"),
            raw_value=item.get("raw_value"),
            confidence=float(item.get("confidence") or 0.0),
            status=str(item.get("status", "missing")),
            source=str(item.get("source", "none")),
            evidence=evidence,
            errors=list(item.get("errors") or []),
            warnings=list(item.get("warnings") or []),
            attempts=int(item.get("attempts") or 0),
            notes=str(item.get("notes") or ""),
        )
    return ExtractionResult(
        source=dict(data.get("source") or {}),
        meta=dict(data.get("meta") or {}),
        fields=fields,
        structured=dict(data.get("structured") or {}),
        validation=dict(data.get("validation") or {}),
        cleaning=dict(data.get("cleaning") or {}),
        review=list(data.get("review") or []),
        audit=dict(data.get("audit") or {}),
        document_type=dict(data.get("document_type") or {}),
    )
