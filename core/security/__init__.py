"""Memora 安全模块 — Prompt 防护与 LLM 输出护栏。

提供两层安全防线：
1. **PromptProtectionService** — 3层 Prompt 防护 (标签包裹 → 关键词清洗 → 算法验证)
2. **Guardrails** — Pydantic 强类型 LLM 输出验证与 JSON 清洗管道
"""

from __future__ import annotations

from .guardrails import (
    GraphExtractionResult,
    MemoryAtomSchema,
    MemoryExtractionResult,
    safe_validate,
    validate_and_clean_json,
    validate_llm_response,
)
from .prompt_sanitizer import (
    DoubleCheckValidator,
    MetaInstructionWrapper,
    PromptProtectionService,
    ResponseSanitizer,
)

__all__ = [
    # Prompt sanitizer
    "PromptProtectionService",
    "MetaInstructionWrapper",
    "ResponseSanitizer",
    "DoubleCheckValidator",
    # Guardrails
    "MemoryExtractionResult",
    "MemoryAtomSchema",
    "GraphExtractionResult",
    "validate_and_clean_json",
    "validate_llm_response",
    "safe_validate",
]
