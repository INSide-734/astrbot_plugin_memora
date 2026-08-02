"""LIFE-04 再巩固闭环：候选不直接写 canonical，应用走 CAS，可回滚。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from core.managers.memory_engine import MemoryEngine
from core.managers.reconsolidation import ReconsolidationManager
from core.retrieval.rrf_fusion import HybridResult
from core.storage.reconsolidation_store import ReconsolidationStore


class _SimulatedCrash(BaseException):
    """模拟 canonical 提交后、候选状态提交前的进程中断。"""


def _memory_dict(content: str, revision: str = "r-7") -> dict[str, object]:
    """构造带稳定 revision 的 canonical 视图。"""

    return {
        "text": content,
        "updated_at": revision,
        "metadata": {
            "memory_id": 7,
            "doc_id": "d7",
            "access_count": 8,
            "importance": 0.7,
        },
    }


@pytest.mark.asyncio
async def test_propose_stages_candidate_without_writing_canonical(
    tmp_path: Path,
) -> None:
    """召回入口只生成候选；LLM 修订不直接写入 canonical。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    manager = ReconsolidationManager(
        store=store,
        get_memory_cb=AsyncMock(return_value=_memory_dict("原始记忆正文")),
        llm_caller=AsyncMock(return_value="修正后的记忆正文内容"),
        enabled=True,
        min_recall_count=5,
    )

    result = await manager.maybe_propose(7, context="近期上下文")

    assert result is not None
    assert result["status"] == "pending"
    candidate = await store.get_candidate(result["candidate_id"])
    assert candidate["memory_id"] == 7
    assert candidate["source_revision"] == "r-7"
    assert candidate["evidence_type"] == "llm_revision"
    assert "近期上下文" not in json.dumps(candidate, ensure_ascii=False)


@pytest.mark.asyncio
async def test_apply_candidate_rejects_stale_revision(tmp_path: Path) -> None:
    """source revision 已变化时应用必须被拒绝，不触碰 canonical。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    manager = ReconsolidationManager(
        store=store,
        get_memory_cb=AsyncMock(return_value=_memory_dict("原始记忆正文")),
        llm_caller=AsyncMock(return_value="修正后的记忆正文内容"),
        enabled=True,
    )
    proposed = await manager.maybe_propose(7, context="上下文")
    update_cb = AsyncMock(return_value=False)
    update_cb._last_write_reason_code = "source_revision_mismatch"

    result = await manager.apply_candidate(proposed["candidate_id"], update_cb)

    assert result["applied"] is False
    assert result["reason_code"] == "source_revision_mismatch"
    update_cb.assert_awaited_once_with(
        7,
        {"content": "修正后的记忆正文内容", "metadata": ANY},
        expected_revision="r-7",
    )
    candidate = await store.get_candidate(proposed["candidate_id"])
    assert candidate["status"] == "rejected"


@pytest.mark.asyncio
async def test_concurrent_apply_claims_canonical_once(tmp_path: Path) -> None:
    """并发审批只能有一个 apply intent 进入 canonical 写入口。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    manager = ReconsolidationManager(
        store=store,
        get_memory_cb=AsyncMock(return_value=_memory_dict("原始记忆正文")),
        llm_caller=AsyncMock(return_value="修正后的记忆正文内容"),
        enabled=True,
    )
    proposed = await manager.maybe_propose(7, context="上下文")
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def update_memory(*args: object, **kwargs: object) -> bool:
        """阻塞第一个 canonical 更新，制造可控的并发窗口。"""

        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return True

    first = asyncio.create_task(
        manager.apply_candidate(proposed["candidate_id"], update_memory)
    )
    await entered.wait()
    second_update = AsyncMock(return_value=True)

    with pytest.raises(RuntimeError, match="apply_in_progress"):
        await manager.apply_candidate(proposed["candidate_id"], second_update)

    release.set()
    result = await first

    assert result["applied"] is True
    assert calls == 1
    second_update.assert_not_awaited()
    candidate = await store.get_candidate(proposed["candidate_id"])
    assert candidate["status"] == "approved"
    assert await store.list_incomplete_applies() == []


