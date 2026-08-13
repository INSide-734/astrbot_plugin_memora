"""读取并校验 Memory Evolution Projection，再附着到 canonical 检索候选。"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from astrbot.api import logger

from ....shared.adapter_capabilities import (
    AdapterCapability,
    AdapterCapabilityContract,
    AdapterKind,
)
from ....shared.contracts import MemorySourceRef
from ....shared.temporal import normalize_datetime, visible_at
from ..domain import (
    DerivedState,
    ProjectionBundle,
    ProjectionSourceView,
    ProjectionType,
    ProjectionView,
)

if TYPE_CHECKING:
    from ...retrieval.rrf_fusion import HybridResult

_PRIVACY_ORDER = {"public": 0, "shared": 1, "confidential": 2}
_PROJECTION_TYPES = frozenset(item.value for item in ProjectionType)


@dataclass(frozen=True, slots=True)
class ProjectionScope:
    """本次读取允许使用的 scope、隐私等级和当前时间。"""

    scope_key: str
    privacy_level: str
    now: datetime | None = None
    reference_time: datetime | None = None


@dataclass(frozen=True, slots=True)
class ProjectionBudget:
    """Projection 注解的独立字符和数量预算。"""

    max_chars: int = 2_000
    max_items: int = 16
    max_per_candidate: int = 4
    max_summary_chars: int = 600

    def __post_init__(self) -> None:
        """拒绝任一维度为负数的 Projection 预算。

        异常：
            ValueError: 字符或数量预算中存在负数。
        """

        if (
            min(
                self.max_chars,
                self.max_items,
                self.max_per_candidate,
                self.max_summary_chars,
            )
            < 0
        ):
            raise ValueError("Projection 预算不能为负数")


@dataclass(frozen=True, slots=True)
class ProjectionReadStats:
    """Projection 读取的安全标量结果，不包含 source ID 或正文。"""

    candidates: list[HybridResult]
    resolved_conflicts: int = 0
    unresolved_conflicts: int = 0
    conflict_decisions: tuple[str, ...] = ()
    projection_count: int = 0
    source_supported_count: int = 0


class ProjectionReader:
    """读取 active projection，并只附着到已命中的 primary canonical memory。"""

    adapter_capabilities = AdapterCapabilityContract(
        kind=AdapterKind.DERIVED_READER,
        native=frozenset({AdapterCapability.REFERENCE_TIME}),
        caller_enforced=frozenset(
            {
                AdapterCapability.FILTERING,
                AdapterCapability.CANCELLATION,
            }
        ),
    )

    def __init__(
        self,
        store: Any,
        *,
        projection_limit: int = 100,
        disabled_types: Iterable[ProjectionType | str] = (),
    ) -> None:
        """绑定派生 Store，并设置读取上限和关闭的 Projection 类型。"""

        self.store = store
        self.projection_limit = max(0, int(projection_limit))
        self.disabled_types = frozenset(
            item if isinstance(item, ProjectionType) else ProjectionType(item)
            for item in disabled_types
        )

    async def attach(
        self,
        candidates: list[HybridResult],
        *,
        scope: ProjectionScope,
        budget: ProjectionBudget,
    ) -> list[HybridResult]:
        """在不改变 canonical 候选数量和分数的前提下附加 Projection。

        参数：
            candidates: 原始 canonical 检索候选。
            scope: 当前请求允许的 scope、隐私与参考时间。
            budget: Projection 注解的数量和字符预算。

        返回：
            仅 metadata 可能附有模型安全 Projection 的候选副本。

        异常：
            asyncio.CancelledError: 调用方取消读取时继续传播。
        """

        return (
            await self.attach_with_stats(candidates, scope=scope, budget=budget)
        ).candidates

    async def attach_with_stats(
        self,
        candidates: list[HybridResult],
        *,
        scope: ProjectionScope,
        budget: ProjectionBudget,
    ) -> ProjectionReadStats:
        """附着 Projection，并返回不含敏感标识的读取统计。

        参数：
            candidates: 原始 canonical 检索候选。
            scope: 当前请求允许的 scope、隐私与参考时间。
            budget: Projection 注解的数量和字符预算。

        返回：
            候选副本、冲突状态和附着数量组成的安全统计。

        异常：
            asyncio.CancelledError: 调用方取消 Store 读取时继续传播。
        """

        baseline = [_copy_candidate(item) for item in candidates]
        if (
            not baseline
            or self.projection_limit <= 0
            or budget.max_chars <= 0
            or budget.max_items <= 0
            or budget.max_per_candidate <= 0
            or budget.max_summary_chars <= 0
        ):
            return ProjectionReadStats(baseline)

        seed_ids = tuple(dict.fromkeys(item.doc_id for item in baseline))
        try:
            bundles = await self.store.active_projection_bundles_for_seeds(
                seed_ids,
                scope_key=scope.scope_key,
                limit=self.projection_limit,
            )
            if not bundles:
                return ProjectionReadStats(baseline)
            source_ids = tuple(
                dict.fromkeys(
                    source.memory_id
                    for bundle in bundles
                    for source in getattr(bundle, "sources", ())
                )
            )
            sources = await self.store.load_sources(
                source_ids,
                max_content_chars=max(1, budget.max_summary_chars),
            )
            sources_by_id = {
                source.memory_id: source
                for source in sources
                if isinstance(source, MemorySourceRef)
            }
            decisions: list[str] = []
            attached, projection_count = self._attach_validated(
                baseline,
                bundles,
                sources_by_id,
                scope,
                budget,
                decisions,
            )
            return ProjectionReadStats(
                attached,
                resolved_conflicts=sum(
                    decision != "unresolved" for decision in decisions
                ),
                unresolved_conflicts=decisions.count("unresolved"),
                conflict_decisions=tuple(decisions),
                projection_count=projection_count,
                source_supported_count=projection_count,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("[Projection读取] 读取失败，回退 canonical baseline")
            return ProjectionReadStats(baseline)

    def _attach_validated(
        self,
        candidates: list[HybridResult],
        bundles: list[ProjectionBundle],
        sources_by_id: dict[int, MemorySourceRef],
        scope: ProjectionScope,
        budget: ProjectionBudget,
        decisions: list[str],
    ) -> tuple[list[HybridResult], int]:
        """按来源证据与预算把合法 Projection 附着到候选副本。

        参数：
            candidates: 已复制的 canonical 检索候选。
            bundles: Store 返回的 Projection 与来源映射集合。
            sources_by_id: 按 canonical ID 索引的当前来源快照。
            scope: 当前请求允许的 scope、隐私与参考时间。
            budget: Projection 注解的数量和字符预算。
            decisions: 用于收集安全冲突判定标量的列表。

        返回：
            附着后的候选列表与实际附着的 Projection 数量。
        """

        candidates_by_id = {item.doc_id: item for item in candidates}
        accepted: dict[int, list[dict[str, Any]]] = {}
        seen_projection_ids: set[str] = set()
        projection_count = 0
        total_chars = 0
        now = _as_utc(scope.reference_time or scope.now or datetime.now(timezone.utc))

        valid_bundles: list[ProjectionBundle] = []
        for bundle in bundles:
            try:
                if isinstance(bundle, ProjectionBundle) and _valid_projection_bundle(
                    bundle.projection, bundle.sources
                ):
                    valid_bundles.append(bundle)
            except (AttributeError, TypeError, ValueError, OverflowError):
                continue

        ordered_bundles = sorted(
            valid_bundles,
            key=lambda bundle: (
                -_safe_confidence(bundle.projection.confidence),
                _projection_type_value(bundle.projection.projection_type),
                str(bundle.projection.projection_id),
            ),
        )
        for bundle in ordered_bundles:
            projection = bundle.projection
            projection_id = str(projection.projection_id)
            if projection_id in seen_projection_ids:
                continue
            if projection.state is not DerivedState.ACTIVE:
                continue
            if projection.projection_type in self.disabled_types:
                continue
            if projection.scope_key != scope.scope_key:
                continue
            if not _privacy_allowed(projection.privacy_level, scope.privacy_level):
                continue
            if not visible_at(
                now,
                valid_from=projection.valid_from,
                valid_to=projection.valid_to,
                invalid_at=projection.invalid_at,
            ):
                continue

            current_pairs = [
                (source, mapping)
                for mapping in bundle.sources
                for source in (sources_by_id.get(mapping.memory_id),)
                if _source_is_current(source, mapping, scope, now)
            ]
            if any(
                source is not None
                and not _source_is_current(source, mapping, scope, now)
                for mapping in bundle.sources
                for source in (sources_by_id.get(mapping.memory_id),)
            ):
                continue
            current_mappings = tuple(mapping for _, mapping in current_pairs)
            if not current_mappings:
                continue
            primary_sources = [
                source for source in current_mappings if source.role == "primary"
            ]
            if len(primary_sources) != 1:
                continue
            primary_id = primary_sources[0].memory_id
            if primary_id not in candidates_by_id:
                continue
            if projection.projection_type is ProjectionType.CONFLICT_SET and not {
                "conflict_left",
                "conflict_right",
            } <= {source.role for source in current_mappings}:
                continue
            if projection.projection_type is ProjectionType.CONFLICT_SET:
                conflict_state = _resolve_conflict_state(
                    current_mappings,
                    sources_by_id,
                )
                decisions.append(conflict_state)

            summary = str(projection.summary or "").strip()
            if not summary:
                continue
            summary = summary[: budget.max_summary_chars]
            if not summary:
                continue
            projection_type = _projection_type_value(projection.projection_type)
            if projection_type not in _PROJECTION_TYPES:
                continue
            visible = {
                "type": projection_type,
                "summary": summary,
                "confidence": _safe_confidence(projection.confidence),
            }
            visible_chars = len(projection_type) + len(summary) + 24
            if total_chars + visible_chars > budget.max_chars:
                continue
            current = accepted.setdefault(primary_id, [])
            if len(current) >= budget.max_per_candidate:
                continue
            if (
                sum(len(item["summary"]) for item in current) + len(summary)
                > budget.max_chars
            ):
                continue
            current.append(visible)
            projection_count += 1
            total_chars += visible_chars
            seen_projection_ids.add(projection_id)
            if sum(len(items) for items in accepted.values()) >= budget.max_items:
                break

        for candidate in candidates:
            items = accepted.get(candidate.doc_id)
            if items:
                metadata = dict(candidate.metadata or {})
                metadata.pop("derived_projections", None)
                metadata["derived_projections"] = items
                candidate.metadata = metadata
        return candidates, projection_count


def _copy_candidate(candidate: HybridResult) -> HybridResult:
    """复制候选及其可变 metadata，避免污染 canonical 检索结果。

    参数：
        candidate: 待复制的检索候选。

    返回：
        内容与分数相同、可变字段独立的候选副本。
    """

    from ...retrieval.rrf_fusion import HybridResult

    return HybridResult(
        doc_id=candidate.doc_id,
        final_score=candidate.final_score,
        rrf_score=candidate.rrf_score,
        bm25_score=candidate.bm25_score,
        vector_score=candidate.vector_score,
        content=candidate.content,
        metadata=dict(candidate.metadata or {}),
        score_breakdown=(
            dict(candidate.score_breakdown)
            if candidate.score_breakdown is not None
            else None
        ),
    )


def _valid_projection_bundle(projection: Any, mappings: tuple[Any, ...]) -> bool:
    """校验 Projection 及其来源映射的结构完整性。

    参数：
        projection: 待校验的 Projection 视图。
        mappings: Projection 声明的来源映射。

    返回：
        类型、置信度、来源集合和角色均合法时返回 ``True``。
    """

    if not isinstance(projection, ProjectionView):
        return False
    confidence = float(projection.confidence)
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        return False
    if not isinstance(projection.projection_type, ProjectionType):
        return False
    if any(not isinstance(mapping, ProjectionSourceView) for mapping in mappings):
        return False
    if not mappings or len({mapping.memory_id for mapping in mappings}) != len(
        mappings
    ):
        return False
    mapping_ids = {mapping.memory_id for mapping in mappings}
    if mapping_ids != set(projection.source_memory_ids):
        return False
    if sum(mapping.role == "primary" for mapping in mappings) != 1:
        return False
    if projection.projection_type is ProjectionType.CONFLICT_SET:
        roles = {mapping.role for mapping in mappings}
        if not {"conflict_left", "conflict_right"} <= roles:
            return False
    return True


def _source_is_current(
    source: MemorySourceRef | None,
    mapping: Any,
    scope: ProjectionScope,
    now: datetime,
) -> bool:
    """校验来源 revision、访问边界和事实时间是否仍匹配映射。

    参数：
        source: 当前 canonical 来源快照。
        mapping: Projection 锚定的来源映射。
        scope: 当前请求允许的 scope 与隐私级别。
        now: 当前请求的统一参考时间。

    返回：
        来源存在且所有证据仍有效时返回 ``True``。
    """

    occurred_at = mapping.occurred_at or (source.occurred_at if source else None)
    return bool(
        source is not None
        and source.revision_token == mapping.revision_token
        and source.scope_key == scope.scope_key
        and _privacy_allowed(source.privacy_level, scope.privacy_level)
        and visible_at(
            now,
            occurred_at=occurred_at,
            valid_from=mapping.valid_from,
            valid_to=mapping.valid_to,
            require_occurred=True,
        )
    )


def _valid_at(
    valid_from: datetime | None,
    valid_to: datetime | None,
    now: datetime,
) -> bool:
    """判断参考时间是否落在闭区间有效窗口内。

    参数：
        valid_from: 可选的有效期起点。
        valid_to: 可选的有效期终点。
        now: 待判断的参考时间。

    返回：
        参考时间未早于起点且未晚于终点时返回 ``True``。
    """

    current = _as_utc(now)
    return (valid_from is None or current >= _as_utc(valid_from)) and (
        valid_to is None or current <= _as_utc(valid_to)
    )


def _resolve_conflict_state(
    mappings: tuple[ProjectionSourceView, ...],
    sources_by_id: dict[int, MemorySourceRef],
) -> str:
    """按 source 事实时间区分可排序冲突和时间未决冲突。

    参数：
        mappings: 当前且合法的 Projection 来源映射。
        sources_by_id: 按 canonical ID 索引的来源快照。

    返回：
        ``unresolved`` 或明确较新的冲突侧安全枚举值。
    """

    times: dict[str, datetime] = {}
    for mapping in mappings:
        if mapping.role not in {"conflict_left", "conflict_right"}:
            continue
        source = sources_by_id.get(mapping.memory_id)
        if source is None or source.time_source not in {"explicit", "metadata"}:
            continue
        if source.time_precision != "instant":
            continue
        occurred_at = mapping.occurred_at or (source.occurred_at if source else None)
        if occurred_at is not None:
            times[mapping.role] = _as_utc(occurred_at)
    if set(times) != {"conflict_left", "conflict_right"}:
        return "unresolved"
    if len(set(times.values())) != 2:
        return "unresolved"
    return (
        "conflict_left_newer"
        if times["conflict_left"] > times["conflict_right"]
        else "conflict_right_newer"
    )


def _privacy_allowed(item_level: str, request_level: str) -> bool:
    """判断 Projection 或来源的隐私级别是否未越过请求边界。

    参数：
        item_level: 对象声明的隐私级别。
        request_level: 当前请求允许的最高隐私级别。

    返回：
        两个级别都合法且对象不越权时返回 ``True``。
    """

    item_value = _PRIVACY_ORDER.get(item_level)
    request_value = _PRIVACY_ORDER.get(request_level)
    return (
        item_value is not None
        and request_value is not None
        and item_value <= request_value
    )


def _projection_type_value(value: Any) -> str:
    """把 Projection 类型规范化为稳定字符串。

    参数：
        value: Projection 类型枚举或待显示值。

    返回：
        枚举值或对象的字符串形式。
    """

    return value.value if isinstance(value, ProjectionType) else str(value)


def _safe_confidence(value: Any) -> float:
    """把任意置信度输入收敛到有限的 ``[0, 1]`` 区间。

    参数：
        value: 待解析的置信度值。

    返回：
        合法且有限的置信度；无法解析时返回 ``0.0``。
    """

    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, parsed)) if math.isfinite(parsed) else 0.0


def _as_utc(value: datetime) -> datetime:
    """把时间规范化为 UTC，无法规范化时使用当前 UTC 时间。

    参数：
        value: 待规范化的时间。

    返回：
        带 UTC 时区的时间。
    """

    normalized = normalize_datetime(value)
    if normalized is None:
        return datetime.now(timezone.utc)
    return normalized


__all__ = [
    "ProjectionBudget",
    "ProjectionReadStats",
    "ProjectionReader",
    "ProjectionScope",
]
