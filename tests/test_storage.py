# -*- coding: utf-8 -*-
"""storage 持久化层：项目 / 任务 / 字段确认值 / 设置。"""

import json

import pytest

from tender_extractor.storage import Database


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "data" / "tender.db")


def test_project_lifecycle(db):
    project = db.create_project("2025年社区服务项目", note="测试")
    assert project["name"] == "2025年社区服务项目"
    assert project["status"] == "active"
    assert project["job_count"] == 0

    # 空名称拒绝
    with pytest.raises(ValueError):
        db.create_project("   ")

    # 归档 → 恢复
    archived = db.update_project(project["id"], status="archived")
    assert archived["status"] == "archived"
    assert archived["archived_at"]
    assert db.list_projects(status="active") == []
    assert [p["id"] for p in db.list_projects(status="archived")] == [project["id"]]

    restored = db.update_project(project["id"], status="active")
    assert restored["status"] == "active"
    assert restored["archived_at"] is None

    # 重命名
    renamed = db.update_project(project["id"], name="新名称")
    assert renamed["name"] == "新名称"

    # 删除
    assert db.delete_project(project["id"]) is True
    assert db.get_project(project["id"]) is None
    assert db.delete_project(project["id"]) is False


def test_job_persistence_roundtrip(db):
    project = db.create_project("项目A")["id"]
    db.create_job("job1", "extract", "需求.docx", project, True, False)
    db.update_job(
        "job1",
        status="done",
        stage="完成",
        summary={"fields_with_value": 14},
        result={"fields": {"project_name": {"value": "X"}}},
        report="# 报告",
        draft={"fields": {}},
        json_path="d:/x.json",
    )

    row = db.get_job("job1")
    assert row["status"] == "done"
    assert row["project_id"] == project
    assert row["summary"] == {"fields_with_value": 14}
    assert row["result"]["fields"]["project_name"]["value"] == "X"
    assert row["report"] == "# 报告"
    assert row["draft"] == {"fields": {}}
    assert row["use_llm"] is True
    assert row["allow_non_tender"] is False

    # 列表与项目过滤
    db.create_job("job2", "compare", "a vs b", None, False, False)
    assert len(db.list_jobs()) == 2
    assert [j["id"] for j in db.list_jobs(project_id=project)] == ["job1"]
    assert [j["id"] for j in db.list_jobs(kind="compare")] == ["job2"]

    # 删除项目级联删除任务
    db.delete_project(project)
    assert db.get_job("job1") is None
    assert db.get_job("job2") is not None


def test_field_values_upsert_and_latest(db):
    project = db.create_project("项目B")["id"]
    db.create_job("jobA", "extract", "a.docx", project, True, False)
    db.create_job("jobB", "extract", "b.docx", project, True, False)

    saved = db.save_field_values(
        "jobA",
        project,
        [
            {"key": "purchaser", "label": "采购人", "template": "采购人", "value": "某局", "source": "manual"},
            {"key": "contract_copies", "label": "合同份数", "value": "肆份"},
        ],
    )
    assert saved == 2
    assert len(db.list_field_values("jobA")) == 2

    # 同 job 同 key 覆盖
    db.save_field_values(
        "jobA", project,
        [{"key": "purchaser", "label": "采购人", "value": "某局更新", "source": "manual"}],
    )
    rows = {r["key"]: r for r in db.list_field_values("jobA")}
    assert rows["purchaser"]["value"] == "某局更新"
    assert len(rows) == 2

    # latest_field_values：每个 key 取最近一次
    db.save_field_values(
        "jobB", project,
        [{"key": "purchaser", "label": "采购人", "value": "另一单位", "source": "manual"}],
    )
    latest = db.latest_field_values()
    assert latest["purchaser"]["value"] == "另一单位"
    assert latest["purchaser"]["job_id"] == "jobB"

    # key 过滤
    filtered = db.latest_field_values(["contract_copies"])
    assert set(filtered) == {"contract_copies"}

    # 空值不进入资产
    db.save_field_values("jobB", project, [{"key": "empty_field", "value": ""}])
    assert "empty_field" not in db.latest_field_values()


def test_settings_roundtrip(db):
    assert db.get_setting("settings_override") is None
    db.set_setting("settings_override", {"llm": {"model": "qwen2.5:7b"}})
    assert db.get_setting("settings_override") == {"llm": {"model": "qwen2.5:7b"}}
    db.set_setting("settings_override", {"llm": {"provider": "openai"}})
    assert db.get_setting("settings_override") == {"llm": {"provider": "openai"}}


def test_reopen_same_db(tmp_path):
    """重启（重新打开库）后数据仍在——持久化的核心验收点。"""
    path = tmp_path / "data" / "tender.db"
    db1 = Database(path)
    db1.create_project("跨重启项目")
    db1.create_job("jobX", "extract", "x.docx", None, True, False)
    db1.update_job("jobX", status="done", result={"meta": {}})

    db2 = Database(path)
    assert db2.get_project(db1.list_projects()[0]["id"])["name"] == "跨重启项目"
    assert db2.get_job("jobX")["result"] == {"meta": {}}
