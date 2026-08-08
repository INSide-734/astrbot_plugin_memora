"""插件资源的集中定位和安全读取边界。

资源路径来自插件自身的固定清单，不接受用户输入作为任意文件路径。source
checkout 直接读取插件根；runtime bundle 可以通过 ``package_reader`` 提供
同名资源，从而不需要消费者猜测 ``__file__`` 的父目录层级。
"""

from __future__ import annotations

import copy
import json
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

    Args:
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
        parts = posix.parts
        if ".." in parts or "." in parts or any(not part for part in parts):
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
        if self._package_reader is not None:
            value = self._package_reader(normalized)
            if value is not None:
                if isinstance(value, bytes):
                    return value
                if isinstance(value, Path):
                    try:
                        return value.read_bytes()
                    except OSError as exc:
                        raise ResourceNotFoundError("resource_unreadable") from exc
                if isinstance(value, str):
                    return value.encode("utf-8")
                raise ResourceNotFoundError("resource_reader_invalid")
        candidate = self.path(normalized)
        try:
            return candidate.read_bytes()
        except OSError as exc:
            raise ResourceNotFoundError("resource_not_found") from exc

    def read_text(self, name: str, *, encoding: str = "utf-8") -> str:
        """读取白名单文本资源。"""

        try:
            return self.read_bytes(name).decode(encoding)
        except UnicodeDecodeError as exc:
            raise ResourceNotFoundError("resource_encoding_invalid") from exc

    def read_json(self, name: str) -> Mapping[str, Any] | None:
        """解析白名单 JSON 对象；畸形内容返回 ``None``。"""

        try:
            value = json.loads(self.read_text(name))
        except (ResourceError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(value, Mapping) or not value:
            return None
        return copy.deepcopy(dict(value))

    def load_schema(
        self,
        host_schema: Any = _MISSING,
    ) -> Mapping[str, Any] | None:
        """按 host 注入 > bundle/source 的固定顺序返回配置 Schema。"""

        if isinstance(host_schema, Mapping) and host_schema:
            return copy.deepcopy(dict(host_schema))
        return self.read_json("_conf_schema.json")


__all__ = [
    "PluginResourceLocator",
    "ResourceError",
    "ResourceNotAllowedError",
    "ResourceNotFoundError",
]
