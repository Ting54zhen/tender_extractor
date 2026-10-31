"""本地网页界面：上传需求报告，查看结构化字段、复核清单、模板映射与审核报告。

设计约束与组件保持一致：

- 只用标准库（``http.server``），不引入 Web 框架，也不使用 CDN；
- 默认只监听 ``127.0.0.1``，文档不出本机；
- 抽取在后台线程执行，页面轮询状态，避免长请求超时。

启动::

    python -m tender_extractor.web              # 打开浏览器
    python -m tender_extractor.web --port 8766  # 换端口
    python -m tender_extractor.web --no-browser
"""

from __future__ import annotations

import argparse
import json
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

from .llm_client import build_llm_client
from .pipeline import Extractor
from .platform_adapter import PlatformRecognitionError, to_platform_draft
from .report import render_markdown

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
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

    def public(self) -> dict[str, Any]:
        end = self.finished_at or time.time()
        return {
            "id": self.id,
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
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="tender-web-"))
        self._output_dir = output_dir
        self._settings: dict[str, Any] | None = None
        self._health: dict[str, Any] | None = None
        self._health_at = 0.0

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

    def get(self, job_id: str) -> Job | None:
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

    def _resolve_output_dir(self) -> Path:
        if self._output_dir is not None:
            return self._output_dir
        configured = (self.settings().get("output") or {}).get("dir")
        if configured:
            return Path(configured)
        return Path.cwd() / "结果审核报告"

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
        if path == "/api/health":
            return self._send_json(self.service.health())
        if path.startswith("/api/jobs/"):
            return self._handle_job_get(path)
        self._send_json({"error": "未找到该路径"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
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
    # 静态页与任务接口
    # ------------------------------------------------------------------

    def _serve_index(self) -> None:
        index = STATIC_DIR / "index.html"
        if not index.is_file():
            return self._send_json({"error": f"缺少前端文件：{index}"}, 500)
        body = index.read_bytes()
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
        if action == "result":
            if job.result is None:
                return self._send_json({"error": "任务尚未完成"}, 409)
            return self._send_json(job.result)
        if action == "report":
            if not job.report:
                return self._send_json({"error": "任务尚未完成"}, 409)
            return self._send_json({"markdown": job.report})
        if action == "download" and len(parts) > 4:
            return self._download(job, parts[4])
        self._send_json({"error": "未找到该路径"}, 404)

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
