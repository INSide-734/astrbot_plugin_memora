"""Memory Evolution 编排的确定性校验与状态辅助函数。"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from typing import Any

from ..domain import (
    DerivedState,
    MemoryProjectionProposal,
    MemorySourceRef,
    ProjectionView,
    RelationType,
)
from .memory_evolution_projection import EvolutionProposalRejected


def _alias(aliases: Mapping[str, MemorySourceRef], name: str) -> MemorySourceRef:
    """解析 proposal 别名指向的 canonical 来源。

    Args:
        aliases: proposal 别名到 canonical 来源的映射。
        name: 待解析的别名。

    Returns:
        别名对应的 canonical 来源。

    Raises:
        EvolutionProposalRejected: 别名不在允许映射中。
    """

    source = aliases.get(name)
    if source is None:
        raise EvolutionProposalRejected("unknown_alias")
    return source


def _ensure_scope_compatible(*sources: MemorySourceRef) -> None:
    """确保来源非空且全部属于同一 scope。

    Args:
        *sources: 待比较的 canonical 来源。

    Raises:
        EvolutionProposalRejected: 来源为空或 scope 不一致。
    """

    if not sources:
        raise EvolutionProposalRejected("source_not_found")
    if any(source.scope_key != sources[0].scope_key for source in sources[1:]):
        raise EvolutionProposalRejected("scope_mismatch")


def _ensure_projection_compatible(*sources: MemorySourceRef) -> None:
    """校验 Projection 的 scope 与 confidential 主体边界。

    Args:
        *sources: Projection 引用的 canonical 来源。

    Raises:
        EvolutionProposalRejected: scope 不一致，或 confidential 来源跨主体。
    """

    _ensure_scope_compatible(*sources)
    if not any(source.privacy_level == "confidential" for source in sources):
        return
    subjects = {source.subject_key for source in sources}
    if len(subjects) != 1 or None in subjects:
        raise EvolutionProposalRejected("subject_mismatch")


def _ensure_compatible(
    first: MemorySourceRef,
    second: MemorySourceRef,
    relation_type: RelationType,
) -> None:
    """校验 relation 的 scope、私聊主体和高影响主体边界。

    Args:
        first: relation 起点来源。
        second: relation 终点来源。
        relation_type: 待建立的关系类型。

    Raises:
        EvolutionProposalRejected: 来源跨 scope 或违反主体隔离。
    """

    _ensure_scope_compatible(first, second)
    subject_required = "confidential" in {
        first.privacy_level,
        second.privacy_level,
    } or relation_type in {
        RelationType.UPDATES,
        RelationType.CONTRADICTS,
        RelationType.PREFERENCE_CHANGE,
        RelationType.SUPERSEDES,
    }
    if subject_required and (
        not first.subject_key or first.subject_key != second.subject_key
    ):
        raise EvolutionProposalRejected("subject_mismatch")


def _strictest_privacy(*sources: MemorySourceRef) -> str:
    """返回来源集合中最严格的 privacy 级别。

    Args:
        *sources: 已通过兼容校验的 canonical 来源。

    Returns:
        ``public``、``shared`` 或 ``confidential``。
    """

    order = {"public": 0, "shared": 1, "confidential": 2}
    return max(sources, key=lambda source: order[source.privacy_level]).privacy_level


def _projection_view(
    projection_id: str,
    item: MemoryProjectionProposal,
    sources: list[MemorySourceRef],
    state: DerivedState,
) -> ProjectionView:
    """把校验后的 proposal 项转换为持久化 Projection 视图。

    Args:
        projection_id: 稳定的 Projection ID。
        item: 结构化 Projection proposal。
        sources: 已校验的 canonical 来源。
        state: 初始派生状态。

    Returns:
        可交给 Store 持久化的 Projection 视图。
    """

    return ProjectionView(
        projection_id,
        item.projection_type,
        item.summary,
        tuple(source.memory_id for source in sources),
        sources[0].scope_key,
        _strictest_privacy(*sources),
        item.confidence,
        state,
        item.valid_from,
        item.valid_to,
    )


def _path_exists(
    edges: set[tuple[int, int]],
    start: int,
    target: int,
) -> bool:
    """判断有向关系集合中是否已存在从起点到目标的路径。

    Args:
        edges: 已接受的有向边集合。
        start: 搜索起点。
        target: 搜索目标。

    Returns:
        存在可达路径时为 ``True``。
    """

    pending = [start]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(next_node for left, next_node in edges if left == current)
    return False


def _same_source_revisions(
    previous: list[MemorySourceRef],
    current: list[MemorySourceRef],
) -> bool:
    """比较两次 canonical 读取的来源集合与 revision 是否一致。

    Args:
        previous: proposal 生成前的来源快照。
        current: 写入前重新读取的来源快照。

    Returns:
        来源数量、ID 与 revision 全部一致时为 ``True``。
    """

    previous_revisions = {
        source.memory_id: source.revision_token for source in previous
    }
    current_revisions = {source.memory_id: source.revision_token for source in current}
    return previous_revisions == current_revisions and len(previous) == len(current)


def _stable_id(prefix: str, *parts: object) -> str:
    """从内部证据生成不暴露正文的稳定派生 ID。

    Args:
        prefix: ID 类型前缀。
        *parts: 参与哈希的 revision、来源和类型证据。

    Returns:
        带类型前缀的截断 SHA-256 ID。
    """

    digest = hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}:{digest}"


def _parse_relation_types(values: Any) -> frozenset[RelationType]:
    """把配置集合解析为合法 relation 类型。

    Args:
        values: 配置中的 relation 类型集合。

    Returns:
        仅包含合法枚举值的不可变集合；非法容器返回空集合。
    """

    if not isinstance(values, (list, tuple, set, frozenset)):
        return frozenset()
    result: set[RelationType] = set()
    for value in values:
        try:
            result.add(RelationType(value))
        except (TypeError, ValueError):
            continue
    return frozenset(result)


def _reason(error: Exception) -> str:
    """把 worker 异常归一化为隐私安全的稳定 reason code。

    Args:
        error: proposal、来源校验或 Provider 阶段异常。

    Returns:
        可用于状态计数的 allowlist reason code。
    """

    if isinstance(error, EvolutionProposalRejected):
        code = str(error)
        if code in {
            "source_not_found",
            "scope_mismatch",
            "source_revision_changed",
            "unknown_alias",
            "self_relation",
            "duplicate_or_cycle",
            "duplicate_projection_source",
            "conflict_source_roles",
            "proposal_schema_invalid",
            "proposal_limit_exceeded",
            "duplicate_source",
            "subject_mismatch",
        }:
            return code
        return "proposal_rejected"
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return "provider_timeout"
    if isinstance(error, ConnectionError):
        return "provider_unavailable"
    if isinstance(error, ValueError):
        reason_code = str(error)
        if reason_code in {
            "source_memory_not_found",
            "source_privacy_mismatch",
            "source_revision_mismatch",
            "source_scope_mismatch",
        }:
            return reason_code
        return "proposal_invalid"
    return "worker_error"


def _is_retryable_error(error: Exception) -> bool:
    """判断异常是否属于允许 retry/backoff 的临时故障。

    Args:
        error: worker 捕获的异常。

    Returns:
        连接或超时异常为 ``True``，其余异常为 ``False``。
    """

    return isinstance(
        error,
        (ConnectionError, TimeoutError, asyncio.TimeoutError),
    )


def _as_int(value: Any, default: int) -> int:
    """把配置值转为整数，非法值返回默认值。

    Args:
        value: 待解析值。
        default: 转换失败时的默认值。

    Returns:
        解析结果或默认值。
    """

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    """把配置值转为浮点数，非法值返回默认值。

    Args:
        value: 待解析值。
        default: 转换失败时的默认值。

    Returns:
        解析结果或默认值。
    """

    try:
        return float(value)
    except (TypeError, ValueError):
        return default
