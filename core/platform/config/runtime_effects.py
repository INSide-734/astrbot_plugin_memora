"""平台配置保存后的重启与派生重建影响分类。"""

from __future__ import annotations

from enum import Enum


class RuntimeConfigEffect(str, Enum):
    """配置叶应用后对当前运行时和派生状态的影响。"""

    RESTART = "restart"
    REBUILD = "rebuild"


REBUILD_REQUIRED_PATHS = frozenset(
    {
        "graph_memory.temporal_edges_enabled",
        "graph_memory.causal_edges_enabled",
    }
)


def classify_config_effects(changed_paths: tuple[str, ...]) -> tuple[bool, bool]:
    """返回配置变更是否需要重启，以及是否需要重建图派生数据。"""

    restart_required = bool(changed_paths)
    rebuild_required = bool(REBUILD_REQUIRED_PATHS.intersection(changed_paths))
    return restart_required, rebuild_required


__all__ = [
    "REBUILD_REQUIRED_PATHS",
    "RuntimeConfigEffect",
    "classify_config_effects",
]