@pytest.mark.asyncio
async def test_restart_recovers_apply_after_canonical_commit_before_finalize(
    tmp_path: Path,
) -> None:
    """canonical 已写入但 Store 未收口时，重启应把 apply intent 终结为 approved。"""

    db_path = tmp_path / "reconsolidation.db"
    store = ReconsolidationStore(db_path)
    await store.initialize()
    manager = ReconsolidationManager(
        store=store,
        get_memory_cb=AsyncMock(return_value=_memory_dict("原始记忆正文")),
        llm_caller=AsyncMock(return_value="修正后的记忆正文内容"),
        enabled=True,
    )
    proposed = await manager.maybe_propose(7, context="上下文")
    await store.begin_apply(
        proposed["candidate_id"],
        expected_revision="r-7",
    )
    restarted = ReconsolidationManager(
        store=ReconsolidationStore(db_path),
        get_memory_cb=AsyncMock(
            return_value=_memory_dict("修正后的记忆正文内容", revision="r-8")
        ),
        update_memory_cb=AsyncMock(return_value=True),
        enabled=True,
    )
    await restarted._store.initialize()

    result = await restarted.recover_incomplete_applies()

    assert result == {"recovered": 1, "blocked": 0}
    restarted._update_memory.assert_not_awaited()
    candidate = await restarted._store.get_candidate(proposed["candidate_id"])
    assert candidate["status"] == "approved"
    assert await restarted._store.list_incomplete_applies() == []


@pytest.mark.asyncio
async def test_restart_blocks_apply_after_unrelated_canonical_edit(
    tmp_path: Path,
) -> None:
    """恢复发现 canonical 已被其他编辑修改时不得覆盖新正文。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    manager = ReconsolidationManager(
        store=store,
        get_memory_cb=AsyncMock(return_value=_memory_dict("原始记忆正文")),
        llm_caller=AsyncMock(return_value="修正后的记忆正文内容"),
        enabled=True,
    )
    proposed = await manager.maybe_propose(7, context="上下文")
    await store.begin_apply(proposed["candidate_id"], expected_revision="r-7")
    update_cb = AsyncMock(return_value=True)
    restarted = ReconsolidationManager(
        store=store,
        get_memory_cb=AsyncMock(
            return_value=_memory_dict("另一位编辑提交的新正文", revision="r-10")
        ),
        update_memory_cb=update_cb,
        enabled=True,
    )

    result = await restarted.recover_incomplete_applies()

    assert result == {"recovered": 0, "blocked": 1}
    update_cb.assert_not_awaited()
    candidate = await store.get_candidate(proposed["candidate_id"])
    assert candidate["status"] == "failed"
    assert candidate["reason_code"] == "source_revision_mismatch"


@pytest.mark.asyncio
async def test_complete_apply_is_atomic_when_action_audit_fails(tmp_path: Path) -> None:
    """apply 审计写失败时，候选和 intent 必须一起保留待恢复。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    candidate = await store.stage_candidate(
        memory_id=7,
        source_revision="r-7",
        old_content="原始记忆正文",
        old_metadata={"access_count": 8},
        proposed_content="修正后的记忆正文内容",
        change_summary="LLM 修订候选",
        evidence_type="llm_revision",
    )
    await store.begin_apply(candidate["candidate_id"], expected_revision="r-7")
    async with aiosqlite.connect(store.db_path) as db:
        await db.execute(
            """
            CREATE TRIGGER reject_apply_action
            BEFORE INSERT ON reconsolidation_actions
            WHEN NEW.action='apply'
            BEGIN
                SELECT RAISE(ABORT, 'apply audit blocked');
            END
            """
        )
        await db.commit()

    with pytest.raises(sqlite3.IntegrityError):
        await store.complete_apply(
            candidate["candidate_id"],
            applied=True,
            reason_code="applied",
        )

    persisted = await store.get_candidate(candidate["candidate_id"])
    assert persisted["status"] == "pending"
    assert [item["candidate_id"] for item in await store.list_incomplete_applies()] == [
        candidate["candidate_id"]
    ]


