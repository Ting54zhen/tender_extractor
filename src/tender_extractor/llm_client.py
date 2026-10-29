"""模型客户端抽象、错误类型和工厂。

支持的接入方式（settings.json 的 `llm.provider`）：

- ``ollama``：本机 Ollama，文档不出内网（默认）；
- ``openai``：任意兼容 ``/v1/chat/completions`` 的接口，例如 DeepSeek、
  通义千问 compatible-mode、Kimi、智谱、硅基流动、OpenAI、vLLM、
  LM Studio、One-API 等。

上层只依赖 ``LLMClient`` 协议，不关心底层是哪家服务。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class LLMError(RuntimeError):
    """模型调用失败。错误信息中不会包含 API key。"""


@dataclass
class LLMHealth:
    available: bool
    reason: str = ""
    models: list[str] = field(default_factory=list)
    model_present: bool = False


@runtime_checkable
class LLMClient(Protocol):
    provider: str
    model: str
    calls: int
    last_error: str

    def health(self) -> LLMHealth: ...

    def chat_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any] | str | None = None,
    ) -> dict[str, Any]: ...


PROVIDER_ALIASES = {
    "ollama": "ollama",
    "local": "ollama",
    "openai": "openai",
    "openai-compatible": "openai",
    "openai_compatible": "openai",
    "api": "openai",
}

SUPPORTED_PROVIDERS = ("ollama", "openai")


def normalize_provider(value: str | None) -> str:
    provider = (value or "ollama").strip().lower()
    provider = PROVIDER_ALIASES.get(provider, provider)
    if provider not in SUPPORTED_PROVIDERS:
        raise LLMError(
            f"未知的模型接入方式 {value!r}，可选：{', '.join(SUPPORTED_PROVIDERS)}"
        )
    return provider


def resolve_api_key(settings: dict[str, Any] | None = None) -> str:
    """按优先级取 API key：settings.api_key > api_key_env > 内置环境变量名。"""
    settings = settings or {}
    direct = str(settings.get("api_key") or "").strip()
    if direct:
        return direct

    names: list[str] = []
    configured = str(settings.get("api_key_env") or "").strip()
    if configured:
        names.append(configured)
    names.extend(["TENDER_EXTRACTOR_API_KEY", "OPENAI_API_KEY"])
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def build_llm_client(settings: dict[str, Any] | None = None) -> LLMClient:
    """根据 settings 的 llm 段创建客户端。"""
    settings = settings or {}
    provider = normalize_provider(settings.get("provider"))

    if provider == "ollama":
        from .ollama_client import OllamaClient

        return OllamaClient(
            base_url=settings.get("base_url") or "http://127.0.0.1:11434",
            model=settings.get("model") or "qwen2.5:3b-instruct",
            timeout=int(settings.get("timeout_seconds", 180)),
            num_ctx=int(settings.get("num_ctx", 8192)),
            num_predict=int(settings.get("num_predict", 2048)),
            temperature=float(settings.get("temperature", 0.0)),
            seed=int(settings.get("seed", 42)),
        )

    from .openai_client import OpenAICompatibleClient

    return OpenAICompatibleClient(
        base_url=str(settings.get("base_url") or "https://api.openai.com/v1"),
        model=str(settings.get("model") or ""),
        api_key=resolve_api_key(settings),
        timeout=int(settings.get("timeout_seconds", 180)),
        temperature=float(settings.get("temperature", 0.0)),
        max_tokens=int(settings.get("max_tokens", settings.get("num_predict", 2048))),
        json_mode=bool(settings.get("json_mode", True)),
    )


def extract_json_object(text: str) -> dict[str, Any]:
    """从模型输出里取出 JSON 对象，容忍 ``` 代码块和前后解释文字。"""
    text = (text or "").strip()
    if not text:
        raise LLMError("模型返回空内容")
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError as exc:
            raise LLMError(f"模型返回不是合法 JSON：{exc}") from exc
    raise LLMError("模型返回中找不到 JSON 对象")
