"""本地网页界面：上传需求报告，查看结构化字段、复核清单、模板映射与审核报告。

设计约束与组件保持一致：

- 只用标准库（``http.server``），不引入 Web 框架，也不使用 CDN；
- 默认只监听 ``127.0.0.1``，文档不出本机；
- 抽取在后台线程执行，页面轮询状态，避免长请求超时。

L2 双文档比对：
- ``POST /api/compare``（multipart：``tender`` + ``bid``）提交比对任务；
- ``GET /api/jobs/{id}/compare`` 取比对结果（requirements / responses /
  results / report）。

启动::

    python -m tender_extractor.web              # 打开浏览器
    python -m tender_extractor.web --port 8766  # 换端口
    python -m tender_extractor.web --no-browser
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from .doc_generator import generate_tender_document
from .llm_client import build_llm_client
from .pipeline import Extractor
from .platform_adapter import PlatformRecognitionError, to_platform_draft
from .report import render_markdown

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent.parent


def _resolve_frontend_dir() -> Path:
    """解析前端目录：环境变量优先，其次项目根 frontend/，最后回退包内 static/。

    前端已从 Python 包中独立为顶层 ``frontend/`` 目录，供团队成员单独修改；
    设置 ``TENDER_FRONTEND_DIR`` 可指向任意自定义前端目录。
    """
    override = os.environ.get("TENDER_FRONTEND_DIR", "")
    if override:
        return Path(override)
    default = PROJECT_ROOT / "frontend"
    if default.is_dir():
        return default
    return BASE_DIR / "static"


STATIC_DIR = _resolve_frontend_dir()
ALLOWED_SUFFIXES = {".docx", ".pdf", ".txt"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
DEFAULT_PORT = 8765
DEFAULT_HOST = "127.0.0.1"
KEEP_JOBS = 30
HEALTH_TTL = 15.0


@dataclass
class Job:
    id: str
    filename: str
    use_llm: bool
    allow_non_tender: bool
    status: str = "queued"          # queued | running | done | skipped | error
    stage: str = "排队中"
    error: str = ""
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    report: str = ""
    draft: dict[str, Any] | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    json_path: Path | None = None
    md_path: Path | None = None
    draft_path: Path | None = None
    result_obj: Any = None          # ExtractionResult 对象，供生成器使用（不参与序列化）

    def public(self) -> dict[str, Any]:
        end = self.finished_at or time.time()
        return {
            "id": self.id,
            "kind": "extract",
            "filename": self.filename,
            "status": self.status,
            "stage": self.stage,
            "error": self.error,
            "use_llm": self.use_llm,
            "allow_non_tender": self.allow_non_tender,
            "elapsed": round(max(0.0, end - self.created_at), 1),
            "summary": self.summary,
            "has_result": self.result is not None,
            "downloads": {
                "json": f"/api/jobs/{self.id}/download/json" if self.json_path else "",
                "md": f"/api/jobs/{self.id}/download/md" if self.md_path else "",
                "draft": f"/api/jobs/{self.id}/download/draft" if self.draft else "",
            },
        }


@dataclass
class CompareJob:
    """双文档比对任务：招标文件 + 投标文件 → 符合性比对结果。"""

    id: str
    tender_filename: str
    bid_filename: str
    use_llm: bool
    status: str = "queued"          # queued | running | done | error
    stage: str = "排队中"
    error: str = ""
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    payload: dict[str, Any] | None = None
    summary: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        end = self.finished_at or time.time()
        return {
            "id": self.id,
            "kind": "compare",
            "tender_filename": self.tender_filename,
            "bid_filename": self.bid_filename,
            "status": self.status,
            "stage": self.stage,
            "error": self.error,
            "use_llm": self.use_llm,
            "elapsed": round(max(0.0, end - self.created_at), 1),
            "summary": self.summary,
            "has_result": self.payload is not None,
            "downloads": {},
        }


def _summarize_compare(payload: dict[str, Any]) -> dict[str, Any]:
    results = payload.get("results") or []
    counts = {"satisfied": 0, "partial": 0, "missing": 0}
    for item in results:
        verdict = item.get("verdict")
        if verdict in counts:
            counts[verdict] += 1
    return {
        "requirements": len(payload.get("requirements") or []),
        "responses": len(payload.get("responses") or []),
        "satisfied": counts["satisfied"],
        "partial": counts["partial"],
        "missing": counts["missing"],
    }


def _parse_multipart(body: bytes, boundary: str) -> dict[str, tuple[str, bytes]]:
    """最小 multipart/form-data 解析：返回 name -> (filename, content)。

    只处理文件/文本字段，够用即可；不引入第三方库。
    """
    delimiter = ("--" + boundary).encode("utf-8")
    fields: dict[str, tuple[str, bytes]] = {}
    for part in body.split(delimiter):
        if not part or part.startswith(b"--"):
            continue
        part = part.lstrip(b"\r\n")
        if not part:
            continue
        header_end = part.find(b"\r\n\r\n")
        if header_end < 0:
            continue
        raw_headers = part[:header_end].decode("utf-8", errors="replace")
        content = part[header_end + 4:]
        if content.endswith(b"\r\n"):
            content = content[:-2]

        name = ""
        filename = ""
        for line in raw_headers.split("\r\n"):
            if line.lower().startswith("content-disposition:"):
                for segment in line.split(";"):
                    segment = segment.strip()
                    lower = segment.lower()
                    if lower.startswith("name="):
                        name = segment[5:].strip().strip('"')
                    elif lower.startswith("filename="):
                        filename = segment[9:].strip().strip('"')
        if name:
            fields[name] = (filename, content)
    return fields


def _unique_path(path: Path) -> Path:
    """与 CLI 一致：重名时追加 -2、-3，不覆盖历史结果。"""
    if not path.exists():
        return path
    for idx in range(2, 1000):
        candidate = path.with_name(f"{path.stem}-{idx}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"输出文件重名过多：{path}")


def _summarize(data: dict[str, Any]) -> dict[str, Any]:
    meta = data.get("meta") or {}
    summary = meta.get("confidence_summary") or {}
    coverage = meta.get("template_coverage") or {}
    review = data.get("review") or []
    doc_type = data.get("document_type") or {}
    review_meta = meta.get("review") or {}
    return {
        "fields_with_value": summary.get("fields_with_value", 0),
        "templates_filled": coverage.get("filled", 0),
        "templates_total": coverage.get("total", 0),
        "high": sum(1 for item in review if item.get("priority") == "high"),
        "medium": sum(1 for item in review if item.get("priority") == "medium"),
        "low": sum(1 for item in review if item.get("priority") == "low"),
        "model": meta.get("model") if meta.get("llm_available") else "",
        "provider": meta.get("llm_provider") if meta.get("llm_available") else "",
        "duration": meta.get("duration_seconds"),
        "skipped": bool(meta.get("extraction_skipped")),
        "reject_reason": meta.get("reject_reason", ""),
        "document_type": doc_type.get("reason", ""),
        "is_tender": doc_type.get("is_tender"),
        "review": {
            "enabled": bool(review_meta.get("enabled")),
            "available": bool(review_meta.get("available")),
            "provider": review_meta.get("provider", ""),
            "model": review_meta.get("model", ""),
            "checked": review_meta.get("checked", 0),
            "supported": review_meta.get("supported", 0),
            "rejected": review_meta.get("rejected", 0),
            "replaced": review_meta.get("replaced", 0),
            "ignored": review_meta.get("ignored", 0),
            "error": review_meta.get("error", ""),
        },
    }


class Service:
    """任务队列 + 结果缓存。抽取线程数等于并发上传数，由 ThreadingHTTPServer 拉起。"""

    def __init__(self, output_dir: Path | None = None) -> None:
        self.jobs: dict[str, Job | CompareJob] = {}
        self.lock = threading.Lock()
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="tender-web-"))
        self._output_dir = output_dir
        self._settings: dict[str, Any] | None = None
        self._health: dict[str, Any] | None = None
        self._health_at = 0.0
        self._groups: list[dict[str, Any]] | None = None

    # ------------------------------------------------------------------
    # 配置与健康检查
    # ------------------------------------------------------------------

    def settings(self) -> dict[str, Any]:
        if self._settings is None:
            try:
                self._settings = Extractor().settings
            except Exception:
                self._settings = {}
        return self._settings

    def health(self) -> dict[str, Any]:
        now = time.time()
        with self.lock:
            if self._health is not None and now - self._health_at < HEALTH_TTL:
                return self._health
        try:
            client = build_llm_client(self.settings().get("llm", {}))
            state = client.health()
            data = {
                "available": bool(state.available and state.model_present),
                "provider": client.provider,
                "model": client.model,
                "reason": state.reason,
                "models": state.models,
            }
        except Exception as exc:  # 配置错误、依赖缺失等
            data = {
                "available": False,
                "provider": "unknown",
                "model": "",
                "reason": str(exc),
                "models": [],
            }
        with self.lock:
            self._health = data
            self._health_at = now
        return data

    # ------------------------------------------------------------------
    # 填写工作台：字段分组与招标文件生成
    # ------------------------------------------------------------------

    def field_groups(self) -> list[dict[str, Any]]:
        """字段分组定义（来自 fields.json），供填写工作台渲染。"""
        if self._groups is None:
            try:
                config = json.loads(
                    (BASE_DIR / "config" / "fields.json").read_text(encoding="utf-8")
                )
                groups: list[dict[str, Any]] = []
                for key, group in (config.get("groups") or {}).items():
                    groups.append(
                        {
                            "key": key,
                            "label": group.get("label", key),
                            "fields": list(group.get("fields") or []),
                        }
                    )
                self._groups = groups
            except Exception:
                self._groups = []
        return self._groups

    def job_fields(self, job: Job) -> dict[str, Any] | None:
        """按分组整理抽取结果，供「AI 建议 + 人工拍板」工作台使用。"""
        if isinstance(job, CompareJob) or job.result is None:
            return None
        raw_fields = job.result.get("fields") or {}
        groups: list[dict[str, Any]] = []
        total = 0
        missing = 0
        for group in self.field_groups():
            items: list[dict[str, Any]] = []
            for key in group["fields"]:
                data = raw_fields.get(key)
                if not data:
                    continue
                item = {
                    "key": data.get("key", key),
                    "label": data.get("label", key),
                    "template": (data.get("template") or "").strip(),
                    "value": data.get("value"),
                    "confidence": data.get("confidence", 0.0),
                    "status": data.get("status", "missing"),
                    "source": data.get("source", ""),
                    "evidence": [
                        {"quote": ev.get("quote", ""), "section": ev.get("section", "")}
                        for ev in (data.get("evidence") or [])
                    ],
                    "errors": data.get("errors") or [],
                    "warnings": data.get("warnings") or [],
                    "notes": data.get("notes") or "",
                }
                items.append(item)
                total += 1
                if item["value"] in (None, ""):
                    missing += 1
            if items:
                groups.append(
                    {"key": group["key"], "label": group["label"], "items": items}
                )
        return {"groups": groups, "total": total, "missing": missing}

    def generate_document(
        self,
        job: Job,
        values: dict[str, Any],
        missing_mode: str = "mark",
    ) -> Path:
        """用用户确认后的值渲染招标文件，返回输出路径。"""
        if job.result_obj is None:
            raise ValueError("该任务没有可用的抽取结果，无法生成招标文件")
        stem = Path(job.filename).stem or "招标文件"
        output_dir = self._output_dir or (Path("data") / "结果审核报告")
        output_dir.mkdir(parents=True, exist_ok=True)
        target = _unique_path(output_dir / f"{stem}-招标文件.docx")
        generated = generate_tender_document(
            job.result_obj,
            output_path=target,
            missing_mode=missing_mode,
            overrides=values,
        )
        return generated.output_path

    # ------------------------------------------------------------------
    # 任务
    # ------------------------------------------------------------------

    def submit(
        self,
        filename: str,
        payload: bytes,
        use_llm: bool,
        allow_non_tender: bool,
    ) -> Job:
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise ValueError(
                f"不支持的文件类型 {suffix or '（无扩展名）'}，仅支持 "
                + "、".join(sorted(ALLOWED_SUFFIXES))
            )
        if not payload:
            raise ValueError("文件内容为空")

        safe_name = Path(filename).name or f"upload{suffix}"
        job = Job(
            id=uuid.uuid4().hex,
            filename=safe_name,
            use_llm=use_llm,
            allow_non_tender=allow_non_tender,
        )
        target = self.tmp_dir / f"{job.id}{suffix}"
        target.write_bytes(payload)

        with self.lock:
            self.jobs[job.id] = job
            self._evict_locked()

        threading.Thread(target=self._run, args=(job, target), daemon=True).start()
        return job

    def submit_compare(
        self,
        tender_filename: str,
        tender_payload: bytes,
        bid_filename: str,
        bid_payload: bytes,
        use_llm: bool,
    ) -> CompareJob:
        tender_suffix = Path(tender_filename).suffix.lower()
        bid_suffix = Path(bid_filename).suffix.lower()
        for label, suffix in (("招标文件", tender_suffix), ("投标文件", bid_suffix)):
            if suffix not in ALLOWED_SUFFIXES:
                raise ValueError(
                    f"{label}类型不支持 {suffix or '（无扩展名）'}，仅支持 "
                    + "、".join(sorted(ALLOWED_SUFFIXES))
                )
        if not tender_payload:
            raise ValueError("招标文件内容为空")
        if not bid_payload:
            raise ValueError("投标文件内容为空")

        job = CompareJob(
            id=uuid.uuid4().hex,
            tender_filename=Path(tender_filename).name or f"tender{tender_suffix}",
            bid_filename=Path(bid_filename).name or f"bid{bid_suffix}",
            use_llm=use_llm,
        )
        tender_target = self.tmp_dir / f"{job.id}-tender{tender_suffix}"
        bid_target = self.tmp_dir / f"{job.id}-bid{bid_suffix}"
        tender_target.write_bytes(tender_payload)
        bid_target.write_bytes(bid_payload)

        with self.lock:
            self.jobs[job.id] = job
            self._evict_locked()

        threading.Thread(
            target=self._run_compare,
            args=(job, tender_target, bid_target),
            daemon=True,
        ).start()
        return job

    def get(self, job_id: str) -> Job | CompareJob | None:
        with self.lock:
            return self.jobs.get(job_id)

    def _evict_locked(self) -> None:
        if len(self.jobs) <= KEEP_JOBS:
            return
        finished = sorted(
            (j for j in self.jobs.values() if j.status in {"done", "skipped", "error"}),
            key=lambda j: j.created_at,
        )
        for old in finished[: max(0, len(self.jobs) - KEEP_JOBS)]:
            self.jobs.pop(old.id, None)

    def _run(self, job: Job, path: Path) -> None:
        job.status = "running"
        job.stage = "解析文档并抽取字段"
        try:
            extractor = Extractor()
            result = extractor.extract(
                path,
                use_llm=job.use_llm,
                allow_non_tender=job.allow_non_tender,
            )
            data = result.to_dict()
            report = render_markdown(result)

            draft: dict[str, Any] | None = None
            if not data.get("meta", {}).get("extraction_skipped"):
                try:
                    draft = to_platform_draft(result)
                except PlatformRecognitionError:
                    draft = None
                except Exception:
                    draft = None

            job.result_obj = result
            job.result = data
            job.report = report
            job.draft = draft
            job.summary = _summarize(data)
            job.json_path, job.md_path, job.draft_path = self._save(
                job.filename, data, report, draft
            )
            job.status = "skipped" if data.get("meta", {}).get("extraction_skipped") else "done"
            job.stage = "已跳过" if job.status == "skipped" else "完成"
        except Exception as exc:
            job.status = "error"
            job.stage = "失败"
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.finished_at = time.time()
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def _run_compare(self, job: CompareJob, tender_path: Path, bid_path: Path) -> None:
        """三阶段：抽招标要求 → 抽投标响应 → 逐条比对。"""
        job.status = "running"
        try:
            from .bid_extract import BidResult, extract_bid_responses
            from .compliance_matcher import match_qualifications, render_compliance_report
            from .document import read_document
            from .relation_extract import RelationResult, extract_relations

            extractor = Extractor()
            settings = extractor.settings
            client = build_llm_client(settings.get("llm", {})) if job.use_llm else None

            tender_doc = read_document(tender_path)
            bid_doc = read_document(bid_path)

            job.stage = "抽取招标文件资格要求"
            relations = (
                extract_relations(client, tender_doc, settings)
                if client is not None
                else RelationResult()
            )

            job.stage = "抽取投标文件响应条目"
            bid_result = (
                extract_bid_responses(client, bid_doc, settings)
                if client is not None
                else BidResult(responses=[], meta={})
            )

            job.stage = "逐条比对"
            results = (
                match_qualifications(
                    client, bid_doc, relations.qualifications, bid_result.responses
                )
                if client is not None
                else []
            )

            payload = {
                "requirements": [item.to_dict() for item in relations.qualifications],
                "responses": [item.to_dict() for item in bid_result.responses],
                "results": [item.to_dict() for item in results],
                "meta": {"relations": relations.meta, "bid": bid_result.meta},
                "report": render_compliance_report(results),
            }
            job.payload = payload
            job.summary = _summarize_compare(payload)
            job.status = "done"
            job.stage = "完成"
        except Exception as exc:
            job.status = "error"
            job.stage = "失败"
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.finished_at = time.time()
            for path in (tender_path, bid_path):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _resolve_output_dir(self) -> Path:
        if self._output_dir is not None:
            return self._output_dir
        configured = (self.settings().get("output") or {}).get("dir")
        if configured:
            return Path(configured)
        return Path.cwd() / "data" / "结果审核报告"

    def _save(
        self,
        filename: str,
        data: dict[str, Any],
        report: str,
        draft: dict[str, Any] | None,
    ) -> tuple[Path, Path, Path | None]:
        stem = Path(filename).stem
        out_dir = self._resolve_output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)

        json_path = _unique_path(out_dir / f"{stem}-结果.json")
        md_path = _unique_path(out_dir / f"{stem}-审核报告.md")
        json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        md_path.write_text(report, encoding="utf-8")

        draft_path: Path | None = None
        if draft is not None:
            draft_path = _unique_path(out_dir / f"{stem}-平台草稿.json")
            draft_path.write_text(
                json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return json_path, md_path, draft_path


class Handler(BaseHTTPRequestHandler):
    service: Service
    server_version = "TenderExtractorWeb/0.1"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    # ------------------------------------------------------------------
    # 路由
    # ------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path in {"/", "/index.html"}:
            return self._serve_index()
        if path in {"/compare", "/compare.html"}:
            return self._serve_static("compare.html")
        if path in {"/fill", "/fill.html"}:
            return self._serve_static("fill.html")
        if path == "/api/health":
            return self._send_json(self.service.health())
        if path.startswith("/api/jobs/"):
            return self._handle_job_get(path)
        self._send_json({"error": "未找到该路径"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/compare":
            return self._handle_compare()
        if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/generate"):
            return self._handle_generate(parsed.path)
        if parsed.path != "/api/extract":
            return self._send_json({"error": "未找到该路径"}, 404)

        query = parse_qs(parsed.query)
        filename = (query.get("filename") or ["upload.docx"])[0].strip()
        use_llm = (query.get("use_llm") or ["1"])[0] not in {"0", "false", "no"}
        allow = (query.get("allow_non_tender") or ["0"])[0] in {"1", "true", "yes"}

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send_json({"error": "Content-Length 无效"}, 400)
        if length <= 0:
            return self._send_json({"error": "请求体为空，未收到文件"}, 400)
        if length > MAX_UPLOAD_BYTES:
            return self._send_json(
                {"error": f"文件超过上限 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB"}, 413
            )

        payload = self.rfile.read(length)
        try:
            job = self.service.submit(filename, payload, use_llm, allow)
        except ValueError as exc:
            return self._send_json({"error": str(exc)}, 400)
        return self._send_json(job.public(), 202)

    # ------------------------------------------------------------------
    # 双文档比对
    # ------------------------------------------------------------------

    def _handle_compare(self) -> None:
        content_type = self.headers.get("Content-Type") or ""
        match = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', content_type)
        if not match:
            return self._send_json({"error": "需要使用 multipart/form-data 上传"}, 400)
        boundary = (match.group(1) or match.group(2) or "").strip()
        if not boundary:
            return self._send_json({"error": "multipart boundary 缺失"}, 400)

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send_json({"error": "Content-Length 无效"}, 400)
        if length <= 0:
            return self._send_json({"error": "请求体为空"}, 400)
        if length > MAX_UPLOAD_BYTES * 2:
            return self._send_json({"error": "两个文件合计超过上限"}, 413)

        body = self.rfile.read(length)
        fields = _parse_multipart(body, boundary)
        tender = fields.get("tender")
        bid = fields.get("bid")
        if not tender or not tender[1]:
            return self._send_json({"error": "缺少招标文件（字段名 tender）"}, 400)
        if not bid or not bid[1]:
            return self._send_json({"error": "缺少投标文件（字段名 bid）"}, 400)

        use_llm = True
        raw_flag = fields.get("use_llm")
        if raw_flag:
            flag = raw_flag[1].decode("utf-8", errors="replace").strip().lower()
            use_llm = flag not in {"0", "false", "no"}

        try:
            job = self.service.submit_compare(
                tender[0] or "tender.docx",
                tender[1],
                bid[0] or "bid.docx",
                bid[1],
                use_llm,
            )
        except ValueError as exc:
            return self._send_json({"error": str(exc)}, 400)
        return self._send_json(job.public(), 202)

    # ------------------------------------------------------------------
    # 静态页与任务接口
    # ------------------------------------------------------------------

    def _serve_index(self) -> None:
        self._serve_static("index.html")

    def _serve_static(self, name: str) -> None:
        target = STATIC_DIR / name
        if not target.is_file():
            return self._send_json({"error": f"缺少前端文件：{target}"}, 500)
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _handle_job_get(self, path: str) -> None:
        parts = [item for item in path.split("/") if item]
        # ["api", "jobs", id, ...]
        if len(parts) < 3:
            return self._send_json({"error": "无效的任务路径"}, 400)
        job = self.service.get(parts[2])
        if job is None:
            return self._send_json({"error": "任务不存在或已过期"}, 404)

        action = parts[3] if len(parts) > 3 else ""
        if action == "":
            return self._send_json(job.public())
        if action == "compare":
            payload = getattr(job, "payload", None)
            if payload is None:
                return self._send_json({"error": "任务尚未完成"}, 409)
            return self._send_json(payload)
        if action == "fields":
            payload = self.service.job_fields(job)
            if payload is None:
                return self._send_json({"error": "任务尚未完成"}, 409)
            return self._send_json(payload)
        if action == "result":
            if isinstance(job, CompareJob) or job.result is None:
                return self._send_json({"error": "任务尚未完成"}, 409)
            return self._send_json(job.result)
        if action == "report":
            if isinstance(job, CompareJob) or not job.report:
                return self._send_json({"error": "任务尚未完成"}, 409)
            return self._send_json({"markdown": job.report})
        if action == "download" and len(parts) > 4:
            if isinstance(job, CompareJob):
                return self._send_json({"error": "该任务不支持下载"}, 404)
            return self._download(job, parts[4])
        self._send_json({"error": "未找到该路径"}, 404)

    def _handle_generate(self, path: str) -> None:
        """接收用户确认的字段值，生成招标文件并直接下载。"""
        parts = [item for item in path.split("/") if item]
        if len(parts) < 4:
            return self._send_json({"error": "无效的任务路径"}, 400)
        job = self.service.get(parts[2])
        if job is None:
            return self._send_json({"error": "任务不存在或已过期"}, 404)
        if isinstance(job, CompareJob):
            return self._send_json({"error": "比对任务不支持生成招标文件"}, 400)

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send_json({"error": "Content-Length 无效"}, 400)
        body: dict[str, Any] = {}
        if length > 0:
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return self._send_json({"error": "请求体不是合法 JSON"}, 400)
        if not isinstance(body, dict):
            return self._send_json({"error": "请求体必须是 JSON 对象"}, 400)

        values = body.get("values") or {}
        if not isinstance(values, dict):
            return self._send_json({"error": "values 必须是对象"}, 400)
        missing_mode = body.get("missing_mode") or "mark"
        if missing_mode not in {"mark", "empty"}:
            return self._send_json({"error": "missing_mode 只能是 mark 或 empty"}, 400)

        try:
            target = self.service.generate_document(job, values, missing_mode)
        except ValueError as exc:
            return self._send_json({"error": str(exc)}, 409)
        except FileNotFoundError as exc:
            return self._send_json({"error": str(exc)}, 500)
        except Exception as exc:  # 渲染异常统一兜底，避免服务线程崩溃
            return self._send_json({"error": f"生成失败：{exc}"}, 500)

        data = target.read_bytes()
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document",
        )
        self.send_header("Content-Length", str(len(data)))
        self.send_header(
            "Content-Disposition",
            "attachment; filename*=UTF-8''" + quote(target.name),
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _download(self, job: Job, kind: str) -> None:
        targets = {
            "json": (job.json_path, "application/json; charset=utf-8"),
            "md": (job.md_path, "text/markdown; charset=utf-8"),
            "draft": (job.draft_path, "application/json; charset=utf-8"),
        }
        path, mime = targets.get(kind, (None, ""))
        if path is None or not path.is_file():
            return self._send_json({"error": "文件不存在"}, 404)

        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header(
            "Content-Disposition",
            "attachment; filename*=UTF-8''" + quote(Path(path).name),
        )
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def make_server(host: str, port: int, service: Service | None = None) -> ThreadingHTTPServer:
    service = service or Service()
    handler = type("BoundHandler", (Handler,), {"service": service})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    return httpd


def serve(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
) -> None:
    try:
        httpd = make_server(host, port)
    except OSError as exc:
        raise SystemExit(
            f"端口 {port} 无法监听：{exc}\n换个端口试试：--port {port + 1}"
        ) from exc

    url = f"http://{host}:{port}/"
    print(f"[web] 已启动：{url}")
    print(f"[web] 双文档比对：{url}compare")
    print("[web] 文档只在本机处理；按 Ctrl+C 停止")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[web] 已停止")
    finally:
        httpd.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tender-extractor-web",
        description="招标需求解析组件的本地网页界面",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口，默认 8765")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    args = parser.parse_args(argv)
    serve(args.host, args.port, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
