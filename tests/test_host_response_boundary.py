"""宿主响应对投递边界的只读能力探测与终态分类。"""

from __future__ import annotations

import asyncio
import enum
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.shared.adapter_capabilities import (
    ASTRBOT_HOST_RESPONSE_CAPABILITIES,
    AdapterCapability,
    AdapterKind,
    ResponseDelivery,
    SupportLevel,
    probe_host_response_boundary,
)


class HostResultContentType(enum.Enum):
    """宿主 ``ResultContentType`` 的成员镜像。

    测试环境以 ``tests/conftest.py`` 的替身替代 AstrBot 包，这里只镜像真实宿主
    （4.27/4.28）已核对的成员名，用于构造事件替身。
    """

    LLM_RESULT = enum.auto()
    AGENT_RUNNER_ERROR = enum.auto()
    GENERAL_RESULT = enum.auto()
    STREAMING_RESULT = enum.auto()
    STREAMING_FINISH = enum.auto()


_ACK_REASON_CODES = frozenset(
    {
        "assistant_response_persisted",
        "delivered",
        "sent",
        "reply_delivered",
        "reply_sent",
    }
)


def _assert_no_delivery_claim(events: list[dict[str, object]]) -> None:
    """阻断/空回复路径不得出现送达或持久化成功声明。"""

    assert all(event.get("reason_code") not in _ACK_REASON_CODES for event in events)
    assert all(event.get("status") != "completed" for event in events)


def _reason_codes(events: list[dict[str, object]]) -> list[object]:
    return [event.get("reason_code") for event in events]


def _streaming_event(**overrides: object) -> SimpleNamespace:
    """构造一个宿主已发布流式结果的事件替身。"""

    event = SimpleNamespace(
        unified_msg_origin="session-1",
        get_extra=lambda key: None,
        get_result=lambda: SimpleNamespace(
            result_content_type=HostResultContentType.STREAMING_RESULT
        ),
    )
    event.__dict__.update(overrides)
    return event


def _direct_event(**overrides: object) -> SimpleNamespace:
    """构造一个已发布非流式结果的事件替身。"""

    event = SimpleNamespace(
        unified_msg_origin="session-1",
        get_extra=lambda key: None,
        get_result=lambda: SimpleNamespace(
            result_content_type=HostResultContentType.LLM_RESULT
        ),
    )
    event.__dict__.update(overrides)
    return event


class TestHostResponseBoundaryProbe:
    """能力探测只能依据宿主显式信号，缺失时保持保守。"""

    def test_host_contract_declares_no_precheck_terminal_or_ack(self) -> None:
        """真实宿主未提供插件可用的预检、失败终态或送达回执。"""

        contract = ASTRBOT_HOST_RESPONSE_CAPABILITIES

        assert contract.kind is AdapterKind.HOST_RESPONSE_BOUNDARY
        for capability in (
            AdapterCapability.STREAMING_PRECHECK,
            AdapterCapability.EXPLICIT_FAILURE_TERMINAL,
            AdapterCapability.DELIVERY_ACK,
        ):
            assert contract.level(capability) is SupportLevel.UNSUPPORTED
            assert not contract.supports(capability)

    def test_streaming_result_published_by_host_is_reported_as_streaming(self) -> None:
        """宿主已发布 STREAMING_RESULT 时不得把投递当作发送前可替换。"""

        boundary = probe_host_response_boundary(_streaming_event(_has_send_oper=True))

        assert boundary.delivery is ResponseDelivery.STREAMING
        assert boundary.send_operation_started is True

    def test_missing_host_signals_stay_conservative(self) -> None:
        """事件缺失、形状不同或读取异常时一律返回未知投递。"""

        class FutureResultContentType(enum.Enum):
            """宿主未来新增的成员名不得被当作已核对类型推断。"""

            FUTURE_STREAMING_DELTA = enum.auto()

        assert probe_host_response_boundary(None).delivery is ResponseDelivery.UNKNOWN
        assert (
            probe_host_response_boundary(SimpleNamespace()).delivery
            is ResponseDelivery.UNKNOWN
        )
        assert (
            probe_host_response_boundary(MagicMock()).delivery
            is ResponseDelivery.UNKNOWN
        )
        assert (
            probe_host_response_boundary(
                SimpleNamespace(
                    get_result=lambda: SimpleNamespace(
                        result_content_type="STREAMING_RESULT"
                    )
                )
            ).delivery
            is ResponseDelivery.UNKNOWN
        )
        assert (
            probe_host_response_boundary(
                SimpleNamespace(
                    get_result=lambda: SimpleNamespace(
                        result_content_type=(
                            FutureResultContentType.FUTURE_STREAMING_DELTA
                        )
                    )
                )
            ).delivery
            is ResponseDelivery.UNKNOWN
        )
        failing = SimpleNamespace(
            get_result=MagicMock(side_effect=RuntimeError("host unavailable"))
        )
        assert (
            probe_host_response_boundary(failing).delivery is ResponseDelivery.UNKNOWN
        )

    def test_missing_result_is_not_inferred_as_direct(self) -> None:
        """工具清理或流式分片期间的空 result 只能保持未知。"""

        event = SimpleNamespace(get_result=lambda: None, _has_send_oper=False)
        boundary = probe_host_response_boundary(event)

        assert boundary.delivery is ResponseDelivery.UNKNOWN
        assert boundary.send_operation_started is False

    def test_probe_property_failure_is_unknown(self) -> None:
        """宿主属性读取异常不能阻断调用方，也不能伪称 direct。"""

        class BrokenEvent:
            @property
            def _has_send_oper(self):
                raise RuntimeError("unavailable")

        assert (
            probe_host_response_boundary(BrokenEvent()).delivery
            is ResponseDelivery.UNKNOWN
        )

    def test_unpublished_direct_result_is_direct_delivery(self) -> None:
        """已发布非流式结果时，结果链在发送前仍可替换。"""

        boundary = probe_host_response_boundary(_direct_event(_has_send_oper=False))

        assert boundary.delivery is ResponseDelivery.DIRECT
        assert boundary.send_operation_started is False


