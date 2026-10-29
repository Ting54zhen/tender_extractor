"""CLI 输出目录测试：默认写「结果 + 审核报告」，重名自动追加序号。"""

from docx import Document as DocxDocument

from tender_extractor.cli import _unique_path, main


def _build_requirement_docx(path):
    doc = DocxDocument()
    doc.add_paragraph("某某单位采购需求报告")
    doc.add_paragraph("一、项目概况")
    doc.add_paragraph("项目名称：测试项目（一期）")
    doc.add_paragraph("采购方式：公开招标")
    project = doc.add_table(rows=2, cols=3)
    for idx, value in enumerate(["采购内容", "采购预算", "合同履行期限"]):
        project.cell(0, idx).text = value
    for idx, value in enumerate(
        ["测试项目（一期）", "100,000.00元", "2026年1月1日至2026年12月31日"]
    ):
        project.cell(1, idx).text = value
    doc.add_paragraph("二、采购标的")
    doc.add_paragraph("本项目不接受联合体投标。")
    doc.save(str(path))


def test_unique_path_appends_suffix(tmp_path):
    first = tmp_path / "需求报告-结果.json"
    assert _unique_path(first) == first
    first.write_text("{}", encoding="utf-8")
    second = _unique_path(first)
    assert second.name == "需求报告-结果-2.json"
    assert not second.exists()


def test_extract_writes_json_and_report_to_output_dir(tmp_path):
    path = tmp_path / "需求报告.docx"
    _build_requirement_docx(path)
    out_dir = tmp_path / "结果审核报告"

    code = main(["extract", str(path), "--no-llm", "--output-dir", str(out_dir)])
    assert code == 0
    assert (out_dir / "需求报告-结果.json").is_file()
    assert (out_dir / "需求报告-审核报告.md").is_file()

    # 再跑一次不覆盖历史结果
    code = main(["extract", str(path), "--no-llm", "--output-dir", str(out_dir)])
    assert code == 0
    assert (out_dir / "需求报告-结果-2.json").is_file()
    assert (out_dir / "需求报告-审核报告-2.md").is_file()


def test_bare_filename_goes_to_output_dir(tmp_path):
    path = tmp_path / "需求报告.docx"
    _build_requirement_docx(path)
    out_dir = tmp_path / "结果审核报告"

    code = main(
        [
            "extract",
            str(path),
            "--no-llm",
            "--output-dir",
            str(out_dir),
            "-o",
            "结果.json",
            "--md",
            "审核报告.md",
        ]
    )
    assert code == 0
    assert (out_dir / "结果.json").is_file()
    assert (out_dir / "审核报告.md").is_file()
    assert not (tmp_path / "结果.json").exists()
    assert not (tmp_path / "审核报告.md").exists()


def test_no_md_skips_report(tmp_path):
    path = tmp_path / "需求报告.docx"
    _build_requirement_docx(path)
    out_dir = tmp_path / "结果审核报告"

    code = main(["extract", str(path), "--no-llm", "--no-md", "--output-dir", str(out_dir)])
    assert code == 0
    assert (out_dir / "需求报告-结果.json").is_file()
    assert not (out_dir / "需求报告-审核报告.md").exists()
