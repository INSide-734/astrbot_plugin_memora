"""Tests for the initialization-independent configuration Page API."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.base.config_manager import (
    ConfigApplyResult,
    ConfigConflictError,
    ConfigPersistenceError,
    ConfigValidationError,
)


class _SchemaConfig(dict):
    def __init__(self, schema: Any) -> None:
        super().__init__({"bot_language": "zh"})
        self.schema = schema


class _Request:
    def __init__(
        self,
        *,
        args: dict[str, Any] | None = None,
        body: Any = None,
        json_error: Exception | None = None,
    ) -> None:
        self.args = args or {}
        self._body = body
        self._json_error = json_error

    async def json(self) -> Any:
        if self._json_error is not None:
            raise self._json_error
        return self._body


class _Provider:
    def __init__(self, provider_id: str, model: str, provider_type: str) -> None:
        self._meta = SimpleNamespace(
            id=provider_id,
            model=model,
            type=provider_type,
        )
        self.provider_config = {
            "id": provider_id,
            "model": model,
            "type": provider_type,
        }

    def meta(self) -> SimpleNamespace:
        return self._meta


def _make_api(
    *,
    schema: Any = None,
    request: _Request | None = None,
    llm_providers: list[Any] | None = None,
    embedding_providers: list[Any] | None = None,
    config_manager: Any = None,
    hot_reload: bool = False,
) -> tuple[Any, Any]:
    from core.api.config_api import ConfigApiMixin

    class _ConfigApi(ConfigApiMixin):
        pass

    context = SimpleNamespace(
        request=request or _Request(),
        get_all_providers=lambda: list(llm_providers or []),
        get_all_embedding_providers=lambda: list(embedding_providers or []),
    )
    plugin = SimpleNamespace(
        astrbot_config=_SchemaConfig(schema if schema is not None else {"field": {"type": "string"}}),
        config_manager=config_manager or MagicMock(),
        context=context,
        instance_id="instance-123",
        supports_plugin_reload=MagicMock(return_value=hot_reload),
        schedule_plugin_reload=MagicMock(return_value=hot_reload),
    )
    api = _ConfigApi()
    api.plugin = plugin
    api._maintenance_write_guard = MagicMock(return_value=None)
    return api, plugin


class TestConfigSchemaApi:
    @pytest.mark.asyncio
    async def test_preserves_schema_metadata_and_returns_deeply_isolated_copy(self) -> None:
        schema = {
            "provider_settings": {
                "type": "object",
                "description": "Provider settings",
                "hint": "Keep every metadata key",
                "items": {
                    "llm_provider_id": {
                        "type": "string",
                        "default": "llm-primary",
                        "_special": "select_provider",
                        "options": ["llm-primary"],
                        "custom_ui": {"group": "models", "order": 3},
                    }
                },
            }
        }
        api, plugin = _make_api(schema=schema, hot_reload=True)

        first = await api.get_config_schema()

        assert first == {
            "status": "ok",
            "data": {
                "plugin_name": "astrbot_plugin_memora",
                "schema": schema,
                "provider_options": {"llm": [], "embedding": []},
                "capabilities": {"hot_reload": True},
            },
        }
        assert first["data"]["schema"] is not plugin.astrbot_config.schema
        first["data"]["schema"]["provider_settings"]["items"]["llm_provider_id"][
            "custom_ui"
        ]["order"] = 99

        second = await api.get_config_schema()

        assert (
            plugin.astrbot_config.schema["provider_settings"]["items"][
                "llm_provider_id"
            ]["custom_ui"]["order"]
            == 3
        )
        assert (
            second["data"]["schema"]["provider_settings"]["items"][
                "llm_provider_id"
            ]["custom_ui"]["order"]
            == 3
        )

    @pytest.mark.asyncio
    async def test_enumerates_llm_and_embedding_provider_options(self) -> None:
        api, _ = _make_api(
            llm_providers=[_Provider("llm-primary", "gpt-5", "openai")],
            embedding_providers=[
                _Provider("embed-primary", "text-embedding-3-large", "openai_embedding")
            ],
        )

        result = await api.get_config_schema()

        assert result["data"]["provider_options"] == {
            "llm": [{"id": "llm-primary", "label": "gpt-5"}],
            "embedding": [
                {"id": "embed-primary", "label": "text-embedding-3-large"}
            ],
        }

    @pytest.mark.asyncio
    async def test_provider_failures_do_not_hide_other_available_options(self) -> None:
        api, plugin = _make_api(
            embedding_providers=[_Provider("embed-ok", "bge-m3", "ollama_embedding")]
        )
        plugin.context.get_all_providers = MagicMock(
            side_effect=RuntimeError("providers are still initializing")
        )

        result = await api.get_config_schema()

        assert result["status"] == "ok"
        assert result["data"]["provider_options"] == {
            "llm": [],
            "embedding": [{"id": "embed-ok", "label": "bge-m3"}],
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("schema", [None, [], {}, "bad-schema"])
    async def test_invalid_or_unavailable_schema_has_stable_error_shape(
        self, schema: Any
    ) -> None:
        api, plugin = _make_api()
        plugin.astrbot_config.schema = schema

        result = await api.get_config_schema()

        assert result["status"] == "error"
        assert result["code"] == "schema_unavailable"
        assert isinstance(result["message"], str) and result["message"]
        assert set(result) == {"status", "code", "message"}

    @pytest.mark.asyncio
    async def test_schema_read_does_not_require_initialized_memory_engine(self) -> None:
        api, plugin = _make_api()
        plugin._ensure_plugin_ready = AsyncMock(
            side_effect=AssertionError("config reads must not check plugin readiness")
        )

        result = await api.get_config_schema()

        assert result["status"] == "ok"
        plugin._ensure_plugin_ready.assert_not_awaited()


class TestConfigStateApi:
    @pytest.mark.asyncio
    async def test_reconciles_external_source_change_before_returning_state(self) -> None:
        from core.base.config_manager import ConfigManager

        source = {"recall_engine": {"top_k": 5}}
        manager = ConfigManager(source)
        _, original_revision = manager.get_config_snapshot()
        source["recall_engine"]["top_k"] = 9
        api, _ = _make_api(
            request=_Request(args={"revision": original_revision}),
            config_manager=manager,
        )

        result = await api.get_config_state()

        assert result["data"]["changed"] is True
        assert result["data"]["revision"] != original_revision
        assert result["data"]["config"]["recall_engine"]["top_k"] == 9

    @pytest.mark.asyncio
    async def test_matching_revision_omits_config_and_marks_unchanged(self) -> None:
        manager = MagicMock()
        manager.get_config_snapshot_async = AsyncMock(
            return_value=({"secret": "not-returned"}, "rev-1")
        )
        api, plugin = _make_api(
            request=_Request(args={"revision": "rev-1"}),
            config_manager=manager,
        )
        plugin._ensure_plugin_ready = AsyncMock(
            side_effect=AssertionError("config state must not require the engine")
        )

        result = await api.get_config_state()

        assert result == {
            "status": "ok",
            "data": {
                "revision": "rev-1",
                "instance_id": "instance-123",
                "changed": False,
            },
        }
        plugin._ensure_plugin_ready.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("args", [{}, {"revision": "rev-old"}])
    async def test_missing_or_stale_revision_returns_full_config(
        self, args: dict[str, str]
    ) -> None:
        config = {"provider_settings": {"llm_provider_id": "llm-primary"}}
        manager = MagicMock()
        manager.get_config_snapshot_async = AsyncMock(
            return_value=(config, "rev-current")
        )
        api, _ = _make_api(request=_Request(args=args), config_manager=manager)

        result = await api.get_config_state()

        assert result == {
            "status": "ok",
            "data": {
                "revision": "rev-current",
                "instance_id": "instance-123",
                "changed": True,
                "config": config,
            },
        }


class TestConfigApplyApi:
    @pytest.mark.asyncio
    async def test_maintenance_guard_runs_before_request_body_is_read(self) -> None:
        manager = MagicMock()
        manager.apply_config_changes = AsyncMock()
        api, plugin = _make_api(config_manager=manager)
        blocked = {"status": "error", "message": "maintenance pending"}
        api._maintenance_write_guard = MagicMock(return_value=blocked)
        plugin.context.request.json = AsyncMock(
            side_effect=AssertionError("guard must run before JSON parsing")
        )

        result = await api.apply_config()

        assert result is blocked
        api._maintenance_write_guard.assert_called_once_with()
        plugin.context.request.json.assert_not_awaited()
        manager.apply_config_changes.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload_request",
        [
            _Request(json_error=ValueError("malformed JSON")),
            _Request(body=["not", "an", "object"]),
            _Request(body={}),
            _Request(body={"base_revision": 123, "changes": {}}),
            _Request(body={"base_revision": "   ", "changes": {}}),
            _Request(body={"base_revision": "rev-1", "changes": []}),
            _Request(
                body={
                    "base_revision": "rev-1",
                    "changes": {},
                    "unexpected": "field",
                }
            ),
        ],
    )
    async def test_invalid_payloads_have_stable_error_shape(
        self, payload_request: _Request
    ) -> None:
        manager = MagicMock()
        manager.apply_config_changes = AsyncMock()
        api, _ = _make_api(request=payload_request, config_manager=manager)

        result = await api.apply_config()

        assert result["status"] == "error"
        assert result["code"] == "invalid_request"
        assert isinstance(result["message"], str) and result["message"]
        assert set(result) == {"status", "code", "message"}
        manager.apply_config_changes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_maps_revision_conflict_to_current_revision(self) -> None:
        manager = MagicMock()
        manager.apply_config_changes = AsyncMock(
            side_effect=ConfigConflictError("rev-old", "rev-current")
        )
        api, _ = _make_api(
            request=_Request(body={"base_revision": "rev-old", "changes": {"debug": True}}),
            config_manager=manager,
        )

        result = await api.apply_config()

        assert result == {
            "status": "error",
            "code": "config_conflict",
            "message": "配置已被其他请求更新，请刷新后重试",
            "data": {"current_revision": "rev-current"},
        }

    @pytest.mark.asyncio
    async def test_maps_validation_errors_to_field_errors(self) -> None:
        manager = MagicMock()
        manager.apply_config_changes = AsyncMock(
            side_effect=ConfigValidationError(
                {
                    "recall_engine.injection_decision_retention_days": (
                        "must be a supported retention period"
                    )
                }
            )
        )
        api, plugin = _make_api(
            request=_Request(
                body={
                    "base_revision": "rev-1",
                    "changes": {
                        "recall_engine.injection_decision_retention_days": 13
                    },
                }
            ),
            config_manager=manager,
        )
        recorder = MagicMock()
        plugin.initializer = SimpleNamespace(injection_decision_recorder=recorder)

        result = await api.apply_config()

        assert result == {
            "status": "error",
            "code": "validation_failed",
            "message": "配置验证失败",
            "data": {
                "field_errors": {
                    "recall_engine.injection_decision_retention_days": (
                        "must be a supported retention period"
                    )
                }
            },
        }
        plugin.schedule_plugin_reload.assert_not_called()
        recorder.schedule_cleanup.assert_not_called()

    @pytest.mark.asyncio
    async def test_maps_persistence_failure_without_scheduling_reload(self) -> None:
        manager = MagicMock()
        manager.apply_config_changes = AsyncMock(
            side_effect=ConfigPersistenceError("disk write failed")
        )
        api, plugin = _make_api(
            request=_Request(body={"base_revision": "rev-1", "changes": {"debug": True}}),
            config_manager=manager,
        )
        recorder = MagicMock()
        plugin.initializer = SimpleNamespace(injection_decision_recorder=recorder)

        result = await api.apply_config()

        assert result == {
            "status": "error",
            "code": "persist_failed",
            "message": "disk write failed",
        }
        plugin.schedule_plugin_reload.assert_not_called()
        recorder.schedule_cleanup.assert_not_called()

    @pytest.mark.asyncio
    async def test_retention_change_schedules_cleanup_without_awaiting(self) -> None:
        changes = {
            "recall_engine.injection_decision_retention_days": 90,
            "recall_engine.injection_decision_max_rows": 200_000,
        }
        manager = MagicMock()
        manager.apply_config_changes = AsyncMock(
            return_value=ConfigApplyResult(
                "rev-new",
                tuple(sorted(changes)),
            )
        )
        manager.get.side_effect = lambda path, default=None: {
            "recall_engine.injection_decision_retention_days": 90,
            "recall_engine.injection_decision_max_rows": 200_000,
        }.get(path, default)
        api, plugin = _make_api(
            request=_Request(
                body={"base_revision": "rev-old", "changes": changes}
            ),
            config_manager=manager,
        )
        recorder = MagicMock()
        plugin.initializer = SimpleNamespace(injection_decision_recorder=recorder)

        result = await api.apply_config()

        assert result["status"] == "ok"
        recorder.schedule_cleanup.assert_called_once_with(
            retention_days=90,
            max_rows=200_000,
        )

    @pytest.mark.asyncio
    async def test_unrelated_config_change_does_not_schedule_cleanup(self) -> None:
        manager = MagicMock()
        manager.apply_config_changes = AsyncMock(
            return_value=ConfigApplyResult(
                "rev-new",
                ("recall_engine.top_k",),
            )
        )
        api, plugin = _make_api(
            request=_Request(
                body={
                    "base_revision": "rev-old",
                    "changes": {"recall_engine.top_k": 8},
                }
            ),
            config_manager=manager,
        )
        recorder = MagicMock()
        plugin.initializer = SimpleNamespace(injection_decision_recorder=recorder)

        result = await api.apply_config()

        assert result["status"] == "ok"
        recorder.schedule_cleanup.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_applies_exact_transaction_and_never_logs_values(self) -> None:
        secret_value = "never-log-this-secret"
        changes = {
            "provider_settings.llm_provider_id": secret_value,
            "recall_engine.top_k": 8,
        }
        manager = MagicMock()
        manager.apply_config_changes = AsyncMock(
            return_value=ConfigApplyResult(
                revision="rev-new",
                changed_paths=(
                    "provider_settings.llm_provider_id",
                    "recall_engine.top_k",
                ),
            )
        )
        api, plugin = _make_api(
            request=_Request(body={"base_revision": "rev-old", "changes": changes}),
            config_manager=manager,
            hot_reload=True,
        )

        with patch("core.api.config_api.logger") as mock_logger:
            result = await api.apply_config()

        manager.apply_config_changes.assert_awaited_once_with(
            changes,
            expected_revision="rev-old",
            persist=True,
        )
        plugin.schedule_plugin_reload.assert_called_once_with()
        assert result == {
            "status": "ok",
            "data": {
                "revision": "rev-new",
                "changed_paths": [
                    "provider_settings.llm_provider_id",
                    "recall_engine.top_k",
                ],
                "reload_scheduled": True,
                "instance_id": "instance-123",
            },
        }
        assert secret_value not in repr(mock_logger.method_calls)

    @pytest.mark.asyncio
    async def test_success_reports_missing_reload_capability(self) -> None:
        manager = MagicMock()
        manager.apply_config_changes = AsyncMock(
            return_value=ConfigApplyResult("rev-new", ("debug",))
        )
        api, _ = _make_api(
            request=_Request(body={"base_revision": "rev-old", "changes": {"debug": True}}),
            config_manager=manager,
            hot_reload=False,
        )

        result = await api.apply_config()

        assert result["status"] == "ok"
        assert result["data"]["reload_scheduled"] is False
