"""自主学习生产动作 Page API 的严格契约测试。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.api.learning_api import LearningApiMixin, _candidate_view
from core.features.learning.infrastructure.learning_config_adapter import (
    LearningConfigAdapter,
)

_CANDIDATE_ID = "J6LuM5hGZz4h8Jx8KmsFjB9Q"
_OPERATION_ID = "Q7VvN6iHak2Nz1cX4d8PmL0R"
_PUBLICATION_ID = "Y8SuQ4qDbn7Lc2wN5x9AkE1T"


class _RawRequest:
    """提供 Quart action handler 所需的最小原始请求接口。"""

    def __init__(self, raw_body: bytes, *, content_length: int | None = None) -> None:
        """保存原始请求体与可覆盖的 Content-Length。"""

        self.raw_body = raw_body
        self.content_length = (
            len(raw_body) if content_length is None else content_length
        )
        self.read_count = 0

    async def get_data(self, *, cache: bool = True) -> bytes:
        """返回原始 body，并记录 handler 是否在 guard 后读取。"""

        del cache
        self.read_count += 1
        return self.raw_body


class _PluginRequest:
    """模拟 AstrBot PluginRequest 的原始 body 与 headers 接口。"""

    def __init__(self, raw_body: bytes, *, content_length: int | None = None) -> None:
        """保存原始请求体，并以字符串 header 暴露长度。"""

        self.raw_body = raw_body
        length = len(raw_body) if content_length is None else content_length
        self.headers = {"Content-Length": str(length)}
        self.read_count = 0

    async def body(self) -> bytes:
        """返回未经解析的 body，并记录读取次数。"""

        self.read_count += 1
        return self.raw_body


def _request(payload: Any) -> _RawRequest:
    """把测试对象编码成 UTF-8 JSON 原始请求。"""

    return _RawRequest(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _valid_payload(*, action: str = "publish") -> dict[str, Any]:
    """构造只含四个允许字段的有效动作请求。"""

    return {
        "action": action,
        "candidate_id": _CANDIDATE_ID,
        "expected_revision": "config-revision-1",
        "confirm": True,
    }


def _make_api(
    *,
    publish_result: dict[str, Any] | None = None,
    rollback_result: dict[str, Any] | None = None,
    guard_result: dict[str, Any] | None = None,
    reload_scheduled: bool = True,
) -> tuple[Any, MagicMock, MagicMock]:
    """构造只暴露 Learning API 所需能力的隔离 mixin。"""

    class _LearningApi(LearningApiMixin):
        """为 mixin 提供可控的宿主依赖。"""

    manager = MagicMock()
    manager.publish_candidate = AsyncMock(
        return_value=publish_result
        or {
            "published": True,
            "reason_code": "published",
            "candidate_id": _CANDIDATE_ID,
            "operation_id": _OPERATION_ID,
            "publication_revision": _PUBLICATION_ID,
            "applied_revision": "config-revision-2",
            "changed_paths": [
                "graph_memory.document_route_weight",
                "graph_memory.graph_route_weight",
            ],
        }
    )
    manager.rollback_last_publish = AsyncMock(
        return_value=rollback_result
        or {
            "restored": True,
            "reason_code": "restored",
            "candidate_id": _CANDIDATE_ID,
            "operation_id": _OPERATION_ID,
            "applied_revision": "config-revision-3",
            "active_publication_revision": None,
            "changed_paths": [
                "graph_memory.document_route_weight",
                "graph_memory.graph_route_weight",
            ],
        }
    )
    manager.reset = AsyncMock(return_value={"reset": True, "reason_code": "reset"})
    manager.record_reload_operation = AsyncMock(
        side_effect=lambda **kwargs: {"state": kwargs["state"]}
    )
    manager.update_reload_operation = AsyncMock(
        side_effect=lambda operation_id, **kwargs: {
            "operation_id": operation_id,
            "state": kwargs["state"],
        }
    )
    engine = MagicMock(spec=[])
    engine.auto_learning = manager
    config_manager = MagicMock()

    api = _LearningApi()
    api.plugin = SimpleNamespace(config_manager=config_manager)
    api._maintenance_write_guard = MagicMock(return_value=guard_result)
    api._ensure_plugin_ready = AsyncMock(return_value=({"memory_engine": engine}, None))
    api._schedule_plugin_reload = MagicMock(return_value=reload_scheduled)
    return api, manager, config_manager


def _split_response(result: Any) -> tuple[dict[str, Any], int]:
    """把 handler 的 JSON/status 返回值标准化为便于断言的二元组。"""

    assert isinstance(result, tuple) and len(result) == 2
    payload, status_code = result
    assert isinstance(payload, dict)
    assert isinstance(status_code, int)
    return payload, status_code


class TestLearningActionValidation:
    """验证 guard 和原始请求严格 schema 在任何业务访问之前生效。"""

    @pytest.mark.asyncio
    async def test_maintenance_guard_runs_before_body_engine_and_config_access(
        self,
    ) -> None:
        """维护阻塞时不得读取 body、engine 或 ConfigManager。"""

        blocked = {"status": "error", "code": "maintenance_blocked"}
        api, manager, _ = _make_api(guard_result=blocked)
        api._ensure_plugin_ready = AsyncMock(
            side_effect=AssertionError("guard 必须先于 engine 访问")
        )
        api._get_web_request = MagicMock(
            side_effect=AssertionError("guard 必须先于请求对象访问")
        )
        raw_request = _request(_valid_payload())

        with patch("core.api.learning_api.request", raw_request):
            result = await api.learning_action()

        assert result is blocked
        assert raw_request.read_count == 0
        api._ensure_plugin_ready.assert_not_awaited()
        api._get_web_request.assert_not_called()
        manager.publish_candidate.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"action": "publish"},
            {**_valid_payload(), "weights": {"document_route_weight": 0.8}},
            {**_valid_payload(), "scope": "private:canary"},
            {**_valid_payload(), "persona_id": "persona-canary"},
            {**_valid_payload(), "candidate_key": "candidate-canary"},
            {**_valid_payload(), "binding": {"evidence_passed": True}},
            {**_valid_payload(), "force": True},
            {**_valid_payload(), "action": "approve"},
            {**_valid_payload(), "candidate_id": "short"},
            {**_valid_payload(), "candidate_id": "Ａ" * 24},
            {**_valid_payload(), "candidate_id": "A" * 129},
            {**_valid_payload(), "expected_revision": ""},
            {**_valid_payload(), "expected_revision": "修订"},
            {**_valid_payload(), "expected_revision": "r" * 129},
            {**_valid_payload(), "confirm": False},
            {**_valid_payload(), "confirm": 1},
        ],
    )
    async def test_invalid_schema_is_rejected_before_manager(
        self,
        payload: dict[str, Any],
    ) -> None:
        """未知字段、错误类型和非法动作句柄统一返回 400。"""

        api, manager, _ = _make_api()

        with patch("core.api.learning_api.request", _request(payload)):
            response, status_code = _split_response(await api.learning_action())

        assert status_code == 400
        assert response == {
            "status": "error",
            "error": {
                "code": "invalid_request",
                "message": "自主学习动作请求无效",
                "retryable": False,
            },
        }
        manager.publish_candidate.assert_not_awaited()
        manager.rollback_last_publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_duplicate_json_key_is_rejected_before_manager(self) -> None:
        """原始 JSON 中重复 key 不得被解析器的后值覆盖。"""

        api, manager, _ = _make_api()
        raw = (
            b'{"action":"publish","action":"rollback",'
            b'"candidate_id":"J6LuM5hGZz4h8Jx8KmsFjB9Q",'
            b'"expected_revision":"config-revision-1","confirm":true}'
        )

        with patch("core.api.learning_api.request", _RawRequest(raw)):
            response, status_code = _split_response(await api.learning_action())

        assert status_code == 400
        assert response["error"]["code"] == "invalid_request"
        manager.publish_candidate.assert_not_awaited()
        manager.rollback_last_publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_oversized_body_is_rejected_without_reading_it(self) -> None:
        """可信 Content-Length 超限时应在 JSON 读取前失败。"""

        api, manager, _ = _make_api()
        raw_request = _RawRequest(b"{}", content_length=16_385)

        with patch("core.api.learning_api.request", raw_request):
            response, status_code = _split_response(await api.learning_action())

        assert status_code == 400
        assert response["error"]["code"] == "invalid_request"
        assert raw_request.read_count == 0
        manager.publish_candidate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_plugin_request_body_is_used_without_parsed_json_fallback(
        self,
    ) -> None:
        """AstrBot PluginRequest 必须通过原始 body 保留重复键检测能力。"""

        api, manager, _ = _make_api()
        raw_request = _PluginRequest(
            json.dumps(_valid_payload(), separators=(",", ":")).encode("utf-8")
        )
        api._get_web_request = MagicMock(return_value=raw_request)

        with patch("core.api.learning_api.request", object()):
            response, status_code = _split_response(await api.learning_action())

        assert status_code == 200
        assert response["data"]["status"] == "published"
        assert raw_request.read_count == 1
        manager.publish_candidate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_plugin_request_content_length_is_checked_before_body(self) -> None:
        """PluginRequest 的 Content-Length 超限时不得读取 body。"""

        api, manager, _ = _make_api()
        raw_request = _PluginRequest(b"{}", content_length=16_385)
        api._get_web_request = MagicMock(return_value=raw_request)

        response, status_code = _split_response(await api.learning_action())

        assert status_code == 400
        assert response["error"]["code"] == "invalid_request"
        assert raw_request.read_count == 0
        manager.publish_candidate.assert_not_awaited()


class TestLearningActionResults:
    """验证 typed manager 结果到公开 envelope 的稳定映射。"""

    @pytest.mark.asyncio
    async def test_publish_uses_typed_adapter_without_client_binding(self) -> None:
        """API 只传 opaque ID、revision 与内部 adapter，不接受权重或 binding。"""

        api, manager, config_manager = _make_api()

        with patch("core.api.learning_api.request", _request(_valid_payload())):
            response, status_code = _split_response(await api.learning_action())

        assert status_code == 200
        assert response == {
            "status": "ok",
            "data": {
                "action": "publish",
                "candidate_id": _CANDIDATE_ID,
                "status": "published",
                "reason_code": "published",
                "operation_id": _OPERATION_ID,
                "publication_revision": _PUBLICATION_ID,
                "applied_revision": "config-revision-2",
                "changed_paths": [
                    "graph_memory.document_route_weight",
                    "graph_memory.graph_route_weight",
                ],
                "reload": {"state": "queued"},
            },
        }
        call = manager.publish_candidate.await_args
        assert call.args == (_CANDIDATE_ID,)
        assert call.kwargs["expected_revision"] == "config-revision-1"
        assert set(call.kwargs) == {"config_adapter", "expected_revision"}
        assert isinstance(call.kwargs["config_adapter"], LearningConfigAdapter)
        assert call.kwargs["config_adapter"]._config_manager is config_manager
        api._schedule_plugin_reload.assert_called_once_with(
            (
                "graph_memory.document_route_weight",
                "graph_memory.graph_route_weight",
            )
        )
        manager.record_reload_operation.assert_awaited_once_with(
            action="publish",
            candidate_id=_CANDIDATE_ID,
            operation_id=_OPERATION_ID,
            applied_revision="config-revision-2",
            changed_paths=(
                "graph_memory.document_route_weight",
                "graph_memory.graph_route_weight",
            ),
            state="queued",
        )

    @pytest.mark.asyncio
    async def test_reload_rejection_persists_restart_required_operation(self) -> None:
        """宿主拒绝排队时把已持久化 queued 收口为 restart_required。"""

        api, manager, _ = _make_api(reload_scheduled=False)

        with patch("core.api.learning_api.request", _request(_valid_payload())):
            response, status_code = _split_response(await api.learning_action())

        assert status_code == 200
        assert response["data"]["reload"] == {"state": "restart_required"}
        manager.record_reload_operation.assert_awaited_once_with(
            action="publish",
            candidate_id=_CANDIDATE_ID,
            operation_id=_OPERATION_ID,
            applied_revision="config-revision-2",
            changed_paths=(
                "graph_memory.document_route_weight",
                "graph_memory.graph_route_weight",
            ),
            state="queued",
        )
        manager.update_reload_operation.assert_awaited_once_with(
            _OPERATION_ID,
            state="restart_required",
            reason_code="reload_not_queued",
        )

    @pytest.mark.asyncio
    async def test_publish_uses_only_manager_reported_changed_paths(self) -> None:
        """成功响应和 reload 调度必须使用 manager 的真实 allowlist 路径。"""

        api, _, _ = _make_api(
            publish_result={
                "published": True,
                "reason_code": "published",
                "candidate_id": _CANDIDATE_ID,
                "operation_id": _OPERATION_ID,
                "publication_revision": _PUBLICATION_ID,
                "applied_revision": "config-revision-2",
                "changed_paths": ["graph_memory.document_route_weight"],
            }
        )

        with patch("core.api.learning_api.request", _request(_valid_payload())):
            response, status_code = _split_response(await api.learning_action())

        assert status_code == 200
        assert response["data"]["changed_paths"] == [
            "graph_memory.document_route_weight"
        ]
        api._schedule_plugin_reload.assert_called_once_with(
            ("graph_memory.document_route_weight",)
        )

    @pytest.mark.asyncio
    async def test_manager_cancellation_propagates_without_reload(self) -> None:
        """发布期间取消必须向上传播，API 不得把未知提交结果伪装成普通失败。"""

        api, manager, _ = _make_api()
        manager.publish_candidate.side_effect = asyncio.CancelledError

        with (
            patch("core.api.learning_api.request", _request(_valid_payload())),
            pytest.raises(asyncio.CancelledError),
        ):
            await api.learning_action()

        api._schedule_plugin_reload.assert_not_called()

    @pytest.mark.asyncio
    async def test_rollback_uses_same_action_endpoint_and_adapter(self) -> None:
        """rollback 复用同一严格入口并只调 manager 的回滚动作。"""

        api, manager, _ = _make_api()

        with patch(
            "core.api.learning_api.request",
            _request(_valid_payload(action="rollback")),
        ):
            response, status_code = _split_response(await api.learning_action())

        assert status_code == 200
        assert response["data"]["action"] == "rollback"
        assert response["data"]["status"] == "restored"
        assert response["data"]["changed_paths"] == [
            "graph_memory.document_route_weight",
            "graph_memory.graph_route_weight",
        ]
        assert response["data"]["reload"] == {"state": "queued"}
        manager.rollback_last_publish.assert_awaited_once()
        manager.publish_candidate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_not_applied_rollback_clears_intent_without_reload(self) -> None:
        """未提交的 prepared intent 收口成功时不得伪造配置恢复或安排重载。"""

        api, _, _ = _make_api(
            rollback_result={
                "restored": True,
                "reason_code": "not_applied",
                "candidate_id": _CANDIDATE_ID,
            }
        )

        with patch(
            "core.api.learning_api.request",
            _request(_valid_payload(action="rollback")),
        ):
            response, status_code = _split_response(await api.learning_action())

        assert status_code == 200
        assert response["data"] == {
            "action": "rollback",
            "candidate_id": _CANDIDATE_ID,
            "status": "not_applied",
            "reason_code": "not_applied",
            "changed_paths": [],
            "reload": {"state": "not_required"},
        }
        api._schedule_plugin_reload.assert_not_called()

    @pytest.mark.asyncio
    async def test_reload_rejection_requires_restart_after_successful_commit(
        self,
    ) -> None:
        """调度器返回 False 只表示未入队，配置成功仍应要求重启。"""

        api, _, _ = _make_api(reload_scheduled=False)

        with patch("core.api.learning_api.request", _request(_valid_payload())):
            response, status_code = _split_response(await api.learning_action())

        assert status_code == 200
        assert response["data"]["reload"] == {"state": "restart_required"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "internal_reason",
        ["learning_candidate_unavailable", "stale", "rejected"],
    )
    async def test_unavailable_candidate_states_share_one_404_oracle(
        self,
        internal_reason: str,
    ) -> None:
        """未知、过期和拒绝候选不得通过响应形状形成枚举 oracle。"""

        api, _, _ = _make_api(
            publish_result={
                "published": False,
                "reason_code": internal_reason,
            }
        )

        with patch("core.api.learning_api.request", _request(_valid_payload())):
            response, status_code = _split_response(await api.learning_action())

        assert status_code == 404
        assert response == {
            "status": "error",
            "error": {
                "code": "learning_candidate_unavailable",
                "message": "自主学习候选不可用",
                "retryable": False,
            },
        }
        assert _CANDIDATE_ID not in json.dumps(response)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("reason_code", "expected_status", "retryable"),
        [
            ("learning_publish_in_progress", 409, True),
            ("config_revision_conflict", 409, True),
            ("config_noop", 409, False),
            ("config_diverged", 409, False),
            ("config_validation_failed", 422, False),
            ("learning_state_persistence_failed", 503, False),
        ],
    )
    async def test_failure_codes_have_fixed_status_and_retryability(
        self,
        reason_code: str,
        expected_status: int,
        retryable: bool,
    ) -> None:
        """并发、CAS、校验和持久化失败使用固定公开映射。"""

        api, _, _ = _make_api(
            publish_result={"published": False, "reason_code": reason_code}
        )

        with patch("core.api.learning_api.request", _request(_valid_payload())):
            response, status_code = _split_response(await api.learning_action())

        assert status_code == expected_status
        assert response["error"]["code"] == reason_code
        assert response["error"]["retryable"] is retryable

    @pytest.mark.asyncio
    async def test_recovery_error_reports_only_verified_commit_metadata(self) -> None:
        """配置已提交但状态未收口时返回真实 revision 与 reload 状态。"""

        api, _, _ = _make_api(
            publish_result={
                "published": False,
                "reason_code": "learning_publish_recovery_required",
                "config_applied": True,
                "recovery_required": True,
                "applied_revision": "config-revision-2",
                "changed_paths": [
                    "graph_memory.document_route_weight",
                    "graph_memory.graph_route_weight",
                ],
                "internal_key": "MUST-NOT-LEAK",
            }
        )

        with patch("core.api.learning_api.request", _request(_valid_payload())):
            response, status_code = _split_response(await api.learning_action())

        assert status_code == 503
        assert response["error"] == {
            "code": "learning_publish_recovery_required",
            "message": "自主学习发布需要人工恢复",
            "retryable": False,
            "config_applied": True,
            "applied_revision": "config-revision-2",
            "reload": {"state": "restart_required"},
        }
        assert "MUST-NOT-LEAK" not in json.dumps(response)

    @pytest.mark.asyncio
    async def test_reset_is_protected_by_maintenance_guard(self) -> None:
        """reset 作为写操作必须在 engine 访问前执行维护守卫。"""

        blocked = {"status": "error", "code": "maintenance_blocked"}
        api, manager, _ = _make_api(guard_result=blocked)
        api._ensure_plugin_ready = AsyncMock(
            side_effect=AssertionError("guard 必须先于 engine 访问")
        )

        result = await api.reset_learning()

        assert result is blocked
        api._ensure_plugin_ready.assert_not_awaited()
        manager.reset.assert_not_awaited()


def test_candidate_view_exposes_only_valid_action_handles() -> None:
    """候选视图提供 action 所需 ID/revision，但不公开 evidence binding。"""

    view = _candidate_view(
        {
            "candidate_id": _CANDIDATE_ID,
            "source_config_revision": "config-revision-1",
            "evidence_revision": "EVIDENCE-MUST-NOT-LEAK",
            "candidate_key": "KEY-MUST-NOT-LEAK",
            "status": "ready_for_review",
            "reason_code": "candidate",
        }
    )

    assert view["candidate_id"] == _CANDIDATE_ID
    assert view["expected_revision"] == "config-revision-1"
    assert "evidence_revision" not in view
    assert "candidate_key" not in view
    assert "MUST-NOT-LEAK" not in json.dumps(view)
