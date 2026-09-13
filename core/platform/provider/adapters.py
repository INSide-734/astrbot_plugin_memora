"""冻结 LLM 与 Embedding Provider 调用入口的内部 adapter。"""

from __future__ import annotations

import inspect
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, cast

from ...shared.adapter_capabilities import (
    AdapterCapability,
    AdapterCapabilityContract,
    AdapterKind,
    UnsupportedAdapterCapability,
)


class EmbeddingCallMode(str, Enum):
    """Embedding Provider 的冻结调用方式。"""

    NATIVE_BATCH = "native_batch"
    COMPAT_BATCH = "compat_batch"
    SINGLE = "single"


class AdapterResponseError(RuntimeError):
    """Adapter 返回值不满足稳定结果契约。"""

    def __init__(self, reason_code: str, kind: AdapterKind) -> None:
        """使用稳定原因码和 adapter 类别构造安全错误。"""

        self.reason_code = reason_code
        self.safe_details = {
            "reason_code": reason_code,
            "adapter_kind": kind.value,
        }
        super().__init__(reason_code)


_LLM_PROVIDER_CAPABILITIES = AdapterCapabilityContract(
    kind=AdapterKind.LLM_PROVIDER,
    native=frozenset({AdapterCapability.TEXT_GENERATION}),
    caller_enforced=frozenset({AdapterCapability.CANCELLATION}),
)


@dataclass(frozen=True, slots=True)
class LLMGenerationResult:
    """保存一次文本生成的正文、token 用量和安全终止原因。"""

    text: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str = "unknown"

    def __post_init__(self) -> None:
        """把终止原因限制为固定低基数集合。"""
        if not isinstance(self.finish_reason, str) or (
            self.finish_reason not in _LLM_FINISH_REASONS
        ):
            object.__setattr__(self, "finish_reason", "unknown")


@dataclass(frozen=True, slots=True)
class LLMProviderAdapter:
    """验证并冻结聊天 Provider 的异步文本生成入口。"""

    provider: Any
    _text_chat: Callable[..., Awaitable[Any]]
    adapter_capabilities: AdapterCapabilityContract = _LLM_PROVIDER_CAPABILITIES

    @classmethod
    def from_provider(cls, provider: Any) -> "LLMProviderAdapter":
        """从 Provider 构建 adapter，缺失入口时稳定失败。"""

        text_chat = getattr(provider, "text_chat", None)
        if not callable(text_chat):
            raise UnsupportedAdapterCapability(
                AdapterKind.LLM_PROVIDER,
                AdapterCapability.TEXT_GENERATION,
            )
        return cls(
            provider=provider,
            _text_chat=cast(Callable[..., Awaitable[Any]], text_chat),
        )

    async def generate(self, prompt: str, system_prompt: str) -> str:
        """调用冻结入口并保持原有的纯文本返回契约。"""

        return (await self.generate_result(prompt, system_prompt)).text

    async def generate_result(
        self,
        prompt: str,
        system_prompt: str,
    ) -> LLMGenerationResult:
        """调用冻结入口并返回文本、用量及受控终止原因。"""

        pending = self._text_chat(prompt=prompt, system_prompt=system_prompt)
        if not inspect.isawaitable(pending):
            raise AdapterResponseError(
                "adapter_response_invalid",
                AdapterKind.LLM_PROVIDER,
            )
        response = await pending
        completion_text = getattr(response, "completion_text", None)
        if not isinstance(completion_text, str):
            raise AdapterResponseError(
                "adapter_response_invalid",
                AdapterKind.LLM_PROVIDER,
            )
        usage = getattr(response, "usage", None)
        return LLMGenerationResult(
            text=completion_text,
            prompt_tokens=_optional_token_count(usage, "input"),
            completion_tokens=_optional_token_count(usage, "output"),
            finish_reason=_safe_finish_reason(
                getattr(response, "raw_completion", None)
            ),
        )


