"""自动笔记 proposal 的 RED/GREEN 回归测试。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from core.base.cost_control import CostControl
from core.base.extra_llm_budget import ExtraLlmBudget, extra_llm_budget_scope
from core.managers.memory_engine import MemoryEngine
from core.managers.note_manager import NoteManager
from core.managers.note_proposal_pipeline import NoteProposalPipeline
from core.models.domain_provenance import DomainObjectOrigin, DomainProvenance
from core.models.memory_evolution import MemorySourceRef
from core.platform.composition import DerivedRebuildCoordinator
from core.storage.note_store import NoteStore

_CONTENT = "部署前需要完成数据库迁移。\n随后重建检索索引并核对健康状态。"


def _source(
    *,
    memory_id: int = 17,
    revision: str = "revision-17",
    content: str = _CONTENT,
) -> MemorySourceRef:
    """构造自动笔记使用的 canonical 来源快照。"""

    return MemorySourceRef(
        memory_id=memory_id,
        revision_token=revision,
        scope_key="session:test",
        privacy_level="shared",
        occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
        content=content,
    )


def _provenance(source: MemorySourceRef | None = None) -> DomainProvenance:
    """构造不携带正文的单来源派生证据。"""

    canonical = source or _source()
    return DomainProvenance(
        DomainObjectOrigin.DERIVED,
        (replace(canonical, content=None, source_role="primary"),),
    )


def _pipeline(
    *,
    sources: list[list[MemorySourceRef]] | None = None,
    generator: MagicMock | None = None,
    manager: MagicMock | None = None,
    source_store: MagicMock | None = None,
    cost_control: CostControl | None = None,
    min_length: int = 10,
    max_tags: int = 2,
) -> tuple[NoteProposalPipeline, MagicMock, MagicMock, MagicMock]:
    """构造具有可观测依赖的自动笔记管线。"""

    if generator is None:
        generator = MagicMock()
        generator.generate = AsyncMock(
            return_value={
                "title": "部署检查清单",
                "content": "先迁移数据库，再重建索引。",
                "tags": ["部署", "迁移", "索引"],
            }
        )
    manager = manager or MagicMock()
    manager.auto_create_from_memory = AsyncMock(return_value=42)
    if source_store is None:
        source_store = MagicMock()
        source_store.load_sources = AsyncMock(
            side_effect=sources or [[_source()], [_source()]]
        )
        source_store.load_all_sources = AsyncMock(return_value=[_source()])
    pipeline = NoteProposalPipeline(
        note_manager=manager,
        source_store=source_store,
        generator=generator,
        cost_control=cost_control or CostControl(mode="quality"),
        auto_create_min_length=min_length,
        max_tags=max_tags,
    )
    return pipeline, generator, manager, source_store


@pytest.mark.asyncio
async def test_note_proposal_persists_canonical_provenance_and_configured_tags() -> (
    None
):
    """预算允许时应生成来源约束笔记，并应用配置的 tag 上限。"""

    pipeline, generator, manager, _source_store = _pipeline(max_tags=2)
    with extra_llm_budget_scope(ExtraLlmBudget(1)):
        applied = await pipeline.apply_for_memory(17)

    assert applied is True
    generator.generate.assert_awaited_once_with(_CONTENT)
    manager.auto_create_from_memory.assert_awaited_once()
    call = manager.auto_create_from_memory.await_args
    assert call.args[0] == _CONTENT
    assert call.kwargs["source_memory_ids"] == [17]
    assert call.kwargs["title"] == "部署检查清单"
    assert call.kwargs["note_content"] == "先迁移数据库，再重建索引。"
    assert call.kwargs["tags"] == ["部署", "迁移"]
    provenance = call.kwargs["provenance"]
    assert provenance.origin is DomainObjectOrigin.DERIVED
    assert provenance.sources[0].memory_id == 17
    assert provenance.sources[0].revision_token == "revision-17"
    assert provenance.sources[0].content is None


@pytest.mark.asyncio
async def test_note_proposal_without_budget_uses_source_backed_fallback() -> None:
    """缺少额外 LLM 预算时应使用确定性 fallback，而不是让闭环失效。"""

    pipeline, generator, manager, _source_store = _pipeline()

    assert await pipeline.apply_for_memory(17) is True

    generator.generate.assert_not_awaited()
    call = manager.auto_create_from_memory.await_args
    assert call.kwargs["title"] == "部署前需要完成数据库迁移。"
    assert call.kwargs["note_content"] == "随后重建检索索引并核对健康状态。"
    assert call.kwargs["tags"] == []


@pytest.mark.asyncio
async def test_note_proposal_balanced_mode_does_not_spend_provider_budget() -> None:
    """均衡模式应拒绝额外 Provider 调用，但仍完成来源 fallback。"""

    pipeline, generator, manager, _source_store = _pipeline(
        cost_control=CostControl(mode="balanced")
    )
    budget = ExtraLlmBudget(1)

    with extra_llm_budget_scope(budget):
        assert await pipeline.apply_for_memory(17) is True

    generator.generate.assert_not_awaited()
    manager.auto_create_from_memory.assert_awaited_once()
    assert budget.snapshot().used == 0
    assert budget.snapshot().remaining == 1


@pytest.mark.asyncio
async def test_note_proposal_sanitizes_malformed_generated_fields() -> None:
    """不可信标题、正文和 tags 应回退或过滤到领域上限。"""

    generator = MagicMock()
    generator.generate = AsyncMock(
        return_value={
            "title": None,
            "content": ["非法正文"],
            "tags": ["合法", 123, "x" * 65, "合法", "第二个", "第三个"],
        }
    )
    pipeline, _generator, manager, _source_store = _pipeline(generator=generator)

    with extra_llm_budget_scope(ExtraLlmBudget(1)):
        assert await pipeline.apply_for_memory(17) is True

    call = manager.auto_create_from_memory.await_args
    assert call.kwargs["title"] == "部署前需要完成数据库迁移。"
    assert call.kwargs["note_content"] == "随后重建检索索引并核对健康状态。"
    assert call.kwargs["tags"] == ["合法", "第二个"]


@pytest.mark.asyncio
async def test_note_proposal_rechecks_source_revision_before_write() -> None:
    """生成期间 source revision 变化时必须丢弃旧 proposal。"""

    pipeline, generator, manager, source_store = _pipeline(
        sources=[
            [_source(revision="revision-old")],
            [_source(revision="revision-new")],
        ]
    )
    with extra_llm_budget_scope(ExtraLlmBudget(1)):
        applied = await pipeline.apply_for_memory(17)

    assert applied is False
    generator.generate.assert_awaited_once()
    manager.auto_create_from_memory.assert_not_awaited()
    assert source_store.load_sources.await_count == 2


@pytest.mark.asyncio
async def test_note_proposal_propagates_cancellation_and_releases_budget() -> None:
    """Provider 取消必须穿透管线，并释放当前 reservation。"""

    generator = MagicMock()
    generator.generate = AsyncMock(side_effect=asyncio.CancelledError)
    pipeline, _generator, manager, _source_store = _pipeline(generator=generator)
    budget = ExtraLlmBudget(1)

    with extra_llm_budget_scope(budget):
        with pytest.raises(asyncio.CancelledError):
            await pipeline.apply_for_memory(17)

    assert budget.snapshot().used == 0
    assert budget.snapshot().reserved == 0
    manager.auto_create_from_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_note_manager_uses_length_and_tag_sentinel_values() -> None:
    """Manager 必须消费 auto_create_min_length 与 max_tags 哨兵值。"""

    store = MagicMock()
    store.create = AsyncMock(return_value=9)
    manager = NoteManager(
        store=store,
        auto_create_min_length=8,
        max_tags=2,
    )
    provenance = _provenance()

    assert (
        await manager.auto_create_from_memory(
            "短内容7",
            source_memory_ids=[17],
            provenance=provenance,
        )
        is None
    )
    note_id = await manager.auto_create_from_memory(
        "长度刚好满足八个字符",
        source_memory_ids=[17],
        provenance=provenance,
        title="配置笔记",
        note_content="受控正文",
        tags=["一", "二", "三"],
    )

    assert note_id == 9
    created = store.create.await_args.args[0]
    assert created.origin is DomainObjectOrigin.DERIVED
    assert created.tags == ["一", "二"]
    with pytest.raises(ValueError, match="source_provenance_required"):
        await manager.auto_create_from_memory("长度足够但没有来源")


@pytest.mark.asyncio
async def test_note_rebuild_uses_all_canonical_sources_without_provider() -> None:
    """自动笔记重建应遍历 canonical source，并保持 Provider-free。"""

    source_store = MagicMock()
    source_store.load_all_sources = AsyncMock(
        return_value=[_source(memory_id=17), _source(memory_id=18, revision="rev-18")]
    )

    async def load_sources(memory_ids, *, max_content_chars=4_000):
        """按请求 ID 返回稳定 source，模拟重建期间的两次读取。"""

        del max_content_chars
        memory_id = int(tuple(memory_ids)[0])
        revision = "revision-17" if memory_id == 17 else "rev-18"
        return [_source(memory_id=memory_id, revision=revision)]

    source_store.load_sources = AsyncMock(side_effect=load_sources)
    pipeline, generator, manager, _store = _pipeline(source_store=source_store)

    result = await pipeline.rebuild_from_canonical()

    assert result["success"] is True
    assert result["created"] == 2
    assert result["errors"] == 0
    assert manager.auto_create_from_memory.await_count == 2
    generator.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_rebuild_coordinator_runs_note_stage_after_canonical_verification() -> (
    None
):
    """统一派生重建协调器必须执行自动笔记重建阶段。"""

    validator = MagicMock()
    validator._get_document_count = AsyncMock(return_value=2)
    validator.rebuild_indexes = AsyncMock(return_value={"success": True})
    engine = MagicMock()
    engine.rebuild_graph_index = AsyncMock(return_value={"success": True})
    engine.note_proposal_pipeline = MagicMock()
    engine.note_proposal_pipeline.rebuild_from_canonical = AsyncMock(
        return_value={"success": True, "created": 2, "errors": 0}
    )
    evolution = MagicMock()
    evolution.mode = "disabled"
    coordinator = DerivedRebuildCoordinator(validator, engine, evolution)

    result = await coordinator.rebuild_all()

    assert result["success"] is True
    assert result["stages"]["notes"]["created"] == 2
    engine.note_proposal_pipeline.rebuild_from_canonical.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_rebuild_notes_directly_uses_configured_pipeline() -> None:
    """notes 阶段应直接委托已装配管线，并透传安全计数结果。"""

    validator = MagicMock()
    engine = MagicMock()
    engine.note_proposal_pipeline = MagicMock()
    engine.note_proposal_pipeline.rebuild_from_canonical = AsyncMock(
        return_value={"success": True, "created": 1, "errors": 0}
    )
    coordinator = DerivedRebuildCoordinator(validator, engine)

    result = await coordinator._rebuild_notes()

    assert result == {"success": True, "created": 1, "errors": 0}
    engine.note_proposal_pipeline.rebuild_from_canonical.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_canonical_add_schedules_note_proposal_without_rollback() -> None:
    """canonical 成功后应触发笔记 proposal，派生失败不能回滚主写。"""

    engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
    engine.hybrid_retriever = MagicMock()
    engine.hybrid_retriever.add_memory = AsyncMock(return_value=123)
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
    engine.note_proposal_pipeline = pipeline
    tasks: list[asyncio.Task] = []

    def create_task(coroutine) -> None:
        """记录引擎派生任务，便于等待确定性完成。"""

        tasks.append(asyncio.create_task(coroutine))

    engine._create_tracked_task = create_task
    doc_id = await engine.add_memory("达到阈值的可信 canonical 记忆正文")
    await asyncio.gather(*tasks)

    assert doc_id == 123
    pipeline.apply_for_memory.assert_awaited_once_with(123)


async def _create_canonical_source(db_path: str) -> None:
    """写入 NoteStore 来源完整性测试使用的 canonical 记录。"""

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS documents (
               id INTEGER PRIMARY KEY, text TEXT NOT NULL, metadata TEXT,
               created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )"""
        )
        await db.execute(
            """INSERT INTO documents (id, text, metadata, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                17,
                _CONTENT,
                json.dumps(
                    {
                        "scope_key": "session:test",
                        "privacy_level": "shared",
                    }
                ),
                "2026-08-01T00:00:00+00:00",
                "revision-17",
            ),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_note_store_rebuild_is_idempotent_and_preserves_manual_versions(
    tmp_db_path: str,
) -> None:
    """同 provenance 重建不得重复或覆盖人工笔记，失效后历史仍可审计。"""

    store = NoteStore(tmp_db_path)
    await store.init_table()
    await _create_canonical_source(tmp_db_path)
    manager = NoteManager(store, auto_create_min_length=1, max_tags=2)
    manual_id = await manager.create_note("人工笔记", "人工正文")
    await manager.update_note(manual_id, content="人工修订正文")
    provenance = _provenance()

    first_id = await manager.auto_create_from_memory(
        _CONTENT,
        source_memory_ids=[17],
        provenance=provenance,
        title="自动笔记",
        note_content="派生正文",
        tags=["自动"],
    )
    second_id = await manager.auto_create_from_memory(
        _CONTENT,
        source_memory_ids=[17],
        provenance=provenance,
        title="自动笔记重放",
        note_content="不应覆盖旧派生正文",
        tags=["重放"],
    )

    assert first_id == second_id
    assert await store.count() == 2
    assert [version.content for version in await store.get_versions(manual_id)] == [
        "人工修订正文",
        "人工正文",
    ]
    async with aiosqlite.connect(tmp_db_path) as db:
        await db.execute("DELETE FROM documents WHERE id = 17")
        await db.commit()

    assert await store.get(first_id) is None
    assert await store.get(manual_id) is not None
    assert [version.content for version in await store.get_versions(first_id)] == [
        "派生正文"
    ]


@pytest.mark.asyncio
async def test_component_factory_wires_note_pipeline_with_runtime_sentinels(
    monkeypatch,
    tmp_path,
) -> None:
    """工厂应把 notes 配置哨兵投影到 Manager、Generator 和 proposal 管线。"""

    from astrbot.core.provider.provider import Provider

    from core.initializer.component_factory import ComponentFactory

    config = MagicMock()
    config.get.side_effect = lambda key, default=None: {
        "graph_memory.enabled": False,
        "importance_decay.decay_rate": 0,
        "forgetting_agent.auto_cleanup_enabled": False,
        "notes.auto_create_min_length": 73,
        "notes.max_tags": 3,
        "notes.max_versions": 7,
    }.get(key, default)
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
    note_manager = MagicMock()
    engine = MagicMock()
    engine.initialize = AsyncMock()
    engine.text_processor = None
    engine.profile_manager = None
    engine.knowledge_manager = None
    engine.note_manager = note_manager
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

    pipeline = engine.note_proposal_pipeline
    assert isinstance(pipeline, NoteProposalPipeline)
    assert pipeline._note_manager is note_manager
    assert pipeline._auto_create_min_length == 73
    assert pipeline._max_tags == 3
    assert pipeline._generator._min_length == 73
    await asyncio.gather(
        components["memory_evolution_store"].close(),
        components["identity_runtime"].close(),
    )
