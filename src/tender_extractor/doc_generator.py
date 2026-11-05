"""招标文件生成器：抽取结果 + 标准模板 → 完整招标文件（.docx）。

设计原则：

1. 模板占位符与 ``fields.json`` 的 ``template`` 属性一一对应；
2. 缺失字段不编造，默认以「【待补充：字段名】」显式标记，便于人工补齐
   （也可切换为留空）；
3. 基于 docxtpl（jinja2）渲染，compiled 模板由
   ``scripts/prepare_template.py`` 从 ``templates/`` 源模板生成。

典型用法::

    from tender_extractor import extract_document
    from tender_extractor.doc_generator import generate_tender_document

    result = extract_document("采购需求.docx")
    gen = generate_tender_document(result)
    print(gen.output_path, gen.fill_rate)
"""

from __future__ import annotations

import copy
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import docx
from docxtpl import DocxTemplate
from docx.oxml.ns import qn
from docx.shared import Pt

from .models import ExtractionResult

PLACEHOLDER = re.compile(r'\{\{\s*d\["([^"]+)"\]\s*\}\}')


def _default_template() -> Path:
    """compiled 标准模板路径：兼容源码运行与 PyInstaller 打包。

    - 源码布局：src/tender_extractor/doc_generator.py → <工程根>/templates/compiled；
    - 打包后：__file__ 位于 _MEIPASS/tender_extractor/ → _MEIPASS/templates/compiled。
    """
    here = Path(__file__).resolve()
    root = here.parents[1] if getattr(sys, "frozen", False) else here.parents[2]
    return root / "templates" / "compiled" / "广东省政府采购公开招标标准模板.docx"


DEFAULT_TEMPLATE = _default_template()
MISSING_TEMPLATE = "【待补充：{name}】"

#: 模板中无法直接从抽取字段取值的占位符 → 派生来源字段（按优先级）
DERIVED_KEYS: dict[str, tuple[str, ...]] = {
    "采购需求": ("procurement_item", "procurement_target", "project_summary"),
}

#: 编制规范默认值：需求报告通常不写、但属招标文件必然字段，生成时用规范默认填充，
#: 避免「待补充」噪音。这些不是原文事实，而是政府采购编制惯例，优先级低于抽取值与人工值。
DEFAULT_TEMPLATE_VALUES: dict[str, str] = {
    "有效供应商家数": "3家",
    "中标供应商数量": "1家",
    "报价方式": "总价",
    "是否接受联合体": "不接受",
}


@dataclass
class GenerationResult:
    """一次招标文件生成的结果摘要。"""

    output_path: Path
    template_path: Path
    total: int
    filled: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def fill_rate(self) -> float:
        """占位符填充率（0-1）。"""
        return len(self.filled) / self.total if self.total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_path": str(self.output_path),
            "template_path": str(self.template_path),
            "total": self.total,
            "filled": len(self.filled),
            "fill_rate": round(self.fill_rate, 4),
            "missing": self.missing,
        }


