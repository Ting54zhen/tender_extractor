"""模板库 HTTP 契约：增删改查、上传校验、预览路由。

只验证路由与状态码/字段形状，模板解析细节在 test_template_library.py。
"""

from __future__ import annotations

import io
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

import docx
import pytest

from tender_extractor.doc_generator import DEFAULT_TEMPLATE
from tender_extractor.models import ExtractionResult, FieldValue
from tender_extractor.storage import Database
from tender_extractor.template_library import TemplateLibrary
from tender_extractor.web import Job, Service, make_server


def _docx_bytes(*paragraphs: str) -> bytes:
    document = docx.Document()
    for text in paragraphs:
        document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@pytest.fixture()
def service(tmp_path: Path) -> Service:
    db = Database(tmp_path / "tender.db")
    templates = TemplateLibrary(
        store_dir=tmp_path / "templates", builtin_dir=tmp_path / "no-builtin"
    )
    return Service(output_dir=tmp_path / "out", db=db, templates=templates)


@pytest.fixture()
def server(service: Service):
    httpd = make_server("127.0.0.1", 0, service)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def _request(url: str, *, method: str = "GET", data: bytes | None = None):
    headers = {"Content-Type": "application/json"} if data else {}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            body = resp.read()
            return resp.status, json.loads(body.decode("utf-8")) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read()
        exc.close()
        return exc.code, json.loads(body.decode("utf-8") or "{}")


def _post(url: str, payload: dict):
    return _request(url, method="POST", data=json.dumps(payload).encode("utf-8"))


def test_template_list_starts_empty(server: str) -> None:
    status, body = _request(f"{server}/api/templates")
    assert status == 200
    assert body["templates"] == []


def test_template_upload_and_crud(server: str) -> None:
    payload = _docx_bytes("项目名称：{项目名称}", "邮箱：{采购人邮箱}")
    status, info = _request(
        f"{server}/api/templates?filename={quote('某省模板.docx')}"
        "&category=service&method=open",
        method="POST",
        data=payload,
    )

    assert status == 201
    assert info["builtin"] is False
    assert info["category"] == "service"
    assert info["placeholder_count"] == 2
    assert info["unmapped"] == ["采购人邮箱"]

    template_id = info["id"]
    status, detail = _request(f"{server}/api/templates/{template_id}")
    assert status == 200
    assert detail["id"] == template_id

    status, renamed = _post(f"{server}/api/templates/{template_id}/rename", {"name": "改名后"})
    assert status == 200
    assert renamed["name"] == "改名后"

    status, default = _post(f"{server}/api/templates/{template_id}/default", {})
    assert status == 200
    assert default["is_default"] is True

    status, listing = _request(f"{server}/api/templates")
    assert status == 200
    assert listing["templates"][0]["is_default"] is True

    status, body = _request(f"{server}/api/templates/{template_id}", method="DELETE")
    assert status == 200
    assert body["ok"] is True

    status, _ = _request(f"{server}/api/templates/{template_id}")
    assert status == 404


def test_template_upload_rejects_non_docx(server: str) -> None:
    status, body = _request(
        f"{server}/api/templates?filename={quote('模板.pdf')}",
        method="POST",
        data=b"%PDF-1.4",
    )
    assert status == 400
    assert ".docx" in body["error"]


def test_template_upload_rejects_template_without_placeholder(server: str) -> None:
    status, body = _request(
        f"{server}/api/templates?filename={quote('空模板.docx')}",
        method="POST",
        data=_docx_bytes("没有任何占位符"),
    )
    assert status == 400
    assert "占位符" in body["error"]


def test_template_missing_id_returns_404(server: str) -> None:
    status, body = _request(f"{server}/api/templates/tpl-not-exist")
    assert status == 404
    assert "不存在" in body["error"]


def test_delete_unknown_template_returns_404(server: str) -> None:
    status, _ = _request(f"{server}/api/templates/tpl-not-exist", method="DELETE")
    assert status == 404


def test_job_preview_unknown_job_returns_404(server: str) -> None:
    status, body = _post(f"{server}/api/jobs/not-a-job/preview", {"values": {}})
    assert status == 404
    assert "任务不存在" in body["error"]


def test_job_fields_accepts_template_id_query(server: str, service: Service) -> None:
    """template_id 走 query string，这里防的是把 parsed.query 当字典用。"""
    job = Job(id="job-fields", filename="需求.docx", use_llm=False, allow_non_tender=False)
    job.result = {"fields": {}}
    service.jobs["job-fields"] = job

    status, body = _request(
        f"{server}/api/jobs/job-fields/fields?template_id=builtin-unknown"
    )
    assert status == 200
    assert "unmapped_placeholders" in body
    assert "template" in body


# ----------------------------------------------------------------------
# Service 层：预览与生成的完整路径（HTTP 层需要真实任务，这里直接调）
# ----------------------------------------------------------------------


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


def _job(job_id: str = "job-1") -> Job:
    return Job(id=job_id, filename="需求.docx", use_llm=False, allow_non_tender=False)


@pytest.mark.skipif(
    not DEFAULT_TEMPLATE.exists(), reason="compiled 模板不存在"
)
def test_preview_job_returns_blocks(service: Service) -> None:
    preview = service.preview_job(
        _job(),
        {"values": {"项目名称": "某测试项目"}, "missing_mode": "mark"},
    )

    assert preview["stats"]["total_placeholders"] > 0
    assert preview["sections"], "至少要有一个章节"
    assert preview["template"]["name"]
    missing_blocks = [
        segment
        for section in preview["sections"]
        for block in section["blocks"]
        if block["type"] == "paragraph"
        for segment in block["segments"]
        if segment["kind"] == "missing"
    ]
    assert missing_blocks, "未填字段应当渲染成待补充片段"


@pytest.mark.skipif(
    not DEFAULT_TEMPLATE.exists(), reason="compiled 模板不存在"
)
def test_generate_document_with_custom_fields(service: Service, tmp_path: Path) -> None:
    job = _job("job-2")
    job.result_obj = _make_result(项目名称="某测试项目")

    target = service.generate_document(
        job,
        {"项目名称": "某测试项目"},
        "mark",
        "",
        [{"label": "履约保证金", "value": "合同金额的5%", "target": "append"}],
    )

    assert target.exists()
    document = docx.Document(str(target))
    paragraphs = [para.text.strip() for para in document.paragraphs if para.text.strip()]
    assert paragraphs[-1] == "补充条款"
