"""总结生成结果与诊断分母的行为回归。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import core.features.recall.processors.llm_client as llm_client_module
import core.features.recall.processors.reflection_generation_observability as generation_observability
from core.features.recall.processors.json_parser import SummaryParseError
from core.features.recall.processors.memory_processor import MemoryProcessor
from core.platform.provider.adapters import LLMGenerationResult, LLMProviderAdapter
from core.shared.contracts.conversation import Message


class _MetricLabel:
    """保存一次固定标签指标增量。"""

    def __init__(self, metric: "_Metric", labels: dict[str, str]) -> None:
        self.metric = metric
        self.labels = labels

    def inc(self, amount: float = 1) -> None:
        self.metric.label_values.append((self.labels, amount))


class _Metric:
    """提供可观察但不依赖 Prometheus 注册表的指标替身。"""

    def __init__(self) -> None:
        self.total = 0.0
        self.label_values: list[tuple[dict[str, str], float]] = []

    def inc(self, amount: float = 1) -> None:
        self.total += amount

    def labels(self, **labels: str) -> _MetricLabel:
        return _MetricLabel(self, labels)


@pytest.fixture
def metric_spies(monkeypatch: pytest.MonkeyPatch) -> dict[str, _Metric]:
    """替换生成指标；不打开 debug reporter，也不触碰全局注册表。"""

    spies = {
        "attempts": _Metric(),
        "finish_reasons": _Metric(),
        "parse_attempts": _Metric(),
        "parse_successes": _Metric(),
        "parse_failures": _Metric(),
    }
    for name, spy in (
        ("REFLECTION_LLM_CALLS", spies["attempts"]),
        ("SUMMARY_FINISH_REASONS", spies["finish_reasons"]),
        ("SUMMARY_PARSE_ATTEMPTS", spies["parse_attempts"]),
        ("SUMMARY_PARSE_SUCCESSES", spies["parse_successes"]),
        ("SUMMARY_PARSE_FAILURES", spies["parse_failures"]),
    ):
        monkeypatch.setattr(generation_observability, name, spy)
    return spies


def _response(
    raw_completion: object | None = None, text: str = "ok"
) -> SimpleNamespace:
    """构造仅暴露 AstrBot 响应边界的测试响应。"""

    return SimpleNamespace(completion_text=text, raw_completion=raw_completion)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_completion", "expected"),
    [
        (
            SimpleNamespace(
                choices=[SimpleNamespace(finish_reason="stop")],
            ),
            "stop",
        ),
        (
            SimpleNamespace(
                status="incomplete",
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            ),
            "length",
        ),
        (
            SimpleNamespace(
                candidates=[SimpleNamespace(finish_reason="SAFETY")],
            ),
            "content_filter",
        ),
        (
            SimpleNamespace(stop_reason="tool_use"),
            "tool_call",
        ),
    ],
)
async def test_adapter_reduces_supported_sdk_finish_reasons(
    raw_completion: object,
    expected: str,
) -> None:
    """适配层只输出已支持 SDK 格式的闭集终止原因。"""

    provider = MagicMock()
    provider.text_chat = AsyncMock(return_value=_response(raw_completion))

    result = await LLMProviderAdapter.from_provider(provider).generate_result(
        "prompt", "system"
    )

    assert result.finish_reason == expected


@pytest.mark.asyncio
async def test_adapter_keeps_invalid_or_unsupported_finish_reason_unknown() -> None:
    """缺失和 Provider 自定义原因不得进入结果或标签。"""

    provider = MagicMock()
    provider.text_chat = AsyncMock(
        return_value=_response(
            SimpleNamespace(
                choices=[SimpleNamespace(finish_reason="provider-private-reason")]
            )
        )
    )

    result = await LLMProviderAdapter.from_provider(provider).generate_result(
        "prompt", "system"
    )
    provider.text_chat.return_value = _response()
    missing = await LLMProviderAdapter.from_provider(provider).generate_result(
        "prompt", "system"
    )
    invalid = LLMGenerationResult(text="ok", finish_reason="dynamic")

    assert result.finish_reason == "unknown"
    assert missing.finish_reason == "unknown"
    assert invalid.finish_reason == "unknown"


def test_finish_reason_metric_reduces_dynamic_value_to_unknown(
    metric_spies: dict[str, _Metric],
) -> None:
    """终止原因指标标签只接受闭集值，不泄露 Provider 自定义字符串。"""

    generation_observability.report_generation_finish_reason("provider/internal/model")
    assert metric_spies["finish_reasons"].label_values == [
        ({"finish_reason": "unknown"}, 1)
    ]


@pytest.mark.asyncio
async def test_physical_attempts_and_finish_reasons_are_recorded_without_debug(
    monkeypatch: pytest.MonkeyPatch,
    metric_spies: dict[str, _Metric],
) -> None:
    """重试按真实物理调用计数，且 debug 关闭不影响指标。"""

    provider = MagicMock()
    provider.text_chat = AsyncMock(
        side_effect=[
            RuntimeError("provider failure"),
            _response(
                SimpleNamespace(
                    choices=[SimpleNamespace(finish_reason="stop")],
                )
            ),
        ]
    )
    monkeypatch.setattr(llm_client_module.random, "uniform", lambda *_: 0.0)
    monkeypatch.setattr(llm_client_module.asyncio, "sleep", AsyncMock())

    client = llm_client_module.LLMClient(llm_provider=provider)
    result = await client.call_llm_with_retry_result(
        "prompt",
        "system",
        max_retries=2,
        operation="summary_extraction",
    )

    assert result.finish_reason == "stop"
    assert metric_spies["attempts"].total == 2
    assert [
        labels["finish_reason"]
        for labels, _ in metric_spies["finish_reasons"].label_values
    ] == [
        "unknown",
        "stop",
    ]


@pytest.fixture
def sample_message() -> list[Message]:
    """构造最小用户窗口，不包含生产身份或持久化信息。"""

    return [
        Message(
            id=1,
            session_id="test-session",
            role="user",
            content="用户喜欢手冲咖啡",
            sender_id="test-user",
            sender_name="测试用户",
            timestamp=1.0,
        )
    ]


@pytest.mark.asyncio
async def test_malformed_strict_summary_is_error_and_parse_failure_is_counted(
    metric_spies: dict[str, _Metric],
    sample_message: list[Message],
) -> None:
    """畸形严格总结不伪成功、不触发嵌套重试，并保留失败子原因。"""

    provider = MagicMock()
    provider.text_chat = AsyncMock(
        return_value=_response('{"memories":[{"content":"unterminated"}')
    )
    processor = MemoryProcessor(llm_provider=provider)

    with pytest.raises(SummaryParseError) as raised:
        await processor.process_conversation(
            sample_message,
            strict_summary=True,
            llm_max_retries=3,
        )

    assert raised.value.reason == "json_invalid"
    assert provider.text_chat.await_count == 1
    assert metric_spies["parse_attempts"].total == 1
    assert metric_spies["parse_successes"].total == 0
    assert metric_spies["parse_failures"].label_values == [
        ({"sub_reason": "json_invalid"}, 1)
    ]


@pytest.mark.asyncio
async def test_provider_retry_then_parse_success_has_separate_denominators(
    monkeypatch: pytest.MonkeyPatch,
    metric_spies: dict[str, _Metric],
    sample_message: list[Message],
) -> None:
    """Provider 首次失败后重试成功只产生一次解析结果。"""

    payload = (
        '{"memories":[{"content":"用户喜欢手冲咖啡","key_facts":["用户喜欢手冲咖啡"]}]}'
    )
    provider = MagicMock()
    provider.text_chat = AsyncMock(
        side_effect=[RuntimeError("transient"), _response(text=payload)]
    )
    monkeypatch.setattr(llm_client_module.random, "uniform", lambda *_: 0.0)
    monkeypatch.setattr(llm_client_module.asyncio, "sleep", AsyncMock())
    processor = MemoryProcessor(llm_provider=provider)

    result = await processor.process_conversation(
        sample_message,
        strict_summary=True,
        llm_max_retries=2,
    )

    assert result
    assert provider.text_chat.await_count == 2
    assert metric_spies["attempts"].total == 2
    assert metric_spies["parse_attempts"].total == 1
    assert metric_spies["parse_successes"].total == 1
    assert metric_spies["parse_failures"].label_values == []


@pytest.mark.asyncio
async def test_cancelled_physical_call_is_recorded_and_propagated(
    metric_spies: dict[str, _Metric],
) -> None:
    """取消不重试，必须计数一次并记录终止原因后继续向上传播。"""

    provider = MagicMock()
    provider.text_chat = AsyncMock(side_effect=asyncio.CancelledError())

    client = llm_client_module.LLMClient(llm_provider=provider)
    with pytest.raises(asyncio.CancelledError):
        await client.call_llm_with_retry_result(
            "prompt",
            "system",
            max_retries=2,
            operation="summary_extraction",
        )

    assert provider.text_chat.await_count == 1
    assert metric_spies["attempts"].total == 1
    assert metric_spies["finish_reasons"].label_values == [
        ({"finish_reason": "unknown"}, 1)
    ]


@pytest.mark.asyncio
async def test_generic_operation_is_not_counted_as_summary_extraction(
    metric_spies: dict[str, _Metric],
) -> None:
    """非总结抽取的 Provider 调用不进入反思物理调用与终止原因分母。"""

    provider = MagicMock()
    provider.text_chat = AsyncMock(return_value=_response(None))

    client = llm_client_module.LLMClient(llm_provider=provider)
    await client.call_llm_with_retry_result("prompt", "system")

    assert provider.text_chat.await_count == 1
    assert metric_spies["attempts"].total == 0
    assert metric_spies["finish_reasons"].label_values == []
