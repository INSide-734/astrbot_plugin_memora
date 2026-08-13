"""把 Pydantic 成本配置转换为 shared 运行时策略。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...shared.cost_control import CostControl, CostControlConfig


def build_cost_control_from_config(
    config: CostControlConfig | Mapping[str, Any],
) -> CostControl:
    """从唯一的 typed cost_control 分支构建不可变运行时成本门。"""

    if isinstance(config, CostControlConfig):
        validated = config
    elif isinstance(config, Mapping):
        unknown = set(config) - set(CostControlConfig.model_fields)
        if unknown:
            raise TypeError("cost_control 配置必须是叶子分支，不能传入完整配置树")
        validated = CostControlConfig.model_validate(dict(config))
    else:
        raise TypeError("cost_control 配置必须是 CostControlConfig 或叶子映射")

    return CostControl(**validated.model_dump())


__all__ = ["CostControl", "build_cost_control_from_config"]