@pytest.mark.asyncio
async def test_mark_apply_blocked_is_atomic_when_action_audit_fails(
    tmp_path: Path,
) -> None:
    """恢复失败审计写入失败时，候选仍保持 pending 并可由后续恢复处理。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    candidate = await store.stage_candidate(
        memory_id=7,
        source_revision="r-7",
        old_content="原始记忆正文",
        old_metadata={"access_count": 8},
        proposed_content="修正后的记忆正文内容",
        change_summary="LLM 修订候选",
        evidence_type="llm_revision",
    )
    await store.begin_apply(candidate["candidate_id"], expected_revision="r-7")
    async with aiosqlite.connect(store.db_path) as db:
        await db.execute(
            """
            CREATE TRIGGER reject_apply_block_action
            BEFORE INSERT ON reconsolidation_actions
            WHEN NEW.action='apply'
            BEGIN
                SELECT RAISE(ABORT, 'apply block audit blocked');
            END
            """
        )
        await db.commit()

    with pytest.raises(sqlite3.IntegrityError):
        await store.mark_apply_blocked(
            candidate["candidate_id"],
            reason_code="source_revision_mismatch",
        )

    persisted = await store.get_candidate(candidate["candidate_id"])
    assert persisted["status"] == "pending"
    assert await store.list_incomplete_applies()


@pytest.mark.asyncio
async def test_rollback_restores_old_content_with_cas(tmp_path: Path) -> None:
    """批准后的候选可按当前 revision CAS 回滚到旧正文。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    manager = ReconsolidationManager(
        store=store,
        get_memory_cb=AsyncMock(return_value=_memory_dict("原始记忆正文")),
        llm_caller=AsyncMock(return_value="修正后的记忆正文内容"),
        enabled=True,
    )
    proposed = await manager.maybe_propose(7, context="上下文")
    await manager.apply_candidate(
        proposed["candidate_id"],
        AsyncMock(return_value=True),
    )
    get_cb = AsyncMock(
        return_value=_memory_dict("修正后的记忆正文内容", revision="r-8")
    )
    update_cb = AsyncMock(return_value=True)

    result = await manager.rollback_candidate(
        proposed["candidate_id"],
        get_memory_cb=get_cb,
        update_memory_cb=update_cb,
    )

    assert result["restored"] is True
    update_cb.assert_awaited_once()
    assert update_cb.await_args.args[1]["content"] == "原始记忆正文"
    assert update_cb.await_args.kwargs["expected_revision"] == "r-8"
    candidate = await store.get_candidate(proposed["candidate_id"])
    assert candidate["status"] == "rolled_back"


