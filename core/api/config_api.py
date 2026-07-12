"""Initialization-independent configuration Page API handlers."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from astrbot.api import logger

from ..base.config_manager import (
    ConfigConflictError,
    ConfigPersistenceError,
    ConfigValidationError,
)
from .response_utils import ok_response

_PLUGIN_NAME = "astrbot_plugin_memora"
_NO_DATA = object()


def _config_error(
    code: str,
    message: str,
    *,
    data: Any = _NO_DATA,
) -> dict[str, Any]:
    """Build the stable error envelope used only by configuration endpoints."""
    response: dict[str, Any] = {
        "status": "error",
        "code": code,
        "message": str(message),
    }
    if data is not _NO_DATA:
        response["data"] = data
    return response


class ConfigApiMixin:
    """Expose schema and transactional configuration operations to the dashboard."""

    async def get_config_schema(self) -> dict[str, Any]:
        """Return the injected AstrBot schema and currently available providers."""
        try:
            schema = getattr(self.plugin.astrbot_config, "schema", None)
            if not isinstance(schema, Mapping) or not schema:
                raise ValueError("invalid schema")
            schema_snapshot = copy.deepcopy(dict(schema))
        except Exception:
            logger.warning("[ConfigApi] AstrBot 配置 Schema 不可用")
            return _config_error(
                "schema_unavailable",
                "AstrBot 配置 Schema 不可用",
            )

        context = self.plugin.context
        return ok_response(
            {
                "plugin_name": _PLUGIN_NAME,
                "schema": schema_snapshot,
                "provider_options": {
                    "llm": self._provider_options(context, "get_all_providers"),
                    "embedding": self._provider_options(
                        context,
                        "get_all_embedding_providers",
                    ),
                },
                "capabilities": {
                    "hot_reload": self._supports_plugin_reload(),
                },
            }
        )

    async def get_config_state(self) -> dict[str, Any]:
        """Return a conditional full snapshot without requiring initialized engines."""
        config, revision = self.plugin.config_manager.get_config_snapshot()
        request = getattr(self.plugin.context, "request", None)
        args = getattr(request, "args", {}) or {}
        try:
            requested_revision = args.get("revision")
        except Exception:
            requested_revision = None

        changed = requested_revision != revision
        data: dict[str, Any] = {
            "revision": revision,
            "instance_id": self.plugin.instance_id,
            "changed": changed,
        }
        if changed:
            data["config"] = config
        return ok_response(data)

    async def apply_config(self) -> dict[str, Any]:
        """Validate, persist, and schedule reload for a revision-guarded update."""
        guard = self._maintenance_write_guard()
        if guard is not None:
            return guard

        base_revision, changes, request_error = await self._read_apply_request()
        if request_error is not None:
            return request_error

        try:
            assert base_revision is not None and changes is not None
            result = await self.plugin.config_manager.apply_config_changes(
                changes,
                expected_revision=base_revision,
                persist=True,
            )
        except (
            ConfigConflictError,
            ConfigValidationError,
            ConfigPersistenceError,
        ) as exc:
            return self._config_apply_error(exc)

        reload_scheduled = self._schedule_plugin_reload(result.changed_paths)

        logger.info(
            "[ConfigApi] 配置已应用 revision=%s paths=%s",
            result.revision,
            list(result.changed_paths),
        )
        return ok_response(
            {
                "revision": result.revision,
                "changed_paths": list(result.changed_paths),
                "reload_scheduled": reload_scheduled,
                "instance_id": self.plugin.instance_id,
            }
        )

    async def _read_apply_request(
        self,
    ) -> tuple[str | None, dict[str, Any] | None, dict[str, Any] | None]:
        try:
            body = await self.plugin.context.request.json()
        except Exception:
            return None, None, _config_error(
                "invalid_request",
                "请求体必须是有效的 JSON 对象",
            )
        if not isinstance(body, dict):
            return None, None, _config_error(
                "invalid_request",
                "请求体必须是 JSON 对象",
            )
        if set(body) != {"base_revision", "changes"}:
            return None, None, _config_error(
                "invalid_request",
                "请求体字段必须严格为 base_revision 和 changes",
            )

        base_revision = body.get("base_revision")
        changes = body.get("changes")
        if not isinstance(base_revision, str) or not base_revision.strip():
            return None, None, _config_error(
                "invalid_request",
                "base_revision 必须是非空字符串",
            )
        if not isinstance(changes, dict):
            return None, None, _config_error(
                "invalid_request",
                "changes 必须是 JSON 对象",
            )
        return base_revision, changes, None

    @staticmethod
    def _config_apply_error(
        exc: ConfigConflictError | ConfigValidationError | ConfigPersistenceError,
    ) -> dict[str, Any]:
        if isinstance(exc, ConfigConflictError):
            logger.warning(
                "[ConfigApi] 配置修订冲突 current_revision=%s",
                exc.current_revision,
            )
            return _config_error(
                "config_conflict",
                str(exc),
                data={"current_revision": exc.current_revision},
            )
        if isinstance(exc, ConfigValidationError):
            logger.warning(
                "[ConfigApi] 配置验证失败 paths=%s",
                sorted(exc.field_errors),
            )
            return _config_error(
                "validation_failed",
                str(exc),
                data={"field_errors": copy.deepcopy(exc.field_errors)},
            )

        logger.error("[ConfigApi] 配置持久化失败")
        return _config_error("persist_failed", str(exc))

    def _schedule_plugin_reload(self, changed_paths: tuple[str, ...]) -> bool:
        if not changed_paths:
            return False
        schedule_reload = getattr(
            self.plugin,
            "schedule_plugin_reload",
            None,
        )
        if not callable(schedule_reload):
            return False
        try:
            return bool(schedule_reload())
        except Exception:
            logger.error("[ConfigApi] 无法安排插件重载", exc_info=True)
            return False

    def _supports_plugin_reload(self) -> bool:
        supports_reload = getattr(
            self.plugin,
            "supports_plugin_reload",
            None,
        )
        if callable(supports_reload):
            try:
                return bool(supports_reload())
            except Exception:
                return False
        return False

    @classmethod
    def _provider_options(
        cls,
        context: Any,
        getter_name: str,
    ) -> list[dict[str, str]]:
        getter = getattr(context, getter_name, None)
        if not callable(getter):
            return []
        try:
            providers = getter()
        except Exception:
            logger.warning("[ConfigApi] Provider 列表暂不可用: %s", getter_name)
            return []
        if providers is None:
            return []

        options: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        try:
            for provider in providers:
                option = cls._provider_option(provider)
                if option is None or option["id"] in seen_ids:
                    continue
                seen_ids.add(option["id"])
                options.append(option)
        except Exception:
            logger.warning("[ConfigApi] Provider 列表尚未完全初始化: %s", getter_name)
        return options

    @classmethod
    def _provider_option(cls, provider: Any) -> dict[str, str] | None:
        sources: list[Any] = []
        meta_method = cls._read_value(provider, "meta")
        if callable(meta_method):
            try:
                sources.append(meta_method())
            except Exception:
                pass
        provider_config = cls._read_value(provider, "provider_config")
        if provider_config is not None:
            sources.append(provider_config)
        sources.append(provider)

        provider_id = cls._first_text(sources, ("id", "provider_id"))
        if provider_id is None:
            return None

        label = cls._first_text(
            sources,
            (
                "label",
                "name",
                "display_name",
                "provider_display_name",
                "model",
                "model_name",
                "type",
            ),
        )
        return {"id": provider_id, "label": label or provider_id}

    @classmethod
    def _first_text(
        cls,
        sources: list[Any],
        field_names: tuple[str, ...],
    ) -> str | None:
        for field_name in field_names:
            for source in sources:
                value = cls._read_value(source, field_name)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    @staticmethod
    def _read_value(source: Any, field_name: str) -> Any:
        if source is None:
            return None
        try:
            if isinstance(source, Mapping):
                return source.get(field_name)
            return getattr(source, field_name, None)
        except Exception:
            return None
