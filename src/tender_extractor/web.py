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
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from .doc_generator import generate_tender_document
from .field_scopes import axis_options, resolve
from .llm_client import build_llm_client
from .models import extraction_result_from_dict
from .paths import app_data_dir, database_path
from .pipeline import Extractor
from .platform_adapter import PlatformRecognitionError, to_platform_draft
from .preview import build_preview
from .report import render_markdown
from .storage import Database
from .template_library import TemplateLibrary

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


_COMPANY_BASIC_FIELDS = (
    "name", "credit_code", "registered_capital", "founded", "address",
    "legal_person", "contact_name", "contact_phone", "business_scope",
)
_QUALIFICATION_FIELDS = ("name", "certificate_no", "issuer", "valid_until", "evidence")
_PERFORMANCE_FIELDS = ("project_name", "amount", "year", "client", "evidence")
_PERSONNEL_FIELDS = ("name", "role", "title", "certificate", "years")


def default_company_profile() -> dict[str, Any]:
    """空企业档案，形状与 CompanyProfile.to_dict() 一致。"""
    return {
        "company": {key: "" for key in _COMPANY_BASIC_FIELDS},
        "qualifications": [],
        "performances": [],
        "personnel": [],
        "statements": {},
        "evidence_index": {},
    }


_METHOD_HINTS: tuple[tuple[str, str], ...] = (
    ("竞争性磋商", "negotiation"),
    ("竞争性谈判", "competitive"),
    ("单一来源", "single"),
    ("询价", "inquiry"),
    ("邀请招标", "invite"),
    ("公开招标", "open"),
)


def _field_text(raw_fields: dict[str, Any], key: str) -> str:
    """抽取结果里取一个字段值；字段缺失或形状异常时返回空串。"""
    item = raw_fields.get(key)
    return str(item.get("value") or "") if isinstance(item, dict) else ""


def _suggested_scope(raw_fields: dict[str, Any]) -> dict[str, str]:
    """从原文推断下拉框初值；业务类型原文通常不写，留空要求用户明确选择。"""
    method_text = _field_text(raw_fields, "procurement_method")
    org_text = _field_text(raw_fields, "tender_org_form")
    method = next((code for word, code in _METHOD_HINTS if word in method_text), "")
    if any(word in org_text for word in ("自行", "自主", "本单位", "自己")):
        org_form = "self"
    elif any(word in org_text for word in ("委托", "代理", "中介")):
        org_form = "entrusted"
    else:
        org_form = ""
    return {"method": method, "category": "", "org_form": org_form}


