"""把已校验的 Evolution proposal 转换为派生写入计划。"""

from __future__ import annotations

from ..domain import (
    DerivedApplyPlan,
    DerivedState,
    EvolutionProposal,
    MemorySourceRef,
    ProjectionSourceView,
    ProjectionType,
    ProjectionView,
    RelationType,
    RelationView,
)
from .memory_evolution_manager_helpers import (
    _alias,
    _ensure_compatible,
    _ensure_projection_compatible,
    _path_exists,
    _projection_view,
    _stable_id,
    _strictest_privacy,
)
from .memory_evolution_projection import EvolutionProposalRejected


class MemoryEvolutionPlanBuilderMixin:
    """为 Manager 提供无 I/O 的 proposal 到派生计划转换。"""

    candidate_limit: int
    confidence_threshold: float
    auto_active_relation_types: frozenset[RelationType]
    require_review_for_high_impact: bool
    _HIGH_IMPACT: frozenset[RelationType]
    _LOW_IMPACT: frozenset[RelationType]

    def _proposal_to_plan(
        self,
        proposal: EvolutionProposal,
        sources: list[MemorySourceRef],
    ) -> DerivedApplyPlan:
        """将结构化 proposal 转成只包含派生写入的计划。

        该边界拒绝过大的 proposal 和重复 source，避免调用方绕过
        ``MemoryConsolidator`` 时静默截断或生成不确定的 source mapping。
        计划不包含 canonical 正文 mutation；正文修改必须走
        ``MemoryEngine`` 的 revision 校验路径。

        Args:
            proposal: 已完成结构解析的演化提案。
            sources: 生成提案时使用的 canonical 来源快照。

        Returns:
            仅包含 relation、Projection 与来源 revision 的派生写入计划。

        Raises:
            EvolutionProposalRejected: proposal 超限、别名非法、来源重复、
                跨 scope/主体或形成重复/环关系。
        """

        if not isinstance(proposal, EvolutionProposal):
            raise EvolutionProposalRejected("proposal_schema_invalid")
        if (
            len(proposal.relations) > self.candidate_limit
            or len(proposal.projections) > self.candidate_limit
        ):
            raise EvolutionProposalRejected("proposal_limit_exceeded")
        if len({source.memory_id for source in sources}) != len(sources):
            raise EvolutionProposalRejected("duplicate_source")
        aliases = {f"M{index}": source for index, source in enumerate(sources, start=1)}
        relations: list[RelationView] = []
        projections: list[ProjectionView] = []
        projection_sources: list[ProjectionSourceView] = []
        seen_edges: set[tuple[int, int]] = set()

        for item in proposal.relations[: self.candidate_limit]:
            source = _alias(aliases, item.source_alias)
            target = _alias(aliases, item.target_alias)
            if source.memory_id == target.memory_id:
                raise EvolutionProposalRejected("self_relation")
            _ensure_compatible(source, target, item.relation_type)
            edge = (source.memory_id, target.memory_id)
            reverse = (target.memory_id, source.memory_id)
            if (
                edge in seen_edges
                or reverse in seen_edges
                or _path_exists(seen_edges, target.memory_id, source.memory_id)
            ):
                raise EvolutionProposalRejected("duplicate_or_cycle")
            seen_edges.add(edge)
            state = (
                DerivedState.ACTIVE
                if item.relation_type in self.auto_active_relation_types
                and item.relation_type in self._LOW_IMPACT
                and item.confidence >= self.confidence_threshold
                and not (
                    item.relation_type in self._HIGH_IMPACT
                    and self.require_review_for_high_impact
                )
                else DerivedState.CANDIDATE
            )
            relation_id = _stable_id(
                "relation",
                source.memory_id,
                source.revision_token,
                target.memory_id,
                target.revision_token,
                item.relation_type.value,
            )
            relations.append(
                RelationView(
                    relation_id,
                    source.memory_id,
                    target.memory_id,
                    item.relation_type,
                    item.confidence,
                    source.scope_key,
                    _strictest_privacy(source, target),
                    state,
                    source.revision_token,
                    target.revision_token,
                    item.valid_from,
                    item.valid_to,
                )
            )

        for item in proposal.projections[: self.candidate_limit]:
            projection_sources_for_item = [
                _alias(aliases, alias) for alias in item.source_aliases
            ]
            if len({source.memory_id for source in projection_sources_for_item}) != len(
                projection_sources_for_item
            ):
                raise EvolutionProposalRejected("duplicate_projection_source")
            _ensure_projection_compatible(*projection_sources_for_item)
            if (
                item.projection_type is ProjectionType.CONFLICT_SET
                and len(projection_sources_for_item) < 3
            ):
                raise EvolutionProposalRejected("conflict_source_roles")
            projection_id = _stable_id(
                "projection",
                item.projection_type.value,
                *(
                    part
                    for source in projection_sources_for_item
                    for part in (source.memory_id, source.revision_token)
                ),
            )
            projection_state = (
                DerivedState.CANDIDATE
                if item.projection_type is ProjectionType.CONFLICT_SET
                or item.confidence < self.confidence_threshold
                else DerivedState.ACTIVE
            )
            projections.append(
                _projection_view(
                    projection_id,
                    item,
                    projection_sources_for_item,
                    projection_state,
                )
            )
            for ordinal, source in enumerate(projection_sources_for_item):
                role = "primary" if ordinal == 0 else "supporting"
                if item.projection_type is ProjectionType.CONFLICT_SET:
                    role = {
                        0: "primary",
                        1: "conflict_left",
                        2: "conflict_right",
                    }.get(ordinal, "supporting")
                projection_sources.append(
                    ProjectionSourceView(
                        projection_id,
                        source.memory_id,
                        source.revision_token,
                        role,
                        ordinal,
                    )
                )
        return DerivedApplyPlan(
            relations=tuple(relations),
            projections=tuple(projections),
            projection_sources=tuple(projection_sources),
            source_revisions={
                source.memory_id: source.revision_token for source in sources
            },
        )
