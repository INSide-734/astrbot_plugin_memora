"""AstrBot 版本检测工具。"""

import re
from importlib import metadata as importlib_metadata

from astrbot.api import logger

_MIN_ASTRBOT_VERSION = "4.24.2"
_ASTRBOT_DISTRIBUTION_NAMES = ("AstrBot", "astrbot")


def _parse_version(v: str) -> tuple[int, ...]:
    m = re.match(r"v?(\d+(?:\.\d+)*)", v.strip(), re.IGNORECASE)
    if not m:
        return ()
    return tuple(int(x) for x in m.group(1).split("."))


def _version_lt(current: str, minimum: str) -> bool:
    current_parts = _parse_version(current)
    minimum_parts = _parse_version(minimum)
    if not current_parts or not minimum_parts:
        return False
    width = max(len(current_parts), len(minimum_parts))
    return current_parts + (0,) * (width - len(current_parts)) < minimum_parts + (
        0,
    ) * (width - len(minimum_parts))


def _detect_astrbot_version() -> str | None:
    for distribution_name in _ASTRBOT_DISTRIBUTION_NAMES:
        try:
            return importlib_metadata.version(distribution_name)
        except importlib_metadata.PackageNotFoundError:
            continue
        except Exception as exc:
            logger.debug(f"读取 AstrBot 分发版本失败 ({distribution_name}): {exc}")

    for module_name in ("astrbot.core.config.default", "astrbot.core.config"):
        try:
            module = __import__(module_name, fromlist=["VERSION"])
            version_value = getattr(module, "VERSION", None)
        except Exception as exc:
            logger.debug(f"读取 AstrBot 模块版本失败 ({module_name}): {exc}")
            continue
        if version_value:
            return str(version_value)

    return None


_CURRENT_ASTRBOT_VERSION = _detect_astrbot_version()

if _CURRENT_ASTRBOT_VERSION is None:
    logger.debug("未能检测到 AstrBot 版本，跳过 Memora 版本兼容提示")
elif _version_lt(_CURRENT_ASTRBOT_VERSION, _MIN_ASTRBOT_VERSION):
    logger.warning(
        f"AstrBot 版本 {_CURRENT_ASTRBOT_VERSION} 低于推荐版本 {_MIN_ASTRBOT_VERSION}。"
        f"插件 Pages / WebUI 功能可能不可用。建议升级 AstrBot 以获得完整体验。"
    )
