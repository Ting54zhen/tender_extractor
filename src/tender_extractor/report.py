"""把抽取结果渲染成人工审核用的 Markdown。

报告面向业务审核人员：不仅列出字段值，还要给出证据、冲突、校验错误、
结构化条款、模板回填情况和审计过程，便于逐项核对后再回填平台。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

from .cleaning import parse_decimal
from .models import ExtractionResult

STATUS_CN = {
    "ok": "正常",
    "low_confidence": "低置信",
    "inferred": "推断",
    "missing": "缺失",
    "invalid": "校验失败",
    "conflict": "冲突",
}

SOURCE_CN = {
    "label": "标签",
    "table": "表格",
    "table_label": "表格标签",
    "regex": "正则",
    "computed": "计算",
    "inferred": "推断",
    "section": "章节",
    "llm": "模型",
    "rule+llm": "规则+模型",
    "none": "无",
}

PRIORITY_CN = {"high": "高", "medium": "中", "low": "低"}
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}

CONFIRM_STATUSES = {"inferred", "low_confidence", "invalid", "conflict"}

TABLE_KIND_CN = {
    "kv": "键值表",
    "items": "标的清单",
    "clauses": "技术条款表",
    "unknown": "未识别",
}


def _clip(text: str, limit: int) -> str:
    """超长截断，省略号计入 limit。"""
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _display(value: Any, escape_pipe: bool = False, limit: int = 400) -> str:
    """表格/引用通用的单行展示：压平换行、空值给破折号、超长截断。

    空值兜底必须发生在截断之前，否则 limit=0 时破折号会被截成省略号。
    """
    if value is None:
        return "—"
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    if not text:
        return "—"
    if escape_pipe:
        text = text.replace("|", "\\|")
    return _clip(text, limit)


def _cell(value: Any, limit: int = 400) -> str:
    return _display(value, escape_pipe=True, limit=limit)


def _plain(value: Any) -> str:
    if value is None:
        return "—"
    text = str(value).replace("\r", " ").strip()
    return text or "—"


def _quote(value: Any, limit: int = 400) -> str:
    return _display(value, limit=limit)


def _confidence(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def _status(value: str) -> str:
    return f"{STATUS_CN.get(value, value or '未知')}（{value or '—'}）"


def _source(value: str) -> str:
    return SOURCE_CN.get(value, value or "—")


def _priority(value: str) -> str:
    return PRIORITY_CN.get(value, value or "—")


def _llm_state(meta: dict[str, Any]) -> str:
    if not meta.get("use_llm"):
        return "未启用，--no-llm"
    return "可用" if meta.get("llm_available") else "已启用但不可用"


def _bool_cn(value: Any) -> str:
    return "是" if value else "否"


def _table(headers: Iterable[str], rows: Iterable[Iterable[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("|" + "|".join("---" for _ in headers) + "|")
    for row in rows:
        lines.append("| " + " | ".join(_cell(item) for item in row) + " |")
    return lines


def _evidence_lines(evidence: list[dict[str, Any]]) -> list[str]:
    lines = []
    for item in evidence:
        block_id = item.get("block_id") or "—"
        section = item.get("section") or "—"
        quote = _quote(item.get("quote"))
        lines.append(f"  - `{block_id}`（{section}）：{quote}")
    return lines


def _advice(item: dict[str, Any]) -> str:
    issue = item.get("issue", "")
    if "必填" in issue:
        return "回原文确认后人工补录，不要推测"
    if "推断" in issue:
        return "核对原文，确认或改填"
    if "不一致" in issue:
        return "对照第四节冲突对比，选择有证据的一方"
    if "占位符" in issue:
        return "平台侧补录，或向采购人索取"
    if "缺失" in issue:
        return "人工确认后补录"
    return "人工复核"


def _money_sum(items: list[dict[str, Any]]) -> str | None:
    total = Decimal(0)
    found = False
    for item in items:
        for key in ("total_amount", "amount"):
            value = parse_decimal(item.get(key))
            if value is None:
                continue
            total += value
            found = True
            break
    return f"{total:,.2f}" if found else None


def _render_skipped(data: dict[str, Any]) -> str:
    meta = data.get("meta") or {}
    doc_type = data.get("document_type") or {}
    lines = [
        "# 招标需求解析审核报告",
        "",
        f"- 文件：`{data['source'].get('filename', '')}`",
        f"- 标题：{data['source'].get('title', '')}",
        "",
        f"> **未执行抽取**：{meta.get('reject_reason', '文档类型不符合要求')}",
        "",
        "## 文档类型判定",
        "",
        f"- 是否招标/采购需求文件：否",
        f"- 匹配得分：{doc_type.get('score', 0)} / 阈值 {doc_type.get('threshold', 0)}",
        f"- 置信度：{doc_type.get('confidence', 0)}",
        f"- 判定说明：{doc_type.get('reason', '')}",
    ]
    if doc_type.get("negative"):
        lines.append(f"- 命中非招标特征：{'、'.join(doc_type['negative'][:12])}")
    if doc_type.get("sections"):
        lines.append(f"- 命中章节：{'、'.join(doc_type['sections'][:12])}")
    if doc_type.get("entities"):
        lines.append(f"- 命中实体：{'、'.join(doc_type['entities'][:12])}")
    lines.extend(
        [
            "",
            "如确认该文件仍需抽取，请使用 `--allow-non-tender` 重新运行。",
        ]
    )
    return "\n".join(lines)


def render_markdown(result: ExtractionResult) -> str:
    data = result.to_dict()
    meta = data["meta"]
    if meta.get("extraction_skipped"):
        return _render_skipped(data)

    fields: dict[str, dict[str, Any]] = data.get("fields", {})
    review: list[dict[str, Any]] = data.get("review", [])
    validation: dict[str, Any] = data.get("validation") or {}
    structured: dict[str, Any] = data.get("structured") or {}
    audit: dict[str, Any] = data.get("audit") or {}
    doc_type: dict[str, Any] = data.get("document_type") or {}
    coverage = meta.get("template_coverage") or {}
    summary = meta.get("confidence_summary") or {}
    by_status = summary.get("by_status") or {}
    conflicts = audit.get("conflicts") or {}

    def field_label(key: str | None) -> str:
        if not key:
            return "—"
        return fields.get(key, {}).get("label") or key

    high = sum(1 for item in review if item.get("priority") == "high")
    medium = sum(1 for item in review if item.get("priority") == "medium")
    low = sum(1 for item in review if item.get("priority") == "low")

    lines: list[str] = ["# 招标需求解析审核报告", ""]

    # ---------------------------------------------------------------- 概览
    lines.append("## 一、概览")
    lines.append("")
    overview = [
        ("文件", f"`{_plain(data['source'].get('filename'))}`"),
        ("原文标题", _plain(data["source"].get("title"))),
        ("文件大小", f"{data['source'].get('size_bytes', 0):,} 字节"),
        ("SHA-256", f"`{_plain(data['source'].get('sha256'))}`"),
        ("抽取时间", f"{meta.get('extracted_at', '')}（耗时 {meta.get('duration_ms', 0)} ms）"),
        (
            "模型",
            f"{meta.get('llm_provider', '—')} / {meta.get('model', '—')}"
            f"（{_llm_state(meta)}）",
        ),
        (
            "组件版本",
            f"pipeline {meta.get('pipeline_version', '—')}，"
            f"prompt {meta.get('prompt_version', '—')}，"
            f"字段配置 {meta.get('field_config_version', '—')}",
        ),
        (
            "文档类型",
            f"{'招标/采购需求类' if doc_type.get('is_tender') else '非招标/采购类'}"
            f"（得分 {doc_type.get('score', 0)} ≥ 阈值 {doc_type.get('threshold', 0)}，"
            f"置信度 {doc_type.get('confidence', 0)}）",
        ),
        (
            "模板占位符",
            f"已填 {coverage.get('filled', 0)} / {coverage.get('total', 0)}"
            f"（{coverage.get('rate', 0) * 100:.1f}%）",
        ),
        (
            "字段状态",
            f"有值 {summary.get('fields_with_value', 0)} 个，平均置信度 "
            f"{_confidence(summary.get('average_confidence'))}；"
            f"正常 {by_status.get('ok', 0)}、推断 {by_status.get('inferred', 0)}、"
            f"冲突 {by_status.get('conflict', 0)}、低置信 {by_status.get('low_confidence', 0)}、"
            f"缺失 {by_status.get('missing', 0)}",
        ),
        (
            "自动校验",
            ("通过" if validation.get("ok") else "未通过")
            + f"（错误 {len(validation.get('errors') or [])}，"
            f"警告 {len(validation.get('warnings') or [])}）",
        ),
        ("审核提示", f"高 {high} / 中 {medium} / 低 {low}"),
    ]
    lines.extend(_table(["项目", "内容"], overview))
    lines.append("")

    if high or validation.get("errors"):
        lines.append(
            f"> **结论**：存在 {high} 个高风险复核项、"
            f"{len(validation.get('errors') or [])} 个校验错误，"
            "建议逐项确认后再回填模板。"
        )
    else:
        lines.append("> **结论**：自动校验通过，无高风险复核项；中低风险项仍建议抽查。")
    lines.append("")

    # ------------------------------------------------------------ 审核提示
    lines.append(f"## 二、审核提示（高 {high} / 中 {medium} / 低 {low}）")
    lines.append("")
    if review:
        ordered = sorted(
            enumerate(review), key=lambda pair: (PRIORITY_ORDER.get(pair[1].get("priority"), 9), pair[0])
        )
        rows = []
        for _, item in ordered:
            if item.get("key") == "_missing_templates":
                issue = f"共 {len(data.get('missing_templates') or [])} 个模板占位符在原文未找到对应信息，见第七节。"
            else:
                issue = item.get("issue", "")
            rows.append(
                [
                    _priority(item.get("priority")),
                    _cell(item.get("label"), 30),
                    _cell(issue, 300),
                    _cell(item.get("value"), 300),
                    _advice(item),
                ]
            )
        lines.extend(_table(["优先级", "字段", "问题", "当前值", "处理建议"], rows))
    else:
        lines.append("无。")
    lines.append("")

    # ------------------------------------------------------------ 字段明细
    lines.append("## 三、字段抽取明细")
    lines.append("")
    lines.append(
        "状态说明：正常=有原文证据且通过校验；推断=来自标题/文件名/条款推断；"
        "冲突=规则与模型不一致；低置信=低于置信度阈值；缺失=原文和模型都没有给出。"
    )
    lines.append("")
    filled = [(key, field) for key, field in fields.items() if field.get("value") not in (None, "")]
    normal = [(key, field) for key, field in filled if field.get("status") == "ok"]
    confirm = [(key, field) for key, field in filled if field.get("status") in CONFIRM_STATUSES]
    others = [
        (key, field)
        for key, field in filled
        if field.get("status") not in {"ok"} | CONFIRM_STATUSES
    ]

    def render_field(key: str, field: dict[str, Any]) -> list[str]:
        block = [f"#### {_plain(field.get('label'))}（`{key}`）", ""]
        block.append(f"- 值：{_plain(field.get('value'))}")
        block.append(
            f"- 置信度：{_confidence(field.get('confidence'))}；"
            f"状态：{_status(field.get('status'))}；"
            f"来源：{_source(field.get('source'))}"
        )
        if field.get("template"):
            block.append(f"- 模板占位符：`{field.get('template')}`")
        if field.get("notes"):
            block.append(f"- 备注：{_plain(field.get('notes'))}")
        evidence = field.get("evidence") or []
        if evidence:
            block.append("- 原文证据：")
            block.extend(_evidence_lines(evidence))
        else:
            block.append("- 原文证据：无")
        if field.get("errors"):
            block.append(f"- 错误：{'；'.join(field['errors'])}")
        if field.get("warnings"):
            block.append(f"- 警告：{'；'.join(field['warnings'])}")
        block.append("")
        return block

    lines.append(f"### 正常字段（{len(normal)} 个）")
    lines.append("")
    if normal:
        for key, field in normal:
            lines.extend(render_field(key, field))
    else:
        lines.append("无。")
        lines.append("")

    lines.append(f"### 需人工确认字段（{len(confirm)} 个）")
    lines.append("")
    if confirm:
        for key, field in confirm:
            lines.extend(render_field(key, field))
    else:
        lines.append("无。")
        lines.append("")

    if others:
        lines.append("### 其他状态字段")
        lines.append("")
        for key, field in others:
            lines.extend(render_field(key, field))

    missing = [(key, field) for key, field in fields.items() if field.get("value") in (None, "")]
    lines.append(f"### 未抽到值的字段（{len(missing)} 个）")
    lines.append("")
    if missing:
        lines.extend(
            _table(
                ["字段", "字段键", "模板占位符"],
                [
                    (
                        _cell(field.get("label"), 30),
                        f"`{key}`",
                        f"`{field.get('template')}`" if field.get("template") else "—",
                    )
                    for key, field in missing
                ],
            )
        )
    else:
        lines.append("无。")
    lines.append("")

    # ------------------------------------------------------------ 冲突对比
    lines.append("## 四、规则与模型冲突对比")
    lines.append("")
    if conflicts:
        rows = []
        for key, item in conflicts.items():
            field = fields.get(key, {})
            rows.append(
                [
                    _cell(field.get("label") or key, 24),
                    _cell(item.get("rule"), 300),
                    _cell(item.get("llm"), 300),
                    _cell(field.get("value"), 300),
                    f"{_confidence(item.get('rule_confidence'))} / "
                    f"{_confidence(item.get('llm_confidence'))}",
                ]
            )
        lines.extend(_table(["字段", "规则值", "模型值", "采用值", "规则/模型置信度"], rows))
        lines.append("")
        lines.append("采用值默认取置信度较高者，并保留双方候选供人工核对；不要直接采信模型值。")
    else:
        lines.append("无。")
    lines.append("")

    # ------------------------------------------------------------ 校验
    lines.append("## 五、校验与归一化")
    lines.append("")
    lines.append(
        f"- 总体结果：{'通过' if validation.get('ok') else '未通过'}"
        f"（错误 {len(validation.get('errors') or [])}，警告 {len(validation.get('warnings') or [])}）"
    )
    errors = validation.get("errors") or []
    warnings = validation.get("warnings") or []
    if errors:
        lines.append("")
        lines.extend(
            _table(
                ["代码", "字段", "说明", "当前值"],
                [
                    (
                        _cell(item.get("code"), 30),
                        _cell(field_label(item.get("field")), 30),
                        _cell(item.get("message"), 300),
                        _cell(item.get("value"), 120),
                    )
                    for item in errors
                ],
            )
        )
    if warnings:
        lines.append("")
        lines.extend(
            _table(
                ["代码", "字段", "说明", "当前值"],
                [
                    (
                        _cell(item.get("code"), 30),
                        _cell(field_label(item.get("field")), 30),
                        _cell(item.get("message"), 300),
                        _cell(item.get("value"), 120),
                    )
                    for item in warnings
                ],
            )
        )
    normalizations = validation.get("normalizations") or (data.get("cleaning") or {}).get(
        "normalizations"
    ) or []
    if normalizations:
        lines.append("")
        lines.append("归一化记录：")
        lines.append("")
        lines.extend(
            _table(
                ["字段", "原始值", "归一化后", "说明"],
                [
                    (
                        _cell(item.get("label") or item.get("field"), 30),
                        _cell(item.get("raw"), 120),
                        _cell(item.get("value"), 120),
                        _cell("；".join(item.get("notes") or []), 300),
                    )
                    for item in normalizations
                ],
            )
        )
    lines.append("")

    # ------------------------------------------------------------ 结构化
    lines.append("## 六、结构化信息")
    lines.append("")
    sub_no = 0

    def _sub(title: str) -> str:
        nonlocal sub_no
        sub_no += 1
        return f"### {sub_no}. {title}"


    project_table = structured.get("project_table") or {}
    if project_table:
        lines.append(_sub("项目概况表"))
        lines.append("")
        lines.extend(_table(["项目", "内容"], [(k, v) for k, v in project_table.items()]))
        lines.append("")

    business = structured.get("business_requirements") or {}
    business = {k: v for k, v in business.items() if v not in (None, "")}
    if business:
        lines.append(_sub("主要商务要求"))
        lines.append("")
        lines.extend(_table(["条款", "内容"], [(k, v) for k, v in business.items()]))
        lines.append("")

    items = structured.get("procurement_items") or []
    if items:
        lines.append(_sub("采购标的"))
        lines.append("")
        rows = []
        for item in items:
            rows.append(
                [
                    _cell(item.get("seq"), 8),
                    _cell(item.get("category"), 20),
                    _cell(item.get("name"), 40),
                    _cell(item.get("quantity"), 10),
                    _cell(item.get("unit"), 8),
                    _cell(item.get("unit_price"), 18),
                    _cell(item.get("total_amount") or item.get("amount"), 18),
                    _cell(item.get("requirement"), 160),
                ]
            )
        lines.extend(
            _table(
                ["序号", "品目", "标的名称", "数量", "单位", "单价", "金额", "技术要求"],
                rows,
            )
        )
        lines.append("")
        declared_total = structured.get("procurement_total_declared")
        computed_total = _money_sum(items)
        lines.append(f"- 原文声明的合计金额：{_plain(declared_total)}")
        lines.append(f"- 按明细计算的合计金额：{_plain(computed_total)}")
        lines.append("")

        sub_rows = []
        for item in items:
            for sub in item.get("sub_items") or []:
                sub_rows.append(
                    [
                        _cell(item.get("seq"), 8),
                        _cell(sub.get("seq"), 8),
                        _cell(sub.get("category"), 20),
                        _cell(sub.get("name"), 40),
                        _cell(sub.get("quantity"), 10),
                        _cell(sub.get("unit"), 8),
                        _cell(sub.get("unit_price"), 18),
                        _cell(sub.get("amount"), 18),
                    ]
                )
        if len(sub_rows) > len(items):
            lines.append("子项明细：")
            lines.append("")
            lines.extend(
                _table(
                    ["主项序号", "子项序号", "品目", "标的名称", "数量", "单位", "单价", "金额"],
                    sub_rows,
                )
            )
            lines.append("")

    payment = structured.get("payment") or {}
    if payment.get("text") or payment.get("installments"):
        lines.append(_sub("付款方式"))
        lines.append("")
        if payment.get("text"):
            lines.append("原文条款：")
            lines.append("")
            for line in str(payment["text"]).splitlines():
                if line.strip():
                    lines.append(f"> {line.strip()}")
            lines.append("")
        installments = payment.get("installments") or []
        if installments:
            rows = []
            for item in installments:
                ratio = item.get("ratio")
                rows.append(
                    [
                        _cell(item.get("period"), 8),
                        _cell(item.get("type"), 12),
                        f"{ratio * 100:.2f}%" if ratio is not None else "—",
                        _cell(item.get("amount_text") or item.get("amount"), 18),
                        _cell(item.get("terms"), 200),
                    ]
                )
            lines.extend(_table(["期次", "类型", "比例", "金额", "条款"], rows))
            lines.append("")
        lines.append(f"- 预付款金额：{_plain(payment.get('prepay_amount'))}")
        lines.append(f"- 尾款金额：{_plain(payment.get('balance_amount'))}")
        ratio = payment.get("prepay_ratio")
        lines.append(f"- 预付款比例：{f'{ratio * 100:.2f}%' if ratio is not None else '—'}")
        lines.append(f"- 支付方式：{'、'.join(payment.get('methods') or []) or '—'}")
        lines.append("")

    delivery = structured.get("delivery") or {}
    if delivery.get("time") or delivery.get("place"):
        lines.append(_sub("交付"))
        lines.append("")
        lines.append(f"- 时间：{_plain(delivery.get('time'))}")
        lines.append(f"- 地点：{_plain(delivery.get('place'))}")
        if delivery.get("detail_time"):
            lines.append(f"- 详细时间：{_plain(delivery.get('detail_time'))}")
        if delivery.get("detail_place"):
            lines.append(f"- 详细地址：{_plain(delivery.get('detail_place'))}")
        if delivery.get("block_id"):
            lines.append(f"- 证据块：`{delivery.get('block_id')}`")
        lines.append("")

    stars = structured.get("star_clauses") or []
    if stars:
        lines.append(_sub(f"★/▲ 条款（{len(stars)} 条）"))
        lines.append("")
        lines.extend(
            _table(
                ["类型", "标的序号", "标的", "条款"],
                [
                    (
                        _cell(item.get("type"), 8),
                        _cell(item.get("item_seq"), 8),
                        _cell(item.get("item_name"), 24),
                        _cell(item.get("text"), 300),
                    )
                    for item in stars
                ],
            )
        )
        lines.append("")

    qualifications = structured.get("qualifications")
    if qualifications:
        lines.append(_sub("资格要求"))
        lines.append("")
        for line in str(qualifications).splitlines():
            if line.strip():
                lines.append(f"> {line.strip()}")
        lines.append("")

    after_sales = structured.get("after_sales")
    if after_sales:
        lines.append(_sub("售后服务 / 质保"))
        lines.append("")
        for line in str(after_sales).splitlines():
            if line.strip():
                lines.append(f"> {line.strip()}")
        lines.append("")

    summary_text = structured.get("project_summary")
    if summary_text:
        lines.append(_sub("项目摘要"))
        lines.append("")
        lines.append(_plain(summary_text))
        lines.append("")

    profiles = structured.get("table_profiles") or []
    if profiles:
        lines.append(_sub("表格识别结果"))
        lines.append("")
        lines.extend(
            _table(
                ["证据块", "章节", "判定表型", "置信度", "列角色", "未映射列名"],
                [
                    (
                        _cell(item.get("block_id"), 12),
                        _cell(item.get("section"), 28),
                        _cell(
                            TABLE_KIND_CN.get(str(item.get("kind")), item.get("kind")),
                            12,
                        ),
                        _cell(item.get("confidence"), 8),
                        _cell(
                            "、".join(
                                f"{col.get('index')}:{col.get('role')}"
                                for col in item.get("columns") or []
                            ),
                            60,
                        ),
                        _cell("、".join(item.get("unmapped_headers") or []), 40),
                    )
                    for item in profiles
                ],
            )
        )
        lines.append("")
        unmapped = structured.get("unmapped_tables") or []
        if unmapped:
            blocks = "、".join(str(item.get("block_id") or "") for item in unmapped)
            lines.append(
                f"> 有 {len(unmapped)} 张表未自动判型（{blocks}），"
                "原文内容未进入结构化结果，请人工确认后补录。"
            )
            lines.append("")

    outline = structured.get("outline") or []
    if outline:
        lines.append(_sub("章节结构"))
        lines.append("")
        lines.extend(
            _table(
                ["章节", "起始块", "块数"],
                [
                    (
                        _cell(item.get("section"), 40),
                        _cell(item.get("start_index"), 10),
                        _cell(item.get("blocks"), 10),
                    )
                    for item in outline
                ],
            )
        )
        lines.append("")

    # ------------------------------------------------------------ 模板
    lines.append("## 七、模板回填")
    lines.append("")
    template_values = data.get("template_values") or {}
    lines.append(
        f"- 已回填：{len(template_values)} 个占位符"
        f"（覆盖率 {coverage.get('rate', 0) * 100:.1f}%）"
    )
    lines.append("")
    if template_values:
        rows = []
        for name, value in template_values.items():
            key = next(
                (
                    field_key
                    for field_key, field in fields.items()
                    if (field.get("template") or "").strip() == name
                ),
                "",
            )
            rows.append([f"`{name}`", f"`{key}`" if key else "—", _cell(value, 300)])
        lines.extend(_table(["模板占位符", "字段键", "值"], rows))
        lines.append("")
    missing_templates = data.get("missing_templates") or []
    if missing_templates:
        lines.append(f"未回填：{len(missing_templates)} 个占位符")
        lines.append("")
        lines.append("、".join(f"`{name}`" for name in missing_templates))
        lines.append("")

    # ------------------------------------------------------------ 审计
    lines.append("## 八、审计与过程信息")
    lines.append("")
    llm = audit.get("llm") or {}
    lines.append(f"- 文档类型：{doc_type.get('reason', '—')}")
    if doc_type.get("title_strong") or doc_type.get("title_medium") or doc_type.get("title_weak"):
        lines.append(
            f"- 标题特征：强 {'、'.join(doc_type.get('title_strong') or []) or '—'}；"
            f"中 {'、'.join(doc_type.get('title_medium') or []) or '—'}；"
            f"弱 {'、'.join(doc_type.get('title_weak') or []) or '—'}"
        )
    if doc_type.get("sections"):
        lines.append(f"- 命中章节：{'、'.join(doc_type['sections'])}")
    if doc_type.get("entities"):
        lines.append(f"- 命中实体：{'、'.join(doc_type['entities'])}")
    if doc_type.get("negative"):
        lines.append(f"- 非招标特征：{'、'.join(doc_type['negative'])}")
    lines.append(
        f"- 模型调用：{llm.get('provider', '—')} / {llm.get('model', '—')}，"
        f"启用：{_bool_cn(llm.get('enabled'))}，可用：{_bool_cn(llm.get('available'))}，"
        f"调用 {llm.get('calls', 0)} 次"
    )
    if llm.get("groups"):
        groups = "、".join(f"{name} {count}" for name, count in llm["groups"].items())
        lines.append(f"- 分组结果：{groups}")
    if llm.get("error"):
        lines.append(f"- 模型错误：{llm['error']}")
    lines.append(f"- 规则候选：{audit.get('rule_candidates', 0)} 个；证据块：{audit.get('evidence_blocks', 0)} 个")

    repair_rounds = audit.get("repair_rounds") or []
    if repair_rounds:
        lines.append("")
        lines.append("校验回喂轮次：")
        lines.append("")

        def repair_label(item: Any) -> str:
            if isinstance(item, dict):
                key = item.get("field")
                reason = item.get("reason")
                if isinstance(reason, list):
                    reason = "；".join(str(part) for part in reason)
                label = field_label(key)
                return f"{label}（{reason}）" if reason else label
            return field_label(item)

        def repair_labels(items: list[Any]) -> str:
            return "、".join(repair_label(item) for item in items) or "—"

        lines.extend(
            _table(
                ["轮次", "请求修复", "接受", "丢弃及原因"],
                [
                    (
                        _cell(round_item.get("round"), 8),
                        repair_labels(round_item.get("requested") or []),
                        repair_labels(round_item.get("accepted") or []),
                        repair_labels(round_item.get("rejected") or []),
                    )
                    for round_item in repair_rounds
                ],
            )
        )
        lines.append("")

    review_audit = audit.get("review") or {}
    if review_audit.get("enabled"):
        lines.append("")
        lines.append("第三方模型复核：")
        lines.append("")
        lines.append(
            f"- {review_audit.get('provider', '—')} / {review_audit.get('model', '—')}，"
            f"可用：{_bool_cn(review_audit.get('available'))}，"
            f"复核 {review_audit.get('checked', 0)} 项："
            f"通过 {review_audit.get('supported', 0)}，驳回 {review_audit.get('rejected', 0)}，"
            f"改为 {review_audit.get('replaced', 0)}，未采纳 {review_audit.get('ignored', 0)}"
        )
        if review_audit.get("error"):
            lines.append(f"- 复核错误：{review_audit['error']}")
        decisions = review_audit.get("decisions") or []
        if decisions:
            verdict_cn = {"support": "通过", "reject": "驳回", "replace": "改为"}
            lines.append("")
            lines.extend(
                _table(
                    ["字段", "结论", "原值", "新值", "依据"],
                    [
                        (
                            field_label(item.get("key")),
                            verdict_cn.get(item.get("verdict"), item.get("verdict") or "—"),
                            _cell(item.get("from"), 24),
                            _cell(item.get("to"), 24),
                            _cell(item.get("reason") or "—", 40),
                        )
                        for item in decisions
                    ],
                )
            )
        lines.append("")

    cleaning = data.get("cleaning") or {}
    if cleaning:
        lines.append(
            f"- 清洗统计：段落 {cleaning.get('paragraphs', 0)}（空 {cleaning.get('empty_paragraphs', 0)}），"
            f"表格 {cleaning.get('tables', 0)}（单元格 {cleaning.get('cells', 0)}），"
            f"字符 {cleaning.get('chars_raw', 0)} → {cleaning.get('chars_clean', 0)}，"
            f"占位符 {cleaning.get('placeholders_found', 0)} 个"
        )
    lines.append(
        f"- 解析规模：文档块 {meta.get('blocks', 0)} 个，表格 {meta.get('tables', 0)} 个，"
        f"字段定义 {meta.get('field_count', 0)} 个"
    )
    lines.append(f"- 原文哈希：`{_plain(data['source'].get('sha256'))}`")
    lines.append("")

    # ------------------------------------------------------------ 合规审查
    compliance = data.get("compliance") or {}
    findings = compliance.get("findings") or []
    counts = compliance.get("counts") or {}
    lines.append(
        f"## 九、合规审查（规则层命中 {len(findings)} 条；"
        f"高 {counts.get('high', 0)} / 中 {counts.get('medium', 0)} / 低 {counts.get('low', 0)}）"
    )
    lines.append("")
    if findings:
        lines.append(
            "> 只提示不改写：以下表述可能构成排他性/倾向性条件，需人工确认后修改；"
            "误报可直接忽略，不影响生成。"
        )
        lines.append("")
        rows = [
            [
                _priority(item.get("severity")),
                _cell(item.get("category"), 24),
                _cell(item.get("quote"), 300),
                _cell(item.get("suggestion"), 300),
            ]
            for item in findings
        ]
        lines.extend(_table(["等级", "类别", "原文", "修改建议"], rows))
    else:
        lines.append("未发现规则库覆盖的排他性/倾向性表述。")
    lines.append("")

    # ------------------------------------------------------------ 说明
    lines.append("## 十、审核说明")
    lines.append("")
    lines.append("- 本报告只做信息抽取和校验，不直接改写 Word 模板；平台按 `template_values` 绑定。")
    lines.append("- 高优先级项必须人工确认后才能提交；`inferred` 值来自标题、文件名或条款推断，不能直接当事实。")
    lines.append("- `conflict` 表示规则与模型给出不同值，报告保留双方候选；采用值仅为默认建议。")
    lines.append("- 证据中的 `block_id` 可回链到解析后的文档块，便于定位原文。")
    lines.append("- 金额按字符串保存；预算默认 4 位小数，其他金额默认 2 位；比例为 0–1 小数。")
    lines.append("- 报告内容与 JSON 不一致时，以 JSON 和原文为准。")
    return "\n".join(lines)
