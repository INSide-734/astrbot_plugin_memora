"""BackfillScheduler 测试：存量记忆话题重分割。"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from core.features.backfill.application import BackfillScheduler

# ---------------------------------------------------------------------------
# 测试夹具
# ---------------------------------------------------------------------------


class TestBackfillScheduler:
    """验证回填任务的生命周期、读取游标与替换语义。"""

    @staticmethod
    def _make_scheduler(engine=None, config=None, embed_fn=None):
        """使用可选依赖构造待测回填调度器。"""

        return BackfillScheduler(
            memory_engine=engine or MagicMock(),
            config=config or {},
            embed_fn=embed_fn,
        )

    @staticmethod
    def _legacy_meta(schema_version: str = "v2", key_facts=None):
        """创建最小旧版 metadata 字典。"""
        return {
            "schema_version": schema_version,
            "key_facts": key_facts or ["fact_a", "fact_b", "fact_c"],
            "summary": "multi-topic memory",
            "topics": ["topic1", "topic2"],
            "importance": 0.7,
            "sentiment": "positive",
            "emotion_tags": ["curious"],
        }

    @staticmethod
    def _make_doc_storage(docs=None, *, side_effect=None):
        """创建支持列表返回或异常注入的文档存储替身。"""

        ds = MagicMock()
        if side_effect is not None:
            ds.get_documents = AsyncMock(side_effect=side_effect)
            ds.get_all_documents = AsyncMock(side_effect=side_effect)
        else:
            ds.get_documents = AsyncMock(return_value=docs or [])
            ds.get_all_documents = AsyncMock(return_value=docs or [])
        return ds

    # ---- 初始化 ----

    def test_default_config_values(self):
        """未提供配置时应采用稳定默认值。"""
        s = self._make_scheduler()
        assert s._enabled is True
        assert s._batch_size == 50
        assert s._max_per_run == 500

    def test_config_overrides(self):
        """显式配置值应覆盖默认值。"""
        s = self._make_scheduler(
            config={
                "enabled": False,
                "batch_size": 10,
                "max_backfill_per_run": 100,
            }
        )
        assert s._enabled is False
        assert s._batch_size == 10
        assert s._max_per_run == 100

    def test_config_false_string_parsed_as_bool(self):
        """字符串 ``False`` 应由 ``_safe_bool`` 解析为假值。"""
        s = self._make_scheduler(config={"enabled": "False"})
        assert s._enabled is False

    def test_initial_progress_is_idle(self):
        """新调度器的进度应处于空闲初态。"""

        s = self._make_scheduler()
        assert s.progress["status"] == "idle"
        assert s.progress["processed"] == 0
        assert s.progress["errors"] == 0
        assert s.is_running is False

    # ---- 启动与生命周期 ----

    @pytest.mark.asyncio
    async def test_start_returns_job_id_and_sets_running(self):
        """启动应返回任务标识并切换到运行状态。"""

        s = self._make_scheduler()
        # 隔离后台执行，只验证启动边界。
        with patch.object(s, "_run", AsyncMock()):
            job_id = await s.start()
            assert job_id.startswith("bf_")
            assert s.is_running is True
            assert s.progress["status"] == "running"

    @pytest.mark.asyncio
    async def test_start_raises_when_already_running(self):
        """已有任务运行时应拒绝重复启动。"""

        s = self._make_scheduler()
        s._progress["status"] = "running"
        with pytest.raises(RuntimeError, match="already running"):
            await s.start()

    @pytest.mark.asyncio
    async def test_get_status_returns_copy(self):
        """状态查询应返回不会污染内部进度的副本。"""

        s = self._make_scheduler()
        status = await s.get_status()
        assert status["status"] == "idle"
        # 修改返回值不得改变调度器内部状态。
        status["modified"] = True
        assert "modified" not in s.progress

    @pytest.mark.asyncio
    async def test_stop_cancels_running_task_and_marks_cancelled(self):
        """停止应取消运行任务并记录取消终态。"""

        s = self._make_scheduler()

        async def _long_running():
            """提供可取消的长运行协程。"""

            await asyncio.sleep(10)

        task = asyncio.create_task(_long_running())
        s._task = task
        s._progress["status"] = "running"

        await s.stop()

        assert s._task is None
        assert s.progress["status"] == "cancelled"
        assert "cancelled_at" in s.progress

    # ---- _run：空数据库 ----

    @pytest.mark.asyncio
    async def test_run_no_legacy_memories_completes_immediately(self):
        """没有旧版记忆时应立即进入完成状态。"""
        s = self._make_scheduler()
        s._job_id = "bf_test"
        s._fetch_legacy_batch = AsyncMock(return_value=[])

        await s._run()

        assert s.progress["status"] == "completed"
        assert s.progress["processed"] == 0

    # ---- _run：成功处理 ----

    @pytest.mark.asyncio
    async def test_run_processes_batch_and_completes(self):
        """单批两条旧文档处理完成后应记录成功终态。"""
        s = self._make_scheduler()
        s._job_id = "bf_test"
        s._max_per_run = 500

        batch = [
            (10, self._legacy_meta(schema_version="v2", key_facts=["a", "b", "c"])),
            (20, self._legacy_meta(schema_version="v1", key_facts=["d", "e", "f"])),
        ]
        s._fetch_legacy_batch = AsyncMock(side_effect=[batch, []])
        s._backfill_one = AsyncMock()

        await s._run()

        assert s.progress["status"] == "completed"
        assert s.progress["processed"] == 2
        assert s.progress["errors"] == 0
        assert s._backfill_one.call_count == 2

    @pytest.mark.asyncio
    async def test_run_respects_max_per_run(self):
        """处理数达到单轮上限后应停止继续取批次。"""
        s = self._make_scheduler(config={"max_backfill_per_run": 3})
        s._job_id = "bf_test"
        s._max_per_run = 3

        batch1 = [(1, self._legacy_meta(key_facts=["a", "b"]))]
        batch2 = [(2, self._legacy_meta(key_facts=["c", "d"]))]
        batch3 = [(3, self._legacy_meta(key_facts=["e", "f"]))]
        s._fetch_legacy_batch = AsyncMock(side_effect=[batch1, batch2, batch3])
        s._backfill_one = AsyncMock()

        await s._run()

        assert s.progress["processed"] == 3
        assert s._backfill_one.call_count == 3

    # ---- _run：错误处理 ----

    @pytest.mark.asyncio
    async def test_run_tracks_backfill_one_errors(self):
        """单条回填失败应计数且不终止同批任务。"""
        s = self._make_scheduler()
        s._job_id = "bf_test"

        batch = [
            (1, self._legacy_meta(key_facts=["a", "b", "c"])),
            (2, self._legacy_meta(key_facts=["d", "e", "f"])),
        ]
        s._fetch_legacy_batch = AsyncMock(side_effect=[batch, []])
        s._backfill_one = AsyncMock(side_effect=[None, RuntimeError("boom")])

        await s._run()

        assert s.progress["processed"] == 1
        assert s.progress["errors"] == 1
        assert s.progress["status"] == "completed_with_errors"

    @pytest.mark.asyncio
    async def test_run_marks_failed_on_unhandled_exception(self):
        """未处理异常应把任务状态设置为 ``failed``。"""
        s = self._make_scheduler()
        s._job_id = "bf_test"
        s._fetch_legacy_batch = AsyncMock(side_effect=RuntimeError("db down"))

        await s._run()

        assert s.progress["status"] == "failed"
        assert "db down" in s.progress["error"]

    @pytest.mark.asyncio
    async def test_run_checkpoint_advances(self):
        """每条文档处理后都应推进内存 checkpoint。"""
        s = self._make_scheduler()
        s._job_id = "bf_test"

        batch = [
            (5, self._legacy_meta(key_facts=["a", "b", "c"])),
            (12, self._legacy_meta(key_facts=["d", "e", "f"])),
        ]
        s._fetch_legacy_batch = AsyncMock(side_effect=[batch, []])
        s._backfill_one = AsyncMock()

        await s._run()

        assert s._checkpoint == 12

    # ---- _fetch_legacy_batch ----

    @pytest.mark.asyncio
    async def test_fetch_legacy_batch_no_engine_returns_empty(self):
        """缺少 memory engine 时批次读取应返回空列表。"""
        s = BackfillScheduler(memory_engine=None)
        result = await s._fetch_legacy_batch()
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_legacy_batch_no_faiss_db_returns_empty(self):
        """缺少 FAISS 数据库时批次读取应返回空列表。"""
        engine = MagicMock()
        engine.faiss_db = None
        s = self._make_scheduler(engine=engine)
        result = await s._fetch_legacy_batch()
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_legacy_batch_filters_by_schema_version(self):
        """只返回 v3 之前且包含多条关键事实的文档。"""
        engine = MagicMock()
        ds = self._make_doc_storage(
            [
                {"id": 1, "metadata": self._legacy_meta("v2", ["a", "b", "c"])},
                {
                    "id": 2,
                    "metadata": self._legacy_meta("v3", ["d", "e", "f"]),
                },  # v3 文档应跳过
                {
                    "id": 3,
                    "metadata": self._legacy_meta("v1", ["g"]),
                },  # 单条事实应跳过
                {"id": 4, "metadata": self._legacy_meta("v2", ["h", "i", "j"])},
                {
                    "id": 5,
                    "metadata": self._legacy_meta("", ["k", "l"]),
                },  # 无版本文档应保留
            ]
        )
        engine.faiss_db = MagicMock()
        engine.faiss_db.document_storage = ds

        s = self._make_scheduler(engine=engine)
        s._batch_size = 10
        result = await s._fetch_legacy_batch()

        # 只有文档 1、4、5 符合回填条件。
        ids = [doc_id for doc_id, _meta in result]
        assert ids == [1, 4, 5]

    @pytest.mark.asyncio
    async def test_fetch_legacy_batch_respects_checkpoint(self):
        """不大于 checkpoint 的文档 ID 应被跳过。"""
        engine = MagicMock()
        ds = self._make_doc_storage(
            [
                {"id": 5, "metadata": self._legacy_meta("v2", ["a", "b", "c"])},
                {"id": 10, "metadata": self._legacy_meta("v2", ["d", "e", "f"])},
                {"id": 15, "metadata": self._legacy_meta("v2", ["g", "h", "i"])},
            ]
        )
        engine.faiss_db = MagicMock()
        engine.faiss_db.document_storage = ds

        s = self._make_scheduler(engine=engine)
        s._checkpoint = 7
        s._batch_size = 10

        result = await s._fetch_legacy_batch()

        ids = [doc_id for doc_id, _meta in result]
        assert ids == [10, 15]

    @pytest.mark.asyncio
    async def test_fetch_legacy_batch_uses_document_storage_after_id(self):
        """文档存储支持 ID 游标时应优先使用该分页能力。"""

        engine = MagicMock()
        ds = MagicMock()
        ds.get_documents_after_id = AsyncMock(
            return_value=[
                {"id": 12, "metadata": self._legacy_meta("v2", ["a", "b", "c"])},
            ]
        )
        engine.faiss_db = MagicMock()
        engine.faiss_db.document_storage = ds
        engine.db_connection = None

        s = self._make_scheduler(engine=engine)
        s._checkpoint = 10
        s._batch_size = 5

        result = await s._fetch_legacy_batch()

        assert [doc_id for doc_id, _meta in result] == [12]
        ds.get_documents_after_id.assert_called_once_with(last_id=10, limit=5)

    @pytest.mark.asyncio
    async def test_fetch_legacy_batch_uses_sqlite_id_page(self, tmp_db_path):
        """文档存储不支持游标时应使用真实 SQLite ID 分页。"""

        async with aiosqlite.connect(tmp_db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("""
                CREATE TABLE documents (
                    id INTEGER PRIMARY KEY,
                    metadata TEXT
                )
            """)
            for doc_id in (1, 2, 3, 4):
                await db.execute(
                    "INSERT INTO documents(id, metadata) VALUES (?, ?)",
                    (
                        doc_id,
                        json.dumps(
                            self._legacy_meta("v2", [f"fact-{doc_id}", "b"]),
                            ensure_ascii=False,
                        ),
                    ),
                )
            await db.commit()

            engine = MagicMock()
            engine.faiss_db = MagicMock()
            engine.faiss_db.document_storage = MagicMock()
            engine.db_connection = db

            s = self._make_scheduler(engine=engine)
            s._checkpoint = 2
            s._batch_size = 1

            result = await s._fetch_legacy_batch()

            assert [doc_id for doc_id, _meta in result] == [3]

    @pytest.mark.asyncio
    async def test_fetch_legacy_batch_respects_batch_size(self):
        """返回结果数量不得超过批次上限。"""
        engine = MagicMock()
        docs = [
            {"id": i, "metadata": self._legacy_meta("v2", ["a", "b", "c"])}
            for i in range(20)
        ]
        ds = self._make_doc_storage(docs)
        engine.faiss_db = MagicMock()
        engine.faiss_db.document_storage = ds

        s = self._make_scheduler(engine=engine)
        s._batch_size = 5

        result = await s._fetch_legacy_batch()
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_fetch_legacy_batch_handles_string_metadata(self):
        """字符串 metadata 应先解析为 JSON 再筛选。"""
        engine = MagicMock()
        ds = self._make_doc_storage(
            [
                {
                    "id": 1,
                    "metadata": json.dumps(self._legacy_meta("v2", ["a", "b", "c"])),
                },
                {
                    "id": 2,
                    "metadata": json.dumps(self._legacy_meta("v3", ["d", "e", "f"])),
                },
                {"id": 3, "metadata": json.dumps(self._legacy_meta("v1", ["g", "h"]))},
            ]
        )
        engine.faiss_db = MagicMock()
        engine.faiss_db.document_storage = ds

        s = self._make_scheduler(engine=engine)
        s._batch_size = 10
        result = await s._fetch_legacy_batch()

        ids = [doc_id for doc_id, _meta in result]
        assert ids == [1, 3]

    @pytest.mark.asyncio
    async def test_fetch_legacy_batch_handles_invalid_json_metadata(self):
        """非法 JSON metadata 应降级为空字典并被跳过。"""
        engine = MagicMock()
        ds = self._make_doc_storage(
            [
                {"id": 1, "metadata": "not valid json {{{"},
            ]
        )
        engine.faiss_db = MagicMock()
        engine.faiss_db.document_storage = ds

        s = self._make_scheduler(engine=engine)
        s._batch_size = 10
        result = await s._fetch_legacy_batch()
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_legacy_batch_exception_returns_empty(self):
        """文档读取异常时批次查询应安全降级为空列表。"""
        engine = MagicMock()
        ds = self._make_doc_storage(side_effect=RuntimeError("db locked"))
        engine.faiss_db = MagicMock()
        engine.faiss_db.document_storage = ds

        s = self._make_scheduler(engine=engine)
        result = await s._fetch_legacy_batch()
        assert result == []

    # ---- _backfill_one ----

    @pytest.mark.asyncio
    async def test_backfill_one_skips_single_fact(self):
        """关键事实不超过一条时应提前返回且不调用引擎。"""
        engine = MagicMock()
        # 故意不设置写入能力，确保提前返回分支不调用引擎。
        s = self._make_scheduler(engine=engine)
        meta = self._legacy_meta(key_facts=["only_one_fact"])
        # 该分支不应抛错，也不应调用引擎。
        await s._backfill_one(1, meta)
        # 新增、删除和 metadata 更新均不应发生。
        engine.add_memory.assert_not_called()
        engine.delete_memory.assert_not_called()

    @pytest.mark.asyncio
    async def test_backfill_one_single_segment_upgrades_version(self):
        """聚类只生成一个片段时应仅升级 schema 版本。"""
        engine = MagicMock()
        engine.hybrid_retriever = AsyncMock()

        s = self._make_scheduler(engine=engine)
        s._cluster_strategy = MagicMock()
        s._cluster_strategy.segment = AsyncMock(
            return_value=[
                MagicMock(
                    content="same",
                    importance=0.5,
                    metadata={},
                    key_facts=["a", "b"],
                    topics=[],
                    atoms=[],
                )
            ]
        )

        meta = self._legacy_meta(key_facts=["a", "b"])
        await s._backfill_one(1, meta)

        engine.hybrid_retriever.update_metadata.assert_called_once_with(
            1,
            {"schema_version": "v3"},
            advance_revision=False,
        )

    @pytest.mark.asyncio
    async def test_backfill_one_splits_into_multiple_segments(self):
        """聚类生成多个片段时应写入新记忆并删除旧记忆。"""
        from core.features.recall.processors.topic_splitter import MemorySegment

        engine = MagicMock()
        engine.add_memory = AsyncMock(side_effect=[101, 102])
        engine.delete_memory = AsyncMock()

        seg1 = MemorySegment(
            content="topic A",
            importance=0.6,
            metadata={},
            key_facts=["a1", "a2"],
            topics=[],
        )
        seg2 = MemorySegment(
            content="topic B", importance=0.8, metadata={}, key_facts=["b1"], topics=[]
        )

        s = self._make_scheduler(engine=engine)
        s._cluster_strategy = MagicMock()
        s._cluster_strategy.segment = AsyncMock(return_value=[seg1, seg2])

        meta = self._legacy_meta(key_facts=["a1", "a2", "b1"])
        await s._backfill_one(1, meta)

        assert engine.add_memory.call_count == 2
        engine.delete_memory.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_backfill_one_partial_write_preserves_old_memory(self):
        """新片段未全部写入成功时必须保留旧记忆。"""
        engine = MagicMock()
        engine.add_memory = AsyncMock(side_effect=[101, Exception("write failed")])
        engine.delete_memory = AsyncMock()

        seg1 = MagicMock(
            content="good",
            importance=0.5,
            metadata={},
            key_facts=[],
            topics=[],
            atoms=[],
        )
        seg2 = MagicMock(
            content="bad",
            importance=0.5,
            metadata={},
            key_facts=[],
            topics=[],
            atoms=[],
        )

        s = self._make_scheduler(engine=engine)
        s._cluster_strategy = MagicMock()
        s._cluster_strategy.segment = AsyncMock(return_value=[seg1, seg2])

        meta = self._legacy_meta(key_facts=["a", "b", "c"])
        await s._backfill_one(1, meta)

        # 仅写入一半片段，因此不得调用删除。
        engine.delete_memory.assert_not_called()

    @pytest.mark.asyncio
    async def test_backfill_one_marks_schema_v3_and_backfill_source(self):
        """新片段应记录 v3 schema 与旧文档来源。"""
        from core.features.recall.processors.topic_splitter import MemorySegment

        engine = MagicMock()
        engine.add_memory = AsyncMock(return_value=999)
        engine.delete_memory = AsyncMock()

        seg = MemorySegment(
            content="new",
            importance=0.5,
            metadata={"existing": "val"},
            key_facts=["x"],
            topics=[],
            atoms=[],
        )

        s = self._make_scheduler(engine=engine)
        s._cluster_strategy = MagicMock()
        # 返回两个片段以进入删除旧项并插入新项的路径。
        s._cluster_strategy.segment = AsyncMock(
            return_value=[
                seg,
                MagicMock(
                    content="other",
                    importance=0.3,
                    metadata={},
                    key_facts=["y"],
                    topics=[],
                    atoms=[],
                ),
            ]
        )

        meta = self._legacy_meta(key_facts=["x", "y"])
        await s._backfill_one(42, meta)

        # 首个片段应保留原 metadata 并补充回填证据。
        assert seg.metadata["schema_version"] == "v3"
        assert seg.metadata["backfill_source"] == 42
        assert seg.metadata["existing"] == "val"

    @pytest.mark.asyncio
    async def test_backfill_one_handles_delete_failure(self):
        """删除旧记忆失败时应标记证据并向任务上抛错误。"""
        from core.features.recall.processors.topic_splitter import MemorySegment

        engine = MagicMock()
        engine.add_memory = AsyncMock(return_value=1)
        engine.delete_memory = AsyncMock(side_effect=RuntimeError("delete error"))
        engine.hybrid_retriever = MagicMock()
        engine.hybrid_retriever.update_metadata = AsyncMock(return_value=True)

        seg = MemorySegment(
            content="x",
            importance=0.5,
            metadata={},
            key_facts=["a"],
            topics=[],
            atoms=[],
        )
        seg2 = MagicMock(
            content="y",
            importance=0.5,
            metadata={},
            key_facts=["b"],
            topics=[],
            atoms=[],
        )

        s = self._make_scheduler(engine=engine)
        s._cluster_strategy = MagicMock()
        s._cluster_strategy.segment = AsyncMock(return_value=[seg, seg2])

        meta = self._legacy_meta(key_facts=["a", "b"])
        with pytest.raises(RuntimeError, match="delete error"):
            await s._backfill_one(1, meta)

        engine.add_memory.assert_called()
        engine.delete_memory.assert_called_once()
        engine.hybrid_retriever.update_metadata.assert_awaited_once_with(
            1,
            {
                "schema_version": "v3",
                "backfill_delete_failed": True,
                "backfill_new_ids": [1, 1],
            },
            advance_revision=False,
        )

    @pytest.mark.asyncio
    async def test_backfill_one_passes_session_and_persona(self):
        """回填写入应透传顶层 session 与 persona。"""
        from core.features.recall.processors.topic_splitter import MemorySegment

        engine = MagicMock()
        engine.add_memory = AsyncMock(return_value=1)
        engine.delete_memory = AsyncMock()

        seg = MemorySegment(
            content="x",
            importance=0.5,
            metadata={},
            key_facts=["a"],
            topics=[],
            atoms=[],
        )
        seg2 = MagicMock(
            content="y",
            importance=0.5,
            metadata={},
            key_facts=["b"],
            topics=[],
            atoms=[],
        )

        s = self._make_scheduler(engine=engine)
        s._cluster_strategy = MagicMock()
        s._cluster_strategy.segment = AsyncMock(return_value=[seg, seg2])

        meta = self._legacy_meta(key_facts=["a", "b"])
        meta["session_id"] = "s123"
        meta["persona_id"] = "p456"
        await s._backfill_one(1, meta)

        call_kwargs = engine.add_memory.call_args
        assert call_kwargs[1]["session_id"] == "s123"
        assert call_kwargs[1]["persona_id"] == "p456"

    @pytest.mark.asyncio
    async def test_backfill_one_session_from_source_window(self):
        """顶层缺少 session 时应回退到 source window。"""
        from core.features.recall.processors.topic_splitter import MemorySegment

        engine = MagicMock()
        engine.add_memory = AsyncMock(return_value=1)
        engine.delete_memory = AsyncMock()

        seg = MemorySegment(
            content="x",
            importance=0.5,
            metadata={},
            key_facts=["a"],
            topics=[],
            atoms=[],
        )
        seg2 = MagicMock(
            content="y",
            importance=0.5,
            metadata={},
            key_facts=["b"],
            topics=[],
            atoms=[],
        )

        s = self._make_scheduler(engine=engine)
        s._cluster_strategy = MagicMock()
        s._cluster_strategy.segment = AsyncMock(return_value=[seg, seg2])

        meta = {
            "key_facts": ["a", "b"],
            "source_window": {"session_id": "sw-sess"},
            "persona_id": "p99",
        }
        await s._backfill_one(1, meta)

        call_kwargs = engine.add_memory.call_args
        assert call_kwargs[1]["session_id"] == "sw-sess"

    # ---- 边界情况 ----

    @pytest.mark.asyncio
    async def test_fetch_batch_null_doc_id_skipped(self):
        """文档 ID 为空时应跳过该候选。"""
        engine = MagicMock()
        ds = self._make_doc_storage(
            [
                {"id": None, "metadata": self._legacy_meta("v2", ["a", "b", "c"])},
                {"id": 5, "metadata": self._legacy_meta("v2", ["d", "e", "f"])},
            ]
        )
        engine.faiss_db = MagicMock()
        engine.faiss_db.document_storage = ds

        s = self._make_scheduler(engine=engine)
        s._batch_size = 10
        result = await s._fetch_legacy_batch()
        ids = [doc_id for doc_id, _meta in result]
        assert ids == [5]

    def test_progress_is_readonly_snapshot(self):
        """进度属性应返回不会修改内部状态的字典副本。"""
        s = self._make_scheduler()
        p = s.progress
        p["processed"] = 9999
        assert s._progress["processed"] == 0
