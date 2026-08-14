"""外部 Projection proposal 的 canonical 来源二次校验边界。"""

from __future__ import annotations

from typing import Any

from ..domain import EvolutionProposal, MemorySourceRef


class EvolutionProposalRejected(ValueError):
    """表示 proposal 没有通过确定性安全校验。"""


class MemoryEvolutionProjectionProposalMixin:
    """为 MemoryEvolutionManager 提供外部 Projection proposal 应用入口。"""

    store: Any
    max_input_chars: int

    async def apply_projection_proposal(
        self,
        proposal: EvolutionProposal,
        sources: list[MemorySourceRef],
    ) -> int:
        """二次核对 canonical 来源并原子应用外部 Projection proposal。

        Args:
            proposal: 只允许包含 Projection 的结构化派生提案。
            sources: 生成提案时读取的 canonical 来源快照。

        Returns:
            成功交给 Store 原子应用的 Projection 数量。

        Raises:
            EvolutionProposalRejected: proposal 含 relation、来源重复、来源已失效，
                或 revision/scope/privacy/role 已变化。
            asyncio.CancelledError: 调用方取消操作时由 Store 调用继续传播。
        """

        if not isinstance(proposal, EvolutionProposal):
            raise EvolutionProposalRejected("proposal_schema_invalid")
        if proposal.relations:
            raise EvolutionProposalRejected("projection_proposal_contains_relation")
        source_ids = tuple(source.memory_id for source in sources)
        if not source_ids:
            raise EvolutionProposalRejected("source_not_found")
        if len(set(source_ids)) != len(source_ids):
            raise EvolutionProposalRejected("duplicate_source")
        fresh_sources = await self.store.load_sources(
            source_ids,
            max_content_chars=self.max_input_chars,
        )
        if not _same_projection_sources(sources, fresh_sources):
            raise EvolutionProposalRejected("source_revision_changed")
        plan = self._proposal_to_plan(proposal, fresh_sources)
        await self.store.apply_derived_plan(plan)
        return len(plan.projections)

    def _proposal_to_plan(
        self,
        proposal: EvolutionProposal,
        sources: list[MemorySourceRef],
    ) -> Any:
        """声明宿主 Manager 必须提供的 proposal 转换实现。"""

        raise NotImplementedError


def _same_projection_sources(
    previous: list[MemorySourceRef],
    current: list[MemorySourceRef],
) -> bool:
    """比较 Projection 写入所需的完整来源身份与安全边界。"""

    def _snapshot(source: MemorySourceRef) -> tuple[str, str, str, str]:
        """提取会影响 Projection 合法性的来源字段。"""

        return (
            source.revision_token,
            source.scope_key,
            source.privacy_level,
            source.source_role,
        )

    previous_by_id = {source.memory_id: _snapshot(source) for source in previous}
    current_by_id = {source.memory_id: _snapshot(source) for source in current}
    return previous_by_id == current_by_id and len(previous) == len(current)


__all__ = [
    "EvolutionProposalRejected",
    "MemoryEvolutionProjectionProposalMixin",
]
