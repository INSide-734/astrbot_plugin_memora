"""C1 独立验收后的真实存储故障回归，不访问宿主运行数据。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import faiss
import numpy as np
import pytest

from core.features.injection.application.selection import _user_supported_candidate
from core.features.memory.application.atom_lifecycle_manager import AtomLifecycleManager
from core.features.memory.domain.memory_atom import MemoryAtom
from core.features.memory.infrastructure.atom_store import AtomStore
from core.features.recall.processors.graph_extractor import GraphExtractor
from core.features.retrieval.bm25_retriever import BM25Retriever
from core.shared.summary_source_fence import SummarySourceFence
from tests.fact_evidence_helpers import fact_evidence
from tests.test_managers_memory_crud import (
    TestMemoryEngineStagedDocumentWrite as _StagedHarness,
)
from tests.test_managers_memory_crud import (
    _active_canonical_ids,
    _canonical_rows,
    _engine_with_canonical_db,
    _latest_operation,
    _ReplaceLifecycleStub,
)


class _VectorStorage:
    """真实 FAISS，失败在 insert 已执行后注入以覆盖重放重复 ID。"""

    def __init__(self) -> None:
        self.index = faiss.IndexIDMap(faiss.IndexFlatL2(2))
        self.failure: BaseException | None = None

    async def insert(self, vector, doc_id) -> None:
        self.index.add_with_ids(
            np.asarray(vector, dtype=np.float32).reshape(1, 2),
            np.asarray([doc_id], dtype=np.int64),
        )
        if self.failure is not None:
            raise self.failure

    async def delete(self, ids: list[int]) -> None:
        self.index.remove_ids(np.asarray(ids, dtype=np.int64))


async def _tokens(content: str, **kwargs) -> list[str]:
    return content.split()


async def _staged_engine(path: str):
    bm25 = BM25Retriever(path, SimpleNamespace(tokenize_async=_tokens))
    engine, db, host, bm25 = await _StagedHarness()._engine(path, bm25_retriever=bm25)
    host.embedding_storage = _VectorStorage()
    return engine, db, host, bm25


async def _fts_count(db, doc_id: int) -> int:
    row = await (
        await db.execute(
            "SELECT COUNT(*) FROM memora_memories_fts WHERE CAST(doc_id AS INTEGER)=?",
            (doc_id,),
        )
    ).fetchone()
    return int(row[0])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fenced,cancelled", [(False, False), (True, False), (False, True)]
)
async def test_index_repair_preserves_one_canonical_and_one_vector(
    tmp_db_path: str, caplog, fenced: bool, cancelled: bool
) -> None:
    """常规、来源 fence 和取消必须都保留索引意图，不能伪报 completed。"""
    engine, db, host, _bm25 = await _staged_engine(tmp_db_path)
    try:
        host.embedding_storage.failure = (
            asyncio.CancelledError()
            if cancelled
            else RuntimeError("PRIVATE_CANARY_SCOPE_BODY")
        )
        kwargs = {"metadata": {"idempotency_key": "same-request"}}
        if fenced:
            engine.set_summary_source_validator(AsyncMock(return_value=True))
            kwargs["source_fence"] = SummarySourceFence(
                job_id="job",
                session_id="s",
                session_epoch=1,
                start_seq=0,
                end_seq=1,
                expected_count=1,
                source_digest="digest",
                worker_generation=1,
                claim_token="claim",
            )
        if cancelled:
            with pytest.raises(asyncio.CancelledError):
                await engine.add_memory("new fact", **kwargs)
            doc_id = (await _canonical_rows(tmp_db_path))[0][0]
        else:
            doc_id = await engine.add_memory("new fact", **kwargs)
        row = await _latest_operation(db, "add")
        assert row["status"] != "completed"
        assert json.loads(row["payload"])["indexes_pending"] is True
        assert await _active_canonical_ids(db) == [doc_id]
        assert "PRIVATE_CANARY_SCOPE_BODY" not in caplog.text

        host.embedding_storage.failure = None
        assert await engine._write_journal.repair_incomplete() == 1
        assert await engine.add_memory("new fact", **kwargs) == doc_id
        assert await _active_canonical_ids(db) == [doc_id]
        assert host.embedding_storage.index.ntotal == 1
        assert await _fts_count(db, doc_id) == 1
        row = await _latest_operation(db, "add")
        assert row["status"] == "completed"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_fts_failure_repairs_current_document_without_duplication(
    tmp_db_path: str,
) -> None:
    """FTS 单独失败时按 ID 重建，成功的 FAISS 不应变成两个同 ID 向量。"""
    engine, db, host, bm25 = await _staged_engine(tmp_db_path)
    try:
        add = bm25.add_document
        bm25.add_document = AsyncMock(side_effect=RuntimeError("fts unavailable"))
        doc_id = await engine.add_memory("new fact")
        assert await _fts_count(db, doc_id) == 0
        bm25.add_document = add
        assert await engine._write_journal.repair_incomplete() == 1
        assert await _fts_count(db, doc_id) == 1
        assert host.embedding_storage.index.ntotal == 1
        assert await _canonical_rows(tmp_db_path) == [(doc_id, "new fact")]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_delete_false_after_canonical_commit_keeps_new_owner(
    tmp_db_path: str,
) -> None:
    """旧行实际已删却返回 False，不能再次补偿删除唯一新行。"""
    engine, db, store = await _engine_with_canonical_db(tmp_db_path)
    try:
        await store.insert_document("old", "old fact", {})
        lifecycle = _ReplaceLifecycleStub(store)
        delete = lifecycle.delete_memory

        async def partial_delete(memory_id: int) -> bool:
            result = await delete(memory_id)
            return False if memory_id == 1 else result

        lifecycle.delete_memory = partial_delete
        engine.hybrid_retriever = lifecycle
        assert await engine.update_memory(1, {"content": "new fact"}) is True
        assert await _canonical_rows(tmp_db_path) == [(2, "new fact")]
        assert await _active_canonical_ids(db) == [2]
        row = await _latest_operation(db, "replace_content")
        assert row["status"] == "needs_repair"
        await engine._write_journal.repair_incomplete()
        assert (await _latest_operation(db, "replace_content"))["status"] == "completed"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_replace_never_exposes_two_active_rows_even_inside_transaction(
    tmp_db_path: str,
) -> None:
    """数据库触发器监视每次写入，不允许仅在函数收尾时才恢复唯一性。"""
    engine, db, store = await _engine_with_canonical_db(tmp_db_path)
    try:
        await store.insert_document(
            "old", "old fact", {"memory_status": "active", "status": "active"}
        )
        active_count = """SELECT COUNT(*) FROM documents WHERE
            COALESCE(json_extract(metadata, '$.memory_status'), json_extract(metadata, '$.status'), 'active') IN ('active','current','stable')
            AND NOT COALESCE(json_extract(metadata, '$.summary_source_orphan'),0)"""
        for action in ("INSERT", "UPDATE"):
            await db.execute(f"""CREATE TRIGGER guard_{action} AFTER {action} ON documents
                BEGIN SELECT CASE WHEN ({active_count}) > 1 THEN RAISE(ABORT, 'double_active') END; END""")
        await db.commit()
        lifecycle = _ReplaceLifecycleStub(store)
        lifecycle.fail_delete_ids = {1, 2}
        engine.hybrid_retriever = lifecycle
        assert await engine.update_memory(1, {"content": "new fact"}) is False
        assert await _canonical_rows(tmp_db_path) == [(1, "old fact"), (2, "new fact")]
        assert await _active_canonical_ids(db) == [1]
        lifecycle.fail_delete_ids.clear()
        assert await engine._write_journal.repair_incomplete() == 1
        assert await _canonical_rows(tmp_db_path) == [(2, "new fact")]
        assert await _active_canonical_ids(db) == [2]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_replacement_clears_stale_fact_metadata(tmp_db_path: str) -> None:
    """正文替换未提供事实证据时，新 owner 不继承旧事实字段。"""
    engine, db, store = await _engine_with_canonical_db(tmp_db_path)
    try:
        await store.insert_document(
            "old",
            "旧事实",
            {
                "scope_key": "test-scope",
                "privacy_level": "public",
                "revision_token": "r1",
                "key_facts": ["旧事实"],
                "fact_source_evidence": [[{"role": "user"}]],
                "canonical_summary": "旧事实",
            },
        )
        lifecycle = _ReplaceLifecycleStub(store)
        engine.hybrid_retriever = lifecycle
        assert await engine.update_memory(1, {"content": "新事实"}) is True
        row = await (
            await db.execute("SELECT metadata FROM documents WHERE id=2")
        ).fetchone()
        metadata = json.loads(row[0])
        assert "key_facts" not in metadata
        assert "fact_source_evidence" not in metadata
        assert metadata["canonical_summary"] == "新事实"
        assert "旧事实" not in repr(GraphExtractor().extract(2, "新事实", metadata))
        assert (
            _user_supported_candidate(
                {"content": "新事实", "metadata": metadata}, frozenset({"user"})
            )
            is None
        )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_replacement_rejects_misaligned_fact_metadata(tmp_db_path: str) -> None:
    """显式提供与新正文不一致的事实证据时拒绝替换且旧行不变。"""
    engine, db, store = await _engine_with_canonical_db(tmp_db_path)
    try:
        await store.insert_document(
            "old",
            "旧事实",
            {"key_facts": ["旧事实"], "fact_source_evidence": [[{"role": "user"}]]},
        )
        engine.hybrid_retriever = _ReplaceLifecycleStub(store)
        result = await engine.update_memory(
            1,
            {
                "content": "新事实",
                "metadata": {
                    "key_facts": ["旧事实"],
                    "fact_source_evidence": [[{"role": "user"}]],
                },
            },
        )
        assert result is False
        assert engine.get_last_write_reason_code() == "fact_evidence_mismatch"
        assert await _canonical_rows(tmp_db_path) == [(1, "旧事实")]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_replacement_accepts_aligned_fact_metadata(tmp_db_path: str) -> None:
    """显式提供对齐的新事实及证据时，消费者只能获得新 owner 的事实。"""
    engine, db, store = await _engine_with_canonical_db(tmp_db_path)
    try:
        await store.insert_document("old", "旧事实", {})
        engine.hybrid_retriever = _ReplaceLifecycleStub(store)
        evidence = fact_evidence(["新事实"])
        assert (
            await engine.update_memory(
                1,
                {
                    "content": "新事实",
                    "metadata": {
                        "key_facts": ["新事实"],
                        "fact_source_evidence": evidence,
                        "canonical_summary": "旧事实",
                    },
                },
            )
            is True
        )
        owner = await engine.find_replacement_memory_id(1)
        assert owner == 2
        current = await engine.get_memory(owner)
        assert current["text"] == "新事实"
        assert current["metadata"]["key_facts"] == ["新事实"]
        assert current["metadata"]["fact_source_evidence"] == evidence
        assert current["metadata"]["canonical_summary"] == "新事实"
        supported = _user_supported_candidate(
            {"content": current["text"], "metadata": current["metadata"]},
            frozenset({"user"}),
        )
        assert supported["content"] == "新事实"
        assert supported["metadata"]["key_facts"] == ["新事实"]
        assert await _active_canonical_ids(db) == [2]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_repair_retries_cleanup_and_preserves_undeleted_derivatives(
    tmp_db_path: str,
) -> None:
    """canonical 删除尚未提交不得清派生；已提交后的清理故障必须下轮重放。"""
    engine, db, store = await _engine_with_canonical_db(tmp_db_path)
    try:
        await store.insert_document("old", "old fact", {})
        lifecycle = _ReplaceLifecycleStub(store)
        lifecycle.fail_delete_ids = {1, 2}
        engine.hybrid_retriever = lifecycle
        assert await engine.update_memory(1, {"content": "new fact"}) is False
        await db.execute("CREATE TABLE test_derived(memory_id INTEGER PRIMARY KEY)")
        await db.execute("INSERT INTO test_derived VALUES(1)")
        await db.commit()
        journal = engine._write_journal
        journal._max_retries = 4
        delete_indexes = journal._delete_doc_indexes_batch
        journal._delete_doc_indexes_batch = AsyncMock(return_value=0)
        cleanup_attempts = 0

        async def delete_derived(ids):
            nonlocal cleanup_attempts
            cleanup_attempts += 1
            if cleanup_attempts == 1:
                raise RuntimeError("derived write unavailable")
            await db.execute("DELETE FROM test_derived WHERE memory_id=?", (ids[0],))
            await db.commit()

        journal._delete_graph_atoms_batch = delete_derived
        await journal.repair_incomplete()
        assert cleanup_attempts == 0
        assert (
            await (await db.execute("SELECT COUNT(*) FROM test_derived")).fetchone()
        )[0] == 1
        journal._delete_doc_indexes_batch = delete_indexes
        await journal.repair_incomplete()
        assert cleanup_attempts == 1
        assert (await _latest_operation(db, "replace_content"))[
            "status"
        ] == "needs_repair"
        await journal.repair_incomplete()
        assert cleanup_attempts == 2
        assert (
            await (await db.execute("SELECT COUNT(*) FROM test_derived")).fetchone()
        )[0] == 0
        assert await _active_canonical_ids(db) == [2]
        assert (await _latest_operation(db, "replace_content"))["status"] == "completed"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_cancel_after_publication_keeps_one_recallable_owner(
    tmp_db_path: str,
) -> None:
    """取消发生在新 owner 发布后，也必须能由 replacement 账本安全收敛。"""
    engine, db, store = await _engine_with_canonical_db(tmp_db_path)
    try:
        await store.insert_document("old", "old fact", {})
        lifecycle = _ReplaceLifecycleStub(store)
        lifecycle.delete_memory = AsyncMock(side_effect=asyncio.CancelledError())
        engine.hybrid_retriever = lifecycle
        with pytest.raises(asyncio.CancelledError):
            await engine.update_memory(1, {"content": "new fact"})
        assert await _canonical_rows(tmp_db_path) == [(1, "old fact"), (2, "new fact")]
        assert await _active_canonical_ids(db) == [2]
        assert await engine.find_replacement_memory_id(1) is None
        # replacement 与取消留下的 delete 账本均已实际收口。
        assert await engine._write_journal.repair_incomplete() == 2
        assert await engine.find_replacement_memory_id(1) == 2
        assert await _active_canonical_ids(db) == [2]
        await db.execute("DELETE FROM documents WHERE id=2")
        await db.commit()
        assert await engine.find_replacement_memory_id(1) is None
        assert await _canonical_rows(tmp_db_path) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_pending_replacement_survives_cleanup_and_api_projection(
    tmp_db_path: str,
) -> None:
    """隐藏的旧 owner 不走归档清理，也不向管理员 allowlist 泄漏内部标记。"""
    from core.platform.transport.page_api.memory_read_api import _project_list_metadata

    engine, db, store = await _engine_with_canonical_db(tmp_db_path)
    try:
        metadata = {
            "memory_status": "deleted",
            "status": "deleted",
            "replacement_pending": True,
            "create_time": 1,
            "status_changed_at": 1,
            "importance": 0.0,
        }
        await store.insert_document("old", "old fact", metadata)
        store.count_documents = AsyncMock(return_value=1)
        engine._maintenance._db = db
        engine._maintenance._batch_delete_memories = AsyncMock(
            side_effect=AssertionError("pending owner deleted")
        )
        assert await engine._maintenance.cleanup_old_memories(days_threshold=1) == 0
        assert await _canonical_rows(tmp_db_path) == [(1, "old fact")]
        assert "replacement_pending" not in _project_list_metadata(metadata)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_cas_graph_failure_is_repairable_without_rolling_back_content(
    tmp_db_path: str, caplog
) -> None:
    """CAS 正文落地后图失败返回成功与稳定修复原因，异常 canary 不落日志。"""
    engine, db, store = await _engine_with_canonical_db(tmp_db_path)
    try:
        await store.insert_document("old", "old fact", {})

        async def cas(memory_id, content, metadata, expected_revision):
            cursor = await db.execute(
                "UPDATE documents SET text=?, metadata=?, updated_at='r2' WHERE id=? AND updated_at=?",
                (content, json.dumps(metadata), memory_id, expected_revision),
            )
            await db.commit()
            return cursor.rowcount == 1

        engine.hybrid_retriever = SimpleNamespace(update_content_if_revision=cas)
        graph = SimpleNamespace(
            index_memory=AsyncMock(side_effect=RuntimeError("PRIVATE_GRAPH_CANARY"))
        )
        engine.graph_memory_manager = graph
        engine._write_journal._graph_memory_manager = graph
        assert (
            await engine.update_memory(
                1, {"content": "new fact"}, expected_revision="r1"
            )
            is True
        )
        assert await _canonical_rows(tmp_db_path) == [(1, "new fact")]
        row = await _latest_operation(db, "graph_reindex")
        assert (row["status"], row["error"]) == ("needs_repair", "graph_reindex_failed")
        assert "PRIVATE_GRAPH_CANARY" not in caplog.text
        graph.index_memory = AsyncMock()
        assert await engine._write_journal.repair_incomplete() == 1
        assert (await _latest_operation(db, "graph_reindex"))["status"] == "completed"
        assert await _canonical_rows(tmp_db_path) == [(1, "new fact")]
    finally:
        await db.close()


class _FactOnlyClassifier:
    """按 ``key_facts`` + ``fact_source_evidence`` 生成 Atom，复刻分类端口形状。"""

    def classify_atoms_from_metadata(
        self,
        metadata: dict,
        parent_importance: float = 0.5,
        **_ignored: object,
    ) -> list[MemoryAtom]:
        """把父元数据的每条事实与其证据组直接映射为未绑定 Atom。"""

        facts = metadata.get("key_facts")
        facts = facts if isinstance(facts, list) else []
        evidence = metadata.get("fact_source_evidence")
        evidence = evidence if isinstance(evidence, list) else []
        atoms: list[MemoryAtom] = []
        for index, fact in enumerate(facts):
            if not isinstance(fact, str) or not fact:
                continue
            group = evidence[index] if index < len(evidence) else []
            atoms.append(
                MemoryAtom(
                    parent_memory_id=0,
                    content=fact,
                    importance=parent_importance,
                    source_evidence=group,
                )
            )
        return atoms


def _cas_content_updater(db) -> SimpleNamespace:
    """返回按 ``updated_at`` 做 CAS 的正文更新端口，写入真实 documents 行。"""

    async def update(
        memory_id: int,
        content: str,
        metadata: dict,
        expected_revision: str,
        **_ignored: object,
    ) -> bool:
        cursor = await db.execute(
            "UPDATE documents SET text=?, metadata=?, updated_at='r2' "
            "WHERE id=? AND updated_at=?",
            (content, json.dumps(metadata), memory_id, expected_revision),
        )
        await db.commit()
        return cursor.rowcount == 1

    return SimpleNamespace(update_content_if_revision=update)


async def _attach_atom_derivation(
    engine, db_path: str
) -> tuple[AtomStore, AtomLifecycleManager]:
    """给引擎挂上真实 AtomStore 与真实重派生管理器（分类端口用事实替身）。"""

    atom_store = AtomStore(db_path)
    await atom_store.initialize()
    manager = AtomLifecycleManager(atom_store, {}, classifier=_FactOnlyClassifier())
    engine.atom_store = atom_store
    engine.atom_lifecycle_manager = manager
    return atom_store, manager


@pytest.mark.asyncio
async def test_cas_content_update_replaces_derived_atoms(tmp_db_path: str) -> None:
    """CAS 正文提交后按当前来源重派生 Atom：旧事实行不得残留为派生信号。"""

    engine, db, store = await _engine_with_canonical_db(tmp_db_path)
    try:
        old_facts = ["旧事实内容"]
        memory_id = await store.insert_document(
            "doc-old",
            "旧事实内容",
            {
                "session_id": "s1",
                "privacy_level": "shared",
                "key_facts": old_facts,
                "fact_source_evidence": fact_evidence(old_facts),
            },
        )
        atom_store, manager = await _attach_atom_derivation(engine, tmp_db_path)
        seeded = await manager.rederive_for_sources([memory_id], "test_seed")
        assert (seeded["rederived"], seeded["failed"]) == (1, 0)
        assert [
            atom.content for atom in await atom_store.get_by_parent_raw(memory_id)
        ] == old_facts

        new_facts = ["新事实内容"]
        engine.hybrid_retriever = _cas_content_updater(db)
        assert (
            await engine.update_memory(
                memory_id,
                {
                    "content": "新事实内容",
                    "metadata": {
                        "key_facts": new_facts,
                        "fact_source_evidence": fact_evidence(new_facts),
                    },
                },
                expected_revision="r1",
            )
            is True
        )

        assert await _canonical_rows(tmp_db_path) == [(memory_id, "新事实内容")]
        atoms = await atom_store.get_by_parent_raw(memory_id)
        assert [atom.content for atom in atoms] == new_facts
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_cas_content_update_degrades_when_atom_rederive_fails(
    tmp_db_path: str, caplog
) -> None:
    """Atom 重派生失败只降级原因码：canonical 已提交且不回滚，异常原文不落日志。"""

    engine, db, store = await _engine_with_canonical_db(tmp_db_path)
    try:
        await store.insert_document("doc-old", "old fact", {"session_id": "s1"})
        engine.atom_lifecycle_manager = SimpleNamespace(
            rederive_for_sources=AsyncMock(
                side_effect=RuntimeError("PRIVATE_ATOM_CANARY")
            )
        )
        engine.hybrid_retriever = _cas_content_updater(db)

        assert (
            await engine.update_memory(
                1, {"content": "new fact"}, expected_revision="r1"
            )
            is True
        )

        assert await _canonical_rows(tmp_db_path) == [(1, "new fact")]
        assert engine._last_write_reason_code == "atom_rederive_failed"
        assert "PRIVATE_ATOM_CANARY" not in caplog.text
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_cas_content_update_propagates_atom_rederive_cancellation(
    tmp_db_path: str,
) -> None:
    """Atom 重派生取消继续传播，且不撤销已提交的 canonical 正文。"""

    engine, db, store = await _engine_with_canonical_db(tmp_db_path)
    try:
        await store.insert_document("doc-old", "old fact", {"session_id": "s1"})
        engine.atom_lifecycle_manager = SimpleNamespace(
            rederive_for_sources=AsyncMock(side_effect=asyncio.CancelledError())
        )
        engine.hybrid_retriever = _cas_content_updater(db)

        with pytest.raises(asyncio.CancelledError):
            await engine.update_memory(
                1, {"content": "new fact"}, expected_revision="r1"
            )

        assert await _canonical_rows(tmp_db_path) == [(1, "new fact")]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_semantic_metadata_update_replaces_derived_atoms(
    tmp_db_path: str,
) -> None:
    """语义 metadata 更新提交后同样按当前来源重派生 Atom。"""

    engine, db, store = await _engine_with_canonical_db(tmp_db_path)
    try:
        seeded_facts = ["用户喜欢苹果"]
        memory_id = await store.insert_document(
            "doc-old",
            # 正文必须逐字包含新旧两组事实：重派生只在事实属于当前正文时保留。
            "用户喜欢苹果，用户喜欢香蕉",
            {
                "session_id": "s1",
                "privacy_level": "shared",
                "key_facts": seeded_facts,
                "fact_source_evidence": fact_evidence(seeded_facts),
            },
        )
        atom_store, manager = await _attach_atom_derivation(engine, tmp_db_path)
        await manager.rederive_for_sources([memory_id], "test_seed")

        async def update_metadata(
            memory_id: int,
            updates: dict,
            **_ignored: object,
        ) -> bool:
            """把 metadata 增量合并进真实 documents 行并推进 revision。"""

            row = await (
                await db.execute(
                    "SELECT metadata FROM documents WHERE id = ?", (memory_id,)
                )
            ).fetchone()
            merged = json.loads(row[0] or "{}")
            merged.update(updates)
            await db.execute(
                "UPDATE documents SET metadata=?, updated_at='r2' WHERE id=?",
                (json.dumps(merged), memory_id),
            )
            await db.commit()
            return True

        engine.hybrid_retriever = SimpleNamespace(update_metadata=update_metadata)
        grown_facts = ["用户喜欢苹果", "用户喜欢香蕉"]

        assert (
            await engine.update_memory(
                memory_id,
                {
                    "metadata": {
                        "key_facts": grown_facts,
                        "fact_source_evidence": fact_evidence(grown_facts),
                    }
                },
            )
            is True
        )

        atoms = await atom_store.get_by_parent_raw(memory_id)
        assert [atom.content for atom in atoms] == grown_facts
    finally:
        await db.close()


class _LedgerCrash(BaseException):
    """进程退出替身：绕开所有 ``except Exception``，模拟账本推进前断电。"""


def _crash_before_step(journal, step: str):
    """把指定 step 的账本推进替换成进程退出替身，返回可恢复的原方法。"""

    original = journal.advance_op

    async def crash(op_id, target_step, **kwargs):
        if target_step == step:
            raise _LedgerCrash(target_step)
        return await original(op_id, target_step, **kwargs)

    journal.advance_op = crash
    return original


async def _canonical_doc_ids(db_path: str) -> list[tuple[int, str]]:
    """返回 canonical 行的整数 ID 与宿主 ``doc_id``，用于核对账本预写意图。"""

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT id, doc_id FROM documents ORDER BY id")
        return [(int(row[0]), str(row[1])) for row in await cursor.fetchall()]


async def _add_op_row(db: aiosqlite.Connection) -> aiosqlite.Row:
    """读取最近一条 add 账本行（含整数 ``memory_id``）以核对崩溃窗口记账状态。"""

    cursor = await db.execute(
        "SELECT id, memory_id, status, step, error, payload FROM memory_write_ops "
        "WHERE op_type = 'add' ORDER BY id DESC LIMIT 1"
    )
    return await cursor.fetchone()


@pytest.mark.asyncio
async def test_add_crash_before_documents_committed_repairs_indexes(
    tmp_db_path: str,
) -> None:
    """canonical 已提交但账本停在预写意图：repair 必须把该行收敛为已建索引。

    这是分段写入拆出索引阶段后的损失面：崩溃后 canonical 行既不在 FAISS 也不在
    FTS，只能靠账本里预写的 ``doc_id`` 找回同一行，且不得产生第二行 canonical。
    """

    engine, db, host, bm25 = await _staged_engine(tmp_db_path)
    try:
        original_advance = _crash_before_step(
            engine._write_journal,
            "documents_committed",
        )
        with pytest.raises(_LedgerCrash):
            await engine.add_memory("崩溃窗口事实", session_id="s1")

        rows = await _canonical_rows(tmp_db_path)
        assert rows == [(1, "崩溃窗口事实")]
        memory_id = rows[0][0]
        row = await _add_op_row(db)
        assert (row["status"], row["step"]) == ("pending", "document_intent")
        assert row["memory_id"] is None
        # 预写意图必须指向真正提交的那一行，否则修复端无从定位。
        assert await _canonical_doc_ids(tmp_db_path) == [
            (memory_id, json.loads(row["payload"])["pending_doc_id"])
        ]
        # 崩溃发生在索引阶段之前：这正是本用例要修复的不可召回状态。
        assert host.embedding_storage.index.ntotal == 0
        assert await _fts_count(db, memory_id) == 0

        engine._write_journal.advance_op = original_advance
        assert await engine._write_journal.repair_incomplete() == 1

        row = await _add_op_row(db)
        assert (row["status"], row["step"]) == ("completed", "completed")
        assert row["memory_id"] == memory_id
        assert await _canonical_rows(tmp_db_path) == [(memory_id, "崩溃窗口事实")]
        assert host.embedding_storage.index.ntotal == 1
        assert [
            int(value)
            for value in faiss.vector_to_array(host.embedding_storage.index.id_map)
        ] == [memory_id]
        assert await _fts_count(db, memory_id) == 1
        assert [
            result.doc_id for result in await bm25.search("崩溃窗口事实", limit=5)
        ] == [memory_id]
        # 重复修复不得再选中该操作，也不得产生第二行 canonical。
        assert await engine._write_journal.repair_incomplete() == 0
        assert await _canonical_rows(tmp_db_path) == [(memory_id, "崩溃窗口事实")]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_add_crash_before_canonical_insert_marks_document_absent(
    tmp_db_path: str,
) -> None:
    """预写意图已落库但 INSERT 未提交：repair 记终态，不补索引也不伪造行。"""

    engine, db, host, _bm25 = await _staged_engine(tmp_db_path)
    try:
        store = host.document_storage
        original_insert = store.insert_document

        async def abort_insert(doc_id: str, text: str, metadata: dict) -> int:
            """模拟 INSERT 未提交时的进程退出：取消继续传播，账本无整数 ID。"""

            raise asyncio.CancelledError()

        store.insert_document = abort_insert
        with pytest.raises(asyncio.CancelledError):
            await engine.add_memory("未提交事实", session_id="s1")
        store.insert_document = original_insert

        row = await _add_op_row(db)
        assert (row["status"], row["step"]) == ("pending", "document_intent")
        assert json.loads(row["payload"])["pending_doc_id"]
        assert await _canonical_rows(tmp_db_path) == []

        assert await engine._write_journal.repair_incomplete() == 0
        row = await _add_op_row(db)
        assert (row["status"], row["step"], row["error"]) == (
            "failed",
            "document_absent",
            "pending_document_missing",
        )
        # 终态不空转：既不再重放，也不补索引、不凭空造出 canonical 行。
        assert await engine._write_journal.repair_incomplete() == 0
        assert (await _add_op_row(db))["step"] == "document_absent"
        assert await _canonical_rows(tmp_db_path) == []
        assert host.embedding_storage.index.ntotal == 0
        assert await _fts_count(db, 1) == 0
    finally:
        await db.close()
