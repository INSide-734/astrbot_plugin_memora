"""Topic catalog schema、dirty、generation 和指标行为测试。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from core.features.memory.infrastructure.topic_catalog_schema import (
    TOPIC_CATALOG_SCHEMA_VERSION,
    create_topic_catalog_schema,
    topic_catalog_schema_is_valid,
)
from core.features.memory.infrastructure.topic_catalog_store import TopicCatalogStore
from core.features.memory.infrastructure.write_op_journal import WriteOpJournal

_WINDOW_HASH = "a" * 64
_SCOPE_HASH = "b" * 64


async def _database(path: Path) -> aiosqlite.Connection:
    """创建带 documents 表的独立 catalog 测试数据库。"""

    db = await aiosqlite.connect(path)
    await db.execute(
        """
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY,
            text TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    await create_topic_catalog_schema(db)
    await db.commit()
    return db


@pytest.mark.asyncio
async def test_schema_is_versioned_and_trigger_writes_memory_id_only(
    tmp_path: Path,
) -> None:
    """目录 schema 可重复创建，canonical 变化只登记 memory ID dirty。"""

    db = await _database(tmp_path / "catalog.db")
    try:
        assert await topic_catalog_schema_is_valid(db)
        meta = await (
            await db.execute(
                "SELECT schema_version FROM topic_catalog_schema_meta WHERE id = 1"
            )
        ).fetchone()
        assert meta == (TOPIC_CATALOG_SCHEMA_VERSION,)
        metadata = json.dumps({"topics": ["话题一"], "scope_key": "scope-a"})
        await db.execute(
            "INSERT INTO documents(id,text,metadata,created_at,updated_at) VALUES(?,?,?,?,?)",
            (1, "正文不应进入 dirty", metadata, "2026-01-01T00:00:00+00:00", "r1"),
        )
        row = await (
            await db.execute(
                "SELECT memory_id,operation,sequence,state FROM topic_catalog_dirty"
            )
        ).fetchone()
        dirty_columns = {
            str(item[1])
            for item in await (
                await db.execute("PRAGMA table_info(topic_catalog_dirty)")
            ).fetchall()
        }
        state = await (
            await db.execute(
                "SELECT canonical_write_watermark,next_dirty_sequence FROM topic_catalog_state"
            )
        ).fetchone()
        assert row == (1, "add", 1, "pending")
        assert "payload" not in dirty_columns
        assert state == (1, 1)
        await db.execute(
            "UPDATE documents SET metadata=? WHERE id=1",
            (json.dumps({"status": "dormant"}),),
        )
        await db.commit()
        updated = await (
            await db.execute(
                "SELECT operation,sequence,state FROM topic_catalog_dirty WHERE memory_id=1"
            )
        ).fetchone()
        assert updated == ("status_update", 2, "pending")
        await db.execute("DELETE FROM documents WHERE id=1")
        await db.commit()
        deleted = await (
            await db.execute(
                "SELECT operation,sequence,state FROM topic_catalog_dirty WHERE memory_id=1"
            )
        ).fetchone()
        watermark = await (
            await db.execute(
                "SELECT canonical_write_watermark,next_dirty_sequence "
                "FROM topic_catalog_state WHERE id=1"
            )
        ).fetchone()
        assert deleted == ("delete", 3, "pending")
        assert watermark == (3, 3)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_schema_upgrade_discards_unfenced_staging(tmp_path: Path) -> None:
    """旧 catalog schema 的 staging 没有新 watermark fence，升级时必须重建。"""

    db = await _database(tmp_path / "catalog.db")
    try:
        await db.execute(
            "UPDATE topic_catalog_schema_meta SET schema_version=1 WHERE id=1"
        )
        await db.execute(
            """
            UPDATE topic_catalog_state
            SET staging_generation=2,status='backfilling',rebuild_owner_token='old',
                rebuild_lease_until=100,backfill_cursor=1,backfill_total=2
            WHERE id=1
            """
        )
        await db.execute(
            """
            INSERT INTO memory_topic_sources(
                generation,memory_id,source_revision,scope_key,chat_type,
                privacy_level,topic_key,display_topic,observed_at
            ) VALUES(2,1,'r1','scope-a','private','shared','topic','Topic',1)
            """
        )
        await db.commit()

        await db.execute("BEGIN IMMEDIATE")
        await create_topic_catalog_schema(db)
        await db.commit()

        state = await (
            await db.execute(
                "SELECT staging_generation,rebuild_owner_token,status "
                "FROM topic_catalog_state WHERE id=1"
            )
        ).fetchone()
        count = await (
            await db.execute(
                "SELECT COUNT(*) FROM memory_topic_sources WHERE generation=2"
            )
        ).fetchone()
        assert state == (None, None, "empty")
        assert count == (0,)
        assert await topic_catalog_schema_is_valid(db)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_store_filters_ineligible_sources_and_revalidates_revision(
    tmp_path: Path,
) -> None:
    """目录只接收完整 provenance 的 active source，并拒绝 stale mapping。"""

    db = await _database(tmp_path / "catalog.db")
    store = TopicCatalogStore(db)
    try:
        metadata = {
            "topics": ["  旅行  ", "旅行"],
            "scope_key": "scope-a",
            "chat_type": "private",
            "privacy_level": "shared",
            "resolver_revision": "resolver-1",
            "source_provenance_complete": True,
        }
        await db.execute(
            "INSERT INTO documents VALUES(?,?,?,?,?)",
            (1, "正文", json.dumps(metadata), "2026-01-01T00:00:00+00:00", "r1"),
        )
        await db.execute(
            "UPDATE topic_catalog_state SET active_generation=1,status='ready' WHERE id=1"
        )
        await db.commit()
        assert await store.replace_memory_mappings(1, 1)
        rows = await store.list_scope_topics(
            1,
            "scope-a",
            "shared",
            resolver_revision="resolver-1",
            chat_type="private",
        )
        assert (
            await store.list_scope_topics(
                1,
                "scope-a",
                "shared",
                resolver_revision="resolver-other",
                chat_type="private",
            )
            == ()
        )
        assert [row["display_topic"] for row in rows] == ["旅行"]
        initial_last_seen = rows[0]["last_seen_at"]
        await db.execute("UPDATE documents SET updated_at=? WHERE id=1", ("r2",))
        await db.commit()
        assert (
            await store.list_scope_topics(
                1,
                "scope-a",
                "shared",
                resolver_revision="resolver-1",
                chat_type="private",
            )
            == ()
        )
        assert await store.replace_memory_mappings(1, 1)
        refreshed = await store.list_scope_topics(
            1,
            "scope-a",
            "shared",
            resolver_revision="resolver-1",
            chat_type="private",
        )
        assert refreshed[0]["last_seen_at"] == initial_last_seen
        await db.execute(
            "UPDATE documents SET metadata=? WHERE id=1",
            (json.dumps({**metadata, "gate_disposition": "mark_write"}),),
        )
        await db.commit()
        assert await store.replace_memory_mappings(1, 1)
        assert (
            await store.list_scope_topics(
                1,
                "scope-a",
                "shared",
                resolver_revision="resolver-1",
                chat_type="private",
            )
            == ()
        )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_dirty_finish_requires_owner_sequence_and_live_lease(
    tmp_path: Path,
) -> None:
    """过期 owner 或旧 sequence 不能收敛新 dirty。"""

    db = await _database(tmp_path / "catalog.db")
    store = TopicCatalogStore(db)
    try:
        sequence = await store.register_dirty(1, "add")
        claimed = await store.claim_dirty("owner", now=10.0, lease_seconds=5.0)
        assert claimed and claimed[0].sequence == sequence
        with pytest.raises(ValueError, match="catalog_reason_invalid"):
            await store.register_dirty(2, "add", reason_code="scope-a")
        assert not await store.finish_dirty(
            claimed[0].dirty_id,
            owner_token="other",
            sequence=sequence,
            success=True,
            now=11.0,
        )
        assert not await store.finish_dirty(
            claimed[0].dirty_id,
            owner_token="owner",
            sequence=sequence - 1,
            success=True,
            now=11.0,
        )
        assert not await store.finish_dirty(
            claimed[0].dirty_id,
            owner_token="owner",
            sequence=sequence,
            success=True,
            now=16.0,
        )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_dirty_repair_rereads_current_canonical_source(tmp_path: Path) -> None:
    """dirty repair 只按 memory ID 重读当前 source，不重放旧 metadata。"""

    db = await _database(tmp_path / "catalog.db")
    store = TopicCatalogStore(db)
    try:
        await db.execute(
            "UPDATE topic_catalog_state SET active_generation=1,status='ready' WHERE id=1"
        )
        metadata = {
            "topics": ["当前话题"],
            "scope_key": "scope-a",
            "chat_type": "private",
            "privacy_level": "shared",
            "resolver_revision": "resolver-1",
            "source_provenance_complete": True,
        }
        await db.execute(
            "INSERT INTO documents VALUES(?,?,?,?,?)",
            (1, "正文", json.dumps(metadata), "2026-01-01T00:00:00+00:00", "r1"),
        )
        await db.commit()
        assert await store.repair_pending("repair-owner") == 1
        dirty = await store.list_dirty(states=("completed",))
        topics = await store.list_scope_topics(
            1,
            "scope-a",
            "shared",
            resolver_revision="resolver-1",
            chat_type="private",
        )
        assert [(item.memory_id, item.state)
                for item in dirty] == [(1, "completed")]
        assert [item["display_topic"] for item in topics] == ["当前话题"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_write_journal_repairs_catalog_dirty_without_snapshot(
    tmp_path: Path,
) -> None:
    """写日志恢复入口委托 catalog dirty，并且只依赖当前 canonical 行。"""

    db = await _database(tmp_path / "catalog.db")
    store = TopicCatalogStore(db)
    journal = WriteOpJournal(
        db_connection=db,
        graph_memory_manager=None,
        atom_store=None,
        topic_catalog_store=store,
    )
    try:
        await journal.create_table()
        await db.execute(
            "UPDATE topic_catalog_state SET active_generation=1,status='ready' WHERE id=1"
        )
        metadata = {
            "topics": ["当前话题"],
            "scope_key": "scope-a",
            "chat_type": "private",
            "privacy_level": "shared",
            "resolver_revision": "resolver-1",
            "source_provenance_complete": True,
        }
        await db.execute(
            "INSERT INTO documents VALUES(?,?,?,?,?)",
            (1, "正文", json.dumps(metadata), "2026-01-01T00:00:00+00:00", "r1"),
        )
        await db.commit()
        assert await journal.repair_incomplete() == 1
        row = await (
            await db.execute("SELECT state FROM topic_catalog_dirty WHERE memory_id=1")
        ).fetchone()
        assert row == ("completed",)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_metric_window_terminal_state_is_idempotent(tmp_path: Path) -> None:
    """同一窗口重复终态只更新一次聚合指标。"""

    db = await _database(tmp_path / "catalog.db")
    store = TopicCatalogStore(db)
    try:
        values = {"candidate_count_sum": 2, "prompt_tokens": 10}
        assert await store.record_metric_window(
            window_key_hash=_WINDOW_HASH,
            hash_key_version=1,
            terminal_state="success",
            token_source_available=True,
            scope_key_hash=_SCOPE_HASH,
            bucket_date="2026-09-03",
            mode="observe",
            topic_count_bucket="0-4",
            values=values,
        )
        assert not await store.record_metric_window(
            window_key_hash=_WINDOW_HASH,
            hash_key_version=1,
            terminal_state="success",
            token_source_available=True,
            scope_key_hash=_SCOPE_HASH,
            bucket_date="2026-09-03",
            mode="observe",
            topic_count_bucket="0-4",
            values=values,
        )
        assert not await store.record_metric_window(
            window_key_hash="raw-window-id",
            hash_key_version=1,
            terminal_state="success",
            token_source_available=True,
            scope_key_hash=_SCOPE_HASH,
            bucket_date="2026-09-03",
            mode="observe",
            topic_count_bucket="0-4",
            values=values,
        )
        assert not await store.record_metric_window(
            window_key_hash="c" * 64,
            hash_key_version=1,
            terminal_state="success",
            token_source_available=False,
            scope_key_hash=_SCOPE_HASH,
            bucket_date="2026-09-03",
            mode="observe",
            topic_count_bucket="0-4",
            values={"candidate_count_sum": 0.5},
        )
        assert not await store.record_metric_window(
            window_key_hash="d" * 64,
            hash_key_version=1,
            terminal_state="success",
            token_source_available=False,
            scope_key_hash=_SCOPE_HASH,
            bucket_date="2026-09-03",
            mode="observe",
            topic_count_bucket="0-4",
            values={"selector_duration_ms": float("nan")},
        )
        row = await (
            await db.execute(
                "SELECT window_count,quality_sample_count,token_sample_count,"
                "candidate_count_sum,prompt_tokens FROM topic_candidate_scope_metrics"
            )
        ).fetchone()
        assert row == (1, 1, 1, 2, 10)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_generation_publish_requires_complete_staging(tmp_path: Path) -> None:
    """未完成 staging 或 dirty 未收敛时不得发布新 generation。"""

    db = await _database(tmp_path / "catalog.db")
    store = TopicCatalogStore(db)
    try:
        # 先写入 canonical 再开始回填：staging_start_watermark 固定，
        # 插入登记的 dirty 由触发器产生（sequence=1）
        await db.execute(
            "INSERT INTO documents(id,text,metadata,created_at,updated_at) "
            "VALUES(1,'正文','{}','t','r1')"
        )
        await db.commit()
        assert await store.begin_generation(
            1, "owner", 100.0, backfill_total=1, now=1.0
        )
        # keyset 语义：cursor（id 水位）未覆盖 MAX(id) 时不得发布
        state = await store.get_state()
        assert not await store.publish_generation(
            1,
            "owner",
            start_watermark=state["canonical_write_watermark"],
            published_dirty_watermark=state["canonical_write_watermark"],
            canonical_snapshot_revision="snapshot",
            expected_source_count=0,
            expected_mapping_count=0,
            now=2.0,
        )
        assert await store.advance_backfill_cursor(1, "owner", 1, now=2.5)
        # 插入登记的 dirty 需先经 staging 覆盖证明收敛（生产路径同序）
        assert await store.mark_dirty_reconciled(
            "owner", up_to_sequence=1, covered_memory_ids={1}, now=2.6
        )
        state = await store.get_state()
        watermark = int(state["canonical_write_watermark"])
        assert await store.publish_generation(
            1,
            "owner",
            start_watermark=watermark,
            published_dirty_watermark=watermark,
            canonical_snapshot_revision="snapshot",
            expected_source_count=0,
            expected_mapping_count=0,
            now=3.0,
        )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_generation_publish_rejects_concurrent_canonical_write(
    tmp_path: Path,
) -> None:
    """回填开始后的 canonical 写入不能通过伪造新 watermark 越过发布。"""

    db = await _database(tmp_path / "catalog.db")
    store = TopicCatalogStore(db)
    try:
        assert await store.begin_generation(
            1, "owner", 100.0, backfill_total=0, now=1.0
        )
        await db.execute(
            "INSERT INTO documents VALUES(?,?,?,?,?)",
            (1, "正文", "{}", "2026-01-01T00:00:00+00:00", "r1"),
        )
        await db.commit()
        assert not await store.publish_generation(
            1,
            "owner",
            start_watermark=1,
            published_dirty_watermark=1,
            canonical_snapshot_revision="snapshot",
            expected_source_count=0,
            expected_mapping_count=0,
            now=2.0,
        )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_missing_catalog_state_rolls_back_canonical_write(tmp_path: Path) -> None:
    """state 单例损坏时 canonical 写入不得绕过 watermark 与 dirty 登记。"""

    db = await _database(tmp_path / "catalog.db")
    try:
        await db.execute("DELETE FROM topic_catalog_state WHERE id=1")
        await db.commit()
        with pytest.raises(sqlite3.IntegrityError, match="topic_catalog_state_missing"):
            await db.execute(
                "INSERT INTO documents VALUES(?,?,?,?,?)",
                (1, "正文", "{}", "2026-01-01T00:00:00+00:00", "r1"),
            )
        await db.rollback()
        count = await (await db.execute("SELECT COUNT(*) FROM documents")).fetchone()
        assert count == (0,)
    finally:
        await db.close()