@pytest.mark.asyncio
async def test_failed_rollback_cancels_intent_and_keeps_candidate_approved(
    tmp_path: Path,
) -> None:
    """canonical CAS 明确失败时应清理意图，并允许管理员重新发起回滚。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    manager = ReconsolidationManager(
        store=store,
        get_memory_cb=AsyncMock(return_value=_memory_dict("原始记忆正文")),
        llm_caller=AsyncMock(return_value="修正后的记忆正文内容"),
        enabled=True,
    )
    proposed = await manager.maybe_propose(7, context="上下文")
    await manager.apply_candidate(
        proposed["candidate_id"],
        AsyncMock(return_value=True),
    )
    update_cb = AsyncMock(return_value=False)
    update_cb._last_write_reason_code = "source_revision_mismatch"

    result = await manager.rollback_candidate(
        proposed["candidate_id"],
        get_memory_cb=AsyncMock(
            return_value=_memory_dict("修正后的记忆正文内容", revision="r-8")
        ),
        update_memory_cb=update_cb,
    )

    assert result == {
        "restored": False,
        "reason_code": "source_revision_mismatch",
    }
    assert await store.list_incomplete_rollbacks() == []
    candidate = await store.get_candidate(proposed["candidate_id"])
    assert candidate["status"] == "approved"


@pytest.mark.asyncio
async def test_restart_recovers_rollback_after_cross_store_crash(
    tmp_path: Path,
) -> None:
    """canonical 已恢复但 Store 未收口时，重启应补刷派生数据并完成回滚。"""

    db_path = tmp_path / "reconsolidation.db"
    store = ReconsolidationStore(db_path)
    await store.initialize()
    manager = ReconsolidationManager(
        store=store,
        get_memory_cb=AsyncMock(return_value=_memory_dict("原始记忆正文")),
        llm_caller=AsyncMock(return_value="修正后的记忆正文内容"),
        enabled=True,
    )
    proposed = await manager.maybe_propose(7, context="上下文")
    await manager.apply_candidate(
        proposed["candidate_id"],
        AsyncMock(return_value=True),
    )
    current = _memory_dict("修正后的记忆正文内容", revision="r-8")
    restored = _memory_dict("原始记忆正文", revision="r-9")
    get_cb = AsyncMock(side_effect=[current, restored, restored])
    crashing_update = AsyncMock(side_effect=_SimulatedCrash())

    with pytest.raises(_SimulatedCrash):
        await manager.rollback_candidate(
            proposed["candidate_id"],
            get_memory_cb=get_cb,
            update_memory_cb=crashing_update,
        )

    restarted_store = ReconsolidationStore(db_path)
    await restarted_store.initialize()
    recovery_update = AsyncMock(return_value=True)
    refresh_derived = AsyncMock(return_value=True)
    restarted = ReconsolidationManager(
        store=restarted_store,
        get_memory_cb=get_cb,
        update_memory_cb=recovery_update,
        refresh_derived_cb=refresh_derived,
        enabled=True,
    )

    result = await restarted.recover_incomplete_rollbacks()

    assert result == {"recovered": 1, "blocked": 0}
    recovery_update.assert_awaited_once()
    assert recovery_update.await_args.args[1]["content"] == "原始记忆正文"
    assert recovery_update.await_args.kwargs["expected_revision"] == "r-9"
    refresh_derived.assert_awaited_once_with(7)
    candidate = await restarted_store.get_candidate(proposed["candidate_id"])
    assert candidate["status"] == "rolled_back"
    assert await restarted_store.list_incomplete_rollbacks() == []


@pytest.mark.asyncio
async def test_restart_blocks_rollback_after_unrelated_canonical_edit(
    tmp_path: Path,
) -> None:
    """崩溃后 canonical 被另行编辑时，恢复器不得覆盖新正文。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    manager = ReconsolidationManager(
        store=store,
        get_memory_cb=AsyncMock(return_value=_memory_dict("原始记忆正文")),
        llm_caller=AsyncMock(return_value="修正后的记忆正文内容"),
        enabled=True,
    )
    proposed = await manager.maybe_propose(7, context="上下文")
    await manager.apply_candidate(
        proposed["candidate_id"],
        AsyncMock(return_value=True),
    )
    await store.begin_rollback(
        proposed["candidate_id"],
        expected_revision="r-8",
    )
    update_cb = AsyncMock(return_value=True)
    restarted = ReconsolidationManager(
        store=store,
        get_memory_cb=AsyncMock(
            return_value=_memory_dict("另一位编辑提交的新正文", revision="r-10")
        ),
        update_memory_cb=update_cb,
        enabled=True,
    )

    result = await restarted.recover_incomplete_rollbacks()

    assert result == {"recovered": 0, "blocked": 1}
    update_cb.assert_not_awaited()
    candidate = await store.get_candidate(proposed["candidate_id"])
    assert candidate["status"] == "approved"
    assert await store.list_incomplete_rollbacks() == []


