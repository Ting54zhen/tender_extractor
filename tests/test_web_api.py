# -*- coding: utf-8 -*-
"""web 项目工作台 / 设置 / 持久化恢复 API。"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from tender_extractor.storage import Database
from tender_extractor.web import Service, make_server


def test_text_extraction_progress_and_restored_completion(service: Service, monkeypatch: pytest.MonkeyPatch) -> None:
    """直接文字上传保持证据链；进度可观察，保存完成与重启恢复都显示 100%。"""
    from tender_extractor import Extractor

    # 暂停实际引擎的一个阶段，让测试确定地读到运行中进度，不依赖执行速度。
    reached = threading.Event()
    proceed = threading.Event()
    extractor = Extractor(settings_override={"review": {"enabled": False}})
    original = extractor.extract

    def extract_with_pause(*args: object, **kwargs: object) -> object:
        """只拦截阶段通知，文档读取、规则、证据校验仍调用真实实现。"""
        callback = kwargs["on_progress"]

        def notify(percent: int, stage: str) -> None:
            callback(percent, stage)
            if percent == 35:
                reached.set()
                assert proceed.wait(10)

        kwargs["on_progress"] = notify
        return original(*args, **kwargs)

    monkeypatch.setattr(extractor, "extract", extract_with_pause)
    monkeypatch.setattr(service, "_new_extractor", lambda: extractor)
    payload = "采购需求报告\n一、项目概况\n项目名称：某服务采购项目\n采购方式：公开招标\n采购预算：100000元\n二、采购需求\n合同履行期限：一年"
    job = service.submit("文字采购需求.txt", payload.encode("utf-8"), False, True)
    try:
        assert reached.wait(10)
        assert job.public()["status"] == "running"
        assert job.public()["progress"] == 35
    finally:
        proceed.set()
    for _ in range(100):
        row = service.db.get_job(job.id)
        if row["status"] in {"done", "error"}:
            break
        time.sleep(0.05)
    assert row["status"] == "done"
    assert job.public()["progress"] == 100
    assert job.result["fields"]["project_name"]["evidence"]
    restored = Service(output_dir=service._output_dir, db=Database(service.db.db_path)).get(job.id)
    assert restored.public()["progress"] == 100


def test_progress_keeps_failure_below_completion(service: Service) -> None:
    """读取损坏文件失败时保留已进入的读取阶段，不把失败渲染成完成。"""
    from tender_extractor.web import Job
    path = service._output_dir / "invalid.docx"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"invalid")
    job = Job(id="failed-progress", filename="invalid.docx", use_llm=False, allow_non_tender=False)
    service._run(job, path)
    assert job.status == "error"
    assert job.public()["progress"] == 5


@pytest.fixture()
def service(tmp_path):
    db = Database(tmp_path / "tender.db")
    return Service(output_dir=tmp_path / "out", db=db)


@pytest.fixture()
def server(service):
    httpd = make_server("127.0.0.1", 0, service)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def _get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read()
        exc.close()
        return exc.code, json.loads(body.decode("utf-8") or "{}")


def _post(url: str, payload: dict) -> tuple[int, dict]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read()
        exc.close()
        return exc.code, json.loads(body.decode("utf-8") or "{}")


def _delete(url: str) -> tuple[int, dict]:
    """通过真实 DELETE 请求验证记录删除，不使用前端夹具替代 API 行为。"""
    request = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))


def test_delete_requirement_removes_record_values_and_cache(server: str, service: Service) -> None:
    """删除完成任务后详情与列表都不可恢复，项目与其他任务的确认值保留。"""
    project = service.db.create_project("某采购项目")["id"]
    for job_id in ("delete-one", "keep-one"):
        service.db.create_job(job_id, "extract", "某需求.txt", project, False, False)
        service.db.update_job(job_id, status="done", stage="完成")
        service.db.save_field_values(job_id, project, [{"key": "project_name", "value": "某需求"}])
    # 先读入内存缓存，避免只验证数据库删除而漏掉旧详情仍可访问的问题。
    assert _get(f"{server}/api/jobs/delete-one")[0] == 200
    status, data = _delete(f"{server}/api/jobs/delete-one")
    assert status == 200 and data == {"deleted": "delete-one"}
    assert "delete-one" not in service.jobs
    assert service.get("delete-one") is None
    assert service.db.list_field_values("delete-one") == []
    assert service.db.list_field_values("keep-one")
    assert service.db.get_project(project) is not None
    assert _get(f"{server}/api/jobs/delete-one")[0] == 404
    assert [item["id"] for item in _get(f"{server}/api/jobs")[1]["jobs"]] == ["keep-one"]
    assert _delete(f"{server}/api/jobs/delete-one")[0] == 404


@pytest.mark.parametrize("status", ["queued", "running"])
def test_delete_requirement_rejects_active_job(server: str, service: Service, status: str) -> None:
    """解析线程运行期间不允许删除，避免结果回写后出现孤立记录或误报成功。"""
    service.db.create_job("active-delete", "extract", "某需求.txt", None, False, False)
    service.db.update_job("active-delete", status=status)
    code, data = _delete(f"{server}/api/jobs/active-delete")
    assert code == 409 and "解析中" in data["error"]
    assert service.db.get_job("active-delete") is not None


def test_delete_requirement_rejects_compare_job(server: str, service: Service) -> None:
    """需求删除入口只作用于抽取记录，比对模块保持现有功能边界。"""
    service.db.create_job("compare-delete", "compare", "某比对", None, False, False)
    service.db.update_job("compare-delete", status="done")
    assert _delete(f"{server}/api/jobs/compare-delete")[0] == 400
    assert service.db.get_job("compare-delete") is not None


def test_project_api_crud(server):
    # 新建
    status, project = _post(f"{server}/api/projects", {"name": "演示项目", "note": "n"})
    assert status == 201
    pid = project["id"]

    # 列表
    status, data = _get(f"{server}/api/projects")
    assert status == 200
    assert [p["id"] for p in data["projects"]] == [pid]
    assert data["projects"][0]["job_count"] == 0

    # 详情带任务列表
    status, detail = _get(f"{server}/api/projects/{pid}")
    assert status == 200
    assert detail["jobs"] == []

    # 归档 / 恢复 / 重命名
    _, archived = _post(f"{server}/api/projects/{pid}/archive", {})
    assert archived["status"] == "archived"
    _, data = _get(f"{server}/api/projects?status=archived")
    assert len(data["projects"]) == 1
    _, restored = _post(f"{server}/api/projects/{pid}/restore", {})
    assert restored["status"] == "active"
    _, renamed = _post(f"{server}/api/projects/{pid}/rename", {"name": "改名后"})
    assert renamed["name"] == "改名后"

    # 删除
    status, _ = _post(f"{server}/api/projects/{pid}/delete", {})
    assert status == 200
    status, _ = _get(f"{server}/api/projects/{pid}")
    assert status == 404

    # 空名拒绝
    status, _ = _post(f"{server}/api/projects", {"name": ""})
    assert status == 400


def test_extract_rejects_unknown_project(server):
    """上传时携带不存在的 project_id 应拒绝，防止任务落到幽灵项目。"""
    boundary = "XtenderX"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="a.docx"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8") + b"docx-bytes" + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        f"{server}/api/extract?project_id=ghost&use_llm=0",
        data=body,
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        raised = False
    except urllib.error.HTTPError as exc:
        raised = exc.code == 400
    assert raised


def test_settings_api_roundtrip(server, service):
    status, data = _get(f"{server}/api/settings")
    assert status == 200
    assert "llm" in data["settings"]

    status, data = _post(
        f"{server}/api/settings",
        {"settings": {"llm": {"model": "qwen2.5:7b-instruct"}}},
    )
    assert status == 200
    assert data["settings"]["llm"]["model"] == "qwen2.5:7b-instruct"
    # 覆盖层落库，Service 缓存已失效并重新合并
    assert service.settings_overrides()["llm"]["model"] == "qwen2.5:7b-instruct"

    # 不支持的设置段拒绝
    status, _ = _post(f"{server}/api/settings", {"settings": {"hacker": {}}})
    assert status == 400


def test_job_persisted_and_restored(server, service):
    """任务结束落库；新建第二个 Service（模拟重启）后仍可取到任务。"""
    project = _post(f"{server}/api/projects", {"name": "持久化"})[1]["id"]
    job = service.submit("t.docx", b"docx-bytes", False, False, project)
    # 等后台线程跑完（假 docx 会走 error 分支，正好覆盖错误落库）
    for _ in range(100):
        if service.get(job.id).status in {"done", "skipped", "error"}:
            break
        time.sleep(0.1)
    finished = service.get(job.id)
    assert finished.status == "error"

    row = service.db.get_job(job.id)
    assert row["status"] == "error"
    assert row["project_id"] == project

    # 模拟重启：全新 Service 挂同一个库
    db2 = Database(service.db.db_path)
    service2 = Service(output_dir=service._output_dir, db=db2)
    restored = service2.get(job.id)
    assert restored is not None
    assert restored.project_id == project
    assert restored.public()["project_id"] == project


def test_job_fields_suggests_history_values(service):
    """字段资产复利：以前填过的值，在空字段上给出 history 建议。"""
    from tender_extractor.web import Job

    project = service.db.create_project("历史资产")["id"]
    # 上一个任务确认过采购人与合同份数
    service.db.save_field_values(
        "old-job", project,
        [
            {"key": "purchaser", "label": "采购人", "value": "某市财政局", "source": "manual"},
            {"key": "agency", "label": "代理机构", "value": "某招标公司"},
        ],
    )

    fields = {
        "purchaser": {"key": "purchaser", "label": "采购人", "template": "采购人", "value": None, "status": "missing"},
        "agency": {"key": "agency", "label": "代理机构", "template": "代理机构", "value": "已抽到的代理机构", "status": "ok"},
    }
    job = Job(id="new-job", filename="b.docx", use_llm=False, allow_non_tender=False,
              project_id=project, status="done", result={"fields": fields})

    data = service.job_fields(job)
    items = {it["key"]: it for g in data["groups"] for it in g["items"]}

    # 空字段：带上历史值
    assert items["purchaser"]["history"]["value"] == "某市财政局"
    assert items["purchaser"]["history"]["job_id"] == "old-job"
    assert data["history_count"] == 1
    # 已有值的字段不给历史建议，避免覆盖可靠值
    assert items["agency"]["history"] is None


def test_fields_api_exposes_history(server, service):
    """HTTP 层：/api/jobs/{id}/fields 返回 history_count 与 history 字段。"""
    from tender_extractor.web import Job

    project = service.db.create_project("HTTP历史")["id"]
    service.db.save_field_values("old", project,
                                 [{"key": "purchaser", "label": "采购人", "value": "某采购单位"}])
    fields = {"purchaser": {"key": "purchaser", "label": "采购人", "template": "采购人",
                            "value": "", "status": "missing"}}
    job = Job(id="j-hist", filename="c.docx", use_llm=False, allow_non_tender=False,
              project_id=project, status="done", result={"fields": fields})
    service.jobs[job.id] = job

    status, data = _get(f"{server}/api/jobs/{job.id}/fields")
    assert status == 200
    assert data["history_count"] == 1
    item = data["groups"][0]["items"][0]
    assert item["history"]["value"] == "某采购单位"


def test_company_api_roundtrip(server, service):
    """企业档案：空壳默认值 → 保存 → 回读；形状兼容 CompanyProfile.load。"""
    status, data = _get(f"{server}/api/company")
    assert status == 200
    assert data["profile"]["company"]["name"] == ""
    assert data["profile"]["qualifications"] == []

    payload = {
        "company": {"name": "演示科技有限公司", "credit_code": "91440300MA5XXXXX0X"},
        "qualifications": [
            {"name": "ISO9001 质量管理体系认证", "certificate_no": "00123", "issuer": "CQC", "valid_until": "2027-12-31"},
            {"name": "", "certificate_no": ""},  # 全空行应被剔除
        ],
        "performances": [{"project_name": "某图书馆智慧化项目", "amount": "198万", "year": "2025"}],
        "personnel": [{"name": "张三", "role": "项目经理", "years": "8"}],
        "statements": {"sme": "中小企业声明函（已盖章）"},
        "evidence_index": {"business_license": "材料/营业执照.pdf", "": "空key应剔除"},
    }
    status, data = _post(f"{server}/api/company", payload)
    assert status == 200
    profile = data["profile"]
    assert len(profile["qualifications"]) == 1
    assert profile["personnel"][0]["years"] == 8  # 字符串转 int
    assert "" not in profile["evidence_index"]

    status, data = _get(f"{server}/api/company")
    assert data["profile"]["company"]["name"] == "演示科技有限公司"
    assert data["profile"]["evidence_index"]["business_license"] == "材料/营业执照.pdf"

    # 与 CompanyProfile 兼容（bid_writer 的读取路径）
    import json as _json
    from pathlib import Path

    from tender_extractor.company_profile import CompanyProfile

    tmp = service.tmp_dir / "company.json"
    tmp.write_text(_json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    cp = CompanyProfile.load(Path(tmp))
    assert cp.name == "演示科技有限公司"
    assert cp.qualifications[0].name == "ISO9001 质量管理体系认证"
    assert cp.find_qualification("质量管理体系") is not None


def test_company_api_rejects_bad_payload(server):
    status, _ = _post(f"{server}/api/company", {"company": "不是对象"})
    assert status == 400
    status, _ = _post(f"{server}/api/company", {"qualifications": "不是数组", "company": {}})
    assert status == 400


def test_bid_generation_flow(server, service, tmp_path):
    """投标书链路：上传留原文 → 解析 → 生成投标书 → 重启后仍可生成。"""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_llm_pipeline import _build_requirement_docx

    docx = tmp_path / "需求报告-投标.docx"
    _build_requirement_docx(docx)

    project = _post(f"{server}/api/projects", {"name": "投标链路"})[1]["id"]
    job = service.submit(docx.name, docx.read_bytes(), False, False, project)
    for _ in range(100):
        if service.get(job.id).status in {"done", "error", "skipped"}:
            break
        time.sleep(0.1)
    finished = service.get(job.id)
    assert finished.status == "done", finished.error
    assert finished.source_path and finished.source_path.is_file()

    status, data = _post(f"{server}/api/jobs/{job.id}/bid", {})
    assert status == 200, data
    assert "markdown" in data and data["markdown"]
    assert "summary" in data
    md_path = Path(data["md_path"])
    assert md_path.is_file()

    # 重启后（新 Service）source_path 从 DB 恢复，仍可生成
    db2 = Database(service.db.db_path)
    service2 = Service(output_dir=service._output_dir, db=db2)
    restored = service2.get(job.id)
    assert restored is not None and restored.source_path is not None
    assert restored.source_path.is_file()


def test_bid_requires_source_file(server, service):
    """原文丢失时给出明确指引而不是 500。"""
    from tender_extractor.web import Job

    job = Job(id="no-source", filename="x.docx", use_llm=False, allow_non_tender=False,
              status="done", result={"fields": {}}, source_path=None)
    service.jobs[job.id] = job
    status, data = _post(f"{server}/api/jobs/{job.id}/bid", {})
    assert status == 409
    assert "重新上传" in data["error"]


def test_graph_analysis_flow(server, service, tmp_path):
    """图谱分析：仅规则任务也能出图（字段节点），返回三类清单 + turtle。"""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_llm_pipeline import _build_requirement_docx

    docx = tmp_path / "需求报告-图谱.docx"
    _build_requirement_docx(docx)

    job = service.submit(docx.name, docx.read_bytes(), False, False, None)
    for _ in range(100):
        if service.get(job.id).status in {"done", "error", "skipped"}:
            break
        time.sleep(0.1)
    assert service.get(job.id).status == "done"

    status, data = _post(f"{server}/api/jobs/{job.id}/analyze", {})
    assert status == 200, data
    analysis = data["analysis"]
    assert set(analysis) == {"compliance_checklist", "risk_checklist", "scoring_material_map"}
    assert data["turtle"].startswith("@prefix")
    assert (data["graph"]["nodes"])  # 项目节点 + 字段映射节点
    assert "仅规则抽取" in data["meta"]["error"]  # 规则任务明确说明无关系实体
    # 关系抽取为空时用规则层 ★ 条款兜底，风险清单不能是空表
    assert "未抽取到废标条款实体" not in analysis["risk_checklist"]
    assert data["meta"]["fallbacks"]["disqualifications"]
    assert any(
        node["type"] == "DisqualificationClause" for node in data["graph"]["nodes"]
    )


def test_graph_requires_result(server, service):
    from tender_extractor.web import Job

    job = Job(id="no-result", filename="y.docx", use_llm=False, allow_non_tender=False,
              status="queued", result=None)
    service.jobs[job.id] = job
    status, data = _post(f"{server}/api/jobs/{job.id}/analyze", {})
    assert status == 409


def test_suggest_requires_model_or_returns_note(server, service, tmp_path):
    """AI 建议：仅规则任务优雅降级（note + 不写入抽取结果）。"""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_llm_pipeline import _build_requirement_docx

    docx = tmp_path / "需求报告-建议.docx"
    _build_requirement_docx(docx)
    job = service.submit(docx.name, docx.read_bytes(), False, False, None)
    for _ in range(100):
        if service.get(job.id).status in {"done", "error", "skipped"}:
            break
        time.sleep(0.1)
    assert service.get(job.id).status == "done"

    status, data = _post(f"{server}/api/jobs/{job.id}/suggest", {})
    assert status == 200, data
    # 仅规则任务：count=0 且带 note，绝不写进抽取结果
    assert data["count"] == 0
    assert "仅规则" in data.get("note", "")
    row = service.db.get_job(job.id)
    for item in (row.get("result") or {}).get("fields", {}).values():
        assert item.get("source") != "llm_suggest"


def test_project_zip_export(server, service, tmp_path):
    """项目批量导出：结果 JSON + 审核报告 + 招标文件 docx 打进 zip。"""
    import io
    import sys
    import zipfile
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_llm_pipeline import _build_requirement_docx

    docx = tmp_path / "需求报告-打包.docx"
    _build_requirement_docx(docx)
    project = _post(f"{server}/api/projects", {"name": "打包项目"})[1]["id"]
    job = service.submit(docx.name, docx.read_bytes(), False, False, project)
    for _ in range(100):
        if service.get(job.id).status in {"done", "error", "skipped"}:
            break
        time.sleep(0.1)
    assert service.get(job.id).status == "done"

    # 空项目 / 无结果项目 → 409
    empty = _post(f"{server}/api/projects", {"name": "空项目"})[1]["id"]
    req = urllib.request.Request(f"{server}/api/projects/{empty}/export", data=b"{}", method="POST")
    try:
        urllib.request.urlopen(req, timeout=30)
        raised = False
    except urllib.error.HTTPError as exc:
        exc.read()
        exc.close()
        raised = exc.code == 409
    assert raised

    # 导出是二进制 zip 响应，不走 JSON helper
    req = urllib.request.Request(f"{server}/api/projects/{project}/export", data=b"{}", method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        assert resp.status == 200
        assert resp.headers["Content-Type"].startswith("application/zip")
        payload = resp.read()
    assert payload[:2] == b"PK"
    # 服务端已落盘一份 zip，同时验证响应体与落盘内容一致
    out_dir = Path(service._output_dir)
    zips = sorted(out_dir.glob("*-导出.zip"), key=lambda p: p.stat().st_mtime)
    assert zips, "zip 未落盘"
    for source in (io.BytesIO(payload), io.BytesIO(zips[-1].read_bytes())):
        with zipfile.ZipFile(source) as zf:
            names = zf.namelist()
        assert any(n.endswith("-结果.json") for n in names)
        assert any(n.endswith("-审核报告.md") for n in names)
        assert any(n.endswith("-招标文件.docx") for n in names)


def test_self_org_marks_agency_na(service):
    """自主招标：agency 整组字段标 na、不计缺失；代理招标不受影响。"""
    from tender_extractor.web import Job

    def make_job(org_value, agency_value):
        fields = {
            "tender_org_form": {"key": "tender_org_form", "label": "招标组织形式",
                                "template": "招标组织形式", "value": org_value, "status": "ok"},
            "agency": {"key": "agency", "label": "采购代理机构", "template": "采购代理机构",
                       "value": agency_value, "status": "ok"},
            "agency_address": {"key": "agency_address", "label": "采购代理机构地址",
                               "template": "采购代理机构地址", "value": None, "status": "missing"},
        }
        return Job(id="org-job-" + str(abs(hash(org_value)))[:6], filename="o.docx",
                   use_llm=False, allow_non_tender=False, status="done",
                   result={"fields": fields})

    data_self = service.job_fields(make_job("自行招标", ""))
    items_self = {it["key"]: it for g in data_self["groups"] for it in g["items"]}
    assert items_self["agency"]["na"] is True
    assert items_self["agency_address"]["na"] is True
    assert data_self["na_count"] == 2
    # agency_address 为空但 na，不计缺失
    assert data_self["missing"] == 0

    data_agent = service.job_fields(make_job("委托代理招标", ""))
    items_agent = {it["key"]: it for g in data_agent["groups"] for it in g["items"]}
    assert items_agent["agency"]["na"] is False
    assert data_agent["na_count"] == 0
    # agency（空）与 agency_address（空）正常计缺失
    assert data_agent["missing"] == 2


def test_cross_validate_org_form_and_method(tmp_path):
    """交叉校验：自主招标 vs 代理机构；方式 vs 评审法。"""
    from tender_extractor.models import FieldValue
    from tender_extractor.validation import cross_validate

    def fv(value):
        return FieldValue(key="k", label="l", value=value, template="tpl")

    # 自主招标 + 有代理机构 → 警告
    fields = {
        "tender_org_form": fv("自行招标"), "agency": fv("某某代理公司"),
        "procurement_method": fv("公开招标"), "price_method": fv("综合评分法"),
    }
    _, warnings = cross_validate(fields, {})
    assert any(w["code"] == "ORG_FORM_AGENCY_CONFLICT" for w in warnings)

    # 委托代理 + 无代理机构 → 警告
    fields["tender_org_form"] = fv("委托代理招标")
    fields["agency"] = fv("")
    _, warnings = cross_validate(fields, {})
    assert any(w["code"] == "ORG_FORM_AGENCY_MISSING" for w in warnings)

    # 竞争性磋商 + 最低价 → 错误
    fields["procurement_method"] = fv("竞争性磋商")
    fields["price_method"] = fv("最低价法")
    errors, _ = cross_validate(fields, {})
    assert any(e["code"] == "METHOD_PRICE_CONFLICT" for e in errors)

    # 谈判 + 综合评分 → 错误
    fields["procurement_method"] = fv("竞争性谈判")
    fields["price_method"] = fv("综合评分法")
    errors, _ = cross_validate(fields, {})
    assert any(e["code"] == "METHOD_PRICE_CONFLICT" for e in errors)

    # 合规组合无告警
    fields["procurement_method"] = fv("公开招标")
    fields["price_method"] = fv("综合评分法")
    fields["tender_org_form"] = fv("委托代理招标")
    fields["agency"] = fv("某某代理公司")
    errors, warnings = cross_validate(fields, {})
    assert not any(w["code"] in {"ORG_FORM_AGENCY_CONFLICT", "ORG_FORM_AGENCY_MISSING"} for w in warnings)
    assert not any(e["code"] == "METHOD_PRICE_CONFLICT" for e in errors)


def test_confirm_fields_api(server, service):
    project = _post(f"{server}/api/projects", {"name": "确认值"})[1]["id"]
    job = service.submit("t2.docx", b"docx-bytes", False, False, project)
    service.get(job.id).status  # 确保创建

    status, data = _post(
        f"{server}/api/jobs/{job.id}/confirm",
        {"values": [{"key": "purchaser", "label": "采购人", "value": "某单位"}]},
    )
    assert status == 200
    assert data["saved"] == 1
    rows = service.db.list_field_values(job.id)
    assert rows[0]["key"] == "purchaser"
    assert rows[0]["value"] == "某单位"


def test_find_soffice_covers_macos_bundle():
    """macOS 的 LibreOffice 不在 PATH 里，必须按应用包路径探测，否则 PDF 导出直接失败。"""
    from tender_extractor import web

    assert any(
        "LibreOffice.app/Contents/MacOS/soffice" in path for path in web._SOFFICE_CANDIDATES
    )


def _scope_job() -> object:
    """构造一个字段齐全的最小 done 任务，供类型字段集断言使用。"""
    from tender_extractor.web import Job

    fields = {
        key: {"key": key, "label": key, "template": key, "value": value, "status": "ok"}
        for key, value in {
            "tender_org_form": "委托招标",
            "procurement_method": "公开招标",
            "price_method": "综合评分法",
            "agency": "某代理机构",
            "agency_fee": "按标准收取",
            "tech_score": "40",
            "business_score": "30",
            "price_score": "30",
            "contract_period": "12 个月",
            "contract_scope": "全部服务",
        }.items()
    }
    return Job(
        id="scope-job", filename="scope.docx", use_llm=False,
        allow_non_tender=False, status="done", result={"fields": fields},
    )


def test_job_fields_scope_query_filters_and_overrides(service):
    """选询价 + 货物 + 自主招标：评分字段不适用、标签覆写、专属字段下发。"""
    data = service.job_fields(
        _scope_job(), None, method="inquiry", category="goods", org_form="self"
    )
    items = {it["key"]: it for group in data["groups"] for it in group["items"]}

    assert items["agency"]["na"] is True
    assert items["tech_score"]["na"] is True
    assert items["price_score"]["na"] is True
    assert items["contract_period"]["label"] == "合同交货期限"
    assert items["contract_scope"]["label"] == "合同供货范围"

    scope = data["scope"]
    assert scope["complete"] is True
    assert scope["axes"]["category"]
    assert any(f["key"] == "scope_goods_warranty" for f in scope["extra_fields"])
    assert any(f["key"] == "scope_import_allowed" for f in scope["extra_fields"])
    assert items["tech_score"]["na_reason"]


def test_job_fields_without_scope_keeps_legacy_behavior(service):
    """不带类型参数时沿用旧行为（从原文推断组织形式），不擅自过滤字段。"""
    data = service.job_fields(_scope_job())

    assert data["scope"]["complete"] is False
    assert data["scope"]["extra_fields"] == []
    items = {it["key"]: it for group in data["groups"] for it in group["items"]}
    assert items["agency"]["na"] is False
    assert items["tech_score"]["na"] is False
    assert items["contract_period"]["label"] == "contract_period"


def test_fields_api_accepts_scope_query(server, service):
    """/fields 的 HTTP 入口要把三个类型参数真正传到 Service。"""
    from tender_extractor.web import Job

    fields = {
        key: {"key": key, "label": key, "template": key, "value": value, "status": "ok"}
        for key, value in {
            "agency": "某代理机构", "tech_score": "40", "contract_period": "12 个月",
            "tender_org_form": "委托招标", "procurement_method": "公开招标",
        }.items()
    }
    job = Job(
        id="http-scope", filename="scope.docx", use_llm=False,
        allow_non_tender=False, status="done", result={"fields": fields},
    )
    service.jobs[job.id] = job

    status, data = _get(
        f"{server}/api/jobs/http-scope/fields?method=inquiry&category=goods&org_form=self"
    )
    assert status == 200
    assert data["scope"]["complete"] is True
    items = {it["key"]: it for group in data["groups"] for it in group["items"]}
    assert items["agency"]["na"] is True
    assert items["tech_score"]["na"] is True
    assert items["contract_period"]["label"] == "合同交货期限"

    status, data = _get(f"{server}/api/jobs/http-scope/fields?method=bogus")
    assert status == 400
    assert "采购方式" in data["error"]


def test_find_soffice_falls_back_to_path(monkeypatch, tmp_path):
    """系统安装路径都不存在时，退回 PATH 里的 soffice，兼容 Linux 与 Homebrew 安装。"""
    from tender_extractor import web

    fake = tmp_path / "soffice"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setattr(web, "_SOFFICE_CANDIDATES", ())
    monkeypatch.setattr(
        "shutil.which", lambda name: str(fake) if name == "soffice" else None
    )
    assert web._find_soffice() == fake

