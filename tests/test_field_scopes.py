"""类型字段集：按采购方式 / 业务类型 / 招标组织形式决定填写页字段。

规则全部来自 config/field_scopes.json，测试直接针对该配置断言，
避免「配置改了但代码里的硬编码还在生效」。
"""

from __future__ import annotations

import pytest

from tender_extractor.field_scopes import axis_options, resolve


def test_axis_options_cover_all_three_axes() -> None:
    """三个维度的取值要能直接喂给填写页的下拉框。"""
    options = axis_options()
    assert {item["code"] for item in options["method"]} >= {
        "open", "negotiation", "competitive", "inquiry", "single"
    }
    assert {item["code"] for item in options["category"]} == {"goods", "service", "works"}
    assert {item["code"] for item in options["org_form"]} == {"self", "entrusted"}


def test_self_org_marks_agency_fields_na() -> None:
    """自主招标不设代理机构，整组字段不适用且给出原因。"""
    scope = resolve(method="open", category="service", org_form="self")
    assert "agency" in scope.na
    assert "agency_fee" in scope.na
    assert scope.na["agency"]


def test_low_price_methods_drop_scoring_fields() -> None:
    """询价 / 竞争性谈判 / 单一来源是低价成交，不设评分细则。"""
    for method in ("inquiry", "competitive", "single"):
        scope = resolve(method=method, category="goods", org_form="entrusted")
        assert "tech_score" in scope.na, method
        assert "business_score" in scope.na, method
        assert "price_score" in scope.na, method


def test_negotiation_keeps_scoring_and_adds_last_offer() -> None:
    """竞争性磋商采用综合评分法，且需要「最后报价」这类专属字段。"""
    scope = resolve(method="negotiation", category="service", org_form="entrusted")
    assert "tech_score" not in scope.na
    assert any(f.key == "scope_negotiation_last_offer" for f in scope.extra_fields)


def test_labels_follow_business_category() -> None:
    """同一字段在货物 / 工程下换成对应术语。"""
    goods = resolve(method="open", category="goods", org_form="entrusted")
    works = resolve(method="open", category="works", org_form="entrusted")
    assert goods.labels["contract_period"] == "合同交货期限"
    assert goods.labels["contract_scope"] == "合同供货范围"
    assert works.labels["contract_period"] == "合同工期"
    assert works.labels["contract_scope"] == "合同承包范围"


def test_category_specific_extra_fields() -> None:
    """工程出工程量清单与限价、货物出质保期，服务类不出这两项；通用项各类型都有。"""
    goods = resolve(method="open", category="goods", org_form="entrusted")
    works = resolve(method="open", category="works", org_form="entrusted")
    service = resolve(method="open", category="service", org_form="entrusted")
    assert any(f.key == "scope_goods_warranty" for f in goods.extra_fields)
    assert any(f.key == "scope_works_bill" for f in works.extra_fields)
    assert any(f.key == "scope_works_bid_ceiling" for f in works.extra_fields)
    assert not any(f.key == "scope_works_bill" for f in service.extra_fields)
    # 样本里 19/22 份招标文件都有「是否允许进口产品」，所以它是通用项而非货物专属
    for scope in (goods, works, service):
        assert any(f.key == "scope_import_allowed" for f in scope.extra_fields)
    assert all(f.label and f.field_type for f in goods.extra_fields)


def test_import_allowed_field_carries_options() -> None:
    """「是否允许进口产品」是二选一，选项要随配置下发而不是前端硬编码。"""
    scope = resolve(method="open", category="goods", org_form="entrusted")
    item = next(f for f in scope.extra_fields if f.key == "scope_import_allowed")
    assert item.field_type == "select"
    assert item.options == ["否", "是"]


def test_incomplete_selection_is_explicit() -> None:
    """没选齐三个维度时不套用任何过滤规则，也不假装已生效。"""
    scope = resolve(method="", category="goods", org_form="self")
    assert scope.complete is False
    assert scope.na == {}
    assert scope.labels == {}
    assert scope.extra_fields == []


def test_unknown_axis_value_raises() -> None:
    """未知取值不静默当成公开招标 / 服务类处理。"""
    with pytest.raises(ValueError, match="采购方式"):
        resolve(method="not-a-method", category="goods", org_form="self")


def test_conflicts_follow_existing_validation_rules() -> None:
    """磋商 + 最低价、谈判 + 综合评分要给出与 validation.py 一致的提示。"""
    scope = resolve(
        method="negotiation", category="service", org_form="entrusted",
        price_method="最低价法",
    )
    assert any("综合评分法" in text for text in scope.conflicts)

    scope = resolve(
        method="competitive", category="goods", org_form="entrusted",
        price_method="综合评分法",
    )
    assert any("最低价" in text for text in scope.conflicts)
