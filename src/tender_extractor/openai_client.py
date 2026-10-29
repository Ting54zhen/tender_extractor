"""OpenAI 兼容的 Chat Completions 客户端（仅标准库）。

适用于任何提供 ``POST {base_url}/chat/completions`` 的服务，例如：

- DeepSeek     base_url = https://api.deepseek.com/v1
- 通义千问     base_url = https://dashscope.aliyuncs.com/compatible-mode/v1
- Kimi         base_url = https://api.moonshot.cn/v1
- 智谱         base_url = https://open.bigmodel.cn/api/paas/v4
- 硅基流动     base_url = https://api.siliconflow.cn/v1
- OpenAI       base_url = https://api.openai.com/v1
- 自建网关     vLLM / LM Studio / One-API / 公司内网模型网关

注意：使用云端接口时文档片段会发送到该服务。涉密或内网资料请继续用
``provider=ollama``，或把 base_url 指向内网自建网关。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from .llm_client import LLMError, LLMHealth, extract_json_object

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class OpenAICompatibleClient:
    provider = "openai"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: int = 180,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        json_mode: bool = True,
        max_retries: int = 2,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.json_mode = json_mode
        self.max_retries = max(0, max_retries)
        self.calls = 0
        self.last_error = ""

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        )
        method = "POST" if data is not None else "GET"

        last_error: LLMError | None = None
        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(
                url, data=data, headers=self._headers(), method=method
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = response.read().decode("utf-8", errors="replace")
                try:
                    return json.loads(body)
                except json.JSONDecodeError as exc:
                    raise LLMError(f"API 返回非 JSON：{body[:300]}") from exc
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                error = LLMError(f"API HTTP {exc.code}：{detail}")
                if exc.code in _RETRYABLE_STATUS and attempt < self.max_retries:
                    time.sleep(1.5 * (attempt + 1))
                    last_error = error
                    continue
                raise error from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                error = LLMError(f"无法连接 API（{self.base_url}）：{exc}")
                if attempt < self.max_retries:
                    time.sleep(1.0 * (attempt + 1))
                    last_error = error
                    continue
                raise error from exc

        raise last_error or LLMError("API 请求失败")

    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------

    def health(self) -> LLMHealth:
        if not self.base_url:
            return LLMHealth(False, "未配置 base_url")
        if not self.model:
            return LLMHealth(False, "未配置模型名（settings.json 的 llm.model 或 --model）")
        if not self.api_key:
            return LLMHealth(
                False,
                "未配置 API key（环境变量 TENDER_EXTRACTOR_API_KEY，或 --api-key）",
            )

        try:
            data = self._request("/models")
        except LLMError as exc:
            message = str(exc)
            self.last_error = message
            if "HTTP 401" in message or "HTTP 403" in message:
                return LLMHealth(False, f"API key 无效或无权访问该模型：{message}")
            if "HTTP 404" in message or "HTTP 405" in message:
                # 部分网关不实现 /models，但聊天接口可用，不阻塞抽取。
                return LLMHealth(
                    True,
                    "接口可达，但该服务不提供 /models，无法预先校验模型名",
                    [],
                    True,
                )
            return LLMHealth(False, message)

        models = [
            str(item.get("id", ""))
            for item in (data.get("data") or [])
            if isinstance(item, dict) and item.get("id")
        ]
        present = any(name == self.model for name in models) if models else True
        reason = ""
        if not present:
            preview = "、".join(models[:10]) or "（空）"
            reason = f"服务端模型列表中没有 {self.model}；可用：{preview}"
        return LLMHealth(True, reason, models, present)

    # ------------------------------------------------------------------
    # 对话
    # ------------------------------------------------------------------

    def _content(self, response: dict[str, Any]) -> tuple[str, str]:
        choices = response.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            raise LLMError("API 返回中没有 choices")
        choice = choices[0]
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        content = str(content or "").strip()
        finish = str(choice.get("finish_reason") or "")
        if not content:
            raise LLMError(f"API 返回空内容（finish_reason={finish or '未知'}）")
        return content, finish

    def chat_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any] | str | None = None,
    ) -> dict[str, Any]:
        # schema 已写在提示词里；OpenAI 兼容接口统一用 json_object 模式，
        # 避免不同服务对 json_schema 的支持差异。
        if not self.model:
            raise LLMError("未配置模型名（settings.json 的 llm.model 或 --model）")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}

        self.calls += 1
        try:
            response = self._request("/chat/completions", payload)
        except LLMError as exc:
            self.last_error = str(exc)
            # 部分兼容服务不支持 response_format，去掉后重试一次。
            if self.json_mode and "HTTP 400" in str(exc):
                payload.pop("response_format", None)
                self.calls += 1
                try:
                    response = self._request("/chat/completions", payload)
                except LLMError as retry_exc:
                    self.last_error = str(retry_exc)
                    raise
            else:
                raise

        try:
            content, finish = self._content(response)
        except LLMError as exc:
            self.last_error = str(exc)
            raise

        try:
            return extract_json_object(content)
        except LLMError as exc:
            hint = "；输出可能被截断，请增大 max_tokens" if finish == "length" else ""
            self.last_error = f"{exc}{hint}"
            raise LLMError(f"{exc}{hint}") from exc
