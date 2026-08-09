"""构建 runtime bundle 的安全 package-resource reader。"""

from __future__ import annotations

import importlib.resources as importlib_resources

from .locator import PackageReader


def build_package_resource_reader(package_name: str | None) -> PackageReader:
    """创建按包名读取插件资源的 reader，缺失资源交给 source fallback。"""

    def read_resource(name: str) -> bytes | None:
        """读取包内相对资源；包不存在或读取失败时返回 ``None``。"""

        if not package_name:
            return None
        try:
            resource = importlib_resources.files(package_name)
            for part in name.split("/"):
                resource = resource.joinpath(part)
            return resource.read_bytes()
        except (AttributeError, ImportError, OSError, TypeError, ValueError):
            return None

    return read_resource


__all__ = ["build_package_resource_reader"]
