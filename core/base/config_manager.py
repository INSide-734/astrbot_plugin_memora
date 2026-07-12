"""集中管理 AstrBot 注入配置的验证、访问与原子更新。"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
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
        self._validation_errors: list[dict[str, Any]] = []
        self._apply_lock = asyncio.Lock()
        self._schema_leaf_paths = self._load_schema_leaf_paths()
        self._load_config()

    def _load_config(self) -> None:
        """合并模型默认值并校验 AstrBot 当前配置。"""
        try:
            source_snapshot = copy.deepcopy(dict(self._source_config))
            merged_config = merge_config_with_defaults(source_snapshot)
            self._config_obj = self._validate_with_branch_fallback(merged_config)
            self._config = self._config_obj.model_dump()
            self._revision = self._compute_revision(self._config)
        except Exception as exc:
            raise ConfigurationError(f"配置加载失败: {exc}") from exc

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
    def _load_schema_leaf_paths(cls) -> frozenset[str] | None:
        schema_path = Path(__file__).resolve().parents[2] / "_conf_schema.json"
        try:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning(f"无法加载 AstrBot 配置 schema，跳过未知字段检查: {exc}")
            return None

        if not isinstance(schema, Mapping):
            logger.warning("AstrBot 配置 schema 不是对象，跳过未知字段检查")
            return None

        paths: set[str] = set()
        cls._collect_schema_leaf_paths(schema, (), paths)
        return frozenset(paths)

    @classmethod
    def _collect_schema_leaf_paths(
        cls,
        schema: Mapping[str, Any],
        prefix: tuple[str, ...],
        result: set[str],
    ) -> None:
        for key, field_schema in schema.items():
            if not isinstance(field_schema, Mapping):
                continue
            path = (*prefix, str(key))
            items = field_schema.get("items")
            if field_schema.get("type") == "object" and isinstance(items, Mapping):
                cls._collect_schema_leaf_paths(items, path, result)
            else:
                result.add(".".join(path))

    def get_config_snapshot(self) -> tuple[dict[str, Any], str]:
        """返回与内部状态隔离的配置副本及其 SHA-256 修订号。"""
        return copy.deepcopy(self._config), self._revision

    async def apply_config_changes(
        self,
        changes: Mapping[str, Any],
        *,
        expected_revision: str | None = None,
        persist: bool = True,
    ) -> ConfigApplyResult:
        """串行校验并应用点号路径配置变更。"""
        async with self._apply_lock:
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

            if persist:
                await self._persist_source(candidate_snapshot)

            self._config_obj = candidate_obj
            self._config = candidate_snapshot
            self._revision = candidate_revision
            return ConfigApplyResult(candidate_revision, changed_paths)

    def _validate_change_paths(
        self,
        changes: Mapping[str, Any],
    ) -> dict[str, str]:
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

    async def _persist_source(self, candidate: dict[str, Any]) -> None:
        source_before = copy.deepcopy(dict(self._source_config))
        try:
            self._replace_source(candidate)
            save_config = getattr(self._source_config, "save_config", None)
            if callable(save_config):
                await asyncio.to_thread(save_config)
        except Exception as exc:
            try:
                self._replace_source(source_before)
            except Exception as rollback_exc:
                logger.error(f"恢复 AstrBot 配置映射失败: {rollback_exc}")
            raise ConfigPersistenceError(f"配置持久化失败: {exc}") from exc

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
