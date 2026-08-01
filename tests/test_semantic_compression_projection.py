"""语义压缩 Projection 的生产闭环测试。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.initializer.derived_rebuild_coordinator import DerivedRebuildCoordinator
from core.managers.memory_evolution_manager import (
    EvolutionProposalRejected,
    MemoryEvolutionManager,
)
from core.managers.semantic_compressor import SemanticCompressor
from core.models.memory_evolution import (
    DerivedState,
    EvolutionProposal,
    MemoryProjectionProposal,
    MemorySourceRef,
    ProjectionBundle,
    ProjectionSourceView,
    ProjectionType,
    ProjectionView,
)
from core.retrieval.projection_reader import (
    ProjectionBudget,
    ProjectionReader,
    ProjectionScope,
)
from core.retrieval.rrf_fusion import HybridResult
from core.schedulers.decay_scheduler import DecayScheduler

UTC = timezone.utc
NOW = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)


def _source(
    memory_id: int,
    *,
    revision: str | None = None,
    scope: str = "private:user-a",
    privacy: str = "shared",
    topics: tuple[str, ...] = ("python", "ai"),
    age_days: int = 90,
) -> MemorySourceRef:
    """构造带完整 canonical 来源证据的旧记忆快照。"""

    ingested_at = NOW - timedelta(days=age_days)
    return MemorySourceRef(
        memory_id=memory_id,
        revision_token=revision or f"rev-{memory_id}",
        scope_key=scope,
        privacy_level=privacy,
        occurred_at=ingested_at,
        content=f"记忆正文 {memory_id}",
        ingested_at=ingested_at,
        topic_keys=topics,
        subject_key="subject:user-a",
    )


class _SourceStore:
    """记录全量扫描次数的 canonical source 测试替身。"""

    def __init__(self, sources: list[MemorySourceRef]) -> None:
        """保存按 ID 排序的来源快照。"""

        self.sources = list(sources)
        self.load_all_calls = 0

    async def load_all_sources(
        self,
        *,
        max_content_chars: int,
    ) -> list[MemorySourceRef]:
        """返回 canonical 快照副本并记录内容预算。"""

        assert max_content_chars > 0
        self.load_all_calls += 1
        return list(self.sources)


class _ProposalApplier:
    """捕获语义摘要 proposal 和对应来源。"""

    def __init__(self) -> None:
        """初始化调用记录。"""

        self.calls: list[tuple[EvolutionProposal, list[MemorySourceRef]]] = []

    async def apply(
        self,
        proposal: EvolutionProposal,
        sources: list[MemorySourceRef],
    ) -> int:
        """记录 proposal，并模拟写入一个 Projection。"""

        self.calls.append((proposal, list(sources)))
        return len(proposal.projections)


@pytest.mark.asyncio
async def test_semantic_compression_emits_source_backed_projection_only() -> None:
    """压缩器应只产出带 source revision 的 semantic_summary proposal。"""

    store = _SourceStore([_source(17), _source(18)])
    applier = _ProposalApplier()
    compressor = SemanticCompressor(
        source_store=store,
        proposal_applier=applier.apply,
        age_days=60,
        similarity_threshold=0.8,
    )

    result = await compressor.compress_old_memories(now=NOW)

    assert result == {
        "candidate_groups": 1,
        "projections_applied": 1,
        "failed_groups": 0,
        "canonical_mutations": 0,
    }
    proposal, sources = applier.calls[0]
    projection = proposal.projections[0]
    assert proposal.relations == ()
    assert projection.projection_type is ProjectionType.SEMANTIC_SUMMARY
    assert projection.source_aliases == ("M1", "M2")
    assert [(item.memory_id, item.revision_token) for item in sources] == [
        (17, "rev-17"),
        (18, "rev-18"),
    ]


@pytest.mark.asyncio
async def test_semantic_compression_never_mixes_scope_or_privacy() -> None:
    """同主题但 scope 或 privacy 不同的来源不得形成摘要。"""

    store = _SourceStore(
        [
            _source(17, scope="private:user-a", privacy="shared"),
            _source(18, scope="private:user-b", privacy="shared"),
            _source(19, scope="private:user-a", privacy="confidential"),
        ]
    )
    applier = _ProposalApplier()
    compressor = SemanticCompressor(
        source_store=store,
        proposal_applier=applier.apply,
        similarity_threshold=0.8,
    )

    result = await compressor.compress_old_memories(now=NOW)

    assert result["candidate_groups"] == 0
    assert result["projections_applied"] == 0
    assert applier.calls == []


@pytest.mark.asyncio
async def test_semantic_compression_filters_recent_and_low_overlap_sources() -> None:
    """年龄门和配置化 Jaccard 阈值都应参与生产候选筛选。"""

    store = _SourceStore(
        [
            _source(17, topics=("python", "ai", "memory")),
            _source(18, topics=("python", "ai"), age_days=10),
            _source(19, topics=("python", "testing")),
        ]
    )
    applier = _ProposalApplier()
    compressor = SemanticCompressor(
        source_store=store,
        proposal_applier=applier.apply,
        age_days=60,
        similarity_threshold=0.8,
    )

    result = await compressor.compress_old_memories(now=NOW)

    assert result["candidate_groups"] == 0
    assert applier.calls == []


@pytest.mark.asyncio
async def test_disabled_semantic_compression_does_not_scan_canonical() -> None:
    """关闭功能时调度入口不得读取 canonical 或写入 proposal。"""

    store = _SourceStore([_source(17), _source(18)])
    applier = _ProposalApplier()
    compressor = SemanticCompressor(
        source_store=store,
        proposal_applier=applier.apply,
        enabled=False,
    )

    result = await compressor.compress_old_memories(now=NOW)

    assert result["canonical_mutations"] == 0
    assert store.load_all_calls == 0
    assert applier.calls == []


@pytest.mark.asyncio
async def test_semantic_compression_propagates_cancellation() -> None:
    """取消 proposal 写入时必须传播 CancelledError。"""

    async def _cancel(
        _proposal: EvolutionProposal,
        _sources: list[MemorySourceRef],
    ) -> int:
        """模拟外部生命周期取消。"""

        raise asyncio.CancelledError

    compressor = SemanticCompressor(
        source_store=_SourceStore([_source(17), _source(18)]),
        proposal_applier=_cancel,
    )

    with pytest.raises(asyncio.CancelledError):
        await compressor.compress_old_memories(now=NOW)


class _PlanStore:
    """为 Manager 的二次 revision 校验提供最小 Store。"""

    def __init__(self, sources: list[MemorySourceRef]) -> None:
        """保存当前来源并初始化写入记录。"""

        self.sources = list(sources)
        self.plans = []

    async def load_sources(
        self,
        memory_ids: tuple[int, ...],
        *,
        max_content_chars: int,
    ) -> list[MemorySourceRef]:
        """按请求 ID 返回当前 canonical 来源。"""

        assert max_content_chars > 0
        source_by_id = {item.memory_id: item for item in self.sources}
        return [source_by_id[item] for item in memory_ids if item in source_by_id]

    async def apply_derived_plan(self, plan) -> None:
        """记录已通过安全校验的派生计划。"""

        self.plans.append(plan)


def _manager(store: _PlanStore) -> MemoryEvolutionManager:
    """构造不启动 worker 的最小 Evolution Manager。"""

    return MemoryEvolutionManager(
        store,
        SimpleNamespace(mode="active"),
        AsyncMock(),
        {"enabled": True, "mode": "active", "trigger_threshold": 0.7},
    )


def _summary_proposal() -> EvolutionProposal:
    """构造两个 canonical source 支持的语义摘要 proposal。"""

    return EvolutionProposal(
        projections=(
            MemoryProjectionProposal(
                projection_type=ProjectionType.SEMANTIC_SUMMARY,
                source_aliases=("M1", "M2"),
                title="语义摘要",
                summary="两条旧记忆的确定性摘要。",
                confidence=0.9,
                valid_from=None,
                valid_to=None,
            ),
        )
    )


@pytest.mark.asyncio
async def test_manager_rechecks_revision_before_applying_projection() -> None:
    """外部 projection proposal 写入前应重新读取并核对 revision。"""

    original = [_source(17), _source(18)]
    store = _PlanStore(original)
    manager = _manager(store)

    applied = await manager.apply_projection_proposal(_summary_proposal(), original)

    assert applied == 1
    assert store.plans[0].source_revisions == {17: "rev-17", 18: "rev-18"}
    assert store.plans[0].projections[0].source_memory_ids == (17, 18)


@pytest.mark.asyncio
async def test_manager_rejects_stale_semantic_projection() -> None:
    """任一 source revision 变化后，旧 proposal 不得进入 Projection Store。"""

    original = [_source(17), _source(18)]
    store = _PlanStore([_source(17, revision="rev-17-new"), _source(18)])
    manager = _manager(store)

    with pytest.raises(EvolutionProposalRejected, match="source_revision_changed"):
        await manager.apply_projection_proposal(_summary_proposal(), original)

    assert store.plans == []


@pytest.mark.parametrize(
    "changes",
    [
        {"scope_key": "private:user-b"},
        {"privacy_level": "confidential"},
        {"source_role": "supporting"},
    ],
    ids=["scope", "privacy", "role"],
)
@pytest.mark.asyncio
async def test_manager_rejects_changed_projection_security_boundary(
    changes: dict[str, str],
) -> None:
    """scope、privacy 或 role 变化后，旧 proposal 都必须被拒绝。"""

    original = [_source(17), _source(18)]
    current = [original[0], replace(original[1], **changes)]
    store = _PlanStore(current)
    manager = _manager(store)

    with pytest.raises(EvolutionProposalRejected, match="source_revision_changed"):
        await manager.apply_projection_proposal(_summary_proposal(), original)

    assert store.plans == []


class _ProjectionStore:
    """返回一个可见 semantic_summary bundle 的读取替身。"""

    def __init__(self) -> None:
        """创建带两个当前来源的 Projection bundle。"""

        projection_id = "semantic-summary:17:18"
        self.sources = [_source(17), _source(18)]
        self.bundle = ProjectionBundle(
            ProjectionView(
                projection_id=projection_id,
                projection_type=ProjectionType.SEMANTIC_SUMMARY,
                summary="不应在关闭后出现的摘要。",
                source_memory_ids=(17, 18),
                scope_key="private:user-a",
                privacy_level="shared",
                confidence=0.9,
                state=DerivedState.ACTIVE,
            ),
            (
                ProjectionSourceView(projection_id, 17, "rev-17", "primary", 0),
                ProjectionSourceView(
                    projection_id,
                    18,
                    "rev-18",
                    "supporting",
                    1,
                ),
            ),
        )

    async def active_projection_bundles_for_seeds(
        self,
        _seed_ids,
        *,
        scope_key: str,
        limit: int,
    ) -> list[ProjectionBundle]:
        """返回当前 scope 下的唯一 bundle。"""

        assert scope_key == "private:user-a"
        assert limit > 0
        return [self.bundle]

    async def load_sources(
        self,
        _source_ids,
        *,
        max_content_chars: int,
    ) -> list[MemorySourceRef]:
        """返回仍匹配 revision 的 canonical sources。"""

        assert max_content_chars > 0
        return list(self.sources)


def _candidate() -> HybridResult:
    """构造 canonical 召回候选。"""

    return HybridResult(
        doc_id=17,
        final_score=0.9,
        rrf_score=0.9,
        bm25_score=0.9,
        vector_score=0.9,
        content="canonical 正文",
        metadata={},
    )


@pytest.mark.asyncio
async def test_disabled_type_hides_existing_semantic_projection() -> None:
    """关闭语义压缩后，已有摘要不得附着到 canonical 候选。"""

    reader = ProjectionReader(
        _ProjectionStore(),
        disabled_types={ProjectionType.SEMANTIC_SUMMARY},
    )

    result = await reader.attach(
        [_candidate()],
        scope=ProjectionScope("private:user-a", "shared", now=NOW),
        budget=ProjectionBudget(),
    )

    assert result[0].content == "canonical 正文"
    assert "derived_projections" not in result[0].metadata


@pytest.mark.asyncio
async def test_daily_scheduler_invokes_semantic_compression() -> None:
    """每日可选维护应真实触发已经装配的语义压缩器。"""

    compressor = SimpleNamespace(compress_old_memories=AsyncMock(return_value={}))
    engine = SimpleNamespace(
        semantic_compressor=compressor,
        profile_manager=None,
        knowledge_manager=None,
        auto_learning=None,
        note_manager=None,
        atom_store=None,
        config={},
    )
    scheduler = DecayScheduler(engine, 0.0, ".", backup_enabled=False)

    await scheduler._run_optional_maintenance()

    compressor.compress_old_memories.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_derived_rebuild_invokes_semantic_projection_rebuild() -> None:
    """统一派生重建应包含 semantic_summary 重建阶段。"""

    compressor = SimpleNamespace(
        rebuild_from_canonical=AsyncMock(
            return_value={"success": True, "projections_applied": 2}
        )
    )
    engine = SimpleNamespace(semantic_compressor=compressor)
    coordinator = DerivedRebuildCoordinator(SimpleNamespace(), engine)

    result = await coordinator._rebuild_semantic_compression()

    assert result["projections_applied"] == 2
    compressor.rebuild_from_canonical.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_component_factory_wires_semantic_compression_sentinels(
    monkeypatch,
    tmp_path,
) -> None:
    """工厂应消费压缩门槛，并在其他维护项关闭时仍启动每日调度。"""

    from astrbot.core.provider.provider import Provider

    from core.initializer.component_factory import ComponentFactory

    config = MagicMock()
    config.get.side_effect = lambda key, default=None: {
        "graph_memory.enabled": False,
        "semantic_compression.enabled": True,
        "semantic_compression.age_days": 77.0,
        "semantic_compression.similarity_threshold": 0.91,
        "importance_decay.decay_rate": 0,
        "forgetting_agent.auto_cleanup_enabled": False,
        "backup_settings.enabled": False,
    }.get(key, default)
    config.get_section.side_effect = lambda key: (
        {
            "enabled": True,
            "mode": "active",
            "trigger_threshold": 0.7,
            "max_query_expansions": 8,
        }
        if key == "memory_evolution"
        else {}
    )
    config.session_manager = {}
    factory = ComponentFactory(MagicMock(), config, str(tmp_path))
    factory._build_injection_components = AsyncMock(
        return_value={
            "injection_decision_store": object(),
            "injection_decision_recorder": object(),
        }
    )
    db = MagicMock()
    db.initialize = AsyncMock()
    db_type = MagicMock(return_value=db)
    engine = MagicMock()
    engine.initialize = AsyncMock()
    engine.text_processor = None
    engine.profile_manager = None
    engine.knowledge_manager = None
    engine.note_manager = None
    engine.semantic_compressor = None
    monkeypatch.setattr(
        "core.initializer.component_factory.MemoryEngine",
        MagicMock(return_value=engine),
    )
    conversation_store = MagicMock()
    conversation_store.initialize = AsyncMock()
    monkeypatch.setattr(
        "core.initializer.component_factory.ConversationStore",
        MagicMock(return_value=conversation_store),
    )
    scheduler = MagicMock()
    scheduler.start = AsyncMock()
    scheduler.stop = AsyncMock()
    scheduler_factory = MagicMock(return_value=scheduler)
    monkeypatch.setattr(
        "core.initializer.component_factory.DecayScheduler",
        scheduler_factory,
    )
    faiss_checker = MagicMock()
    faiss_checker.check_and_fix_dimension_mismatch = AsyncMock()
    db_setup = MagicMock()
    db_setup.repair_message_counts = AsyncMock()
    db_setup.auto_rebuild_index_if_needed = AsyncMock()
    llm_provider = MagicMock(spec=Provider)
    llm_provider.text_chat = AsyncMock()

    components = await factory.build_all(
        MagicMock(),
        llm_provider,
        db_type,
        faiss_checker,
        db_setup,
    )

    compressor = engine.semantic_compressor
    assert isinstance(compressor, SemanticCompressor)
    assert compressor._age_days == 77.0
    assert compressor._sim_threshold == 0.91
    scheduler_factory.assert_called_once()
    scheduler.start.assert_awaited_once_with()
    await asyncio.gather(
        components["memory_evolution_manager"].stop(),
        components["memory_evolution_store"].close(),
        components["identity_runtime"].close(),
    )
