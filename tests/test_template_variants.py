"""派生模板（货物类 / 竞争性磋商）的结构与生成回归。

为什么单独测：这两套模板由 scripts/derive_template.py 生成，条款替换是
按词表批量做的，最容易悄悄坏掉的是两件事——占位符被术语替换改坏（导致
字段填不上），以及采购方式切换不彻底（磋商版里冒出「投标人」）。
"""

from __future__ import annotations

import re
from pathlib import Path

import docx
import pytest

from tender_extractor.doc_generator import generate_tender_document, template_names
from tender_extractor.models import ExtractionResult, FieldValue

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPILED_DIR = PROJECT_ROOT / "templates" / "compiled"

BASE = "广东省政府采购公开招标标准模板.docx"
GOODS = "广东省政府采购公开招标标准模板（货物类）.docx"
NEGOTIATION = "广东省政府采购竞争性磋商标准模板（服务类）.docx"
VARIANTS = [BASE, GOODS, NEGOTIATION]

requires_variants = pytest.mark.skipif(
    not all((COMPILED_DIR / name).exists() for name in VARIANTS),
    reason="compiled 模板不存在，请先运行 scripts/prepare_template.py",
)


def _make_result(**templates: str) -> ExtractionResult:
    fields = {
        name: FieldValue(key=name, label=name, template=name, value=value)
        for name, value in templates.items()
    }
    return ExtractionResult(
        source={"name": "测试需求.docx"},
        meta={},
        fields=fields,
        structured={},
        validation={},
        cleaning={},
        review=[],
        audit={},
    )


def _document_text(path: Path) -> str:
    document = docx.Document(str(path))
    parts = [para.text for para in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.extend(para.text for para in cell.paragraphs)
    return "\n".join(parts)


@requires_variants
def test_variants_share_placeholder_set() -> None:
    """三套模板的占位符集合必须一致，否则字段映射会在变体上静默失效。"""
    base = template_names(COMPILED_DIR / BASE)
    assert base, "标准模板未解析出占位符，compiled 可能没生成"
    for name in (GOODS, NEGOTIATION):
        assert template_names(COMPILED_DIR / name) == base


@requires_variants
def test_placeholder_names_have_no_padding() -> None:
    """占位符名不能带前后空格：带空格的键永远匹配不上 fields.json。"""
    for name in VARIANTS:
        for item in template_names(COMPILED_DIR / name):
            assert item == item.strip(), f"{name} 的占位符「{item}」带多余空格"


@requires_variants
def test_negotiation_switches_terms() -> None:
    """磋商版不应残留招标术语（占位符内的「中标」是字段名，允许保留）。"""
    text = _document_text(COMPILED_DIR / NEGOTIATION)
    text = re.sub(r'\{\{ d\["[^"]+"\] \}\}', "", text)  # 排除占位符文本
    for word in ("投标人", "投标文件", "招标文件", "开标", "评标", "招标公告", "中标通知书"):
        assert word not in text, f"磋商版仍残留「{word}」"
    assert "磋商" in text and "响应文件" in text


@requires_variants
def test_goods_keeps_bidding_terms() -> None:
    """货物版只是改了品目口径，采购方式仍是公开招标，术语不能被误改。"""
    text = _document_text(COMPILED_DIR / GOODS)
    assert "公开招标文件（货物类）" in text
    assert "投标人" in text and "招标文件" in text
    assert "全部货物应由中小微企业" in text
    assert "3.1 核心产品" in text


@requires_variants
@pytest.mark.parametrize("name", VARIANTS)
def test_generate_with_variant(tmp_path: Path, name: str) -> None:
    """三套模板都能渲染成稿：值填得进去，且没有 jinja 泄漏。"""
    target = tmp_path / f"成稿-{name}"
    generated = generate_tender_document(
        _make_result(项目名称="某单位安保服务采购项目", 采购人="某采购人"),
        template_path=COMPILED_DIR / name,
        output_path=target,
    )

    assert generated.output_path.exists()
    text = _document_text(generated.output_path)
    assert "某单位安保服务采购项目" in text
    assert "{{" not in text and "}}" not in text
    assert 'd["' not in text
    assert "【待补充：项目名称】" not in text