@pytest.mark.asyncio
async def test_real_engine_wires_reconsolidation_when_enabled(tmp_path: Path) -> None:
    """启用时引擎应装配 Store 与 Manager，关闭时保持 None。"""

    config = {
        "graph_memory_enabled": False,
        "recall_engine.stopwords_path": "",
        "write_reliability.repair_enabled": False,
        "user_profile.enabled": False,
        "auto_learning.enabled": False,
        "knowledge_base.enabled": False,
        "notes.enabled": False,
        "reranker.enabled": False,
        "export.enabled": False,
        "continuity_tracking.enabled": False,
        "reconsolidation.enabled": True,
        "reconsolidation.min_recall_count": 4,
        "data_dir": str(tmp_path),
    }
    engine = MemoryEngine(
        db_path=str(tmp_path / "memora.db"),
        faiss_db=MagicMock(),
        config=config,
    )
    engine._schema.create_tables = AsyncMock()
    try:
        with patch("core.managers.memory_engine_lifecycle.BM25Retriever") as bm25_cls:
            bm25_cls.return_value.initialize = AsyncMock()
            await engine.initialize()

        assert isinstance(engine.reconsolidation, ReconsolidationManager)
        assert isinstance(engine.reconsolidation_store, ReconsolidationStore)
        assert engine.reconsolidation._enabled is True
        assert engine.reconsolidation._refresh_derived is not None
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_reconsolidation_derived_refresh_reindexes_current_source() -> None:
    """回滚派生刷新必须读取当前 canonical，并重建同一 ID 的 graph 条目。"""

    engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
    engine.get_memory = AsyncMock(
        return_value=_memory_dict("恢复后的记忆正文", revision="r-9")
    )
    engine.graph_memory_manager = MagicMock()
    engine.graph_memory_manager.index_memory = AsyncMock()

    result = await engine._refresh_reconsolidation_derived(7)

    assert result is True
    engine.graph_memory_manager.index_memory.assert_awaited_once()
    args = engine.graph_memory_manager.index_memory.await_args.args
    assert args[0] == 7
    assert args[1] == "恢复后的记忆正文"


@pytest.mark.asyncio
async def test_recall_proposes_candidate_but_never_writes_canonical() -> None:
    """召回钩子只调用候选生成，不直接调用 update_memory。"""

    manager = AsyncMock(spec=ReconsolidationManager)
    manager.maybe_propose.return_value = {
        "candidate_id": "c1",
        "memory_id": 7,
        "status": "pending",
    }
    engine = SimpleNamespace(reconsolidation=manager)
    handler = object.__new__(type("RecallHandlerStub", (), {}))
    handler._memory_engine = engine

    from core.handlers.recall_handler import RecallHandler

    normalized = RecallHandler._safe_candidates(
        [
            HybridResult(
                doc_id=7,
                final_score=1.0,
                rrf_score=1.0,
                bm25_score=None,
                vector_score=None,
                content="记忆",
                metadata={},
            )
        ]
    )

    await RecallHandler._maybe_propose_reconsolidation(
        handler,
        normalized,
        "查询文本",
    )

    manager.maybe_propose.assert_awaited_once_with(7, context="查询文本")


@pytest.mark.asyncio
async def test_stage_candidate_reuses_existing_pending_row(tmp_path: Path) -> None:
    """相同来源与提案重复写入时应复用同一条 pending 候选。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    payload = {
        "memory_id": 7,
        "source_revision": "r-7",
        "old_content": "原始记忆正文",
        "old_metadata": {"access_count": 8},
        "proposed_content": "修正后的记忆正文内容",
        "change_summary": "LLM 修订候选",
        "evidence_type": "llm_revision",
    }

    first = await store.stage_candidate(**payload)
    second = await store.stage_candidate(**payload)

    assert second["candidate_id"] == first["candidate_id"]
    assert len(await store.list_candidates()) == 1


@pytest.mark.asyncio
async def test_stage_candidate_serializes_concurrent_duplicates(tmp_path: Path) -> None:
    """并发重复提案只能保留一条 pending 候选。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    payload = {
        "memory_id": 7,
        "source_revision": "r-7",
        "old_content": "原始记忆正文",
        "old_metadata": {"access_count": 8},
        "proposed_content": "修正后的记忆正文内容",
        "change_summary": "LLM 修订候选",
        "evidence_type": "llm_revision",
    }

    results = await asyncio.gather(
        *(store.stage_candidate(**payload) for _ in range(8)),
        return_exceptions=True,
    )

    assert not [result for result in results if isinstance(result, Exception)]
    assert len(await store.list_candidates()) == 1


