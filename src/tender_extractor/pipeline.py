"""编排层：解析 -> 规则 -> 本地 LLM -> 合并 -> 校验 -> 回喂修复 -> 结构化输出。"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .cleaning import normalize_for_match, parse_money, parse_number, parse_percent
from .doc_type import DocTypeResult, classify_document
from .document import read_document
from .llm_extract import (
    LLMStats,
    evidence_supports_value,
    extract_group,
    repair_fields,
    select_context,
)
from .llm_client import LLMClient, LLMError, build_llm_client
from .models import Candidate, Evidence, ExtractionResult, FieldValue
from .rules import extract_rules
from .validation import (
    apply_validation,
    check_grounding,
    normalize_value,
    validate_field,
)

PROMPT_VERSION = "2026-10-27.1"
PIPELINE_VERSION = "0.1.0"
GROUP_ORDER = ["basic", "commercial", "scoring", "contract"]


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _values_equivalent(field_def: dict[str, Any], left: Any, right: Any) -> bool:
    field_type = field_def.get("type", "text")
    if field_type == "money":
        a, b = parse_money(left), parse_money(right)
        return a is not None and b is not None and a == b
    if field_type in {"integer", "score"}:
        a, b = parse_number(left), parse_number(right)
        return a is not None and b is not None and a == b
    if field_type == "ratio":
        a, b = parse_percent(left), parse_percent(right)
        return a is not None and b is not None and abs(a - b) < 0.0005
    a, b = normalize_for_match(left), normalize_for_match(right)
    if not a or not b:
        return False
    if a == b:
        return True
    # 项目名称允许一个包含另一个（“XX项目” vs “XX项目委托服务”）
    return (a in b or b in a) and min(len(a), len(b)) >= 8


class Extractor:
    def __init__(
        self,
        config_dir: str | Path | None = None,
        settings_override: dict[str, Any] | None = None,
    ) -> None:
        self.config_dir = self._resolve_config_dir(config_dir)
        self.fields_config = json.loads((self.config_dir / "fields.json").read_text(encoding="utf-8"))
        base_settings = json.loads((self.config_dir / "settings.json").read_text(encoding="utf-8"))
        self.settings = _deep_merge(base_settings, settings_override or {})
        self.field_defs: dict[str, dict[str, Any]] = {
            field["key"]: field for field in self.fields_config["fields"]
        }
        self.groups: dict[str, dict[str, Any]] = self.fields_config["groups"]

    @staticmethod
    def _resolve_config_dir(config_dir: str | Path | None) -> Path:
        if config_dir:
            return Path(config_dir)
        env = os.environ.get("TENDER_EXTRACTOR_CONFIG")
        if env:
            return Path(env)
        candidates = [
            Path(__file__).resolve().parents[2] / "config",  # src 布局的工程根目录
            Path(__file__).resolve().parent / "config",      # 打包进 wheel 时的默认配置
        ]
        for candidate in candidates:
            if (candidate / "fields.json").exists():
                return candidate
        return candidates[0]

    # ------------------------------------------------------------------
    # 公开入口
    # ------------------------------------------------------------------

    def extract(
        self,
        path: str | Path,
        use_llm: bool = True,
        overrides: dict[str, Any] | None = None,
        allow_non_tender: bool = False,
    ) -> ExtractionResult:
        start = time.perf_counter()
        path = Path(path)
        doc = read_document(path)
        source = self._source_info(path, doc)

        extraction_settings = self.settings.get("extraction", {})
        doc_type = classify_document(
            doc,
            float(extraction_settings.get("document_type_threshold", 4.0)),
        )
        require_tender = bool(extraction_settings.get("require_tender_document", True))
        if require_tender and not allow_non_tender and not doc_type.is_tender:
            return self._skipped_result(source, doc, doc_type, start)

        rules_output = extract_rules(doc, self.field_defs)

        merged: dict[str, Candidate] = dict(rules_output.candidates)
        conflicts: dict[str, dict[str, Any]] = {}
        attempts: dict[str, int] = {}

        llm_settings = self.settings.get("llm", {})
        llm_stats = LLMStats(
            enabled=bool(use_llm and llm_settings.get("enabled", True)),
            provider=str(llm_settings.get("provider", "ollama")),
            model=llm_settings.get("model", ""),
        )

        client: LLMClient | None = None
        if llm_stats.enabled:
            try:
                client = build_llm_client(llm_settings)
            except LLMError as exc:
                llm_stats.error = str(exc)
                client = None
            if client is not None:
                llm_stats.provider = client.provider
                llm_stats.model = client.model
                health = client.health()
                llm_stats.available = bool(health.available and health.model_present)
                llm_stats.error = health.reason if not llm_stats.available else ""
            if llm_stats.available and client is not None:
                llm_candidates = self._run_llm(doc, client, llm_stats, int(llm_settings.get("context_char_budget", 7000)))
                merged, conflicts = self._merge_all(merged, llm_candidates)
            llm_stats.calls = client.calls if client is not None else 0

        validation: dict[str, Any] = {}
        normalizations: list[dict[str, Any]] = []
        repair_audit: list[dict[str, Any]] = []

        if llm_stats.available and client is not None:
            max_rounds = int(llm_settings.get("max_repair_rounds", 2))
            for round_no in range(1, max_rounds + 1):
                field_values = self._build_field_values(merged, attempts, conflicts)
                validation, normalizations = apply_validation(
                    field_values,
                    self.field_defs,
                    rules_output.structured,
                    doc,
                    float(self.settings.get("extraction", {}).get("min_confidence", 0.6)),
                    float(self.settings.get("extraction", {}).get("grounding_fuzzy_threshold", 0.86)),
                )
                targets = self._repair_targets(field_values, round_no)
                if not targets:
                    break
                errors = {
                    key: field_values[key].errors + field_values[key].warnings
                    for key in targets
                    if key in field_values
                }
                current = {key: field_values[key].value for key in targets if key in field_values}
                field_subset = [self.field_defs[key] for key in targets if key in self.field_defs]
                try:
                    repaired = repair_fields(
                        client,
                        doc,
                        field_subset,
                        current,
                        errors,
                        int(llm_settings.get("context_char_budget", 7000)),
                    )
                except LLMError as exc:
                    llm_stats.error = str(exc)
                    repair_audit.append(
                        {"round": round_no, "requested": targets, "error": str(exc), "accepted": [], "rejected": []}
                    )
                    break

                accepted: list[str] = []
                rejected: list[dict[str, Any]] = []
                for key, candidate in repaired.items():
                    field_def = self.field_defs[key]
                    normalized, _ = normalize_value(field_def, candidate.value)
                    errs, _ = validate_field(field_def, normalized)
                    if errs:
                        rejected.append({"field": key, "reason": errs, "value": candidate.value})
                        continue
                    if candidate.evidence:
                        quote = candidate.evidence[0].quote
                        ok, reason = evidence_supports_value(
                            candidate.value,
                            quote,
                            [key, *(field_def.get("aliases") or [])],
                        )
                        if not ok:
                            rejected.append({"field": key, "reason": [reason], "value": candidate.value})
                            continue
                        grounded, _ = check_grounding(
                            normalized,
                            candidate.evidence,
                            doc,
                            float(self.settings.get("extraction", {}).get("grounding_fuzzy_threshold", 0.86)),
                        )
                        if not grounded:
                            rejected.append({"field": key, "reason": ["证据无法回链原文"], "value": candidate.value})
                            continue
                    else:
                        rejected.append({"field": key, "reason": ["模型未提供原文证据"], "value": candidate.value})
                        continue

                    candidate.value = normalized
                    candidate.source = "llm-repair"
                    candidate.note = f"第 {round_no} 轮校验回喂后修正"
                    merged[key] = candidate
                    attempts[key] = attempts.get(key, 0) + 1
                    accepted.append(key)

                repair_audit.append(
                    {
                        "round": round_no,
                        "requested": targets,
                        "accepted": accepted,
                        "rejected": rejected,
                    }
                )
                if not accepted:
                    break

        if llm_stats.available and client is not None:
            llm_stats.calls = client.calls
        field_values = self._build_field_values(merged, attempts, conflicts)
        validation, normalizations = apply_validation(
            field_values,
            self.field_defs,
            rules_output.structured,
            doc,
            float(self.settings.get("extraction", {}).get("min_confidence", 0.6)),
            float(self.settings.get("extraction", {}).get("grounding_fuzzy_threshold", 0.86)),
        )
        for key in conflicts:
            if key in field_values and field_values[key].value not in (None, ""):
                field_values[key].warnings.append(
                    f"规则与模型结果不一致：规则={conflicts[key]['rule']}，模型={conflicts[key]['llm']}"
                )
                if field_values[key].status in {"ok", "low_confidence"}:
                    field_values[key].status = "conflict"

        structured = dict(rules_output.structured)
        structured["project_summary"] = (
            field_values["project_summary"].value if "project_summary" in field_values else ""
        )
        structured["outline"] = doc.outline
        structured["business_requirements"] = rules_output.structured.get("business_requirements", {})

        review = self._build_review(field_values, validation)
        duration_ms = int((time.perf_counter() - start) * 1000)

        result = ExtractionResult(
            source=source,
            meta={
                "pipeline_version": PIPELINE_VERSION,
                "prompt_version": PROMPT_VERSION,
                "field_config_version": self.fields_config.get("version", ""),
                "extracted_at": datetime.now().isoformat(timespec="seconds"),
                "duration_ms": duration_ms,
                "use_llm": llm_stats.enabled,
                "llm_available": llm_stats.available,
                "llm_provider": llm_stats.provider,
                "model": llm_stats.model,
                "blocks": len(doc.blocks),
                "tables": len(doc.tables),
                "field_count": len(field_values),
                "extraction_skipped": False,
                "document_type": doc_type.to_dict(),
                "confidence_summary": self._confidence_summary(field_values),
                "template_coverage": self._template_coverage(field_values),
            },
            fields=field_values,
            structured=structured,
            validation=validation,
            cleaning={
                **doc.cleaning,
                "normalizations": normalizations,
            },
            review=review,
            audit={
                "document_type": doc_type.to_dict(),
                "extraction_skipped": False,
                "llm": llm_stats.to_dict(),
                "repair_rounds": repair_audit,
                "conflicts": conflicts,
                "rule_candidates": len(rules_output.candidates),
                "evidence_blocks": sum(len(f.evidence) for f in field_values.values()),
            },
            document_type=doc_type.to_dict(),
        )
        return result

    # ------------------------------------------------------------------
    # LLM
    # ------------------------------------------------------------------

    def _fields_for_group(self, group: str) -> list[dict[str, Any]]:
        keys = self.groups.get(group, {}).get("fields", [])
        return [self.field_defs[key] for key in keys if key in self.field_defs]

    def _run_llm(
        self,
        doc: Any,
        client: LLMClient,
        stats: LLMStats,
        char_budget: int,
    ) -> dict[str, Candidate]:
        candidates: dict[str, Candidate] = {}
        errors = 0
        for group in GROUP_ORDER:
            fields = self._fields_for_group(group)
            if not fields:
                continue
            context = select_context(doc, group, fields, char_budget)
            if not context:
                continue
            if group == "scoring" and not any(
                ("评分" in block.text or "评审" in block.text or "分值" in block.text)
                for block in doc.blocks
            ):
                continue
            try:
                group_candidates, _ = extract_group(client, doc, group, fields, char_budget)
                candidates.update(group_candidates)
                stats.groups[group] = len(group_candidates)
            except LLMError as exc:
                errors += 1
                stats.error = str(exc)
                stats.groups[group] = -1
                if errors >= 2:
                    break
        return candidates

    def _merge_all(
        self,
        rules: dict[str, Candidate],
        llm: dict[str, Candidate],
    ) -> tuple[dict[str, Candidate], dict[str, dict[str, Any]]]:
        merged: dict[str, Candidate] = {}
        conflicts: dict[str, dict[str, Any]] = {}
        for key in set(rules) | set(llm):
            rule_candidate = rules.get(key)
            llm_candidate = llm.get(key)
            field_def = self.field_defs.get(key, {"key": key, "type": "text"})

            if rule_candidate and llm_candidate:
                if _values_equivalent(field_def, rule_candidate.value, llm_candidate.value):
                    evidence = list(rule_candidate.evidence)
                    for item in llm_candidate.evidence:
                        if item.block_id not in {e.block_id for e in evidence}:
                            evidence.append(item)
                    # 推断值得到模型确认也不能升级成“确定值”，仍保留推断状态。
                    inferred = rule_candidate.source == "inferred"
                    merged[key] = Candidate(
                        value=rule_candidate.value,
                        confidence=min(0.99, max(rule_candidate.confidence, llm_candidate.confidence) + 0.06),
                        source="inferred" if inferred else "rule+llm",
                        evidence=evidence,
                        note=(
                            "规则从上下文推断，模型结果一致；仍需人工确认"
                            if inferred
                            else "规则与模型结果一致"
                        ),
                    )
                else:
                    conflicts[key] = {
                        "rule": rule_candidate.value,
                        "llm": llm_candidate.value,
                        "rule_confidence": round(rule_candidate.confidence, 4),
                        "llm_confidence": round(llm_candidate.confidence, 4),
                    }
                    if rule_candidate.confidence >= llm_candidate.confidence:
                        rule_candidate.note = (
                            rule_candidate.note + f"；模型给出不同值：{llm_candidate.value}"
                        ).strip("；")
                        merged[key] = rule_candidate
                    else:
                        llm_candidate.note = (
                            llm_candidate.note + f"；规则给出不同值：{rule_candidate.value}"
                        ).strip("；")
                        merged[key] = llm_candidate
            elif rule_candidate:
                merged[key] = rule_candidate
            elif llm_candidate:
                merged[key] = llm_candidate
        return merged, conflicts

    # ------------------------------------------------------------------
    # 构建/校验/修复
    # ------------------------------------------------------------------

    def _build_field_values(
        self,
        candidates: dict[str, Candidate],
        attempts: dict[str, int],
        conflicts: dict[str, dict[str, Any]],
    ) -> dict[str, FieldValue]:
        values: dict[str, FieldValue] = {}
        for key, field_def in self.field_defs.items():
            candidate = candidates.get(key)
            if candidate is None:
                values[key] = FieldValue(
                    key=key,
                    label=field_def.get("label", key),
                    template=field_def.get("template"),
                    status="missing",
                )
                continue
            values[key] = FieldValue(
                key=key,
                label=field_def.get("label", key),
                template=field_def.get("template"),
                value=candidate.value,
                confidence=candidate.confidence,
                source=candidate.source,
                evidence=list(candidate.evidence),
                attempts=attempts.get(key, 0),
                notes=candidate.note,
            )
        return values

    def _validate(
        self,
        candidates: dict[str, Candidate],
        doc: Any,
        attempts: dict[str, int],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        field_values = self._build_field_values(candidates, attempts, {})
        return apply_validation(
            field_values,
            self.field_defs,
            {},
            doc,
            float(self.settings.get("extraction", {}).get("min_confidence", 0.6)),
            float(self.settings.get("extraction", {}).get("grounding_fuzzy_threshold", 0.86)),
        )

    def _repair_targets(self, field_values: dict[str, FieldValue], round_no: int) -> list[str]:
        targets: list[str] = []
        for key, field in field_values.items():
            if key == "project_summary":
                continue
            if field.errors:
                targets.append(key)
            elif field.status == "low_confidence" and field.value not in (None, ""):
                targets.append(key)
        # 每轮最多修 8 个，避免小模型在长 prompt 里迷路。
        targets.sort(key=lambda key: (0 if field_values[key].errors else 1, key))
        return targets[:8]

    # ------------------------------------------------------------------
    # 输出
    # ------------------------------------------------------------------

    def _build_review(
        self,
        fields: dict[str, FieldValue],
        validation: dict[str, Any],
    ) -> list[dict[str, Any]]:
        review: list[dict[str, Any]] = []
        for key, field in fields.items():
            if field.status == "invalid":
                review.append(
                    {
                        "priority": "high",
                        "key": key,
                        "label": field.label,
                        "issue": "校验未通过：" + "；".join(field.errors),
                        "value": field.value,
                        "evidence": [e.quote for e in field.evidence][:1],
                    }
                )
            elif field.status == "missing" and self.field_defs.get(key, {}).get("required"):
                review.append(
                    {
                        "priority": "high",
                        "key": key,
                        "label": field.label,
                        "issue": "必填字段缺失",
                        "value": None,
                        "evidence": [],
                    }
                )
            elif field.status == "conflict":
                review.append(
                    {
                        "priority": "medium",
                        "key": key,
                        "label": field.label,
                        "issue": "规则与模型结果不一致，已保留置信度较高者",
                        "value": field.value,
                        "evidence": [e.quote for e in field.evidence][:1],
                    }
                )
            elif field.status == "inferred":
                review.append(
                    {
                        "priority": "medium",
                        "key": key,
                        "label": field.label,
                        "issue": field.notes or "推断值，需人工确认",
                        "value": field.value,
                        "evidence": [e.quote for e in field.evidence][:1],
                    }
                )
            elif field.status == "low_confidence":
                review.append(
                    {
                        "priority": "medium",
                        "key": key,
                        "label": field.label,
                        "issue": "置信度较低：" + "；".join(field.warnings[:2] or [field.notes or "请复核"]),
                        "value": field.value,
                        "evidence": [e.quote for e in field.evidence][:1],
                    }
                )

        missing_optional = [
            f.template.strip()
            for f in fields.values()
            if f.template and f.value in (None, "") and not self.field_defs.get(f.key, {}).get("required")
        ]
        if missing_optional:
            review.append(
                {
                    "priority": "low",
                    "key": "_missing_templates",
                    "label": "未填写的模板占位符",
                    "issue": "以下占位符在需求报告中未找到对应信息，需平台侧补充：" + "、".join(missing_optional[:30]),
                    "value": None,
                    "evidence": [],
                }
            )

        for error in validation.get("errors", []):
            if "field" in error:
                continue
            review.append(
                {
                    "priority": "high",
                    "key": error.get("code", "cross_check"),
                    "label": "跨字段校验",
                    "issue": error.get("message", ""),
                    "value": None,
                    "evidence": [],
                }
            )
        order = {"high": 0, "medium": 1, "low": 2}
        review.sort(key=lambda item: order.get(item["priority"], 9))
        return review

    def _confidence_summary(self, fields: dict[str, FieldValue]) -> dict[str, Any]:
        statuses: dict[str, int] = {}
        confidences = [f.confidence for f in fields.values() if f.value not in (None, "")]
        for field in fields.values():
            statuses[field.status] = statuses.get(field.status, 0) + 1
        return {
            "by_status": statuses,
            "fields_with_value": len(confidences),
            "average_confidence": round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
            "low_confidence_fields": [
                key for key, field in fields.items()
                if field.value not in (None, "") and field.confidence < 0.6
            ],
        }

    def _template_coverage(self, fields: dict[str, FieldValue]) -> dict[str, Any]:
        templates = [f for f in fields.values() if f.template]
        filled = [f for f in templates if f.value not in (None, "")]
        return {
            "total": len(templates),
            "filled": len(filled),
            "rate": round(len(filled) / len(templates), 4) if templates else 0.0,
        }

    @staticmethod
    def _source_info(path: Path, doc: Any) -> dict[str, Any]:
        return {
            "path": str(path),
            "filename": path.name,
            "suffix": path.suffix.lower(),
            "size_bytes": path.stat().st_size if path.exists() else None,
            "sha256": Extractor._sha256(path),
            "title": doc.title,
        }

    def _skipped_result(
        self,
        source: dict[str, Any],
        doc: Any,
        doc_type: DocTypeResult,
        start: float,
    ) -> ExtractionResult:
        """非招标/采购需求文档：不抽取字段，只返回类型判定和提示。"""
        message = doc_type.reason
        return ExtractionResult(
            source=source,
            meta={
                "pipeline_version": PIPELINE_VERSION,
                "prompt_version": PROMPT_VERSION,
                "field_config_version": self.fields_config.get("version", ""),
                "extracted_at": datetime.now().isoformat(timespec="seconds"),
                "duration_ms": int((time.perf_counter() - start) * 1000),
                "use_llm": False,
                "llm_available": False,
                "llm_provider": "",
                "model": "",
                "blocks": len(doc.blocks),
                "tables": len(doc.tables),
                "field_count": 0,
                "extraction_skipped": True,
                "reject_reason": message,
                "document_type": doc_type.to_dict(),
                "confidence_summary": {
                    "by_status": {},
                    "fields_with_value": 0,
                    "average_confidence": 0.0,
                    "low_confidence_fields": [],
                },
                "template_coverage": {"total": 0, "filled": 0, "rate": 0.0},
            },
            fields={},
            structured={},
            validation={
                "ok": False,
                "errors": [],
                "warnings": [{"code": "NOT_TENDER_DOCUMENT", "message": message}],
                "normalizations": [],
            },
            cleaning=dict(doc.cleaning),
            review=[
                {
                    "priority": "high",
                    "key": "document_type",
                    "label": "文档类型",
                    "issue": message,
                    "value": None,
                    "evidence": [],
                }
            ],
            audit={
                "document_type": doc_type.to_dict(),
                "extraction_skipped": True,
                "llm": {},
                "repair_rounds": [],
                "conflicts": {},
                "rule_candidates": 0,
                "evidence_blocks": 0,
            },
            document_type=doc_type.to_dict(),
        )

    @staticmethod
    def _sha256(path: Path) -> str | None:
        if not path.exists() or path.is_dir():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
