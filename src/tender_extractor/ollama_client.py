"""Ollama 本地模型客户端（仅标准库，避免额外依赖和商用许可问题）。

所有请求只发往本地 base_url，不经过任何外部服务。
错误类型和健康检查结果与 OpenAI 兼容客户端共用，见 ``llm_common``。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .llm_common import LLMError, LLMHealth, extract_json_object

# 兼容旧代码里的名字。
OllamaError = LLMError
OllamaHealth = LLMHealth


class OllamaClient:
    provider = "ollama"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "qwen2.5:3b-instruct",
        timeout: int = 180,
        num_ctx: int = 8192,
        num_predict: int = 2048,
        temperature: float = 0.0,
        seed: int = 42,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.options = {
            "temperature": temperature,
            "seed": seed,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
            "top_p": 0.1,
        }
        self.calls = 0
        self.last_error = ""

    def _request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = None
        headers = {"Content-Type": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise OllamaError(f"Ollama HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OllamaError(f"无法连接本地 Ollama（{url}）：{exc}") from exc
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise OllamaError(f"Ollama 返回非 JSON：{body[:300]}") from exc

    def health(self) -> OllamaHealth:
        try:
            data = self._request("/api/tags")
        except OllamaError as exc:
            self.last_error = str(exc)
            return OllamaHealth(False, str(exc))
        models = [item.get("name", "") for item in data.get("models", [])]
        present = any(
            name == self.model or name.split(":")[0] == self.model.split(":")[0]
            for name in models
        )
        reason = "" if present else f"本地没有模型 {self.model}，请执行 ollama pull {self.model}"
        return OllamaHealth(True, reason, models, present)

    def chat_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any] | str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": self.options,
        }
        if schema is not None:
            payload["format"] = schema

        self.calls += 1
        try:
            response = self._request("/api/chat", payload)
            content = (response.get("message") or {}).get("content", "")
            return extract_json_object(content)
        except OllamaError as exc:
            self.last_error = str(exc)
            # 某些旧版本不支持 JSON Schema，退化为 format=json 再试一次。
            if schema is not None and not isinstance(schema, str):
                payload["format"] = "json"
                self.calls += 1
                response = self._request("/api/chat", payload)
                content = (response.get("message") or {}).get("content", "")
                return extract_json_object(content)
            raise
