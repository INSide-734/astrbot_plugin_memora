"""提供平台配置的默认合并与验证编排。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from astrbot.api import logger

if TYPE_CHECKING:
    from .config_validator import MemoraConfig

__all__ = [
    "get_default_config",
    "merge_config_with_defaults",
    "validate_config",
    "validate_runtime_config_changes",
]


def _config_model_type() -> type[MemoraConfig]:
    """延迟取得根配置模型，避免新 owner 优先导入时形成循环依赖。"""

    from .config_validator import MemoraConfig

    return MemoraConfig


def validate_config(raw_config: dict[str, Any]) -> MemoraConfig:
    """验证原始映射并返回规范化的根配置对象。

    参数：
        raw_config: 待验证的原始配置字典。

    返回：
        验证后的根配置对象。

    异常：
        ValueError: 配置不满足根模型约束。
    """

    config_model = _config_model_type()
    try:
        config = config_model(**raw_config)
        logger.info("配置验证成功")
        return config
    except Exception as exc:
        logger.error(f"配置验证失败: {exc}")
        raise ValueError(f"插件配置无效: {exc}") from exc


def get_default_config() -> dict[str, Any]:
    """返回根配置模型生成的完整默认配置字典。"""

    return _config_model_type()().model_dump()


def _deep_merge(default: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    """递归合并默认配置与用户配置，并让用户叶子值优先。"""

    result = default.copy()
    for key, value in user.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def merge_config_with_defaults(user_config: dict[str, Any]) -> dict[str, Any]:
    """将用户配置递归覆盖到完整默认配置之上。

    参数：
        user_config: 用户提供的配置字典。

    返回：
        保留未覆盖默认值的合并配置字典。
    """

    merged = _deep_merge(get_default_config(), user_config)
    logger.debug("配置已与默认值合并")
    return merged


def _update_nested_dict(target: dict[str, Any], updates: dict[str, Any]) -> None:
    """按点号路径把运行时变更写入待验证的配置副本。"""

    for key, value in updates.items():
        if "." not in key:
            target[key] = value
            continue

        parts = key.split(".")
        current = target
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value


def validate_runtime_config_changes(
    current_config: MemoraConfig,
    changes: dict[str, Any],
) -> bool:
    """检查运行时点号路径变更能否构成有效的完整配置。

    参数：
        current_config: 当前已验证的根配置对象。
        changes: 待应用的点号路径变更。

    返回：
        完整配置通过验证时返回 ``True``，否则返回 ``False``。
    """

    try:
        updated_dict = current_config.model_dump()
        _update_nested_dict(updated_dict, changes)
        _config_model_type()(**updated_dict)
        return True
    except Exception as exc:
        logger.error(f"运行时配置更改验证失败: {exc}")
        return False
