"""插件资源的集中定位和安全读取边界。

资源路径来自插件自身的固定清单，不接受用户输入作为任意文件路径。source
checkout 直接读取插件根；runtime bundle 可以通过 ``package_reader`` 提供
同名资源，从而不需要消费者猜测 ``__file__`` 的父目录层级。
"""

from __future__ import annotations

import copy
import json
import math
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

_MISSING = object()


class ResourceError(ValueError):
    """插件资源定位失败。"""


class ResourceNotAllowedError(ResourceError):
    """请求的资源不在插件白名单内。"""


class ResourceNotFoundError(ResourceError):
    """白名单资源在当前 source/bundle 中不存在。"""


PackageReader = Callable[[str], bytes | str | Path | None]


class PluginResourceLocator:
    """解析插件根目录和 bundle 资源的唯一 locator。

    参数:
        plugin_root: source checkout 或已解包 runtime 的插件根目录。
        package_reader: 可选 bundle reader；返回 bytes、文本、Path 或 ``None``。
    """

    _EXACT_NAMES = frozenset({"_conf_schema.json", "metadata.yaml"})
    _PREFIXES = (
        "static/stopwords/",
        "core/prompts/",
        "core/i18n/",
        ".astrbot-plugin/i18n/",
    )

    def __init__(
        self,
        plugin_root: str | Path,
        *,
        package_reader: PackageReader | None = None,
    ) -> None:
        """保存并规范化插件根目录，不读取资源或创建文件。"""

        root = Path(plugin_root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise ResourceNotFoundError("plugin_root_unavailable")
        self._plugin_root = root
        self._package_reader = package_reader

    @property
    def plugin_root(self) -> Path:
        """返回已解析的插件根目录。"""

        return self._plugin_root

    def normalize_name(self, name: str) -> str:
        """规范化并校验资源名，拒绝绝对路径和父目录穿越。"""

        if not isinstance(name, str) or not name.strip():
            raise ResourceNotAllowedError("resource_name_required")
        raw = name.replace("\\", "/")
        windows = PureWindowsPath(name)
        posix = PurePosixPath(raw)
        if windows.is_absolute() or posix.is_absolute():
            raise ResourceNotAllowedError("resource_absolute_path_denied")
        if any(part in {"", ".", ".."} for part in raw.split("/")):
            raise ResourceNotAllowedError("resource_path_traversal_denied")
        normalized = posix.as_posix()
        if not self.is_allowed(normalized):
            raise ResourceNotAllowedError("resource_not_allowlisted")
        return normalized

    def is_allowed(self, name: str) -> bool:
        """返回资源名是否属于固定白名单。"""

        return name in self._EXACT_NAMES or any(
            name.startswith(prefix) and name != prefix for prefix in self._PREFIXES
        )

    def path(self, name: str) -> Path:
        """返回 source checkout 中的资源路径，并再次确认位于插件根内。"""

        normalized = self.normalize_name(name)
        candidate = (self._plugin_root / normalized).resolve()
        try:
            candidate.relative_to(self._plugin_root)
        except ValueError as exc:
            raise ResourceNotAllowedError("resource_path_escape_denied") from exc
        return candidate

    def read_bytes(self, name: str) -> bytes:
        """从 bundle 优先、source fallback 读取白名单资源。"""

        normalized = self.normalize_name(name)
        package_value = self._read_package_bytes(normalized)
        if package_value is not None:
            return package_value
        return self._read_source_bytes(normalized)

    def read_text(self, name: str, *, encoding: str = "utf-8") -> str:
        """读取白名单文本资源。"""

        try:
            return self.read_bytes(name).decode(encoding)
        except UnicodeDecodeError as exc:
            raise ResourceNotFoundError("resource_encoding_invalid") from exc

    def read_json(self, name: str) -> Mapping[str, Any] | None:
        """解析白名单 JSON 对象；bundle 畸形时继续尝试 source。"""

        normalized = self.normalize_name(name)
        package_value = self._read_package_bytes(normalized)
        if package_value is not None:
            parsed = self._parse_json_object(package_value)
            if parsed is not None:
                return parsed
        try:
            parsed = self._parse_json_object(self._read_source_bytes(normalized))
        except ResourceError:
            return None
        return parsed

    def _read_package_bytes(self, normalized: str) -> bytes | None:
        """读取 bundle 候选，异常时返回 ``None`` 以便 source fallback。"""

        if self._package_reader is None:
            return None
        try:
            value = self._package_reader(normalized)
            if value is None:
                return None
            if isinstance(value, bytes):
                return value
            if isinstance(value, Path):
                return value.read_bytes()
            if isinstance(value, str):
                return value.encode("utf-8")
        except (OSError, TypeError, ValueError):
            return None
        return None

    def _read_source_bytes(self, normalized: str) -> bytes:
        """读取 source checkout 中的白名单资源。"""

        candidate = self.path(normalized)
        try:
            return candidate.read_bytes()
        except OSError as exc:
            raise ResourceNotFoundError("resource_not_found") from exc

    @staticmethod
    def _parse_json_object(raw: bytes) -> Mapping[str, Any] | None:
        """解析非空 JSON 对象并返回隔离副本。"""

        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(value, Mapping) or not value:
            return None
        return copy.deepcopy(dict(value))

    def load_schema(
        self,
        host_schema: Any = _MISSING,
    ) -> Mapping[str, Any] | None:
        """按 host 注入 > bundle/source 的固定顺序返回配置 Schema。"""

        if self._is_schema_mapping(host_schema):
            return copy.deepcopy(dict(host_schema))
        normalized = self.normalize_name("_conf_schema.json")
        package_value = self._read_package_bytes(normalized)
        if package_value is not None:
            package_schema = self._parse_json_object(package_value)
            if self._is_schema_mapping(package_schema):
                return package_schema
        try:
            source_schema = self._parse_json_object(self._read_source_bytes(normalized))
        except ResourceError:
            return None
        return source_schema if self._is_schema_mapping(source_schema) else None

    @classmethod
    def _is_schema_mapping(cls, value: Any) -> bool:
        """校验 AstrBot Schema 的结构边界，不解释具体 feature 字段。"""

        if not isinstance(value, Mapping) or not value:
            return False
        for key, field_schema in value.items():
            if (
                not isinstance(key, str)
                or not key
                or not isinstance(field_schema, Mapping)
                or not isinstance(field_schema.get("type"), str)
            ):
                return False
            if field_schema["type"] == "object":
                if not cls._is_schema_mapping(field_schema.get("items")):
                    return False
            elif "options" in field_schema:
                options = field_schema["options"]
                if not isinstance(options, list) or any(
                    not cls._is_schema_scalar(option) for option in options
                ):
                    return False
        return True

    @staticmethod
    def _is_schema_scalar(value: Any) -> bool:
        """校验 Schema options 中可安全序列化的 JSON 标量。"""

        if value is None or type(value) in (bool, int, str):
            return True
        return type(value) is float and math.isfinite(value)


__all__ = [
    "PluginResourceLocator",
    "ResourceError",
    "ResourceNotAllowedError",
    "ResourceNotFoundError",
]
