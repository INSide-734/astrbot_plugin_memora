"""自动知识 proposal 的 RED/GREEN 回归测试。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.knowledge.application import (
    KnowledgeManager,
    KnowledgeProposalPipeline,
)
from core.features.knowledge.domain import KnowledgeEntry, KnowledgeType
from core.features.memory.application.memory_engine import MemoryEngine
from core.shared.contracts import MemorySourceRef
from core.shared.cost_control import CostControl
from core.shared.domain_provenance import DomainObjectOrigin, DomainProvenance
from core.shared.extra_llm_budget import ExtraLlmBudget, extra_llm_budget_scope


def _source(
    *,
    revision: str = "revision-17",
    scope: str = "session:test",
    privacy: str = "shared",
) -> MemorySourceRef:
    """构造自动知识使用的 canonical 来源快照。"""

    return MemorySourceRef(
        memory_id=17,
        revision_token=revision,
        scope_key=scope,
        privacy_level=privacy,
        occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
        content="项目部署需要先执行数据库迁移，再重建检索索引。",
    )


def _memory(
    *,
    importance: float = 0.8,
    confidence: float = 0.9,
    stability: float = 0.9,
) -> dict:
    """构造满足自动知识门槛的 canonical memory 读取结果。"""

    return {
        "id": 17,
        "text": "项目部署需要先执行数据库迁移，再重建检索索引。",
        "metadata": {
            "importance": importance,
            "confidence": confidence,
            "stability": stability,
            "status": "active",
            "scope_key": "session:test",
            "privacy_level": "shared",
        },
    }


def _entry() -> KnowledgeEntry:
    """构造抽取器返回的合法知识条目。"""

    return KnowledgeEntry(
        title="部署顺序",
        content="部署时先执行数据库迁移，再重建检索索引。",
        category=KnowledgeType.PROCEDURE,
        confidence=0.92,
        tags=["部署", "迁移"],
    )


def _pipeline(
    *,
    memory: dict | None = None,
    source: MemorySourceRef | None = None,
    sources: list[list[MemorySourceRef]] | None = None,
    extractor: MagicMock | None = None,
    manager: MagicMock | None = None,
    cost_control: CostControl | None = None,
):
    """构造具有可观测依赖的自动知识管线。"""

    if extractor is None:
        extractor = MagicMock()
        extractor.extract = AsyncMock(return_value=_entry())
    manager = manager or MagicMock()
    manager.add_derived_entry = AsyncMock(return_value=42)
    source_store = MagicMock()
    source_values = sources or [[source or _source()], [source or _source()]]
    source_store.load_sources = AsyncMock(side_effect=source_values)
    get_memory = AsyncMock(return_value=memory or _memory())
    pipeline = KnowledgeProposalPipeline(
        knowledge_manager=manager,
        source_store=source_store,
        get_memory=get_memory,
        extractor=extractor,
        cost_control=cost_control or CostControl(mode="quality"),
        expire_days=365,
    )
    return pipeline, extractor, manager, source_store


@pytest.mark.asyncio
async def test_knowledge_proposal_persists_canonical_provenance() -> None:
    """合格 canonical memory 应生成带 revision/scope/privacy 的派生知识。"""

    pipeline, extractor, manager, _source_store = _pipeline()
    with extra_llm_budget_scope(ExtraLlmBudget(1)):
        applied = await pipeline.apply_for_memory(17)

    assert applied is True
    extractor.extract.assert_awaited_once()
    manager.add_derived_entry.assert_awaited_once()
    entry, provenance = manager.add_derived_entry.await_args.args
    assert entry.origin is DomainObjectOrigin.DERIVED
    assert entry.expires_at > entry.created_at
    assert provenance.origin is DomainObjectOrigin.DERIVED
    assert provenance.sources[0].memory_id == 17
    assert provenance.sources[0].revision_token == "revision-17"
    assert provenance.sources[0].scope_key == "session:test"
    assert provenance.sources[0].privacy_level == "shared"
    assert provenance.sources[0].content is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "memory",
    [
        _memory(importance=0.2),
        _memory(confidence=0.2),
        _memory(stability=0.2),
        {**_memory(), "metadata": {**_memory()["metadata"], "status": "archived"}},
    ],
)
async def test_knowledge_proposal_skips_below_quality_stability_gates(
    memory: dict,
) -> None:
    """重要性、置信度或稳定性不足时不得调用 Provider 或写入知识。"""

    pipeline, extractor, manager, _source_store = _pipeline(memory=memory)
    with extra_llm_budget_scope(ExtraLlmBudget(1)):
        applied = await pipeline.apply_for_memory(17)

    assert applied is False
    extractor.extract.assert_not_awaited()
    manager.add_derived_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_knowledge_proposal_rejects_malformed_extraction() -> None:
    """category、title、content 和 tags 不满足结构约束时不得落库。"""

    extractor = MagicMock()
    extractor.extract = AsyncMock(
        return_value=KnowledgeEntry(
            title="",
            content="正文",
            category=KnowledgeType.FACT,
            confidence=0.9,
            tags=cast(list[str], ["合法", 123]),
        )
    )
    pipeline, _extractor, manager, _source_store = _pipeline(extractor=extractor)
    with extra_llm_budget_scope(ExtraLlmBudget(1)):
        applied = await pipeline.apply_for_memory(17)

    assert applied is False
    manager.add_derived_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_knowledge_proposal_does_not_call_provider_without_budget() -> None:
    """缺少请求级预算时只能安全跳过，不能裸调用 Provider。"""

    pipeline, extractor, manager, _source_store = _pipeline()
    assert await pipeline.apply_for_memory(17) is False
    extractor.extract.assert_not_awaited()
    manager.add_derived_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_knowledge_proposal_rechecks_revision_before_write() -> None:
    """抽取期间 source revision 变化时，旧 proposal 必须被丢弃。"""

    pipeline, extractor, manager, source_store = _pipeline(
        sources=[[_source(revision="revision-old")], [_source(revision="revision-new")]]
    )
    with extra_llm_budget_scope(ExtraLlmBudget(1)):
        applied = await pipeline.apply_for_memory(17)

    assert applied is False
    extractor.extract.assert_awaited_once()
    manager.add_derived_entry.assert_not_awaited()
    assert source_store.load_sources.await_count == 2


@pytest.mark.asyncio
async def test_knowledge_proposal_propagates_cancellation_and_releases_budget() -> None:
    """Provider 取消必须穿透管线并释放 reservation。"""

    extractor = MagicMock()
    extractor.extract = AsyncMock(side_effect=asyncio.CancelledError)
    pipeline, _extractor, manager, _source_store = _pipeline(extractor=extractor)
    budget = ExtraLlmBudget(1)
    with extra_llm_budget_scope(budget):
        with pytest.raises(asyncio.CancelledError):
            await pipeline.apply_for_memory(17)

    assert budget.snapshot().used == 0
    assert budget.snapshot().reserved == 0
    manager.add_derived_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_canonical_add_schedules_knowledge_proposal_without_rollback() -> None:
    """canonical 成功后应触发知识 proposal，派生失败不能回滚主写。"""

    engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
    hybrid_retriever = MagicMock()
    hybrid_retriever.add_memory = AsyncMock(return_value=123)
    setattr(engine, "hybrid_retriever", hybrid_retriever)
    engine.graph_memory_manager = None
    engine.atom_store = None
    engine._write_journal.start_op = AsyncMock(return_value=1)
    engine._write_journal.advance_op = AsyncMock()
    engine._retrieval = MagicMock()
    engine._retrieval.invalidate_cache = MagicMock()
    engine._retrieval.apply_interference = AsyncMock()
    engine._retrieval.extract_triggers = AsyncMock()
    pipeline = MagicMock()
    pipeline.apply_for_memory = AsyncMock(side_effect=RuntimeError("derived failed"))
    setattr(engine, "knowledge_proposal_pipeline", pipeline)
    tasks: list[asyncio.Task] = []

    def create_task(coroutine) -> None:
        """记录引擎派生任务，便于等待确定性完成。"""

        tasks.append(asyncio.create_task(coroutine))

    setattr(engine, "_create_tracked_task", create_task)
    doc_id = await engine.add_memory("部署顺序需要保持稳定")
    await asyncio.gather(*tasks)

    assert doc_id == 123
    pipeline.apply_for_memory.assert_awaited_once_with(123)


@pytest.mark.asyncio
async def test_derived_knowledge_cannot_change_manual_entry() -> None:
    """派生 proposal 与人工知识相似时必须让人工权威保持不变。"""

    existing = KnowledgeEntry(
        title="部署顺序",
        content="人工确认的部署顺序。",
        entry_id=8,
    )
    incoming = KnowledgeEntry(
        title="部署顺序",
        content="人工确认的部署顺序。",
        origin=DomainObjectOrigin.DERIVED,
        provenance=DomainProvenance(DomainObjectOrigin.DERIVED, (_source(),)),
    )
    store = AsyncMock()
    store.search.return_value = ([existing], 1)
    store.insert.return_value = 99
    manager = KnowledgeManager(store)

    assert await manager.add_entry(incoming) == 99
    store.update.assert_not_awaited()
    store.insert.assert_awaited_once_with(incoming)
    assert existing.origin is DomainObjectOrigin.MANUAL


@pytest.mark.asyncio
async def test_derived_knowledge_does_not_merge_incompatible_scope_or_privacy() -> None:
    """scope 或 privacy 不兼容的派生知识必须分开保存。"""

    existing = KnowledgeEntry(
        title="部署顺序",
        content="先迁移再重建索引。",
        entry_id=8,
        origin=DomainObjectOrigin.DERIVED,
        provenance=DomainProvenance(
            DomainObjectOrigin.DERIVED,
            (_source(scope="session:one", privacy="shared"),),
        ),
    )
    incoming = KnowledgeEntry(
        title="部署顺序",
        content="先迁移再重建索引。",
        origin=DomainObjectOrigin.DERIVED,
        provenance=DomainProvenance(
            DomainObjectOrigin.DERIVED,
            (_source(scope="session:two", privacy="confidential"),),
        ),
    )
    store = AsyncMock()
    store.search.return_value = ([existing], 1)
    store.insert.return_value = 99
    manager = KnowledgeManager(store)

    assert await manager.add_entry(incoming) == 99
    store.update.assert_not_awaited()
    store.insert.assert_awaited_once_with(incoming)


def test_knowledge_manager_merge_compatibility_is_conservative() -> None:
    """人工权威或不兼容来源都不得进入派生合并分支。"""

    manual = KnowledgeEntry(title="T", content="C")
    derived = KnowledgeEntry(
        title="T",
        content="C",
        origin=DomainObjectOrigin.DERIVED,
        provenance=DomainProvenance(DomainObjectOrigin.DERIVED, (_source(),)),
    )
    assert KnowledgeManager._merge_compatible(manual, derived) is False
    assert KnowledgeManager._merge_compatible(derived, derived) is True


@pytest.mark.asyncio
async def test_add_derived_entry_assigns_provenance_and_expiration() -> None:
    """Manager 入口必须统一补齐派生来源和运行时过期策略。"""

    store = AsyncMock()
    store.search.return_value = ([], 0)
    store.insert.return_value = 99
    manager = KnowledgeManager(store, expire_days=2)
    entry = _entry()
    provenance = DomainProvenance(DomainObjectOrigin.DERIVED, (_source(),))

    assert await manager.add_derived_entry(entry, provenance) == 99
    assert entry.origin is DomainObjectOrigin.DERIVED
    assert entry.provenance == provenance
    assert entry.expires_at > entry.created_at
