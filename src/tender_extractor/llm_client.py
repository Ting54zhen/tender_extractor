"""模型客户端工厂与接入方式解析。

支持的接入方式（settings.json 的 `llm.provider`）：

- ``ollama``：本机 Ollama，文档不出内网（默认）；
- ``openai``：任意兼容 ``/v1/chat/completions`` 的接口，例如 DeepSeek、
  通义千问 compatible-mode、Kimi、智谱、硅基流动、OpenAI、vLLM、
  LM Studio、One-API 等。

共用类型（``LLMClient`` / ``LLMError`` / ``LLMHealth`` / ``extract_json_object``）
定义在 ``llm_common``，这里继续导出，保持既有导入路径可用。
"""

from __future__ import annotations

import os
from typing import Any

from .llm_common import LLMClient, LLMError, LLMHealth, extract_json_object

__all__ = [
    "LLMClient",
    "LLMError",
    "LLMHealth",
    "PROVIDER_ALIASES",
    "SUPPORTED_PROVIDERS",
    "build_llm_client",
    "extract_json_object",
    "normalize_provider",
    "resolve_api_key",
]


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