def _stringify(value: Any) -> str:
    """把字段值转成模板可用的字符串。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (list, tuple)):
        return "、".join(_stringify(item) for item in value if _stringify(item))
    text = str(value).strip()
    # 多行值（如多项采购标的）在 Word 模板占位符里无法换行，用「；」衔接
    return re.sub(r"\s*\n+\s*", "；", text)


def _iter_paragraphs(document):
    """遍历正文、表格单元格、页眉页脚中的所有段落。"""
    for para in document.paragraphs:
        yield para
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    yield para
    for section in document.sections:
        for container in (section.header, section.footer):
            for para in container.paragraphs:
                yield para


def template_names(template_path: Path) -> set[str]:
    """从 compiled 模板中提取全部占位符名称。"""
    document = docx.Document(str(template_path))
    names: set[str] = set()
    for para in _iter_paragraphs(document):
        names.update(PLACEHOLDER.findall(para.text))
    return names


def _resolve_output_path(result: ExtractionResult, output_path: Path | None) -> Path:
    if output_path is not None:
        return Path(output_path)
    source_name = str(result.source.get("name") or result.source.get("path") or "")
    stem = Path(source_name).stem if source_name else "招标文件"
    return Path("data") / "结果审核报告" / f"{stem}-招标文件.docx"


def generate_tender_document(
    result: ExtractionResult,
    template_path: Path | str | None = None,
    output_path: Path | str | None = None,
    missing_mode: str = "mark",
    overrides: dict[str, Any] | None = None,
    custom_fields: list[dict[str, Any]] | None = None,
) -> GenerationResult:
    """把抽取结果渲染成完整招标文件。

    Args:
        result: 抽取结果。
        template_path: compiled 模板路径，默认使用内置广东省标准模板。
        output_path: 输出 .docx 路径，默认写入 ``data/结果审核报告/``。
        missing_mode: 缺失字段的呈现方式，``mark``（默认，标记待补充）
            或 ``empty``（留空）。
        overrides: 用户人工确认/修改后的值，键为模板占位符名（如「项目名称」）。
            优先级高于抽取结果，用于「AI 建议 + 人工拍板」工作流。
        custom_fields: 用户自定义字段。``target=placeholder`` 的写入同名
            占位符（优先级低于 overrides）；``target=append`` 的在文末生成
            「补充条款」表格。

    Returns:
        GenerationResult：输出路径、填充清单、缺失清单与填充率。

    Raises:
        FileNotFoundError: 模板文件不存在。
        ValueError: missing_mode 取值非法。
    """
    if missing_mode not in {"mark", "empty"}:
        raise ValueError(f"missing_mode 必须是 mark 或 empty，收到: {missing_mode!r}")

    template = Path(template_path) if template_path is not None else DEFAULT_TEMPLATE
    if not template.exists():
        raise FileNotFoundError(
            f"模板不存在: {template}（请先运行 scripts/prepare_template.py 生成 compiled 模板）"
        )

    names = template_names(template)
    values = {
        str(key).strip(): _stringify(value)
        for key, value in result.template_values().items()
    }
    if overrides:
        for name, value in overrides.items():
            key = str(name).strip()
            if key:
                values[key] = _stringify(value)

    # 自定义字段：绑定占位符的补空缺（不覆盖人工值），追加类在文末补表
    customs = [item for item in (custom_fields or []) if isinstance(item, dict)]
    for item in customs:
        if (item.get("target") or "append") != "placeholder":
            continue
        placeholder = str(item.get("placeholder") or "").strip()
        if placeholder and not values.get(placeholder):
            values[placeholder] = _stringify(item.get("value"))

    for name, source_keys in DERIVED_KEYS.items():
        if name in names and not values.get(name):
            for key in source_keys:
                field_value = result.fields.get(key)
                text = _stringify(field_value.value) if field_value else ""
                if text:
                    values[name] = text
                    break

    # 编制规范默认值兜底（优先级最低，仅在仍为空时生效）
    for name, default in DEFAULT_TEMPLATE_VALUES.items():
        if name in names and not values.get(name):
            values[name] = default

    missing = sorted(name for name in names if not values.get(name))
    context = {
        name: values.get(name)
        or ("" if missing_mode == "empty" else MISSING_TEMPLATE.format(name=name))
        for name in names
    }

    tpl = DocxTemplate(str(template))
    tpl.render({"d": context})

    # 渲染字段占位符后，把 structured 里的表格型数据回填进招标文件的表格/附表
    _fill_document_tables(tpl, result)
    _append_custom_fields(tpl, customs)

    target = _resolve_output_path(result, Path(output_path) if output_path else None)
    target.parent.mkdir(parents=True, exist_ok=True)
    tpl.save(str(target))

    return GenerationResult(
        output_path=target,
        template_path=template,
        total=len(names),
        filled=sorted(name for name in names if values.get(name)),
        missing=missing,
    )


# ---------------------------------------------------------------------------
# 表格型数据回填：字段占位符之外，第二章「采购需求」里的商务要求表、
# 采购标的清单（简表 + 详表）与每项标的技术参数附表，都来自 structured，
# 需要在此用 python-docx 直接填表，才能让生成的招标文件接近真实成稿。
# ---------------------------------------------------------------------------

_CN_NUM = "一二三四五六七八九十"


def _field_text(result: ExtractionResult, key: str) -> str:
    """从字段结果里取字符串值，空值归一为 ``""``。"""
    fv = result.fields.get(key)
    if fv is not None and fv.value not in (None, ""):
        return str(fv.value)
    return ""


def _fmt_qty(value: Any) -> str:
    """数量格式化：5.0 → ``5``，避免出现多余小数。"""
    try:
        number = float(value)
        return str(int(number)) if number == int(number) else str(number)
    except (TypeError, ValueError):
        return str(value or "")


def _write_row_cells(row, values: list[str]) -> None:
    """把一串值依次写入一行的单元格。"""
    for cell, val in zip(row.cells, values):
        cell.text = str(val or "")


def _set_paragraph_text(p, text: str) -> None:
    """改写段落文本，保留首个 run 的格式（用于复制后的附表标题）。"""
    from docx.oxml import OxmlElement

    runs = p.findall(qn("w:r"))
    if not runs:
        return
    first = runs[0]
    for t in first.findall(qn("w:t")):
        first.remove(t)
    t = OxmlElement("w:t")
    t.text = text
    t.set(qn("xml:space"), "preserve")
    first.append(t)
    for extra in runs[1:]:
        p.remove(extra)


def _star_clauses_for(star_clauses: list[dict[str, Any]], item: dict[str, Any]) -> list[dict[str, Any]]:
    """筛出属于某一项标的的 ★/▲ 条款（按 item_seq 或 item_name 匹配）。"""
    seq = str(item.get("seq") or "")
    name = str(item.get("name") or "")
    matched = [
        c for c in star_clauses
        if str(c.get("item_seq") or "") == seq
        or (name and str(c.get("item_name") or "") == name)
    ]
    return matched


def _fill_business_table(table, business: dict[str, Any]) -> None:
    """主要商务要求表：第一列是键，把 structured.business_requirements 填到第二列。

    该表没有表头行，6 行都是「键 | 值」，需逐行按第一列取键。
    """
    for row in table.rows:
        key = (row.cells[0].text or "").strip()
        if not key:
            continue
        row.cells[1].text = str(business.get(key) or "")


def _fill_item_brief_table(table, items: list[dict[str, Any]]) -> None:
    """品目简表（品目号/品目名称/采购标的/数量/技术规格/是否进口）。"""
    data_row = table.rows[1]
    for idx, item in enumerate(items):
        name = str(item.get("name") or "")
        unit = str(item.get("unit") or "")
        qty = _fmt_qty(item.get("quantity"))
        values = [
            f"1-{idx + 1}",
            str(item.get("category") or ""),
            name,
            f"{qty}({unit})" if unit else qty,
            "详见第二章",
            "否",
        ]
        if idx == 0:
            _write_row_cells(data_row, values)
        else:
            _write_row_cells(table.add_row(), values)


def _fill_item_detail_table(
    table, items: list[dict[str, Any]], industry: str
) -> None:
    """标的清单详表（序号/品目/标的/单位/数量/单价/总价/所属行业/技术要求）。"""
    data_row = table.rows[1]
    for idx, item in enumerate(items):
        values = [
            str(item.get("seq") or idx + 1),
            str(item.get("category") or ""),
            str(item.get("name") or ""),
            str(item.get("unit") or ""),
            _fmt_qty(item.get("quantity")),
            str(item.get("unit_price") or ""),
            str(item.get("amount") or item.get("total_amount") or ""),
            industry,
            f"详见附表{_CN_NUM[idx] if idx < len(_CN_NUM) else idx + 1}",
        ]
        if idx == 0:
            _write_row_cells(data_row, values)
        else:
            _write_row_cells(table.add_row(), values)


def _fill_tech_table(
    table, clauses: list[dict[str, Any]], fallback: str
) -> None:
    """单个技术参数附表：★/▲ 条款逐行写入，无条款时回退为整体要求。"""
    data_row = table.rows[1]
    note_row = table.rows[-1]  # 说明行保持在最后
    if clauses:
        rows_data = [
            (str(c.get("type") or ""), str(i), str(c.get("text") or ""))
            for i, c in enumerate(clauses, 1)
        ]
    else:
        rows_data = [("", "1", str(fallback or ""))]

    _write_row_cells(data_row, list(rows_data[0]))
    for ptype, seq, text in rows_data[1:]:
        row = table.add_row()
        _write_row_cells(row, [ptype, seq, text])
        note_row._tr.addprevious(row._tr)


def _append_custom_fields(document, items: list[dict[str, Any]]) -> None:
    """把「追加到文末」的自定义字段写成补充条款表。

    为什么不插到正文指定位置：占位符只能落在模板已有的位置，任意插段需要
    段落级定位与格式复制，收益远低于成本；文末补充条款是招标文件的常规
    做法，位置稳定、可预期，也不会打乱已有表格的填充索引。
    """
    rows = [
        (str(item.get("label") or "").strip(), str(item.get("value") or "").strip())
        for item in items
        if (item.get("target") or "append") == "append"
    ]
    rows = [pair for pair in rows if pair[0] and pair[1]]
    if not rows:
        return

    document.add_page_break()
    heading = document.add_paragraph("补充条款")
    if heading.runs:
        heading.runs[0].bold = True
        heading.runs[0].font.size = Pt(14)

    table = document.add_table(rows=1, cols=2)
    try:
        table.style = "Table Grid"
    except KeyError:
        pass  # 模板缺该样式时退化为无边框表，内容仍然可读
    header = table.rows[0].cells
    header[0].text = "字段"
    header[1].text = "内容"
    for label, value in rows:
        cells = table.add_row().cells
        cells[0].text = label
        cells[1].text = value


def _fill_document_tables(document, result: ExtractionResult) -> None:
    """把 structured 数据回填进招标文件的表格与多附表。"""
    structured = result.structured or {}
    business = structured.get("business_requirements") or {}
    items = structured.get("procurement_items") or []
    star_clauses = structured.get("star_clauses") or []
    industry = _field_text(result, "industry")

    for table in document.tables:
        if not table.rows:
            continue
        head = [(c.text or "").strip() for c in table.rows[0].cells]
        if head and head[0] == "标的提供的时间":
            _fill_business_table(table, business)
        elif any("是否允许进口产品" in h for h in head):
            _fill_item_brief_table(table, items)
        elif any("分项预算单价" in h for h in head):
            _fill_item_detail_table(table, items, industry)
        elif head and "参数性质" in head[0]:
            _fill_tech_tables(document, table, items, star_clauses)


def _fill_tech_tables(
    document,
    tech_table,
    items: list[dict[str, Any]],
    star_clauses: list[dict[str, Any]],
) -> None:
    """为每项标的生成一张技术参数附表：复制标题段落与参数表，逐项填充。"""
    title_p = None
    for p in document.paragraphs:
        if (p.text or "").strip().startswith("附表"):
            title_p = p
            break
    if title_p is None or not items:
        return

    from docx.table import Table

    prev_tbl = tech_table._tbl
    for idx, item in enumerate(items):
        name = str(item.get("name") or "")
        clauses = _star_clauses_for(star_clauses, item)
        label = f"附表{_CN_NUM[idx] if idx < len(_CN_NUM) else idx + 1}：{name}"
        if idx == 0:
            _set_paragraph_text(title_p._p, label)
            _fill_tech_table(tech_table, clauses, str(item.get("requirement") or ""))
        else:
            new_p = copy.deepcopy(title_p._p)
            new_tbl = copy.deepcopy(tech_table._tbl)
            _set_paragraph_text(new_p, label)
            _fill_tech_table(Table(new_tbl, document), clauses, str(item.get("requirement") or ""))
            prev_tbl.addnext(new_p)
            new_p.addnext(new_tbl)
            prev_tbl = new_tbl
