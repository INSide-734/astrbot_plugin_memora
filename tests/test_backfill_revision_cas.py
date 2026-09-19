"""回填写入必须按读取时的 canonical revision 执行 CAS。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.backfill.application import BackfillScheduler


def _legacy_meta(key_facts=None):
    """创建最小旧版 metadata 字典。"""

    return {
        "schema_version": "v2",
        "key_facts": key_facts or ["fact_a", "fact_b", "fact_c"],
        "summary": "multi-topic memory",
        "topics": ["topic1", "topic2"],
        "importance": 0.7,
        "sentiment": "positive",
        "emotion_tags": ["curious"],
    }


def _make_scheduler(engine=None):
    """使用可控引擎构造待测回填调度器。"""

    return BackfillScheduler(memory_engine=engine or MagicMock(), config={})


class TestBackfillRevisionCas:
    """覆盖 revision 透传与 CAS 参数构造。"""

    @pytest.mark.asyncio
    async def test_backfill_one_uses_entry_revision_for_cas(self):
        """批次携带 revision 时，schema 标记写入必须按它做 CAS。"""

        engine = MagicMock()
        engine.hybrid_retriever = MagicMock()
        engine.hybrid_retriever.update_metadata = AsyncMock(return_value=True)

        s = _make_scheduler(engine=engine)
        s._cluster_strategy.segment = AsyncMock(return_value=[object()])

        meta = _legacy_meta(key_facts=["a", "b"])
        await s._backfill_one(1, meta, "rev-1")

        engine.hybrid_retriever.update_metadata.assert_awaited_once_with(
            1,
            {"schema_version": "v3"},
            advance_revision=False,
            expected_revision="rev-1",
        )

    @pytest.mark.asyncio
    async def test_run_forwards_batch_revision_to_backfill(self):
        """批次中的 revision 必须逐条传给回填写入，不能在调度层丢失。"""

        s = _make_scheduler()
        s._job_id = "bf_revision"
        batch = [(7, _legacy_meta(key_facts=["a", "b"]), "rev-7")]
        s._max_per_run = 10
        s._fetch_legacy_batch = AsyncMock(side_effect=[batch, []])
        s._backfill_one = AsyncMock()

        await s._run()

        assert s._backfill_one.await_args.args == (7, batch[0][1], "rev-7")
