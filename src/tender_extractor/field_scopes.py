"""填写工作台的类型字段集：按采购方式 / 业务类型 / 招标组织形式解析规则。

为什么单独一层而不是改 ``fields.json``：抽取层必须继续按字段契约抽全量字段，
否则换一种业务类型就会丢掉原文证据；这里只决定「填写页展示什么」，是视图规则。
规则放在 ``config/field_scopes.json``，业务口径调整不需要改代码，也不用重新抽取。

模块只做纯规则计算：不读网络、不读数据库、不碰模型，便于在 ``--no-llm``
和离线测试里直接调用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(__file__).resolve().parent / "config" / "field_scopes.json"

_AXIS_NAMES: dict[str, str] = {
    "method": "采购方式",
    "category": "业务类型",
    "org_form": "招标组织形式",
}


@dataclass(frozen=True)
class ScopeField:
    """某一类型下才需要人工填写的字段。

    这类字段没有对应的模板占位符，走自定义字段的「追加到补充条款」通道，
    因此不能冒充抽取结果，也没有证据引文。
    """

    key: str
    label: str
    field_type: str = "text"
    hint: str = ""
    options: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FieldScope:
    """一组类型选择解析出来的字段视图规则。"""

    method: str = ""
    category: str = ""
    org_form: str = ""
    complete: bool = False
    na: dict[str, str] = field(default_factory=dict)
    na_tags: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    extra_fields: list[ScopeField] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """转成接口可直接 JSON 序列化的形状。"""
        return {
            "method": self.method,
            "category": self.category,
            "org_form": self.org_form,
            "complete": self.complete,
            "na": dict(self.na),
            "na_tags": dict(self.na_tags),
            "labels": dict(self.labels),
            "extra_fields": [
                {
                    "key": item.key,
                    "label": item.label,
                    "field_type": item.field_type,
                    "hint": item.hint,
                    "options": list(item.options),
                }
                for item in self.extra_fields
            ],
            "conflicts": list(self.conflicts),
        }


def load_config(path: Path | None = None) -> dict[str, Any]:
    """读取类型规则；调用方每次重新读，配置改完刷新页面即可生效。"""
    return json.loads((path or CONFIG_PATH).read_text(encoding="utf-8"))


def axis_options(config: dict[str, Any] | None = None) -> dict[str, list[dict[str, str]]]:
    """三个维度的下拉选项，避免前端再硬编码一份会漂移的枚举。"""
    cfg = config if config is not None else load_config()
    return {
        axis: [{"code": code, "label": str(label)} for code, label in values.items()]
        for axis, values in (cfg.get("axes") or {}).items()
    }


def _rule_matches(rule: dict[str, Any], values: dict[str, str]) -> bool:
    when: dict[str, Any] = rule.get("when") or {}
    return all(values.get(axis, "") in allowed for axis, allowed in when.items())


def _conflict_matches(
    rule: dict[str, Any],
    values: dict[str, str],
    price_method: str,
    agency: str,
) -> bool:
    if "method" in rule and values["method"] not in rule["method"]:
        return False
    if "org_form" in rule and values["org_form"] not in rule["org_form"]:
        return False
    needle = rule.get("price_method_contains")
    if needle and needle not in price_method:
        return False
    if rule.get("agency_present") and not agency.strip():
        return False
    return True


def resolve(
    method: str = "",
    category: str = "",
    org_form: str = "",
    *,
    price_method: str = "",
    agency: str = "",
    config: dict[str, Any] | None = None,
) -> FieldScope:
    """把三个维度的选择解析成不适用字段、标签覆写、专属字段与冲突提示。

    允许留空（用户还没选完），此时 ``complete=False`` 且不带任何过滤规则，
    调用方据此要求用户先选类型；未知取值直接报错，不静默当成默认类型。
    """
    cfg = config if config is not None else load_config()
    axes: dict[str, Any] = cfg.get("axes") or {}
    values = {"method": method, "category": category, "org_form": org_form}
    for axis, value in values.items():
        if value and value not in (axes.get(axis) or {}):
            raise ValueError(f"未知的{_AXIS_NAMES.get(axis, axis)}：{value}")

    if not all(values.values()):
        return FieldScope(method=method, category=category, org_form=org_form)

    na: dict[str, str] = {}
    na_tags: dict[str, str] = {}
    for rule in cfg.get("na_rules") or []:
        if not _rule_matches(rule, values):
            continue
        for key in rule.get("fields") or []:
            # 先命中的规则保留原因：更具体的规则排在后面时也不覆盖业务解释
            na.setdefault(key, str(rule.get("reason") or "该类型下不适用"))
            na_tags.setdefault(key, str(rule.get("tag") or "不适用"))

    overrides: dict[str, Any] = cfg.get("label_overrides") or {}
    labels = {
        key: str(label)
        for key, label in (
            (overrides.get("category") or {}).get(values["category"]) or {}
        ).items()
    }

    extra_cfg: dict[str, Any] = cfg.get("extra_fields") or {}
    raw_extra: list[dict[str, Any]] = []
    # by_all 是各类型都出现过的栏目（样本里 19/22 份都有「是否允许进口产品」），
    # 放在最前，让填写页先看到通用项，再看到类型专属项
    raw_extra.extend(extra_cfg.get("by_all") or [])
    raw_extra.extend((extra_cfg.get("by_category") or {}).get(values["category"]) or [])
    raw_extra.extend((extra_cfg.get("by_method") or {}).get(values["method"]) or [])
    extra_fields = [
        ScopeField(
            key=str(item.get("key") or ""),
            label=str(item.get("label") or ""),
            field_type=str(item.get("field_type") or "text"),
            hint=str(item.get("hint") or ""),
            options=[str(option) for option in item.get("options") or []],
        )
        for item in raw_extra
        if item.get("key") and item.get("label")
    ]

    conflicts = [
        str(rule.get("message") or "")
        for rule in cfg.get("conflict_rules") or []
        if _conflict_matches(rule, values, price_method, agency)
    ]

    return FieldScope(
        method=method,
        category=category,
        org_form=org_form,
        complete=True,
        na=na,
        na_tags=na_tags,
        labels=labels,
        extra_fields=extra_fields,
        conflicts=[text for text in conflicts if text],
    )
