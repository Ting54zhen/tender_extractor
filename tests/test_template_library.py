"""模板库：上传体检、默认模板、增删改的限制。

全部用现场构造的最小 docx，不依赖 templates/ 下的真实模板，避免测试
因为模板被重新生成而漂移。
"""

from __future__ import annotations

import io
from pathlib import Path

import docx
import pytest

from tender_extractor.template_library import BUILTIN_DIR, TemplateLibrary


def _docx_bytes(*paragraphs: str) -> bytes:
    document = docx.Document()
    for text in paragraphs:
        document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@pytest.fixture()
def library(tmp_path: Path) -> TemplateLibrary:
    return TemplateLibrary(store_dir=tmp_path / "templates", builtin_dir=tmp_path / "empty")


def test_create_parses_placeholders(library: TemplateLibrary) -> None:
    info = library.create(
        "某省服务类模板.docx",
        _docx_bytes("项目名称：{项目名称}", "采购人：{采购人}", "邮箱：{采购人邮箱}"),
        category="service",
        method="open",
    )

    assert info.id.startswith("tpl-")
    assert info.name == "某省服务类模板"
    assert info.builtin is False
    assert "项目名称" in info.placeholders
    assert info.unmapped == ["采购人邮箱"]
    assert "项目名称" not in info.missing_fields
    assert library.get(info.id) is not None


def test_create_rejects_non_docx(library: TemplateLibrary) -> None:
    with pytest.raises(ValueError, match="只支持 .docx"):
        library.create("模板.doc", b"whatever")


def test_create_rejects_empty_body(library: TemplateLibrary) -> None:
    with pytest.raises(ValueError, match="内容为空"):
        library.create("模板.docx", b"")


def test_create_rejects_broken_docx(library: TemplateLibrary) -> None:
    with pytest.raises(ValueError, match="无法解析"):
        library.create("模板.docx", b"not a real docx")


def test_create_rejects_template_without_placeholder(library: TemplateLibrary) -> None:
    with pytest.raises(ValueError, match="没有找到"):
        library.create("模板.docx", _docx_bytes("这是一份没有占位符的文档"))


def test_set_default_and_delete(library: TemplateLibrary) -> None:
    first = library.create("A.docx", _docx_bytes("{项目名称}"))
    second = library.create("B.docx", _docx_bytes("{项目名称}"))

    assert library.set_default(second.id).is_default
    assert {info.id for info in library.list()} == {first.id, second.id}
    assert library.get(second.id).is_default is True
    assert library.get(first.id).is_default is False
    assert library.effective_default() == second.id

    library.delete(second.id)
    assert library.get(second.id) is None
    assert library.effective_default() != second.id
    assert first.id  # 另一套模板不受影响


def test_rename(library: TemplateLibrary) -> None:
    info = library.create("旧名字.docx", _docx_bytes("{项目名称}"))
    renamed = library.rename(info.id, "新名字")
    assert renamed.name == "新名字"
    assert library.get(info.id).name == "新名字"


def test_rename_rejects_empty(library: TemplateLibrary) -> None:
    info = library.create("模板.docx", _docx_bytes("{项目名称}"))
    with pytest.raises(ValueError, match="不能为空"):
        library.rename(info.id, "   ")


def test_delete_missing_raises(library: TemplateLibrary) -> None:
    with pytest.raises(KeyError):
        library.delete("tpl-not-exist")


@pytest.mark.skipif(not BUILTIN_DIR.is_dir(), reason="内置 compiled 模板不存在")
def test_builtin_templates_are_read_only(tmp_path: Path) -> None:
    library = TemplateLibrary(store_dir=tmp_path / "templates", builtin_dir=BUILTIN_DIR)
    builtins = [info for info in library.list() if info.builtin]

    assert builtins, "内置模板应当出现在列表里"
    assert library.effective_default() in {info.id for info in builtins}
    # 「采购需求」由生成器从采购品目/标的/概况派生，不应算作未匹配占位符
    assert all("采购需求" not in info.unmapped for info in builtins)
    with pytest.raises(ValueError, match="不能删除"):
        library.delete(builtins[0].id)
    with pytest.raises(ValueError, match="不能重命名"):
        library.rename(builtins[0].id, "改名")


@pytest.mark.skipif(not BUILTIN_DIR.is_dir(), reason="内置 compiled 模板不存在")
def test_compiled_path_falls_back_to_default(tmp_path: Path) -> None:
    library = TemplateLibrary(store_dir=tmp_path / "templates", builtin_dir=BUILTIN_DIR)
    resolved = library.compiled_path("tpl-not-exist")
    assert resolved.is_file()
    assert resolved.parent == BUILTIN_DIR


@pytest.mark.skipif(not BUILTIN_DIR.is_dir(), reason="内置 compiled 模板不存在")
def test_builtin_templates_carry_category_and_method(tmp_path: Path) -> None:
    """内置模板也要有类型标签，否则「按所选类型推荐模板」永远匹配不上。"""
    library = TemplateLibrary(store_dir=tmp_path / "templates", builtin_dir=BUILTIN_DIR)
    infos = {info.name: info for info in library.list() if info.builtin}

    base = next(info for name, info in infos.items() if name.endswith("公开招标标准模板"))
    goods = next(info for name, info in infos.items() if "货物类" in name)
    negotiation = next(info for name, info in infos.items() if "竞争性磋商" in name)

    assert (base.category, base.method) == ("service", "open")
    assert (goods.category, goods.method) == ("goods", "open")
    assert (negotiation.category, negotiation.method) == ("service", "negotiation")