class TestReflectionReplyClassification:
    """回复终态分类必须区分阻断、空回复、中间步与多模态。"""

    def _handler(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        sanitize_result: tuple[str, dict[str, object]] | None = None,
        has_scope: bool = True,
    ) -> tuple[Any, list[dict[str, object]]]:
        from core.features.reflection.application import (
            reflection_context as context_module,
        )
        from core.features.reflection.application.reflection_handler import (
            ReflectionHandler,
        )

        cfg = MagicMock()
        cfg.get.side_effect = lambda key, default=None: {
            "security.sanitize_llm_response": True,
            "security.double_check_enabled": True,
        }.get(key, default)
        service = MagicMock()
        service.has_scope.return_value = has_scope
        service.sanitize_response.return_value = (
            sanitize_result
            if sanitize_result is not None
            else ("", {"validation_passed": True})
        )
        events: list[dict[str, object]] = []
        monkeypatch.setattr(
            context_module.observability,
            "report_debug_event",
            lambda *args, **kwargs: events.append(kwargs),
        )
        handler = ReflectionHandler(
            context=MagicMock(),
            config_manager=cfg,
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
            enforce_limit_cb=MagicMock(),
            prompt_protection_service=service,
        )
        return handler, events

    @pytest.mark.asyncio
    async def test_unverifiable_host_shape_is_reported_as_unverified_boundary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """宿主事件形状无法验证投递边界时记录未验证状态，不假设保护已先行。"""

        handler, events = self._handler(
            monkeypatch,
            sanitize_result=("正常回复", {"validation_passed": True}),
        )
        event = _direct_event(
            get_result=lambda: SimpleNamespace(result_content_type="STREAMING_RESULT"),
            _memora_prompt_protection_scope="scope-a",
            _memora_prompt_protection_required=True,
        )
        resp = SimpleNamespace(
            role="assistant",
            completion_text="正常回复",
            result_chain=None,
            tools_call_name=None,
            tools_call_extra_content=None,
        )

        await handler.handle_memory_reflection(event, resp)

        assert _reason_codes(events) == [
            "response_received",
            "reply_precheck_unverified",
        ]
        _assert_no_delivery_claim(events)

    @pytest.mark.asyncio
    async def test_streaming_precheck_unavailable_is_recorded_without_ack(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """流式投递下保护无法先于首片，记录不可用状态且不伪证送达。"""

        handler, events = self._handler(
            monkeypatch,
            sanitize_result=("", {"validation_passed": False}),
        )
        event = _streaming_event(
            _memora_prompt_protection_scope="scope-a",
            _memora_prompt_protection_required=True,
        )
        resp = SimpleNamespace(
            role="assistant",
            completion_text="被拒正文",
            result_chain=None,
            tools_call_name=None,
            tools_call_extra_content=None,
        )

        await handler.handle_memory_reflection(event, resp)

        assert _reason_codes(events) == [
            "response_received",
            "reply_streaming_precheck_unavailable",
            "reply_blocked_validation",
            "empty_response_after_sanitization",
        ]
        assert resp.completion_text == ""
        assert all("被拒正文" not in str(entry) for entry in events)
        _assert_no_delivery_claim(events)

    @pytest.mark.asyncio
    async def test_provider_empty_reply_is_classified_without_fake_ack(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Provider 最终空回复使用独立原因码，且不声明已经送达。"""

        handler, events = self._handler(monkeypatch)
        event = _direct_event(_has_send_oper=False)
        resp = SimpleNamespace(
            role="assistant",
            completion_text="",
            result_chain=None,
            tools_call_name=None,
            tools_call_extra_content=None,
        )

        await handler.handle_memory_reflection(event, resp)

        assert _reason_codes(events) == ["response_received", "reply_empty_provider"]
        _assert_no_delivery_claim(events)

    @pytest.mark.asyncio
    async def test_sanitizer_emptied_reply_keeps_distinct_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """清洗清空原文时不得与 Provider 空回复共用一个原因码。"""

        handler, events = self._handler(
            monkeypatch,
            sanitize_result=("", {"validation_passed": True, "leaks_removed": ["x"]}),
        )
        event = _direct_event(_has_send_oper=False)
        resp = SimpleNamespace(
            role="assistant",
            completion_text="包含内部原文的回复",
            result_chain=None,
            tools_call_name=None,
            tools_call_extra_content=None,
        )

        await handler.handle_memory_reflection(event, resp)

        assert _reason_codes(events) == [
            "response_received",
            "empty_response_after_sanitization",
        ]
        assert all("包含内部原文的回复" not in str(entry) for entry in events)
        _assert_no_delivery_claim(events)

    @pytest.mark.asyncio
    async def test_tool_intermediate_step_is_not_a_final_empty_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """工具中间步即使已发送内容也不得被计为最终空失败。"""

        handler, events = self._handler(monkeypatch)
        event = _direct_event(_has_send_oper=True)
        resp = SimpleNamespace(
            role="assistant",
            completion_text="",
            result_chain=None,
            tools_call_name=["recall_long_term_memory"],
            tools_call_extra_content=None,
        )

        await handler.handle_memory_reflection(event, resp)

        assert _reason_codes(events) == ["response_received", "tool_call_response"]
        _assert_no_delivery_claim(events)

    @pytest.mark.asyncio
    async def test_reply_already_sent_is_not_reported_as_provider_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """宿主已记录发送操作时不得把空文本当作未解释的生成失败。"""

        handler, events = self._handler(monkeypatch)
        event = _direct_event(_has_send_oper=True)
        resp = SimpleNamespace(
            role="assistant",
            completion_text="",
            result_chain=None,
            tools_call_name=None,
            tools_call_extra_content=None,
        )

        await handler.handle_memory_reflection(event, resp)

        assert _reason_codes(events) == [
            "response_received",
            "reply_empty_after_send_operation",
        ]
        _assert_no_delivery_claim(events)

    @pytest.mark.asyncio
    async def test_multimodal_reply_is_not_killed_as_empty_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """图片等非纯文本回复不得被文本为空误判为最终空失败。"""

        from astrbot.api.message_components import Image

        handler, events = self._handler(monkeypatch)
        event = _direct_event(_has_send_oper=False)
        resp = SimpleNamespace(
            role="assistant",
            completion_text="",
            result_chain=SimpleNamespace(chain=[Image(file="a.png")]),
            tools_call_name=None,
            tools_call_extra_content=None,
        )

        await handler.handle_memory_reflection(event, resp)

        assert _reason_codes(events) == ["response_received", "reply_non_text_content"]
        _assert_no_delivery_claim(events)


class TestReflectionCancellationBoundary:
    """取消信号必须穿透清洗边界并释放请求作用域。"""

    def test_cancellation_propagates_and_releases_scope(self) -> None:
        from core.features.reflection.application.reflection_handler import (
            ReflectionHandler,
        )

        service = MagicMock()
        service.sanitize_response.side_effect = asyncio.CancelledError()
        handler = ReflectionHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=MagicMock(),
            enforce_limit_cb=MagicMock(),
            prompt_protection_service=service,
        )

        with pytest.raises(asyncio.CancelledError):
            handler._sanitize_response_text(
                "普通回复",
                "session-1",
                scope_id="scope-a",
                protection_required=True,
            )

        service.discard_scope.assert_called_once_with("scope-a")
