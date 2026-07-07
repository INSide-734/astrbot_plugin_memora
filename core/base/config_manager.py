"""
配置管理器
集中管理插件配置的加载、验证和访问

三层配置加载：AstrBot 配置 → 持久化 JSON → Pydantic 默认值
"""

import json
import os
from typing import Any

from astrbot.api import logger

from .config_validator import (
    MemoraConfig,
    get_default_config,
    merge_config_with_defaults,
    validate_runtime_config_changes,
    validate_config,
)
from .exceptions import ConfigurationError

_SENTINEL = object()


class ConfigManager:
    """配置管理器

    实现三层配置合并：
    - 第 1 层：合并 AstrBot 用户配置与 Pydantic 默认值
    - 第 2 层：加载持久化 JSON 覆盖（由 Dashboard 写入）
    - 第 3 层：使用 Pydantic 校验最终合并结果
    """

    def __init__(
        self,
        user_config: dict[str, Any] | None = None,
        persisted_config_path: str | None = None,
    ):
        """
        初始化配置管理器

        参数：
            user_config: 用户提供的配置字典（AstrBot 侧）。
            persisted_config_path: 持久化配置 JSON 文件路径（Dashboard 覆盖）。
        """
        self._raw_config = user_config or {}
        self._persisted_config_path = persisted_config_path
        self._config: dict[str, Any] = {}
        self._config_obj = None
        self._validation_errors: list[dict[str, Any]] = []
        self._load_config()

    def _load_config(self) -> None:
        """加载并验证配置（三层合并）"""
        try:
            # 第 1 层：合并 AstrBot 用户配置和默认值
            merged_config = merge_config_with_defaults(self._raw_config)

            # 第 2 层：加载持久化 JSON 覆盖（Dashboard 写入）
            if self._persisted_config_path:
                persisted = self._load_persisted_config()
                if persisted:
                    merged_config = self._deep_merge(merged_config, persisted)
                    logger.info(f"已加载持久化配置: {self._persisted_config_path}")

            # 第 3 层：校验最终合并配置
            self._config_obj = self._validate_with_branch_fallback(merged_config)
            self._config = self._config_obj.model_dump()
        except Exception as e:
            raise ConfigurationError(f"配置加载失败: {e}") from e

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
            logger.warning(
                "配置验证失败，尝试按分支降级",
                exc_info=True,
            )

        defaults = get_default_config()
        candidate = dict(merged_config)
        invalid_sections = self._extract_invalid_sections(validation_error)

        if not invalid_sections:
            invalid_sections = self._probe_invalid_sections(candidate, defaults)

        for section in sorted(invalid_sections):
            if section in defaults:
                candidate[section] = defaults[section]
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
                raise ConfigurationError(f"加载默认配置失败: {default_error}") from default_error

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
            candidate = dict(merged_config)
            candidate[section] = defaults[section]
            try:
                MemoraConfig(**candidate)
                invalid.add(section)
            except Exception:
                continue
        return invalid

    def _load_persisted_config(self) -> dict[str, Any] | None:
        """加载持久化的 Dashboard 配置 JSON（若存在）。

        只返回非空 dict；读取失败时记录 warning 并返回 None。
        注：此方法在 __init__ 中同步调用，同步 I/O 在此处是可接受的。
        """
        try:
            if os.path.exists(self._persisted_config_path):
                data = self._read_json_file(self._persisted_config_path)
                if isinstance(data, dict) and data:
                    return data
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(f"读取持久化配置失败: {e}")
        return None

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> dict:
        """深度合并 override 到 base，override 中的值优先。

        嵌套 dict 递归合并；非 dict 类型按 key 覆盖。
        """
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = ConfigManager._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    async def save_persisted_config(self, updates: dict[str, Any]) -> bool:
        """持久化配置变更到 JSON 文件（由 Dashboard API 调用）。

        将 ``updates```` 合并到现有持久化配置中并写入磁盘。
        若未配置持久化路径则返回 False。
        """
        if not self._persisted_config_path:
            logger.warning("未配置持久化路径，跳过保存")
            return False

        import asyncio as _asyncio

        try:
            existing: dict[str, Any] = {}
            if os.path.exists(self._persisted_config_path):
                existing = await _asyncio.to_thread(
                    self._read_json_file, self._persisted_config_path
                )
            merged = self._deep_merge(existing, updates)
            os.makedirs(os.path.dirname(self._persisted_config_path), exist_ok=True)
            await _asyncio.to_thread(
                self._write_json_file, self._persisted_config_path, merged
            )
            logger.info(f"持久化配置已更新 ({len(updates)} 项)")
            return True
        except Exception as e:
            logger.error(f"保存持久化配置失败: {e}")
            return False

    async def update_runtime_config(
        self,
        updates: dict[str, Any],
        *,
        persist: bool = True,
    ) -> bool:
        """应用已校验的运行时配置更新。

        ``updates`` 支持诸如 ``topic_segmentation.strategy`` 的点号键，
        并会写入 :meth:`get` 所使用的嵌套配置结构中。
        """
        if not updates:
            return True
        if self._config_obj is None:
            self._config_obj = validate_config(self._config)
        if not validate_runtime_config_changes(self._config_obj, updates):
            return False

        updated = self._deep_merge(self._config, self._expand_dotted_updates(updates))
        self._config_obj = MemoraConfig(**updated)
        self._config = self._config_obj.model_dump()

        if persist:
            return await self.save_persisted_config(self._expand_dotted_updates(updates))
        return True

    @staticmethod
    def _expand_dotted_updates(updates: dict[str, Any]) -> dict[str, Any]:
        """将点号键形式的更新展开为嵌套字典。"""
        expanded: dict[str, Any] = {}
        for key, value in updates.items():
            current = expanded
            parts = key.split(".")
            for part in parts[:-1]:
                next_value = current.get(part)
                if not isinstance(next_value, dict):
                    next_value = {}
                    current[part] = next_value
                current = next_value
            current[parts[-1]] = value
        return expanded

    @staticmethod
    def _read_json_file(path: str) -> dict[str, Any]:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _write_json_file(path: str, data: dict[str, Any]) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get(self, key: str, default: Any = None) -> Any:
        """
        获取配置项

        参数：
            key: 配置键，支持点号分隔的嵌套键（如 "provider_settings.llm_provider_id"）。
            default: 默认值，仅在配置键不存在时返回，不影响值为 None 的情况。

        返回：
            配置值（可能是 None、0、False、空字符串等假值）。
        """
        keys = key.split(".")
        value: Any = self._config

        for k in keys:
            if isinstance(value, dict):
                value = value.get(k, _SENTINEL)
                if value is _SENTINEL:
                    return default
            else:
                return default

        return value

    def get_section(self, section: str) -> dict[str, Any]:
        """
        获取配置节

        参数：
            section: 配置节名称。

        返回：
            配置节字典。
        """
        return self._config.get(section, {})

    def get_all(self) -> dict[str, Any]:
        """获取所有配置"""
        return self._config.copy()

    @property
    def validation_errors(self) -> list[dict[str, Any]]:
        """返回加载过程中被降级处理的配置分支。"""
        return list(self._validation_errors)

    @property
    def provider_settings(self) -> dict[str, Any]:
        """Provider 设置。"""
        return self.get_section("provider_settings")

    @property
    def session_manager(self) -> dict[str, Any]:
        """会话管理器配置"""
        return self.get_section("session_manager")

    @property
    def recall_engine(self) -> dict[str, Any]:
        """召回引擎配置"""
        return self.get_section("recall_engine")

    @property
    def reflection_engine(self) -> dict[str, Any]:
        """反思引擎配置"""
        return self.get_section("reflection_engine")

    @property
    def filtering_settings(self) -> dict[str, Any]:
        """过滤设置"""
        return self.get_section("filtering_settings")

    @property
    def graph_memory(self) -> dict[str, Any]:
        """图记忆设置。"""
        return self.get_section("graph_memory")
