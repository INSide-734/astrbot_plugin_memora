"""安全阻断与空回复的闭集原因码契约。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class TestReflectionReplyReasons:
    """安全阻断路径的原因码必须稳定、闭集且不含被拒内容。"""

    def test_validation_failure_emits_blocked_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """校验失败必须返回空串并发出闭集原因码，且原因不含被拒正文。"""

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
        service.sanitize_response.return_value = ("", {"validation_passed": False})
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

        assert handler._sanitize_response_text("被拒正文", "session-1") == ""
        assert [event.get("reason_code") for event in events] == [
            "reply_blocked_validation"
        ]
        assert all("被拒正文" not in str(event) for event in events)

    def test_missing_protection_scope_emits_blocked_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """必需保护作用域缺失时按固定终态阻断并记录原因码。"""

        from core.features.reflection.application import (
            reflection_context as context_module,
        )
        from core.features.reflection.application.reflection_handler import (
            ReflectionHandler,
        )

        cfg = MagicMock()
        cfg.get.side_effect = lambda key, default=None: default
        service = MagicMock()
        service.has_scope.return_value = False
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

        assert (
            handler._sanitize_response_text(
                "正常回复",
                "session-1",
                scope_id="scope-a",
                protection_required=True,
            )
            == ""
        )
        assert [event.get("reason_code") for event in events] == ["reply_blocked_scope"]