_LLM_FINISH_REASONS = frozenset(
    {"unknown", "stop", "length", "tool_call", "content_filter", "error"}
)
_CHAT_FINISH_REASONS = {
    "stop": "stop",
    "length": "length",
    "tool_calls": "tool_call",
    "function_call": "tool_call",
    "content_filter": "content_filter",
}
_RESPONSE_STATUS_REASONS = {
    "completed": "stop",
    "failed": "error",
}
_RESPONSE_INCOMPLETE_REASONS = {
    "max_output_tokens": "length",
    "content_filter": "content_filter",
}
_ANTHROPIC_STOP_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "model_context_window_exceeded": "length",
    "tool_use": "tool_call",
    "refusal": "content_filter",
}
_GEMINI_FINISH_REASONS = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "PROHIBITED_CONTENT": "content_filter",
    "BLOCKLIST": "content_filter",
    "SPII": "content_filter",
    "IMAGE_SAFETY": "content_filter",
    "LANGUAGE": "content_filter",
    "MALFORMED_FUNCTION_CALL": "tool_call",
    "UNEXPECTED_TOOL_CALL": "tool_call",
}


def _sdk_enum_value(value: Any) -> str | None:
    """读取 SDK 枚举的字符串值，不回显任意 Provider 对象。"""

    if isinstance(value, str):
        return value
    enum_value = getattr(value, "value", None)
    return enum_value if isinstance(enum_value, str) else None


def _safe_finish_reason(raw_completion: Any) -> str:
    """按 AstrBot LLMResponse 的已支持 raw_completion 形状归约原因。

    AstrBot 4.28 的 raw_completion 联合类型是 OpenAI ChatCompletion/Response、
    Gemini GenerateContentResponse 或 Anthropic Message。仅读取这些 SDK 已知的
    字段；缺少、非法或其他 Provider 形状均保持 ``unknown``。
    """

    if raw_completion is None:
        return "unknown"
    choices = getattr(raw_completion, "choices", None)
    if isinstance(choices, (list, tuple)) and choices:
        reason = _sdk_enum_value(getattr(choices[0], "finish_reason", None))
        return (
            _CHAT_FINISH_REASONS.get(reason, "unknown")
            if reason is not None
            else "unknown"
        )

    status = _sdk_enum_value(getattr(raw_completion, "status", None))
    if status in _RESPONSE_STATUS_REASONS:
        return _RESPONSE_STATUS_REASONS[status]
    if status == "incomplete":
        details = getattr(raw_completion, "incomplete_details", None)
        reason = _sdk_enum_value(getattr(details, "reason", None))
        return (
            _RESPONSE_INCOMPLETE_REASONS.get(reason, "unknown")
            if reason is not None
            else "unknown"
        )

    candidates = getattr(raw_completion, "candidates", None)
    if isinstance(candidates, (list, tuple)) and candidates:
        reason = _sdk_enum_value(getattr(candidates[0], "finish_reason", None))
        return (
            _GEMINI_FINISH_REASONS.get(reason, "unknown")
            if reason is not None
            else "unknown"
        )

    reason = _sdk_enum_value(getattr(raw_completion, "stop_reason", None))
    return (
        _ANTHROPIC_STOP_REASONS.get(reason, "unknown")
        if reason is not None
        else "unknown"
    )


def _optional_token_count(usage: Any, field: str) -> int | None:
    """读取非负整数 token 字段；缺失、布尔或非法值均保持未知。"""

    value = getattr(usage, field, None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _accepts_extended_batch(call: Callable[..., Any]) -> bool:
    """在构建时判断 batch 入口是否接受 AstrBot 扩展参数。"""

    try:
        parameters = inspect.signature(call).parameters.values()
    except (TypeError, ValueError):
        return False
    names = {parameter.name for parameter in parameters}
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters):
        return True
    return {"batch_size", "tasks_limit", "max_retries"} <= names


def _embedding_capabilities(mode: EmbeddingCallMode) -> AdapterCapabilityContract:
    """按冻结调用模式生成 Embedding 能力快照。"""

    native = {AdapterCapability.EMBEDDING}
    caller_enforced = {AdapterCapability.CANCELLATION}
    if mode is EmbeddingCallMode.SINGLE:
        caller_enforced.add(AdapterCapability.BATCH_READ)
    else:
        native.add(AdapterCapability.BATCH_READ)
    return AdapterCapabilityContract(
        kind=AdapterKind.EMBEDDING_PROVIDER,
        native=frozenset(native),
        caller_enforced=frozenset(caller_enforced),
    )


