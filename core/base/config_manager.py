"""集中管理 AstrBot 注入配置的验证、访问与原子更新。"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astrbot.api import logger
from pydantic import ValidationError as PydanticValidationError

from .config_validator import (
    MemoraConfig,
    get_default_config,
    merge_config_with_defaults,
)
from .exceptions import ConfigurationError

_SENTINEL = object()


@dataclass(frozen=True, slots=True)
class ConfigApplyResult:
    """成功应用配置后的稳定结果。"""

    revision: str
    changed_paths: tuple[str, ...]


class ConfigConflictError(ConfigurationError):
    """更新所基于的配置版本已过期。"""

    def __init__(self, expected_revision: str, current_revision: str):
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__("配置已被其他请求更新，请刷新后重试")


class ConfigValidationError(ConfigurationError):
    """配置字段或候选配置未通过验证。"""

    def __init__(self, field_errors: Mapping[str, str]):
        self.field_errors = dict(field_errors)
        super().__init__("配置验证失败")


class ConfigPersistenceError(ConfigurationError):
    """AstrBotConfig 原子保存失败。"""

    def __init__(self, message: str = "配置持久化失败"):
        super().__init__(message)


class ConfigManager:
    """以 AstrBot 注入的可变映射作为唯一配置来源。"""

    def __init__(
        self,
        user_config: MutableMapping[str, Any] | None = None,
    ) -> None:
        self._source_config = user_config if user_config is not None else {}
        self._config: dict[str, Any] = {}
        self._config_obj: MemoraConfig | None = None
        self._revision = ""
        self._source_revision = ""
        self._validation_errors: list[dict[str, Any]] = []
        self._apply_lock = asyncio.Lock()
        self._persistence_capable = callable(
            getattr(self._source_config, "save_config", None)
        )
        schema_contract = self._load_schema_contract(self._source_config)
        if schema_contract is None:
            self._schema_leaf_paths = None
            self._schema_leaf_options: dict[str, tuple[Any, ...]] = {}
        else:
            self._schema_leaf_paths, self._schema_leaf_options = schema_contract
        self._load_config()

    def _load_config(self) -> None:
        """合并模型默认值并校验 AstrBot 当前配置。"""
        try:
            config_obj, config, revision = self._read_source_state()
            self._config_obj = config_obj
            self._config = config
            self._revision = revision
            self._source_revision = revision
        except Exception as exc:
            raise ConfigurationError(f"配置加载失败: {exc}") from exc

    def _read_source_state(self) -> tuple[MemoraConfig, dict[str, Any], str]:
        source_snapshot = copy.deepcopy(dict(self._source_config))
        merged_config = merge_config_with_defaults(source_snapshot)
        config_obj = self._validate_with_branch_fallback(merged_config)
        config = config_obj.model_dump()
        return config_obj, config, self._compute_revision(config)

    def _reconcile_source_locked(self) -> None:
        """Publish external AstrBotConfig changes without persisting them."""
        config_obj, config, revision = self._read_source_state()
        if revision == self._source_revision:
            return
        self._config_obj = config_obj
        self._config = config
        self._revision = revision
        self._source_revision = revision

    def _validate_with_branch_fallback(
        self,
        merged_config: dict[str, Any],
    ) -> MemoraConfig:
        self._validation_errors = []
        validation_error: Exception | None = None
        try:
            return MemoraConfig(**merged_config)
        except Exception as first_error:
            validation_error = first_error
            logger.warning("配置验证失败，尝试按分支降级", exc_info=True)

        defaults = get_default_config()
        candidate = copy.deepcopy(merged_config)
        invalid_sections = self._extract_invalid_sections(validation_error)
        if not invalid_sections:
            invalid_sections = self._probe_invalid_sections(candidate, defaults)

        for section in sorted(invalid_sections):
            if section in defaults:
                candidate[section] = copy.deepcopy(defaults[section])
                self._validation_errors.append(
                    {
                        "section": section,
                        "action": "defaulted",
                        "error": str(validation_error or ""),
                    }
                )

        try:
            return MemoraConfig(**candidate)
        except Exception as second_error:
            logger.warning(
                "分支级配置降级后仍验证失败，已降级为默认配置",
                exc_info=True,
            )
            self._validation_errors.append(
                {
                    "section": "*",
                    "action": "full_default",
                    "error": str(second_error),
                }
            )
            try:
                return MemoraConfig(**defaults)
            except Exception as default_error:
                raise ConfigurationError(
                    f"加载默认配置失败: {default_error}"
                ) from default_error

    @staticmethod
    def _extract_invalid_sections(error: Exception) -> set[str]:
        errors = getattr(error, "errors", None)
        if not callable(errors):
            return set()
        sections: set[str] = set()
        try:
            for item in errors():
                loc = item.get("loc") if isinstance(item, dict) else None
                if loc:
                    sections.add(str(loc[0]))
        except Exception:
            return set()
        return sections

    @staticmethod
    def _probe_invalid_sections(
        merged_config: dict[str, Any],
        defaults: dict[str, Any],
    ) -> set[str]:
        invalid: set[str] = set()
        for section in defaults:
            if section not in merged_config:
                continue
            candidate = copy.deepcopy(merged_config)
            candidate[section] = copy.deepcopy(defaults[section])
            try:
                MemoraConfig(**candidate)
                invalid.add(section)
            except Exception:
                continue
        return invalid

    @staticmethod
    def _compute_revision(config: Mapping[str, Any]) -> str:
        canonical = json.dumps(
            config,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @classmethod
    def _load_schema_contract(
        cls,
        source_config: MutableMapping[str, Any],
    ) -> tuple[frozenset[str], dict[str, tuple[Any, ...]]] | None:
        injected_schema = getattr(source_config, "schema", None)
        injected_contract = cls._parse_schema_contract(injected_schema)
        if injected_contract is not None:
            return injected_contract
        if injected_schema is not None:
            logger.warning("AstrBot 注入的配置 schema 无效，尝试仓库 schema")

        schema_path = Path(__file__).resolve().parents[2] / "_conf_schema.json"
        try:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning(f"无法加载 AstrBot 配置 schema，跳过未知字段检查: {exc}")
            return None

        contract = cls._parse_schema_contract(schema)
        if contract is None:
            logger.warning("仓库 AstrBot 配置 schema 无效，无法检查配置字段")
            return None
        return contract

    @classmethod
    def _parse_schema_contract(
        cls,
        schema: Any,
    ) -> tuple[frozenset[str], dict[str, tuple[Any, ...]]] | None:
        if not isinstance(schema, dict) or not schema:
            return None
        paths: set[str] = set()
        options: dict[str, tuple[Any, ...]] = {}
        if (
            not cls._collect_schema_contract(schema, (), paths, options)
            or not paths
        ):
            return None
        return frozenset(paths), options

    @classmethod
    def _collect_schema_contract(
        cls,
        schema: Mapping[str, Any],
        prefix: tuple[str, ...],
        paths: set[str],
        options: dict[str, tuple[Any, ...]],
    ) -> bool:
        for key, field_schema in schema.items():
            if (
                not isinstance(key, str)
                or not key
                or not isinstance(field_schema, dict)
                or not isinstance(field_schema.get("type"), str)
            ):
                return False
            path = (*prefix, key)
            items = field_schema.get("items")
            if field_schema["type"] == "object":
                if not isinstance(items, dict) or not cls._collect_schema_contract(
                    items, path, paths, options
                ):
                    return False
            else:
                dotted_path = ".".join(path)
                paths.add(dotted_path)
                if "options" in field_schema:
                    raw_options = field_schema["options"]
                    if not isinstance(raw_options, list) or any(
                        not cls._is_json_scalar(option)
                        for option in raw_options
                    ):
                        return False
                    options[dotted_path] = tuple(copy.deepcopy(raw_options))
        return True

    @staticmethod
    def _is_json_scalar(value: Any) -> bool:
        if value is None or type(value) in (bool, int, str):
            return True
        return type(value) is float and math.isfinite(value)

    @classmethod
    def _matches_schema_option(
        cls,
        value: Any,
        options: tuple[Any, ...],
    ) -> bool:
        return cls._is_json_scalar(value) and any(
            type(value) is type(option) and value == option
            for option in options
        )

    def get_config_snapshot(self) -> tuple[dict[str, Any], str]:
        """返回与内部状态隔离的配置副本及其 SHA-256 修订号。"""
        return copy.deepcopy(self._config), self._revision

    async def get_config_snapshot_async(self) -> tuple[dict[str, Any], str]:
        """Reconcile the live AstrBotConfig and return an isolated snapshot."""
        async with self._apply_lock:
            self._reconcile_source_locked()
            return self.get_config_snapshot()

    async def apply_config_changes(
        self,
        changes: Mapping[str, Any],
        *,
        expected_revision: str | None = None,
        persist: bool = True,
    ) -> ConfigApplyResult:
        """串行校验并应用点号路径配置变更。"""
        async with self._apply_lock:
            self._reconcile_source_locked()
            if (
                expected_revision is not None
                and expected_revision != self._revision
            ):
                raise ConfigConflictError(expected_revision, self._revision)

            normalized_changes = dict(changes)
            if not normalized_changes:
                return ConfigApplyResult(self._revision, ())

            field_errors = self._validate_change_paths(normalized_changes)
            if field_errors:
                raise ConfigValidationError(field_errors)

            candidate = copy.deepcopy(self._config)
            for path, value in normalized_changes.items():
                self._set_dotted_value(candidate, path, copy.deepcopy(value))

            try:
                candidate_obj = MemoraConfig(**candidate)
            except PydanticValidationError as exc:
                raise ConfigValidationError(
                    self._pydantic_field_errors(exc)
                ) from exc
            except Exception as exc:
                raise ConfigValidationError({"*": str(exc)}) from exc

            candidate_snapshot = candidate_obj.model_dump()
            candidate_revision = self._compute_revision(candidate_snapshot)
            changed_paths = tuple(sorted(normalized_changes))

            persistence_cancelled = False
            persistence_conflict_revision: str | None = None
            if persist:
                persistence_cancelled = await self._persist_source(
                    candidate_snapshot
                )
                source_obj, source_snapshot, source_revision = (
                    self._read_source_state()
                )
                if source_revision != candidate_revision:
                    self._config_obj = source_obj
                    self._config = source_snapshot
                    self._revision = source_revision
                    self._source_revision = source_revision
                    persistence_conflict_revision = source_revision

            if persistence_conflict_revision is None:
                self._config_obj = candidate_obj
                self._config = candidate_snapshot
                self._revision = candidate_revision
                if persist:
                    self._source_revision = candidate_revision
            if persistence_cancelled:
                raise asyncio.CancelledError
            if persistence_conflict_revision is not None:
                raise ConfigConflictError(
                    expected_revision or candidate_revision,
                    persistence_conflict_revision,
                )
            return ConfigApplyResult(candidate_revision, changed_paths)

    def _validate_change_paths(
        self,
        changes: Mapping[str, Any],
    ) -> dict[str, str]:
        if self._persistence_capable and self._schema_leaf_paths is None:
            return {"*": "AstrBot 配置 schema 不可用，已拒绝持久化配置更新"}

        field_errors: dict[str, str] = {}
        for raw_path in changes:
            if not isinstance(raw_path, str):
                field_errors[str(raw_path)] = "配置路径必须是字符串"
                continue
            parts = raw_path.split(".")
            if not raw_path or any(not part for part in parts):
                field_errors[raw_path] = "配置路径格式无效"
            elif (
                self._schema_leaf_paths is not None
                and raw_path not in self._schema_leaf_paths
            ):
                field_errors[raw_path] = "配置路径不在 AstrBot schema 中"
            elif (
                raw_path in self._schema_leaf_options
                and not self._matches_schema_option(
                    changes[raw_path],
                    self._schema_leaf_options[raw_path],
                )
            ):
                field_errors[raw_path] = "配置值不在 AstrBot schema 允许的选项中"
        return field_errors

    @staticmethod
    def _set_dotted_value(
        target: dict[str, Any],
        path: str,
        value: Any,
    ) -> None:
        current = target
        parts = path.split(".")
        for part in parts[:-1]:
            next_value = current.get(part)
            if not isinstance(next_value, dict):
                next_value = {}
                current[part] = next_value
            current = next_value
        current[parts[-1]] = value

    @staticmethod
    def _pydantic_field_errors(exc: PydanticValidationError) -> dict[str, str]:
        errors: dict[str, str] = {}
        for item in exc.errors():
            path = ".".join(str(part) for part in item.get("loc", ())) or "*"
            errors[path] = str(item.get("msg", "配置值无效"))
        return errors or {"*": str(exc)}

    async def _persist_source(self, candidate: dict[str, Any]) -> bool:
        source_before = copy.deepcopy(dict(self._source_config))
        cancellation_requested = False
        try:
            self._replace_source(candidate)
            save_config = getattr(self._source_config, "save_config", None)
            if callable(save_config):
                save_task = asyncio.create_task(asyncio.to_thread(save_config))
                while True:
                    try:
                        await asyncio.shield(save_task)
                        break
                    except asyncio.CancelledError:
                        cancellation_requested = True
                        self._consume_pending_cancellation()
        except Exception as exc:
            try:
                self._replace_source(source_before)
            except Exception as rollback_exc:
                logger.error(f"恢复 AstrBot 配置映射失败: {rollback_exc}")
            if cancellation_requested:
                raise asyncio.CancelledError from exc
            raise ConfigPersistenceError(f"配置持久化失败: {exc}") from exc
        return cancellation_requested

    @staticmethod
    def _consume_pending_cancellation() -> None:
        current_task = asyncio.current_task()
        if current_task is None:
            return
        while current_task.cancelling():
            current_task.uncancel()

    def _replace_source(self, config: Mapping[str, Any]) -> None:
        self._source_config.clear()
        self._source_config.update(copy.deepcopy(dict(config)))

    async def update_runtime_config(
        self,
        updates: dict[str, Any],
        *,
        persist: bool = True,
    ) -> bool:
        """兼容旧调用方，将新配置事务结果转换为布尔值。"""
        try:
            await self.apply_config_changes(updates, persist=persist)
            return True
        except (
            ConfigConflictError,
            ConfigValidationError,
            ConfigPersistenceError,
        ) as exc:
            logger.error(f"运行时配置更新失败: {exc}")
            return False

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置项，支持点号分隔的嵌套键。"""
        keys = key.split(".")
        value: Any = self._config
        for part in keys:
            if isinstance(value, dict):
                value = value.get(part, _SENTINEL)
                if value is _SENTINEL:
                    return default
            else:
                return default
        if isinstance(value, (dict, list)):
            return copy.deepcopy(value)
        return value

    def get_section(self, section: str) -> dict[str, Any]:
        """获取与内部状态隔离的配置节。"""
        value = self._config.get(section, {})
        return copy.deepcopy(value) if isinstance(value, dict) else {}

    def get_all(self) -> dict[str, Any]:
        """获取与内部状态隔离的完整配置。"""
        return copy.deepcopy(self._config)

    @property
    def validation_errors(self) -> list[dict[str, Any]]:
        """返回加载过程中被降级处理的配置分支。"""
        return copy.deepcopy(self._validation_errors)

    @property
    def provider_settings(self) -> dict[str, Any]:
        """Provider 设置。"""
        return self.get_section("provider_settings")

    @property
    def session_manager(self) -> dict[str, Any]:
        """会话管理器配置。"""
        return self.get_section("session_manager")

    @property
    def recall_engine(self) -> dict[str, Any]:
        """召回引擎配置。"""
        return self.get_section("recall_engine")

    @property
    def reflection_engine(self) -> dict[str, Any]:
        """反思引擎配置。"""
        return self.get_section("reflection_engine")

    @property
    def filtering_settings(self) -> dict[str, Any]:
        """过滤设置。"""
        return self.get_section("filtering_settings")

    @property
    def graph_memory(self) -> dict[str, Any]:
        """图记忆设置。"""
        return self.get_section("graph_memory")