@pytest.mark.asyncio
async def test_transition_rolls_back_status_when_action_audit_fails(
    tmp_path: Path,
) -> None:
    """动作审计写入失败时，候选状态迁移必须一并回滚。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    candidate = await store.stage_candidate(
        memory_id=7,
        source_revision="r-7",
        old_content="原始记忆正文",
        old_metadata={"access_count": 8},
        proposed_content="修正后的记忆正文内容",
        change_summary="LLM 修订候选",
        evidence_type="llm_revision",
    )
    async with aiosqlite.connect(store.db_path) as db:
        await db.execute(
            """
            CREATE TRIGGER reject_reconsolidation_action
            BEFORE INSERT ON reconsolidation_actions
            WHEN NEW.action='reject'
            BEGIN
                SELECT RAISE(ABORT, 'action audit blocked');
            END
            """
        )
        await db.commit()

    with pytest.raises(sqlite3.IntegrityError):
        await store.transition(
            candidate["candidate_id"],
            expected_status="pending",
            new_status="rejected",
            reason_code="manual_reject",
            action="reject",
        )

    persisted = await store.get_candidate(candidate["candidate_id"])
    assert persisted is not None
    assert persisted["status"] == "pending"
    assert [
        item["action"] for item in await store.list_actions(candidate["candidate_id"])
    ] == ["stage"]


@pytest.mark.asyncio
async def test_complete_rollback_is_atomic_when_action_audit_fails(
    tmp_path: Path,
) -> None:
    """回滚动作审计失败时，候选状态和恢复操作必须一起保留。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    candidate = await store.stage_candidate(
        memory_id=7,
        source_revision="r-7",
        old_content="原始记忆正文",
        old_metadata={"access_count": 8},
        proposed_content="修正后的记忆正文内容",
        change_summary="LLM 修订候选",
        evidence_type="llm_revision",
    )
    await store.transition(
        candidate["candidate_id"],
        expected_status="pending",
        new_status="approved",
        reason_code="applied",
        action="apply",
    )
    await store.begin_rollback(
        candidate["candidate_id"],
        expected_revision="r-8",
    )
    async with aiosqlite.connect(store.db_path) as db:
        await db.execute(
            """
            CREATE TRIGGER reject_rollback_action
            BEFORE INSERT ON reconsolidation_actions
            WHEN NEW.action='rollback'
            BEGIN
                SELECT RAISE(ABORT, 'rollback audit blocked');
            END
            """
        )
        await db.commit()

    with pytest.raises(sqlite3.IntegrityError):
        await store.complete_rollback(candidate["candidate_id"])

    persisted = await store.get_candidate(candidate["candidate_id"])
    assert persisted is not None
    assert persisted["status"] == "approved"
    operations = await store.list_incomplete_rollbacks()
    assert [item["candidate_id"] for item in operations] == [candidate["candidate_id"]]
    assert [
        item["action"] for item in await store.list_actions(candidate["candidate_id"])
    ] == [
        "stage",
        "apply",
    ]


@pytest.mark.asyncio
async def test_unchanged_llm_output_creates_no_candidate(tmp_path: Path) -> None:
    """未使用 LLM 或输出未变化时不创建候选。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    manager = ReconsolidationManager(
        store=store,
        get_memory_cb=AsyncMock(return_value=_memory_dict("原样返回的正文内容")),
        llm_caller=AsyncMock(return_value="原样返回的正文内容"),
        enabled=True,
    )

    assert await manager.maybe_propose(7, context="上下文") is None
    assert await store.list_candidates() == []
