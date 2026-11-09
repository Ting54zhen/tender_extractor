"""SQLite 持久化层：projects / jobs / field_values / settings。

这是「从脚本到软件」的分界线：任务、字段确认值、项目归属全部落盘，
重启后历史任务仍可打开，确认过的字段成为可复用的数据资产。

设计约定（为将来迁移 PostgreSQL 留余地）：

- 时间戳统一存 ISO8601 字符串；
- 复杂结构（summary / result / draft）序列化为 JSON 文本列；
- 布尔用 0/1 整数；
- 每次操作使用短连接 + WAL 模式，天然适配 ThreadingHTTPServer 的多线程；
- 主键用 uuid hex，不依赖自增 ID，跨库迁移无冲突。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active',   -- active | archived
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id               TEXT PRIMARY KEY,
    project_id       TEXT,
    kind             TEXT NOT NULL DEFAULT 'extract',  -- extract | compare
    filename         TEXT NOT NULL,
    tender_filename  TEXT NOT NULL DEFAULT '',
    bid_filename     TEXT NOT NULL DEFAULT '',
    use_llm          INTEGER NOT NULL DEFAULT 1,
    allow_non_tender INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'queued',
    stage            TEXT NOT NULL DEFAULT '排队中',
    error            TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    finished_at      TEXT,
    summary_json     TEXT NOT NULL DEFAULT '{}',
    result_json      TEXT,
    report_md        TEXT,
    draft_json       TEXT,
    json_path        TEXT NOT NULL DEFAULT '',
    md_path          TEXT NOT NULL DEFAULT '',
    draft_path       TEXT NOT NULL DEFAULT '',
    source_path      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id, created_at);

CREATE TABLE IF NOT EXISTS field_values (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    project_id  TEXT,
    field_key   TEXT NOT NULL,
    field_label TEXT NOT NULL DEFAULT '',
    template    TEXT NOT NULL DEFAULT '',
    value       TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT '',
    confirmed   INTEGER NOT NULL DEFAULT 1,
    updated_at  TEXT NOT NULL,
    UNIQUE(job_id, field_key)
);

CREATE INDEX IF NOT EXISTS idx_field_values_project
    ON field_values(project_id, field_key);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS company (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    data_json  TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Database:
    """SQLite 封装：projects / jobs / field_values / settings 四张表。

    每个操作打开独立短连接（WAL），无共享连接句柄，多线程安全。
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._write_lock:
            conn = self._connect()
            try:
                conn.execute("PRAGMA journal_mode = WAL")
                conn.executescript(_SCHEMA)
                # 旧库迁移：jobs.source_path（上传原文留存，投标书生成用）
                cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
                if "source_path" not in cols:
                    conn.execute("ALTER TABLE jobs ADD COLUMN source_path TEXT NOT NULL DEFAULT ''")
                conn.commit()
            finally:
                conn.close()

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        conn = self._connect()
        try:
            return list(conn.execute(sql, params))
        finally:
            conn.close()

    def _write(self, sql: str, params: tuple = ()) -> None:
        with self._write_lock:
            conn = self._connect()
            try:
                conn.execute(sql, params)
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # 项目
    # ------------------------------------------------------------------

    def create_project(self, name: str, note: str = "") -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise ValueError("项目名称不能为空")
        pid = uuid.uuid4().hex
        now = _now()
        self._write(
            "INSERT INTO projects (id, name, status, note, created_at, updated_at)"
            " VALUES (?, ?, 'active', ?, ?, ?)",
            (pid, name, note.strip(), now, now),
        )
        return self.get_project(pid) or {}

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM projects WHERE id = ?", (project_id,))
        return self._project_row(rows[0]) if rows else None

    def list_projects(self, status: str | None = None) -> list[dict[str, Any]]:
        """项目列表，附带任务数与最近一次任务时间。"""
        where = "WHERE p.status = ?" if status else ""
        params: tuple = (status,) if status else ()
        rows = self._query(
            "SELECT p.*,"
            " (SELECT COUNT(*) FROM jobs j WHERE j.project_id = p.id) AS job_count,"
            " (SELECT MAX(j.created_at) FROM jobs j WHERE j.project_id = p.id)"
            "   AS last_job_at"
            f" FROM projects p {where} ORDER BY p.updated_at DESC",
            params,
        )
        return [self._project_row(row) for row in rows]

    def update_project(
        self,
        project_id: str,
        name: str | None = None,
        note: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any] | None:
        if self.get_project(project_id) is None:
            return None
        sets: list[str] = ["updated_at = ?"]
        params: list[Any] = [_now()]
        if name is not None and name.strip():
            sets.append("name = ?")
            params.append(name.strip())
        if note is not None:
            sets.append("note = ?")
            params.append(note.strip())
        if status in {"active", "archived"}:
            sets.append("status = ?")
            params.append(status)
            sets.append("archived_at = ?")
            params.append(_now() if status == "archived" else None)
        params.append(project_id)
        self._write(f"UPDATE projects SET {', '.join(sets)} WHERE id = ?", tuple(params))
        return self.get_project(project_id)

    def delete_project(self, project_id: str) -> bool:
        if self.get_project(project_id) is None:
            return False
        with self._write_lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM field_values WHERE project_id = ?", (project_id,))
                conn.execute("DELETE FROM jobs WHERE project_id = ?", (project_id,))
                conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
                conn.commit()
            finally:
                conn.close()
        return True

    @staticmethod
    def _project_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["job_count"] = int(data.pop("job_count", 0) or 0)
        data["last_job_at"] = data.pop("last_job_at", None) or None
        return data

    # ------------------------------------------------------------------
    # 任务
    # ------------------------------------------------------------------

    def create_job(
        self,
        job_id: str,
        kind: str,
        filename: str,
        project_id: str | None,
        use_llm: bool,
        allow_non_tender: bool,
        tender_filename: str = "",
        bid_filename: str = "",
        status: str = "queued",
        stage: str = "排队中",
        source_path: str = "",
    ) -> None:
        self._write(
            "INSERT INTO jobs (id, project_id, kind, filename, tender_filename,"
            " bid_filename, use_llm, allow_non_tender, status, stage, created_at, source_path)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id,
                project_id,
                kind,
                filename,
                tender_filename,
                bid_filename,
                1 if use_llm else 0,
                1 if allow_non_tender else 0,
                status,
                stage,
                _now(),
                source_path,
            ),
        )

    def update_job(self, job_id: str, **fields: Any) -> None:
        """更新任务字段。JSON 型字段（summary/result/draft）传 dict 自动序列化。"""
        if not fields:
            return
        column_map = {
            "status": "status",
            "stage": "stage",
            "error": "error",
            "finished_at": "finished_at",
            "json_path": "json_path",
            "md_path": "md_path",
            "draft_path": "draft_path",
            "source_path": "source_path",
            "result": "result_json",
            "report": "report_md",
            "draft": "draft_json",
            "summary": "summary_json",
        }
        json_columns = {"result", "draft", "summary"}
        sets: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            column = column_map.get(key)
            if column is None:
                raise ValueError(f"不支持更新任务字段：{key}")
            if key in json_columns:
                value = json.dumps(value, ensure_ascii=False) if value is not None else None
            sets.append(f"{column} = ?")
            params.append(value)
        params.append(job_id)
        self._write(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", tuple(params))

    def interrupt_unfinished_jobs(self, stage: str, error: str, finished_at: str) -> int:
        """把没有执行线程的 queued/running 任务一次性标为中断，返回条数。

        启动对账要覆盖全部历史任务：逐条 SELECT 会受 list_jobs 分页上限影响，
        也没必要把整表读进内存，用一条带状态条件的 UPDATE 最准确。
        """
        with self._write_lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "UPDATE jobs SET status = 'error', stage = ?, error = ?,"
                    " finished_at = ? WHERE status IN ('queued', 'running')",
                    (stage, error, finished_at),
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM jobs WHERE id = ?", (job_id,))
        return self._job_row(rows[0]) if rows else None

    def list_jobs(
        self,
        project_id: str | None = None,
        kind: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM jobs"
        clauses: list[str] = []
        params: list[Any] = []
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return [self._job_row(row) for row in self._query(sql, tuple(params))]

    def delete_job(self, job_id: str) -> bool:
        if self.get_job(job_id) is None:
            return False
        with self._write_lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM field_values WHERE job_id = ?", (job_id,))
                conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
                conn.commit()
            finally:
                conn.close()
        return True

    @staticmethod
    def _job_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["use_llm"] = bool(data["use_llm"])
        data["allow_non_tender"] = bool(data["allow_non_tender"])
        for key in ("summary", "result", "draft"):
            column = f"{key}_json"
            raw = data.pop(column)
            if raw:
                try:
                    data[key] = json.loads(raw)
                except json.JSONDecodeError:
                    data[key] = None
            else:
                data[key] = None
        data["report"] = data.pop("report_md")
        return data

    # ------------------------------------------------------------------
    # 字段确认值（数据资产：下个同类项目可作默认值复用）
    # ------------------------------------------------------------------

    def save_field_values(
        self,
        job_id: str,
        project_id: str | None,
        values: list[dict[str, Any]],
    ) -> int:
        """保存一组字段确认值（同 job 同 key 覆盖）。返回写入条数。"""
        rows: list[tuple[Any, ...]] = []
        for item in values:
            key = str(item.get("key") or "").strip()
            if not key:
                continue
            rows.append(
                (
                    job_id,
                    project_id,
                    key,
                    str(item.get("label") or ""),
                    str(item.get("template") or ""),
                    "" if item.get("value") is None else str(item.get("value")),
                    str(item.get("source") or "manual"),
                    str(item.get("status") or "ok"),
                    1 if item.get("confirmed", True) else 0,
                    _now(),
                )
            )
        if not rows:
            return 0
        with self._write_lock:
            conn = self._connect()
            try:
                conn.executemany(
                    "INSERT INTO field_values (job_id, project_id, field_key,"
                    " field_label, template, value, source, status, confirmed, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(job_id, field_key) DO UPDATE SET"
                    " value = excluded.value, source = excluded.source,"
                    " status = excluded.status, confirmed = excluded.confirmed,"
                    " updated_at = excluded.updated_at, field_label = excluded.field_label,"
                    " template = excluded.template",
                    rows,
                )
                conn.commit()
            finally:
                conn.close()
        return len(rows)

    def list_field_values(self, job_id: str) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM field_values WHERE job_id = ? ORDER BY id", (job_id,)
        )
        return [self._field_row(row) for row in rows]

    def latest_field_values(self, keys: list[str] | None = None) -> dict[str, dict[str, Any]]:
        """历史字段资产：每个字段 key 最近一次确认值（阶段二复用的基础）。"""
        where = ""
        params: tuple = ()
        if keys:
            marks = ",".join("?" for _ in keys)
            where = f"WHERE field_key IN ({marks})"
            params = tuple(keys)
        rows = self._query(
            "SELECT job_id, project_id, field_key, field_label, template,"
            " value, source, status, confirmed, updated_at"
            f" FROM field_values {where} ORDER BY updated_at DESC, id DESC",
            params,
        )
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = row["field_key"]
            if key not in latest and row["value"] not in (None, ""):
                latest[key] = self._field_row(row)
        return latest

    @staticmethod
    def _field_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["confirmed"] = bool(data["confirmed"])
        # 对外统一叫 key/label（与 fields.json 一致），列名保留 field_key
        data["key"] = data["field_key"]
        data["label"] = data["field_label"]
        return data

    # ------------------------------------------------------------------
    # 设置（用户覆盖层，打包后存数据目录，可写）
    # ------------------------------------------------------------------

    def get_setting(self, key: str) -> Any | None:
        rows = self._query("SELECT value_json FROM settings WHERE key = ?", (key,))
        if not rows:
            return None
        try:
            return json.loads(rows[0]["value_json"])
        except json.JSONDecodeError:
            return None

    def set_setting(self, key: str, value: Any) -> None:
        self._write(
            "INSERT INTO settings (key, value_json, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET"
            " value_json = excluded.value_json, updated_at = excluded.updated_at",
            (key, json.dumps(value, ensure_ascii=False), _now()),
        )

    # ------------------------------------------------------------------
    # 企业档案（单实例，整份 JSON 存储；形状 = CompanyProfile.to_dict()）
    # ------------------------------------------------------------------

    def get_company(self) -> dict[str, Any] | None:
        rows = self._query("SELECT data_json, updated_at FROM company WHERE id = 1")
        if not rows:
            return None
        try:
            data = json.loads(rows[0]["data_json"])
        except json.JSONDecodeError:
            return None
        if isinstance(data, dict):
            data["updated_at"] = rows[0]["updated_at"]
        return data

    def save_company(self, data: dict[str, Any]) -> dict[str, Any]:
        updated = _now()
        self._write(
            "INSERT INTO company (id, data_json, updated_at) VALUES (1, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET"
            " data_json = excluded.data_json, updated_at = excluded.updated_at",
            (json.dumps(data, ensure_ascii=False), updated),
        )
        stored = dict(data)
        stored["updated_at"] = updated
        return stored