@dataclass(frozen=True, slots=True)
class EmbeddingProviderAdapter:
    """冻结 Embedding Provider 的 batch 或逐项调用入口。"""

    provider: Any
    mode: EmbeddingCallMode
    _call: Callable[..., Awaitable[Any]]
    _extended_batch: bool
    adapter_capabilities: AdapterCapabilityContract

    @classmethod
    def from_provider(cls, provider: Any) -> "EmbeddingProviderAdapter":
        """按稳定优先级选择一次 Embedding 调用入口。"""

        get_embeddings = getattr(provider, "get_embeddings", None)
        if callable(get_embeddings):
            mode = EmbeddingCallMode.NATIVE_BATCH
            call = get_embeddings
            extended = False
        else:
            get_batch = getattr(provider, "get_embeddings_batch", None)
            if callable(get_batch):
                mode = EmbeddingCallMode.COMPAT_BATCH
                call = get_batch
                extended = _accepts_extended_batch(get_batch)
            else:
                get_single = getattr(provider, "get_embedding", None)
                if not callable(get_single):
                    raise UnsupportedAdapterCapability(
                        AdapterKind.EMBEDDING_PROVIDER,
                        AdapterCapability.EMBEDDING,
                    )
                mode = EmbeddingCallMode.SINGLE
                call = get_single
                extended = False
        return cls(
            provider=provider,
            mode=mode,
            _call=cast(Callable[..., Awaitable[Any]], call),
            _extended_batch=extended,
            adapter_capabilities=_embedding_capabilities(mode),
        )

    async def embed(self, contents: list[str]) -> list[list[float]]:
        """按冻结模式生成向量，并验证数量、维度和有限性。"""

        if not contents:
            return []
        if self.mode is EmbeddingCallMode.SINGLE:
            raw_vectors = [await self._call(content) for content in contents]
        elif self._extended_batch:
            raw_vectors = await self._call(
                contents,
                batch_size=len(contents),
                tasks_limit=1,
                max_retries=1,
            )
        else:
            raw_vectors = await self._call(contents)
        return self._validate_vectors(raw_vectors, expected_count=len(contents))

    @staticmethod
    def _validate_vectors(
        raw_vectors: Any, *, expected_count: int
    ) -> list[list[float]]:
        """把 Provider 向量规范化为有限、同维度的 float 列表。"""

        if isinstance(raw_vectors, (str, bytes, bytearray)):
            raise AdapterResponseError(
                "embedding_result_invalid",
                AdapterKind.EMBEDDING_PROVIDER,
            )
        try:
            vectors = list(raw_vectors)
        except TypeError as exc:
            raise AdapterResponseError(
                "embedding_result_invalid",
                AdapterKind.EMBEDDING_PROVIDER,
            ) from exc
        if len(vectors) != expected_count:
            raise AdapterResponseError(
                "embedding_count_mismatch",
                AdapterKind.EMBEDDING_PROVIDER,
            )
        normalized: list[list[float]] = []
        dimension: int | None = None
        for raw_vector in vectors:
            if isinstance(raw_vector, (str, bytes, bytearray)):
                raise AdapterResponseError(
                    "embedding_result_invalid",
                    AdapterKind.EMBEDDING_PROVIDER,
                )
            try:
                vector = [float(value) for value in raw_vector]
            except (TypeError, ValueError) as exc:
                raise AdapterResponseError(
                    "embedding_result_invalid",
                    AdapterKind.EMBEDDING_PROVIDER,
                ) from exc
            if not vector or any(not math.isfinite(value) for value in vector):
                raise AdapterResponseError(
                    "embedding_non_finite",
                    AdapterKind.EMBEDDING_PROVIDER,
                )
            if dimension is None:
                dimension = len(vector)
            elif len(vector) != dimension:
                raise AdapterResponseError(
                    "embedding_dimension_mismatch",
                    AdapterKind.EMBEDDING_PROVIDER,
                )
            normalized.append(vector)
        return normalized


__all__ = [
    "AdapterResponseError",
    "EmbeddingCallMode",
    "EmbeddingProviderAdapter",
    "LLMGenerationResult",
    "LLMProviderAdapter",
]
