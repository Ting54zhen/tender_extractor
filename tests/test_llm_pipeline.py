"""LLM 链路测试：用本地 mock Ollama 服务验证请求、补缺与证据回链。

不需要真实模型，也不会访问外网。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from docx import Document as DocxDocument

from tender_extractor import Extractor


def _build_requirement_docx(path, with_bad_count=False):
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
    items = doc.add_table(rows=3, cols=7)
    header = ["序号", "服务项目名称", "服务项目要求", "数量", "单位", "单价", "金额"]
    for idx, value in enumerate(header):
        items.cell(0, idx).text = value
    for idx, value in enumerate(
        ["1", "平台运维", "★7×24小时响应；▲提供月度报告", "2", "年", "50,000", "100,000"]
    ):
        items.cell(1, idx).text = value
    for idx, value in enumerate(["合计", "", "", "", "", "", "100,000.00"]):
        items.cell(2, idx).text = value

    doc.add_paragraph("三、主要商务要求")
    business = doc.add_table(rows=6, cols=2)
    rows = [
        ["商务要求", "内容"],
        ["标的提供的时间", "2026年1月1日"],
        ["标的提供的地点", "某某市某某路1号"],
        ["付款方式", "1期：支付预付款30,000元；2期：支付尾款70,000元。"],
        ["验收要求", "按合同约定验收"],
        ["履约保证金", "不收取"],
    ]
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            business.cell(r, c).text = value

    doc.add_paragraph("四、合同条款")
    doc.add_paragraph("知识产权归采购人所有。")
    if with_bad_count:
        # “三家”不是整数，用来触发校验失败 -> 回喂修复；
        # 后一句给出可回链的正确写法。
        doc.add_paragraph("推荐家数：三家")
        doc.add_paragraph("中标候选供应商推荐家数为3家。")
    doc.save(str(path))


class _MockOllama(BaseHTTPRequestHandler):
    groups: list[str] = []

    def log_message(self, format, *args):  # noqa: A002 - 与基类签名一致
        pass

    def _write_json(self, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tags":
            self._write_json({"models": [{"name": "qwen2.5:3b-instruct"}]})
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        user = payload["messages"][-1]["content"]
        if "上一轮抽取结果未通过校验" in user:
            self.groups.append("repair")
            fields = [
                {
                    "key": "candidate_count",
                    "value": "3",
                    "evidence": "中标候选供应商推荐家数为3家。",
                }
            ]
        elif "合同条款" in user:
            self.groups.append("contract")
            fields = [
                {
                    "key": "contract_ip",
                    "value": "知识产权归采购人所有",
                    "evidence": "知识产权归采购人所有。",
                }
            ]
        elif "项目概况" in user or "项目基本信息" in user:
            self.groups.append("basic")
            fields = []
        else:
            self.groups.append("other")
            fields = []
        self._write_json(
            {
                "model": "qwen2.5:3b-instruct",
                "message": {"role": "assistant", "content": json.dumps({"fields": fields}, ensure_ascii=False)},
                "done": True,
            }
        )


def test_llm_candidate_is_merged_and_grounded(tmp_path):
    path = tmp_path / "需求报告.docx"
    _build_requirement_docx(path)

    _MockOllama.groups = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockOllama)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        extractor = Extractor(
            settings_override={
                "llm": {
                    "enabled": True,
                    "base_url": base_url,
                    "model": "qwen2.5:3b-instruct",
                    "timeout_seconds": 10,
                    "max_repair_rounds": 0,
                }
            }
        )
        result = extractor.extract(path, use_llm=True).to_dict()
    finally:
        server.shutdown()
        server.server_close()

    assert result["meta"]["llm_available"] is True
    assert result["audit"]["llm"]["calls"] >= 1
    assert "basic" in result["audit"]["llm"]["groups"]

    field = result["fields"]["contract_ip"]
    assert field["value"] == "知识产权归采购人所有"
    assert field["source"] == "llm"
    assert field["evidence"][0]["block_id"]
    assert field["status"] == "ok"
    assert result["validation"]["errors"] == []


def test_repair_loop_feeds_validation_errors_back_to_model(tmp_path):
    path = tmp_path / "需求报告.docx"
    _build_requirement_docx(path, with_bad_count=True)

    _MockOllama.groups = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockOllama)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        extractor = Extractor(
            settings_override={
                "llm": {
                    "enabled": True,
                    "base_url": base_url,
                    "model": "qwen2.5:3b-instruct",
                    "timeout_seconds": 10,
                    "max_repair_rounds": 1,
                }
            }
        )
        result = extractor.extract(path, use_llm=True).to_dict()
    finally:
        server.shutdown()
        server.server_close()

    rounds = result["audit"]["repair_rounds"]
    sent_groups = result["audit"]["llm"]["groups"]
    # 每个「有缺口」的分组一次调用 + 每轮回喂一次，不写死分组数
    assert sent_groups, "至少应有一个分组被送去补缺"
    assert result["audit"]["llm"]["calls"] == len(sent_groups) + len(rounds)
    assert len(rounds) == 1
    assert "candidate_count" in rounds[0]["requested"]
    assert "candidate_count" in rounds[0]["accepted"]

    field = result["fields"]["candidate_count"]
    assert field["value"] == 3
    assert field["source"] == "llm-repair"
    assert field["status"] == "ok"
    assert field["attempts"] == 1
    assert result["validation"]["errors"] == []
