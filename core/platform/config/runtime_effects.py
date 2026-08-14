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


GATE_HOT_RELOAD_PATHS = ("quality.gate",)


def classify_config_effects(changed_paths: tuple[str, ...]) -> tuple[bool, bool]:
    """返回配置变更是否需要重启，以及是否需要重建图派生数据。"""

    restart_required = any(
        not path.startswith(GATE_HOT_RELOAD_PATHS) for path in changed_paths
    )
    rebuild_required = bool(REBUILD_REQUIRED_PATHS.intersection(changed_paths))
    return restart_required, rebuild_required


def gate_hot_reload_required(changed_paths: tuple[str, ...]) -> bool:
    """返回变更是否包含可热重载的门禁叶子。"""

    return bool(changed_paths) and any(
        path.startswith(GATE_HOT_RELOAD_PATHS) for path in changed_paths
    )


__all__ = [
    "GATE_HOT_RELOAD_PATHS",
    "REBUILD_REQUIRED_PATHS",
    "RuntimeConfigEffect",
    "classify_config_effects",
    "gate_hot_reload_required",
]
