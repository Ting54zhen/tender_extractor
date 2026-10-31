"""模型客户端共用的类型、错误与 JSON 解析。

单独成模块，避免 ``llm_client`` 与其创建的客户端互相导入造成循环依赖：
``ollama_client`` / ``openai_client`` 只依赖本模块，``llm_client`` 再向上提供工厂。
"""

from __future__ import annotations

import json
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
