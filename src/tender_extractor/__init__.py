"""招标需求报告关键信息抽取组件。

典型用法：
    from tender_extractor import extract_document
    result = extract_document("需求报告.docx")
    data = result.to_dict()
    template_values = data["template_values"]  # {占位符名: 值}

设计目标是作为招标平台的一个后端部件：输入需求报告，输出结构化字段，
由平台自己的表单/模板渲染层完成填充。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import ExtractionResult
from .pipeline import Extractor
from .platform_adapter import (
    PlatformRecognitionError,
    recognize_to_draft,
    to_platform_draft,
)

__version__ = "0.2.0"

__all__ = [
    "Extractor",
    "ExtractionResult",
    "PlatformRecognitionError",
    "extract_document",
    "recognize_to_draft",
    "to_platform_draft",
    "__version__",
]


def extract_document(
    path: str | Path,
    config_dir: str | Path | None = None,
    use_llm: bool = True,
    settings_override: dict[str, Any] | None = None,
    allow_non_tender: bool = False,
) -> ExtractionResult:
    """一行式入口，适合平台在请求处理里直接调用。"""
    extractor = Extractor(config_dir=config_dir, settings_override=settings_override)
    return extractor.extract(
        path,
        use_llm=use_llm,
        allow_non_tender=allow_non_tender,
    )