def _str_map(value: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("条目必须是 JSON 对象")
    out: dict[str, Any] = {}
    for key in fields:
        raw = value.get(key, "")
        if key == "years":
            try:
                out[key] = int(raw or 0)
            except (TypeError, ValueError):
                out[key] = 0
        else:
            out[key] = str(raw or "").strip()
    return out


def normalize_company_profile(data: Any) -> dict[str, Any]:
    """把前端提交的档案归一化成 CompanyProfile 兼容的形状，非法输入直接报错。"""
    if not isinstance(data, dict):
        raise ValueError("企业档案必须是 JSON 对象")
    company = data.get("company")
    if not isinstance(company, dict):
        raise ValueError("company 必须是 JSON 对象")
    out = default_company_profile()
    for key in _COMPANY_BASIC_FIELDS:
        out["company"][key] = str(company.get(key, "") or "").strip()
    for section, fields in (
        ("qualifications", _QUALIFICATION_FIELDS),
        ("performances", _PERFORMANCE_FIELDS),
        ("personnel", _PERSONNEL_FIELDS),
    ):
        rows = data.get(section)
        if not isinstance(rows, list):
            raise ValueError(f"{section} 必须是数组")
        cleaned = [_str_map(row, fields) for row in rows if isinstance(row, dict)]
        # 跳过整行全空的条目
        out[section] = [row for row in cleaned if any(str(v) for v in row.values())]
    for section in ("statements", "evidence_index"):
        mapping = data.get(section)
        if not isinstance(mapping, dict):
            raise ValueError(f"{section} 必须是 JSON 对象")
        out[section] = {str(k): str(v or "") for k, v in mapping.items() if str(k).strip()}
    return out


# LibreOffice 的常见安装位置：Windows 装到 Program Files，macOS 在应用包内，
# Linux 由发行版放到 /usr/bin 或 /usr/lib。macOS 不在 PATH 里，必须按应用包路径找。
_SOFFICE_CANDIDATES = (
    "C:/Program Files/LibreOffice/program/soffice.exe",
    "C:/Program Files (x86)/LibreOffice/program/soffice.exe",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/lib/libreoffice/program/soffice",
    "/usr/bin/libreoffice",
)


def _find_soffice() -> Path | None:
    """找 LibreOffice 的 soffice：系统安装路径优先，其次 PATH。"""
    import shutil

    for candidate in _SOFFICE_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return path
    found = shutil.which("soffice") or shutil.which("libreoffice")
    return Path(found) if found else None


def _docx_to_pdf(docx_path: Path, out_dir: Path) -> tuple[Path | None, str]:
    """docx → PDF。优先 LibreOffice，回退 Word COM（pywin32）。

    返回 (pdf 路径或 None, 使用的转换器说明)。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    soffice = _find_soffice()
    if soffice is not None:
        try:
            subprocess.run(
                [str(soffice), "--headless", "--convert-to", "pdf",
                 "--outdir", str(out_dir), str(docx_path)],
                timeout=180, capture_output=True,
            )
            pdf = out_dir / (docx_path.stem + ".pdf")
            if pdf.is_file():
                return pdf, "libreoffice"
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        import win32com.client  # noqa: F401 - pywin32 未装则跳过

        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False
        doc = word.Documents.Open(str(docx_path.resolve()))
        pdf = out_dir / (docx_path.stem + ".pdf")
        doc.SaveAs(str(pdf.resolve()), FileFormat=17)
        doc.Close(False)
        word.Quit()
        if pdf.is_file():
            return pdf, "word"
    except Exception:  # noqa: BLE001 - 无 pywin32 / 无 Word / 转换失败
        pass
    return None, ""


@dataclass
class Job:
    id: str
    filename: str
    use_llm: bool
    allow_non_tender: bool
    project_id: str | None = None
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
    source_path: Path | None = None  # 上传原文留存路径（投标书生成/重跑用）
    result_obj: Any = None          # ExtractionResult 对象，供生成器使用（不参与序列化）

    def public(self) -> dict[str, Any]:
        end = self.finished_at or time.time()
        return {
            "id": self.id,
            "kind": "extract",
            "project_id": self.project_id,
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
    project_id: str | None = None
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
            "project_id": self.project_id,
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


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并设置：override 有值则覆盖，嵌套 dict 深合并。"""
    merged = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _iso_to_ts(value: str | None) -> float | None:
    """ISO 时间戳转 epoch 秒（用于从 DB 恢复任务后计算 elapsed）。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


# 启动对账按数据库路径只做一次：同一进程里可能对同一个 DB 再建 Service
# （测试的「模拟重启」、未来同进程多入口），第二个实例可能正带着活跃任务，
# 重复对账会把它们误标为中断。不同 DB 各自仍需对账。
_RECONCILE_LOCK = threading.Lock()
_RECONCILED_DBS: set[str] = set()


class Service:
    """任务队列 + 结果缓存 + SQLite 持久化。

    内存 dict 是活跃任务的缓存，SQLite 是事实数据源：
    - 提交任务时写库，运行结束回写结果；
    - 重启后历史任务从库中恢复，仍可查看与生成招标文件。
    """

    def __init__(
        self,
        output_dir: Path | None = None,
        db: Database | None = None,
        templates: TemplateLibrary | None = None,
    ) -> None:
        self.jobs: dict[str, Job | CompareJob] = {}
        self.lock = threading.Lock()
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="tender-web-"))
        self.db = db or Database(database_path())
        self.templates = templates or TemplateLibrary()
        self._output_dir = output_dir
        self._settings: dict[str, Any] | None = None
        self._health: dict[str, Any] | None = None
        self._health_at = 0.0
        self._groups: list[dict[str, Any]] | None = None
        self._interrupt_orphan_jobs()

    def _interrupt_orphan_jobs(self) -> None:
        """把上次进程没跑完的任务标为「已中断」。

        工作线程是 daemon，强制关窗或进程被结束时 ``_run`` 的 finally 不一定执行，
        SQLite 会永久留下 queued/running。首页启动时会扫描进行中任务并自动恢复轮询，
        这些没有线程推进的孤儿任务就表现为「还没上传就在解析」且永不结束。构造
        Service 时内存 jobs 必为空，DB 里仍标进行中的只能属于上一次进程，因此启动
        时做状态对账是唯一能区分「真在跑」与「僵尸任务」的位置。
        """
        key = str(self.db.db_path)
        with _RECONCILE_LOCK:
            if key in _RECONCILED_DBS:
                return
            _RECONCILED_DBS.add(key)
        count = self.db.interrupt_unfinished_jobs(
            stage="已中断",
            error="程序在任务完成前退出，本次启动已将该任务标记为中断，请重新上传文件",
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )
        if count:
            _log(f"[web] 启动时中断 {count} 个未完成任务（进程退出时未回写状态）")

    # ------------------------------------------------------------------
    # 配置与健康检查
    # ------------------------------------------------------------------

    def settings(self) -> dict[str, Any]:
        """出厂 settings.json + 设置页用户覆盖层（存 SQLite）。"""
        if self._settings is None:
            try:
                self._settings = Extractor().settings
            except Exception:
                self._settings = {}
            overrides = self.db.get_setting("settings_override")
            if isinstance(overrides, dict):
                self._settings = _deep_merge(self._settings, overrides)
        return self._settings

    def settings_overrides(self) -> dict[str, Any]:
        stored = self.db.get_setting("settings_override")
        return stored if isinstance(stored, dict) else {}

    def save_settings(self, overrides: dict[str, Any]) -> dict[str, Any]:
        """保存设置页覆盖层，并失效缓存让后续请求立即生效。"""
        if not isinstance(overrides, dict):
            raise ValueError("设置内容必须是 JSON 对象")
        self.db.set_setting("settings_override", overrides)
        self._settings = None
        return self.settings()

    # ------------------------------------------------------------------
    # 企业档案（投标书生成的企业侧素材库）
    # ------------------------------------------------------------------

    def company_profile(self) -> dict[str, Any]:
        stored = self.db.get_company()
        return stored if isinstance(stored, dict) else default_company_profile()

    def save_company_profile(self, data: Any) -> dict[str, Any]:
        profile = normalize_company_profile(data)
        return self.db.save_company(profile)

    def _new_extractor(self) -> Extractor:
        """按当前生效配置构建 Extractor。"""
        return Extractor(settings_override=self.settings_overrides())

    def projects(self) -> Database:
        return self.db

    # ------------------------------------------------------------------
    # 模板库
    # ------------------------------------------------------------------

    def list_templates(self) -> list[dict[str, Any]]:
        """模板列表：内置（只读）+ 用户上传，各自标注是否默认。"""
        return [info.public() for info in self.templates.list()]

    def get_template(self, template_id: str) -> dict[str, Any] | None:
        info = self.templates.get(template_id)
        return info.public() if info else None

    def create_template(
        self, filename: str, payload: bytes, **meta: Any
    ) -> dict[str, Any]:
        return self.templates.create(filename, payload, **meta).public()

    def rename_template(self, template_id: str, name: str) -> dict[str, Any]:
        return self.templates.rename(template_id, name).public()

    def set_default_template(self, template_id: str) -> dict[str, Any]:
        return self.templates.set_default(template_id).public()

    def delete_template(self, template_id: str) -> None:
        self.templates.delete(template_id)

    @staticmethod
    def normalize_custom_fields(raw: Any) -> list[dict[str, Any]]:
        """自定义字段只留白名单键，避免任意结构透传给生成器。"""
        if not isinstance(raw, list):
            return []
        items: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            if not label:
                continue
            target = str(item.get("target") or "append")
            if target not in {"append", "placeholder"}:
                target = "append"
            items.append(
                {
                    "key": str(item.get("key") or ""),
                    "label": label[:60],
                    "value": str(item.get("value") or ""),
                    "target": target,
                    "placeholder": str(item.get("placeholder") or "").strip(),
                }
            )
        return items

    def preview_job(
        self, job: Job | CompareJob, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """按当前填写值生成预览结构（不落盘、不写库）。"""
        if isinstance(job, CompareJob):
            raise ValueError("比对任务不支持预览招标文件")
        template_id = str(payload.get("template_id") or "").strip()
        values = payload.get("values") or {}
        if not isinstance(values, dict):
            raise ValueError("values 必须是对象")
        missing_mode = str(payload.get("missing_mode") or "mark")
        if missing_mode not in {"mark", "empty"}:
            raise ValueError("missing_mode 只能是 mark 或 empty")

        template = self.templates.compiled_path(template_id)
        preview = build_preview(
            template,
            {str(key): value for key, value in values.items()},
            missing_mode,
            self.normalize_custom_fields(payload.get("custom_fields")),
        )
        preview["template"] = self.template_brief(template_id, template)
        return preview

    def template_brief(self, template_id: str, path: Path) -> dict[str, Any]:
        info = self.templates.get(template_id or "") or self.templates.get(
            self.templates.effective_default() or ""
        )
        if info is None:
            return {"id": "", "name": path.stem}
        return {"id": info.id, "name": info.name}

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

    def job_fields(
        self,
        job: Job,
        template_id: str | None = None,
        *,
        method: str = "",
        category: str = "",
        org_form: str = "",
    ) -> dict[str, Any] | None:
        """按分组整理抽取结果，供「AI 建议 + 人工拍板」工作台使用。

        缺失字段会附带 history（历史确认值），这是「字段资产复利」的入口：
        本项目或上一个项目填过的采购人、代理机构、合同份数等，直接提示复用。

        同时回传当前模板的「未匹配占位符」：填写页拿它做自定义字段的
        「写入已有占位符」下拉框，避免用户手敲占位符名。

        ``method/category/org_form`` 由填写工作台的类型选择传入。不带这组参数时
        保持旧行为（只从原文推断组织形式），避免旧页面与既有回归用例的字段集
        在用户还没做选择时就被悄悄改掉。
        """
        if isinstance(job, CompareJob) or job.result is None:
            return None
        raw_fields = job.result.get("fields") or {}
        history = self.db.latest_field_values()

        org_form_value = _field_text(raw_fields, "tender_org_form")
        scope = resolve(
            method,
            category,
            org_form,
            price_method=_field_text(raw_fields, "price_method"),
            agency=_field_text(raw_fields, "agency"),
        )
        suggested = _suggested_scope(raw_fields)

        if scope.complete:
            na_keys = set(scope.na)
            na_tags = dict(scope.na_tags)
            na_reasons = dict(scope.na)
        else:
            # 旧行为：自主（自行）招标时代理机构整组字段不适用
            is_self_org = any(
                word in org_form_value for word in ("自行", "自主", "本单位", "自己")
            )
            na_keys = (
                {"agency", "agency_address", "agency_contact",
                 "agency_project_contact", "agency_project_phone", "agency_fee"}
                if is_self_org else set()
            )
            na_tags = {key: "自主招标" for key in na_keys}
            na_reasons = {
                key: "自主（自行）招标由采购人自行组织，无需填写代理机构信息。"
                for key in na_keys
            }

        groups: list[dict[str, Any]] = []
        total = 0
        missing = 0
        history_count = 0
        na_count = 0
        for group in self.field_groups():
            items: list[dict[str, Any]] = []
            for key in group["fields"]:
                data = raw_fields.get(key)
                if not data:
                    continue
                item = {
                    "key": data.get("key", key),
                    # 标签覆写只改显示名，占位符与字段 key 不变，生成链路不受影响
                    "label": scope.labels.get(key, data.get("label", key)),
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
                    "history": None,
                    "na": key in na_keys,
                    "na_reason": na_reasons.get(key, ""),
                    "na_tag": na_tags.get(key, "不适用"),
                }
                if key in na_keys:
                    na_count += 1
                # 只在字段为空且不适用排除时给历史建议：已有可靠值时不该被旧值干扰
                if item["value"] in (None, "") and key not in na_keys:
                    past = history.get(key)
                    if past and past.get("value") not in (None, ""):
                        item["history"] = {
                            "value": past["value"],
                            "job_id": past.get("job_id", ""),
                            "project_id": past.get("project_id"),
                            "updated_at": past.get("updated_at", ""),
                            "source": past.get("source", ""),
                        }
                        history_count += 1
                items.append(item)
                total += 1
                if key in na_keys:
                    continue  # 不适用字段不算缺失
                if item["value"] in (None, ""):
                    missing += 1
            if items:
                groups.append(
                    {"key": group["key"], "label": group["label"], "items": items}
                )
        info = self.templates.get(template_id or "") or self.templates.get(
            self.templates.effective_default() or ""
        )

        # 只推荐、不切换：模板是用户可能手选过的，静默改掉会让成稿版式出乎意料
        template_hint: dict[str, Any] | None = None
        if scope.complete:
            matches = [
                item for item in self.templates.list()
                if item.category == scope.category and item.method == scope.method
            ]
            template_hint = (
                {"matched": True, "id": matches[0].id, "name": matches[0].name, "note": ""}
                if matches else
                {
                    "matched": False,
                    "id": None,
                    "name": "",
                    "note": "模板库暂无该类型组合的模板，请到「模板库」上传，或继续手动选择模板。",
                }
            )
        return {
            "groups": groups,
            "total": total,
            "missing": missing,
            "history_count": history_count,
            "na_count": na_count,
            "org_form": org_form_value,
            "template": {"id": info.id, "name": info.name} if info else None,
            "template_hint": template_hint,
            "unmapped_placeholders": list(info.unmapped) if info else [],
            "scope": {
                **scope.to_dict(),
                "axes": axis_options(),
                "suggested": suggested,
                "template_hint": template_hint,
            },
        }

    def generate_document(
        self,
        job: Job,
        values: dict[str, Any],
        missing_mode: str = "mark",
        template_id: str = "",
        custom_fields: list[dict[str, Any]] | None = None,
    ) -> Path:
        """用用户确认后的值渲染招标文件，返回输出路径。"""
        if job.result_obj is None and job.result is not None:
            # 从 SQLite 恢复的历史任务：由持久化的抽取结果重建生成器所需对象
            job.result_obj = extraction_result_from_dict(job.result)
        if job.result_obj is None:
            raise ValueError("该任务没有可用的抽取结果，无法生成招标文件")
        stem = Path(job.filename).stem or "招标文件"
        output_dir = self._resolve_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        target = _unique_path(output_dir / f"{stem}-招标文件.docx")
        generated = generate_tender_document(
            job.result_obj,
            template_path=self.templates.compiled_path(template_id),
            output_path=target,
            missing_mode=missing_mode,
            overrides=values,
            custom_fields=custom_fields,
        )
        return generated.output_path

    def generate_bid(self, job: Job) -> dict[str, Any]:
        """基于已有抽取结果 + 企业档案生成投标书初稿。

        关系抽取（资格要求/评分点）按任务原样走模型；原文留存缺失时降级为
        仅项目信息骨架，并在 meta 里说明。
        """
        from .bid_writer import generate_bid_document

        if isinstance(job, CompareJob):
            raise ValueError("比对任务不能生成投标书")
        if job.result is None:
            raise ValueError("该任务还没有抽取结果")
        source = job.source_path
        if not source or not Path(source).is_file():
            raise ValueError(
                "上传原文已不存在，无法抽取资格要求与评分点；请重新上传该招标文件后再试"
            )
        profile_data = self.company_profile()
        profile_file = self.tmp_dir / f"{job.id}-company.json"
        profile_file.write_text(
            json.dumps(profile_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        result = generate_bid_document(
            source,
            profile_file,
            use_llm=job.use_llm,
            fields=job.result.get("fields") or {},
        )
        # 投标书 markdown 落盘，随任务可下载
        stem = Path(job.filename).stem or "投标书"
        out_dir = self._resolve_output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        md_path = _unique_path(out_dir / f"{stem}-投标书.md")
        md_path.write_text(result.markdown, encoding="utf-8")
        payload = result.to_dict()
        payload["md_path"] = str(md_path)
        return payload

    def analyze_job_graph(self, job: Job) -> dict[str, Any]:
        """知识图谱风险分析：字段 + 关系抽取 → 建图 → 三类清单 + Turtle。

        关系抽取依赖模型与原文；模型不可用时图退化为字段节点，
        meta 里带原因，不阻塞返回。
        """
        from .graph_builder import build_graph_from_result, to_turtle
        from .graph_queries import analyze_graph, render_knowledge_report

        if isinstance(job, CompareJob):
            raise ValueError("比对任务暂不支持图谱分析")
        if job.result is None:
            raise ValueError("该任务还没有抽取结果")
        relations_dict: dict[str, Any] = {}
        meta: dict[str, Any] = {}
        if job.use_llm:
            if not job.source_path or not Path(job.source_path).is_file():
                meta["error"] = "上传原文已不存在，图谱只含字段节点（无资格/评分/废标实体）"
            else:
                try:
                    from .document import read_document
                    from .llm_client import build_llm_client
                    from .relation_extract import extract_relations

                    extractor = self._new_extractor()
                    doc = read_document(Path(job.source_path))
                    client = build_llm_client(extractor.settings.get("llm", {}))
                    relations_dict = extract_relations(client, doc, extractor.settings).to_dict()
                    meta["relations"] = relations_dict.get("meta", {})
                except Exception as exc:  # noqa: BLE001 - 关系抽取失败不阻塞字段图
                    meta["error"] = f"{type(exc).__name__}: {exc}"
        else:
            meta["error"] = "该任务是仅规则抽取，无关系实体（开启完整链路重跑可获得）"

        graph = build_graph_from_result(job.result, relations_dict)
        analysis = analyze_graph(graph)
        if graph.fallbacks:
            meta["fallbacks"] = graph.fallbacks
        return {
            "project_name": graph.project_name,
            "analysis": analysis,
            "knowledge_report": render_knowledge_report(graph),
            "turtle": to_turtle(graph),
            "graph": graph.to_dict(),
            "meta": meta,
        }

    def suggest_values(self, job: Job) -> dict[str, Any]:
        """为「原文没有、但模板需要」的字段起草建议值。

        铁律：建议值绝不写入抽取结果（抽取结果只放原文事实），也不参与
        模板回填——只有用户在填写页显式采纳后才生效。每个建议带 reason，
        标注 source=llm_suggest / status=suggested。
        """
        if isinstance(job, CompareJob):
            raise ValueError("比对任务没有可建议的字段")
        if job.result is None:
            raise ValueError("该任务还没有抽取结果")

        fields = job.result.get("fields") or {}
        targets: dict[str, dict[str, Any]] = {}
        for key, data in fields.items():
            if not isinstance(data, dict):
                continue
            if not (data.get("template") or "").strip():
                continue
            if data.get("value") not in (None, ""):
                continue
            targets[key] = data
        if not targets:
            return {"suggestions": {}, "count": 0, "note": "没有需要起草的缺失字段"}
        if not job.use_llm:
            return {
                "suggestions": {},
                "count": 0,
                "note": "该任务是仅规则抽取，没有模型上下文；用完整链路重新解析后可起草",
            }

        known: dict[str, Any] = {}
        for key, data in fields.items():
            if isinstance(data, dict) and data.get("value") not in (None, ""):
                known[data.get("label") or key] = data.get("value")

        extractor = self._new_extractor()
        settings = extractor.settings.get("llm", {})
        try:
            from .llm_client import build_llm_client

            client = build_llm_client(settings)
        except Exception as exc:  # noqa: BLE001
            return {"suggestions": {}, "count": 0, "note": f"模型不可用：{exc}"}

        items = [
            {"key": key, "label": (data.get("label") or key)}
            for key, data in sorted(targets.items())
        ][:20]
        prompt = (
            "你是政府采购招投标顾问。下面是某个采购项目已确认的信息，"
            "以及招标文件模板中需要、但原文没有写的字段。请为每个字段起草一个"
            "贴合该项目的合理建议值（拿不准的写通用合规表述），并给一句话理由。\n"
            "这些是建议值，最终以人工确认为准。\n\n"
            "已知项目信息：" + json.dumps(known, ensure_ascii=False, default=str)[:2000] + "\n"
            "待起草字段：" + json.dumps(items, ensure_ascii=False)
        )
        schema = {
            "type": "object",
            "properties": {
                "suggestions": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "properties": {"value": {"type": "string"}, "reason": {"type": "string"}},
                    },
                }
            },
        }
        try:
            parsed = client.chat_json(
                "你是严谨的招投标文档助手，只输出 JSON。", prompt, schema=schema
            )
        except Exception as exc:  # noqa: BLE001
            return {"suggestions": {}, "count": 0, "note": f"起草失败：{exc}"}

        suggestions: dict[str, Any] = {}
        for key in targets:
            item = (parsed.get("suggestions") or {}).get(key)
            if not isinstance(item, dict):
                continue
            value = str(item.get("value") or "").strip()
            if not value:
                continue
            suggestions[key] = {
                "value": value,
                "reason": str(item.get("reason") or "").strip(),
                "source": "llm_suggest",
                "status": "suggested",
                "label": targets[key].get("label") or key,
            }
        return {"suggestions": suggestions, "count": len(suggestions)}

    def export_job_pdf(self, job: Job) -> Path:
        """生成招标文件 docx 并转成 PDF，返回 PDF 路径。"""
        if isinstance(job, CompareJob):
            raise ValueError("比对任务没有可导出的招标文件")
        docx = self.generate_document(job, {}, "mark")
        pdf, method = _docx_to_pdf(docx, docx.parent)
        if pdf is None:
            raise ValueError(
                "本机没有可用的 docx→PDF 转换器：安装 LibreOffice（推荐）或"
                " pip install pywin32 + 本机 Word 后重试"
            )
        _log(f"[web] PDF 导出完成（{method}）：{pdf.name}")
        return pdf

    def export_project_zip(self, project_id: str) -> tuple[Path, int]:
        """把项目内所有已完成任务的结果打包成 zip，返回 (zip 路径, 文件数)。"""
        import io
        import zipfile

        project = self.db.get_project(project_id)
        if project is None:
            raise ValueError("项目不存在")
        jobs = [row for row in self.db.list_jobs(project_id=project_id)
                if row.get("kind") == "extract" and row.get("result")]
        if not jobs:
            raise ValueError("该项目还没有已完成的解析任务")

        buffer = io.BytesIO()
        count = 0
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for row in jobs:
                stem = Path(row.get("filename") or row["id"]).stem or row["id"]
                safe_stem = "".join(ch if ch not in '\\/:*?"<>|' else "_" for ch in stem)[:60]
                for label, key, mime in (
                    ("结果", "result", ".json"), ("审核报告", "report", ".md")
                ):
                    content = row.get(key)
                    if content:
                        text = content if isinstance(content, str) else json.dumps(
                            content, ensure_ascii=False, indent=2
                        )
                        zf.writestr(f"{safe_stem}-{label}{mime}", text)
                        count += 1
                draft = row.get("draft")
                if draft:
                    zf.writestr(f"{safe_stem}-平台草稿.json",
                                json.dumps(draft, ensure_ascii=False, indent=2))
                    count += 1
                # 生成招标文件 docx（缺失字段标「待补充」）
                job = self.get(row["id"])
                if isinstance(job, Job) and job.result_obj is None and job.result is not None:
                    job.result_obj = extraction_result_from_dict(job.result)
                if isinstance(job, Job) and job.result_obj is not None:
                    try:
                        docx = self.generate_document(job, {}, "mark")
                        zf.write(docx, f"{safe_stem}-招标文件.docx")
                        count += 1
                    except Exception:  # noqa: BLE001 - 单个任务生成失败不阻塞打包
                        pass
        out_dir = self._resolve_output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        name = "".join(ch if ch not in '\\/:*?"<>|' else "_" for ch in project["name"])[:50]
        zip_path = _unique_path(out_dir / f"{name}-导出.zip")
        zip_path.write_bytes(buffer.getvalue())
        return zip_path, count

    def save_confirmed_fields(
        self,
        job: Job,
        values: list[dict[str, Any]],
    ) -> int:
        """保存用户确认后的字段值（字段资产，供后续项目复用）。"""
        if isinstance(job, CompareJob):
            raise ValueError("比对任务没有字段可确认")
        return self.db.save_field_values(job.id, job.project_id, values)

    # ------------------------------------------------------------------
    # 任务
    # ------------------------------------------------------------------

    def submit(
        self,
        filename: str,
        payload: bytes,
        use_llm: bool,
        allow_non_tender: bool,
        project_id: str | None = None,
    ) -> Job:
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise ValueError(
                f"不支持的文件类型 {suffix or '（无扩展名）'}，仅支持 "
                + "、".join(sorted(ALLOWED_SUFFIXES))
            )
        if not payload:
            raise ValueError("文件内容为空")
        if project_id and self.db.get_project(project_id) is None:
            raise ValueError("所属项目不存在")

        safe_name = Path(filename).name or f"upload{suffix}"
        job = Job(
            id=uuid.uuid4().hex,
            filename=safe_name,
            use_llm=use_llm,
            allow_non_tender=allow_non_tender,
            project_id=project_id,
        )
        target = self.tmp_dir / f"{job.id}{suffix}"
        target.write_bytes(payload)
        # 原文留存（临时目录会被清理）：投标书生成、失败重跑都依赖它
        keep_dir = self._resolve_output_dir() / "uploads"
        keep_dir.mkdir(parents=True, exist_ok=True)
        source_path = _unique_path(keep_dir / f"{job.id}_{safe_name}")
        source_path.write_bytes(payload)
        job.source_path = source_path
        self.db.create_job(
            job.id, "extract", safe_name, project_id, use_llm, allow_non_tender,
            source_path=str(source_path),
        )

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
        project_id: str | None = None,
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
        if project_id and self.db.get_project(project_id) is None:
            raise ValueError("所属项目不存在")

        safe_tender = Path(tender_filename).name or f"tender{tender_suffix}"
        safe_bid = Path(bid_filename).name or f"bid{bid_suffix}"
        job = CompareJob(
            id=uuid.uuid4().hex,
            tender_filename=safe_tender,
            bid_filename=safe_bid,
            use_llm=use_llm,
            project_id=project_id,
        )
        tender_target = self.tmp_dir / f"{job.id}-tender{tender_suffix}"
        bid_target = self.tmp_dir / f"{job.id}-bid{bid_suffix}"
        tender_target.write_bytes(tender_payload)
        bid_target.write_bytes(bid_payload)
        self.db.create_job(
            job.id,
            "compare",
            f"{safe_tender} vs {safe_bid}",
            project_id,
            use_llm,
            False,
            tender_filename=safe_tender,
            bid_filename=safe_bid,
        )

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
            job = self.jobs.get(job_id)
        if job is not None:
            return job
        # 内存缓存未命中（重启后首次访问）→ 从 SQLite 恢复
        row = self.db.get_job(job_id)
        if row is None:
            return None
        restored = self._job_from_row(row)
        if restored is None:
            return None
        with self.lock:
            self.jobs[job_id] = restored
            self._evict_locked()
        return restored

    def _job_from_row(self, row: dict[str, Any]) -> Job | CompareJob | None:
        """把 DB 行还原为内存任务对象。"""
        created = _iso_to_ts(row.get("created_at")) or time.time()
        finished = _iso_to_ts(row.get("finished_at"))
        if row.get("kind") == "compare":
            return CompareJob(
                id=row["id"],
                tender_filename=row.get("tender_filename") or "",
                bid_filename=row.get("bid_filename") or "",
                use_llm=bool(row.get("use_llm")),
                project_id=row.get("project_id"),
                status=row.get("status", "done"),
                stage=row.get("stage", ""),
                error=row.get("error", ""),
                created_at=created,
                finished_at=finished,
                payload=row.get("result"),
                summary=row.get("summary") or {},
            )
        if row.get("kind") == "extract":
            return Job(
                id=row["id"],
                filename=row.get("filename") or "",
                use_llm=bool(row.get("use_llm")),
                allow_non_tender=bool(row.get("allow_non_tender")),
                project_id=row.get("project_id"),
                status=row.get("status", "done"),
                stage=row.get("stage", ""),
                error=row.get("error", ""),
                created_at=created,
                finished_at=finished,
                result=row.get("result"),
                report=row.get("report") or "",
                draft=row.get("draft"),
                summary=row.get("summary") or {},
                json_path=Path(row["json_path"]) if row.get("json_path") else None,
                md_path=Path(row["md_path"]) if row.get("md_path") else None,
                draft_path=Path(row["draft_path"]) if row.get("draft_path") else None,
                source_path=Path(row["source_path"]) if row.get("source_path") else None,
            )
        return None

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
        self.db.update_job(job.id, status="running", stage=job.stage)
        try:
            extractor = self._new_extractor()
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
            self.db.update_job(
                job.id,
                status=job.status,
                stage=job.stage,
                error=job.error,
                summary=job.summary,
                result=job.result,
                report=job.report,
                draft=job.draft,
                json_path=str(job.json_path) if job.json_path else "",
                md_path=str(job.md_path) if job.md_path else "",
                draft_path=str(job.draft_path) if job.draft_path else "",
                finished_at=datetime.now().isoformat(timespec="seconds"),
            )
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def _run_compare(self, job: CompareJob, tender_path: Path, bid_path: Path) -> None:
        """三阶段：抽招标要求 → 抽投标响应 → 逐条比对。"""
        job.status = "running"
        self.db.update_job(job.id, status="running", stage=job.stage)
        try:
            from .bid_extract import BidResult, extract_bid_responses
            from .compliance_matcher import match_qualifications, render_compliance_report
            from .document import read_document
            from .relation_extract import RelationResult, extract_relations

            extractor = self._new_extractor()
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
            self.db.update_job(
                job.id,
                status=job.status,
                stage=job.stage,
                error=job.error,
                summary=job.summary,
                result=job.payload,
                finished_at=datetime.now().isoformat(timespec="seconds"),
            )
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
        return app_data_dir() / "结果审核报告"

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
            return self._serve_static("index.html")
        if path in {"/compare", "/compare.html"}:
            return self._serve_static("compare.html")
        if path in {"/fill", "/fill.html"}:
            return self._serve_static("fill.html")
        if path in {"/projects", "/projects.html"}:
            return self._serve_static("projects.html")
        if path in {"/settings", "/settings.html"}:
            return self._serve_static("settings.html")
        if path in {"/company", "/company.html"}:
            return self._serve_static("company.html")
        if path in {"/templates", "/templates.html"}:
            return self._serve_static("templates.html")
        if path.startswith("/assets/"):
            return self._serve_static(path[len("/assets/"):])
        if path.startswith("/api/templates"):
            return self._handle_template_get(path)
        if path == "/api/health":
            return self._send_json(self.service.health())
        if path == "/api/company":
            return self._send_json({"profile": self.service.company_profile()})
        if path == "/api/settings":
            return self._send_json(
                {
                    "settings": self.service.settings(),
                    "overrides": self.service.settings_overrides(),
                }
            )
        if path == "/api/projects":
            status = (parse_qs(parsed.query).get("status") or [None])[0]
            return self._send_json({"projects": self.service.db.list_projects(status)})
        if path == "/api/jobs":
            query = parse_qs(parsed.query)
            project_id = (query.get("project_id") or [None])[0] or None
            return self._send_json(
                {"jobs": self.service.db.list_jobs(project_id=project_id)}
            )
        if path.startswith("/api/projects/"):
            return self._handle_project_get(parsed.path)
        if path.startswith("/api/jobs/"):
            return self._handle_job_get(path, parse_qs(parsed.query))
        self._send_json({"error": "未找到该路径"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/templates":
            return self._handle_template_create(parse_qs(parsed.query))
        if re.fullmatch(r"/api/templates/[^/]+/(rename|default)", parsed.path):
            return self._handle_template_action(parsed.path)
        if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/preview"):
            return self._handle_preview(parsed.path)
        if parsed.path == "/api/compare":
            return self._handle_compare()
        if parsed.path == "/api/projects":
            return self._handle_project_create()
        if re.fullmatch(r"/api/projects/[^/]+/export", parsed.path):
            return self._handle_project_export(parsed.path)
        if parsed.path.startswith("/api/projects/"):
            return self._handle_project_action(parsed.path)
        if parsed.path == "/api/settings":
            return self._handle_settings_save()
        if parsed.path == "/api/company":
            return self._handle_company_save()
        if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/generate"):
            return self._handle_generate(parsed.path)
        if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/confirm"):
            return self._handle_confirm(parsed.path)
        if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/bid"):
            return self._handle_bid(parsed.path)
        if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/analyze"):
            return self._handle_analyze(parsed.path)
        if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/suggest"):
            return self._handle_suggest(parsed.path)
        if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/export/pdf"):
            return self._handle_export_pdf(parsed.path)
        if parsed.path != "/api/extract":
            return self._send_json({"error": "未找到该路径"}, 404)

        query = parse_qs(parsed.query)
        filename = (query.get("filename") or ["upload.docx"])[0].strip()
        use_llm = (query.get("use_llm") or ["1"])[0] not in {"0", "false", "no"}
        allow = (query.get("allow_non_tender") or ["0"])[0] in {"1", "true", "yes"}
        project_id = (query.get("project_id") or [None])[0] or None

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
            job = self.service.submit(filename, payload, use_llm, allow, project_id)
        except ValueError as exc:
            return self._send_json({"error": str(exc)}, 400)
        return self._send_json(job.public(), 202)

    # ------------------------------------------------------------------
    # 项目工作台
    # ------------------------------------------------------------------

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if re.fullmatch(r"/api/templates/[^/]+", parsed.path):
            return self._handle_template_delete(parsed.path)
        return self._send_json({"error": "未找到该路径"}, 404)

    # ------------------------------------------------------------------
    # 模板库
    # ------------------------------------------------------------------

    def _handle_template_get(self, path: str) -> None:
        """GET /api/templates、/api/templates/{id}、/api/templates/{id}/preview。"""
        parts = [item for item in path.split("/") if item]
        if len(parts) == 2:  # ["api", "templates"]
            return self._send_json({"templates": self.service.list_templates()})
        if len(parts) < 3:
            return self._send_json({"error": "无效的模板路径"}, 400)

        template_id = parts[2]
        if len(parts) == 3:
            info = self.service.get_template(template_id)
            if info is None:
                return self._send_json({"error": "模板不存在"}, 404)
            return self._send_json(info)
        if parts[3] == "preview":
            return self._handle_template_preview(template_id)
        return self._send_json({"error": "未找到该路径"}, 404)

    def _handle_template_preview(self, template_id: str) -> None:
        """空值预览：模板本身长什么样、哪些占位符没对应字段。"""
        try:
            compiled = self.service.templates.compiled_path(template_id)
            preview = build_preview(compiled, {}, "mark", None)
        except FileNotFoundError as exc:
            return self._send_json({"error": str(exc)}, 404)
        except Exception as exc:  # noqa: BLE001 - 模板损坏统一兜底
            return self._send_json({"error": f"预览失败：{exc}"}, 500)
        preview["template"] = self.service.template_brief(template_id, compiled)
        return self._send_json(preview)

    def _handle_template_create(self, query: dict[str, list[str]]) -> None:
        """上传模板：body 为 .docx 二进制，与 /api/extract 同风格。"""
        filename = (query.get("filename") or [""])[0].strip()
        meta = {
            "name": (query.get("name") or [""])[0].strip(),
            "category": (query.get("category") or ["other"])[0].strip(),
            "method": (query.get("method") or ["other"])[0].strip(),
            "version": (query.get("version") or ["1.0"])[0].strip(),
        }
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send_json({"error": "Content-Length 无效"}, 400)
        if length <= 0:
            return self._send_json({"error": "请求体为空，未收到模板文件"}, 400)
        if length > MAX_UPLOAD_BYTES:
            return self._send_json(
                {"error": f"文件超过上限 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB"}, 413
            )

        payload = self.rfile.read(length)
        try:
            info = self.service.create_template(filename, payload, **meta)
        except ValueError as exc:
            return self._send_json({"error": str(exc)}, 400)
        return self._send_json(info, 201)

    def _handle_template_action(self, path: str) -> None:
        parts = [item for item in path.split("/") if item]
        if len(parts) != 4:
            return self._send_json({"error": "无效的模板路径"}, 400)
        template_id, action = parts[2], parts[3]
        body = self._read_json_body()
        if body is None:
            return self._send_json({"error": "请求体不是合法 JSON 对象"}, 400)
        try:
            if action == "rename":
                info = self.service.rename_template(
                    template_id, str(body.get("name") or "")
                )
            else:
                info = self.service.set_default_template(template_id)
        except KeyError:
            return self._send_json({"error": "模板不存在"}, 404)
        except ValueError as exc:
            return self._send_json({"error": str(exc)}, 400)
        return self._send_json(info)

    def _handle_template_delete(self, path: str) -> None:
        self._drain_body()
        parts = [item for item in path.split("/") if item]
        if len(parts) != 3:
            return self._send_json({"error": "无效的模板路径"}, 400)
        try:
            self.service.delete_template(parts[2])
        except KeyError:
            return self._send_json({"error": "模板不存在"}, 404)
        except ValueError as exc:
            return self._send_json({"error": str(exc)}, 400)
        return self._send_json({"ok": True})

    def _handle_preview(self, path: str) -> None:
        """按当前填写值预览成稿（不落盘）。"""
        parts = [item for item in path.split("/") if item]
        if len(parts) < 4:
            return self._send_json({"error": "无效的任务路径"}, 400)
        job = self.service.get(parts[2])
        if job is None:
            return self._send_json({"error": "任务不存在或已过期"}, 404)
        body = self._read_json_body()
        if body is None:
            return self._send_json({"error": "请求体不是合法 JSON 对象"}, 400)
        try:
            payload = self.service.preview_job(job, body)
        except ValueError as exc:
            return self._send_json({"error": str(exc)}, 400)
        except FileNotFoundError as exc:
            return self._send_json({"error": str(exc)}, 500)
        except Exception as exc:  # noqa: BLE001 - 模板损坏统一兜底
            return self._send_json({"error": f"预览失败：{exc}"}, 500)
        return self._send_json(payload)

    def _read_json_body(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return body if isinstance(body, dict) else None

    def _drain_body(self) -> None:
        """读掉未消费的请求体。

        带请求体的 POST 若未读 body 就返回/关连接，Windows 回环下会把
        未读数据触发成 RST，影响同 server 的后续连接（实测 409 分支引发
        下一个请求 ConnectionReset）。
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > 0:
            try:
                self.rfile.read(length)
            except OSError:
                pass

    def _handle_project_create(self) -> None:
        body = self._read_json_body()
        if body is None:
            return self._send_json({"error": "请求体不是合法 JSON 对象"}, 400)
        try:
            project = self.service.db.create_project(
                str(body.get("name") or ""), str(body.get("note") or "")
            )
        except ValueError as exc:
            return self._send_json({"error": str(exc)}, 400)
        return self._send_json(project, 201)

    def _handle_project_get(self, path: str) -> None:
        parts = [item for item in path.split("/") if item]
        if len(parts) != 3:  # ["api", "projects", id]
            return self._send_json({"error": "无效的项目路径"}, 400)
        project = self.service.db.get_project(parts[2])
        if project is None:
            return self._send_json({"error": "项目不存在"}, 404)
        project["jobs"] = self.service.db.list_jobs(project_id=parts[2])
        return self._send_json(project)

    def _handle_project_action(self, path: str) -> None:
        parts = [item for item in path.split("/") if item]
        if len(parts) != 4:  # ["api", "projects", id, action]
            return self._send_json({"error": "无效的项目路径"}, 400)
        project_id, action = parts[2], parts[3]
        db = self.service.db

        if action == "delete":
            if not db.delete_project(project_id):
                return self._send_json({"error": "项目不存在"}, 404)
            return self._send_json({"ok": True})
        if action not in {"archive", "restore", "rename"}:
            return self._send_json({"error": "未找到该路径"}, 404)

        body: dict[str, Any] = {}
        if action == "rename":
            body = self._read_json_body() or {}
            if not body:
                return self._send_json({"error": "缺少新名称"}, 400)
        if action == "archive":
            project = db.update_project(project_id, status="archived")
        elif action == "restore":
            project = db.update_project(project_id, status="active")
        else:  # rename
            project = db.update_project(project_id, name=str(body.get("name") or ""))
        if project is None:
            return self._send_json({"error": "项目不存在"}, 404)
        return self._send_json(project)

    # ------------------------------------------------------------------
    # 设置页
    # ------------------------------------------------------------------

    def _handle_company_save(self) -> None:
        body = self._read_json_body()
        if body is None:
            return self._send_json({"error": "请求体不是合法 JSON 对象"}, 400)
        try:
            saved = self.service.save_company_profile(body)
        except ValueError as exc:
            return self._send_json({"error": str(exc)}, 400)
        return self._send_json({"profile": saved})

    def _handle_settings_save(self) -> None:
        body = self._read_json_body()
        if body is None:
            return self._send_json({"error": "请求体不是合法 JSON 对象"}, 400)
        if body.get("reset"):
            try:
                merged = self.service.save_settings({})
            except ValueError as exc:
                return self._send_json({"error": str(exc)}, 400)
            return self._send_json({"settings": merged, "overrides": {}})
        allowed_sections = {"llm", "review", "extraction", "output"}
        overrides = self.service.settings_overrides()
        for section, value in (body.get("settings") or {}).items():
            if section not in allowed_sections:
                return self._send_json({"error": f"不支持的设置段：{section}"}, 400)
            overrides[section] = value
        try:
            merged = self.service.save_settings(overrides)
        except ValueError as exc:
            return self._send_json({"error": str(exc)}, 400)
        return self._send_json({"settings": merged, "overrides": overrides})

    # ------------------------------------------------------------------
    # 字段确认值
    # ------------------------------------------------------------------

    def _handle_confirm(self, path: str) -> None:
        parts = [item for item in path.split("/") if item]
        if len(parts) < 4:
            return self._send_json({"error": "无效的任务路径"}, 400)
        job = self.service.get(parts[2])
        if job is None:
            return self._send_json({"error": "任务不存在或已过期"}, 404)
        body = self._read_json_body()
        if body is None:
            return self._send_json({"error": "请求体不是合法 JSON 对象"}, 400)
        values = body.get("values")
        if not isinstance(values, list):
            return self._send_json({"error": "values 必须是数组"}, 400)
        try:
            saved = self.service.save_confirmed_fields(job, values)
        except ValueError as exc:
            return self._send_json({"error": str(exc)}, 400)
        return self._send_json({"ok": True, "saved": saved})

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

    MIME_TYPES = {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".ico": "image/x-icon",
    }

    def _serve_static(self, name: str) -> None:
        target = (STATIC_DIR / name).resolve()
        # 只允许静态目录内的文件，防目录穿越
        if not str(target).startswith(str(STATIC_DIR.resolve())):
            return self._send_json({"error": "非法路径"}, 403)
        if not target.is_file():
            return self._send_json({"error": f"缺少前端文件：{target}"}, 500)
        body = target.read_bytes()
        mime = self.MIME_TYPES.get(target.suffix.lower(), "text/html; charset=utf-8")
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _handle_job_get(
        self, path: str, query: dict[str, list[str]] | None = None
    ) -> None:
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
            query = query or {}
            template_id = (query.get("template_id") or [None])[0]
            scope_args = {
                axis: (query.get(axis) or [""])[0]
                for axis in ("method", "category", "org_form")
            }
            try:
                payload = self.service.job_fields(job, template_id, **scope_args)
            except ValueError as exc:
                # 类型取值非法直接 400，别把配置校验错误变成 500 或静默按默认值处理
                return self._send_json({"error": str(exc)}, 400)
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

    def _send_binary(self, path: Path, mime: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header(
            "Content-Disposition",
            "attachment; filename*=UTF-8''" + quote(path.name),
        )
        self.end_headers()
        # Windows 回环下一次性写大响应偶发 RST，分块写稳妥
        chunk = 64 * 1024
        for start in range(0, len(data), chunk):
            self.wfile.write(data[start:start + chunk])
        self.wfile.flush()

    def _handle_export_pdf(self, path: str) -> None:
        """招标文件 → PDF（LibreOffice / Word COM）。"""
        parts = [item for item in path.split("/") if item]
        if len(parts) < 4:
            return self._send_json({"error": "无效的任务路径"}, 400)
        job = self.service.get(parts[2])
        if job is None:
            return self._send_json({"error": "任务不存在或已过期"}, 404)
        try:
            pdf = self.service.export_job_pdf(job)
        except ValueError as exc:
            return self._send_json({"error": str(exc)}, 409)
        except Exception as exc:  # noqa: BLE001
            return self._send_json({"error": f"PDF 导出失败：{exc}"}, 500)
        return self._send_binary(pdf, "application/pdf")

    def _handle_project_export(self, path: str) -> None:
        """项目内所有已完成任务打包 zip 下载。"""
        self._drain_body()
        parts = [item for item in path.split("/") if item]
        if len(parts) != 4:
            return self._send_json({"error": "无效的项目路径"}, 400)
        try:
            zip_path, count = self.service.export_project_zip(parts[2])
        except ValueError as exc:
            return self._send_json({"error": str(exc)}, 409)
        except Exception as exc:  # noqa: BLE001
            return self._send_json({"error": f"打包失败：{exc}"}, 500)
        _log(f"[web] 项目导出：{zip_path.name}（{count} 个文件）")
        return self._send_binary(zip_path, "application/zip")

    def _handle_suggest(self, path: str) -> None:
        """为缺失字段起草 AI 建议值（suggested，需人工采纳）。"""
        parts = [item for item in path.split("/") if item]
        if len(parts) < 4:
            return self._send_json({"error": "无效的任务路径"}, 400)
        job = self.service.get(parts[2])
        if job is None:
            return self._send_json({"error": "任务不存在或已过期"}, 404)
        try:
            data = self.service.suggest_values(job)
        except ValueError as exc:
            return self._send_json({"error": str(exc)}, 409)
        except Exception as exc:  # noqa: BLE001
            return self._send_json({"error": f"建议起草失败：{exc}"}, 500)
        return self._send_json(data)

    def _handle_analyze(self, path: str) -> None:
        """知识图谱风险分析：废标预警 / 符合性自查 / 评分点材料映射。"""
        parts = [item for item in path.split("/") if item]
        if len(parts) < 4:
            return self._send_json({"error": "无效的任务路径"}, 400)
        job = self.service.get(parts[2])
        if job is None:
            return self._send_json({"error": "任务不存在或已过期"}, 404)
        try:
            data = self.service.analyze_job_graph(job)
        except ValueError as exc:
            return self._send_json({"error": str(exc)}, 409)
        except Exception as exc:  # noqa: BLE001
            return self._send_json({"error": f"图谱分析失败：{exc}"}, 500)
        return self._send_json(data)

    def _handle_bid(self, path: str) -> None:
        """基于该任务的抽取结果与企业档案，生成投标书初稿。"""
        parts = [item for item in path.split("/") if item]
        if len(parts) < 4:
            return self._send_json({"error": "无效的任务路径"}, 400)
        job = self.service.get(parts[2])
        if job is None:
            return self._send_json({"error": "任务不存在或已过期"}, 404)
        try:
            data = self.service.generate_bid(job)
        except ValueError as exc:
            return self._send_json({"error": str(exc)}, 409)
        except Exception as exc:  # noqa: BLE001 - 模型/文档异常统一兜底
            return self._send_json({"error": f"投标书生成失败：{exc}"}, 500)
        return self._send_json(data)

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
        template_id = str(body.get("template_id") or "").strip()
        custom_fields = self.service.normalize_custom_fields(body.get("custom_fields"))

        try:
            target = self.service.generate_document(
                job, values, missing_mode, template_id, custom_fields
            )
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


def _log(message: str) -> None:
    """同时输出到控制台与 data/web.log（打包后双击运行便于事后排障）。"""
    print(message, flush=True)
    try:
        log_path = app_data_dir() / "web.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass


def serve(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
) -> None:
    _log(f"[web] 启动中（frozen={getattr(sys, 'frozen', False)}）…")
    try:
        httpd = make_server(host, port)
    except OSError as exc:
        _log(f"[web] 端口 {port} 无法监听：{exc}")
        raise SystemExit(
            f"端口 {port} 无法监听：{exc}\n换个端口试试：--port {port + 1}"
        ) from exc

    url = f"http://{host}:{port}/"
    _log(f"[web] 已启动：{url}")
    _log(f"[web] 项目工作台：{url}projects")
    _log(f"[web] 双文档比对：{url}compare")
    _log(f"[web] 可视化设置：{url}settings")
    _log(f"[web] 数据目录：{app_data_dir()}（任务与字段确认值持久化于此）")
    _log("[web] 文档只在本机处理；按 Ctrl+C 停止")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        _log("[web] 已停止")
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
