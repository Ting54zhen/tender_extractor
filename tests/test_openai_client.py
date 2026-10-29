"""OpenAI 兼容接口测试：用本地 mock 服务验证请求格式、健康检查和端到端抽取。

不访问外网，不需要 API key。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from docx import Document as DocxDocument

from tender_extractor import Extractor
from tender_extractor.openai_client import OpenAICompatibleClient


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

    doc.add_paragraph("四、合同条款")
    doc.add_paragraph("知识产权归采购人所有。")
    doc.save(str(path))


class _MockOpenAI(BaseHTTPRequestHandler):
    requests: list[dict] = []

    def log_message(self, format, *args):  # noqa: A002 - 与基类签名一致
        pass

    def _write_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - 与基类签名一致
        if self.path == "/v1/models":
            self._write_json({"data": [{"id": "test-model"}]})
        else:
            self.send_error(404)

    def do_POST(self):  # noqa: N802 - 与基类签名一致
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        self.requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization", ""),
                "payload": payload,
            }
        )

        user = payload["messages"][-1]["content"]
        if "合同条款" in user:
            fields = [
                {
                    "key": "contract_ip",
                    "value": "知识产权归采购人所有",
                    "evidence": "知识产权归采购人所有。",
                }
            ]
        else:
            fields = []
        content = json.dumps({"fields": fields}, ensure_ascii=False)
        self._write_json(
            {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
            }
        )


def _start_server():
    _MockOpenAI.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockOpenAI)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1"


def test_health_and_chat_request_format():
    server, base_url = _start_server()
    try:
        client = OpenAICompatibleClient(
            base_url=base_url,
            model="test-model",
            api_key="test-key",
            timeout=10,
        )
        health = client.health()
        assert health.available is True
        assert health.model_present is True
        assert health.models == ["test-model"]

        data = client.chat_json("system", "用户问题")
        assert data == {"fields": []}
    finally:
        server.shutdown()
        server.server_close()

    assert client.calls == 1
    request = _MockOpenAI.requests[-1]
    assert request["path"] == "/v1/chat/completions"
    assert request["authorization"] == "Bearer test-key"
    assert request["payload"]["model"] == "test-model"
    assert request["payload"]["response_format"] == {"type": "json_object"}
    assert request["payload"]["stream"] is False


def test_extractor_with_openai_compatible_provider(tmp_path):
    path = tmp_path / "需求报告.docx"
    _build_requirement_docx(path)
    server, base_url = _start_server()
    try:
        extractor = Extractor(
            settings_override={
                "llm": {
                    "enabled": True,
                    "provider": "openai",
                    "base_url": base_url,
                    "model": "test-model",
                    "api_key": "test-key",
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
    assert result["meta"]["llm_provider"] == "openai"
    assert result["meta"]["model"] == "test-model"
    assert result["audit"]["llm"]["provider"] == "openai"

    field = result["fields"]["contract_ip"]
    assert field["value"] == "知识产权归采购人所有"
    assert field["source"] == "llm"
    assert field["status"] == "ok"


def test_missing_api_key_reports_unavailable(monkeypatch):
    monkeypatch.delenv("TENDER_EXTRACTOR_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = OpenAICompatibleClient(
        base_url="https://api.example.com/v1",
        model="test-model",
        api_key="",
        timeout=1,
    )
    health = client.health()
    assert health.available is False
    assert "API key" in health.reason
