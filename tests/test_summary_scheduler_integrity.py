"""总结任务 schema 与来源完整性回归测试。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest

from core.features.conversation.infrastructure.conversation_store import (
    ConversationStore,
)
from core.features.quality.application.gate_runtime import (
    default_gate_snapshot,
    gate_snapshot_to_json,
)
from core.features.reflection.domain.summary_models import SummaryWindowContext
from core.shared.contracts.conversation import Message


def _context(session_id: str, epoch: int = 1, cursor: int = 0) -> SummaryWindowContext:
    """构造最小合法私聊总结上下文。"""
    snapshot = default_gate_snapshot()
    return SummaryWindowContext(
        session_id=session_id,
        session_epoch=epoch,
        start_seq=cursor,
        end_seq=cursor,
        chat_type="private",
        scope_id=session_id,
        gate_revision=snapshot.revision,
        gate_snapshot_json=gate_snapshot_to_json(snapshot),
        window_size=2,
    )


def _message(session_id: str, index: int, *, group_id: str | None = None) -> Message:
    """构造具有确定序号来源和可选群组作用域的测试消息。"""
    return Message.from_dict(
        {
            "id": 0,
            "session_id": session_id,
            "role": "user",
            "content": f"消息-{index}",
            "sender_id": "user-1",
            "sender_name": None,
            "group_id": group_id,
            "platform": "test",
            "timestamp": float(index),
            "metadata": {},
        }
    )


@pytest.mark.asyncio
async def test_invalid_legacy_pending_becomes_blocked_job(tmp_db_path: str) -> None:
    """无效旧 pending 必须迁移为可观察 blocked 任务。"""
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.set_summary_clock(lambda: 100.0)
    try:
        for index in range(3):
            await store.add_message(_message("legacy-invalid", index))
        assert store.connection is not None
        await store.connection.execute(
            "UPDATE sessions SET metadata=? WHERE session_id=?",
            (
                json.dumps(
                    {"pending_summary": {"start_index": 1, "end_index": 1}},
                    ensure_ascii=False,
                ),
                "legacy-invalid",
            ),
        )
        await store.connection.commit()
        assert await store.recover_legacy_pending() == 1
        cursor = await store.connection.execute(
            "SELECT status,reason_code FROM summary_jobs WHERE session_id=?",
            ("legacy-invalid",),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert tuple(row) == ("blocked", "legacy_pending_invalid")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_legacy_pending_recovers_authoritative_group_scope(
    tmp_db_path: str,
) -> None:
    """旧 pending 缺少作用域时必须从持久消息恢复，不能按私聊执行。"""
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.set_summary_clock(lambda: 100.0)
    try:
        for index in range(2):
            await store.add_message(_message("legacy-group", index, group_id="group-1"))
        snapshot = default_gate_snapshot()
        assert store.connection is not None
        await store.connection.execute(
            "UPDATE sessions SET metadata=? WHERE session_id=?",
            (
                json.dumps(
                    {
                        "pending_summary": {
                            "start_index": 0,
                            "end_index": 2,
                            "gate_revision": snapshot.revision,
                            "gate_snapshot_json": gate_snapshot_to_json(snapshot),
                        }
                    },
                    ensure_ascii=False,
                ),
                "legacy-group",
            ),
        )
        await store.connection.commit()

        assert await store.recover_legacy_pending() == 1
        claims = await store.claim_ready(100.0, "scheduler", 1)

        assert len(claims) == 1
        assert claims[0].chat_type == "group"
        assert claims[0].group_id == "group-1"
        assert claims[0].scope_id == "group-1"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_incomplete_schema_is_rejected_before_store_ready(
    tmp_db_path: str,
) -> None:
    """缺少调度必需列的已有任务表必须在初始化阶段拒绝。"""
    with sqlite3.connect(tmp_db_path) as connection:
        connection.execute("CREATE TABLE summary_jobs (job_id TEXT PRIMARY KEY)")
    store = ConversationStore(tmp_db_path)
    with pytest.raises(RuntimeError, match="summary_schema_incomplete"):
        await store.initialize()
    assert store.connection is None


@pytest.mark.asyncio
async def test_incomplete_source_is_reported_as_blocked_enqueue(
    tmp_db_path: str,
) -> None:
    """规划发现缺行时应保留 blocked 保护并返回 source_incomplete。"""
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.set_summary_clock(lambda: 100.0)
    try:
        for index in range(3):
            await store.add_message(_message("source-gap", index))
        assert store.connection is not None
        await store.connection.execute(
            "DELETE FROM messages WHERE session_id=? AND message_seq=?",
            ("source-gap", 2),
        )
        await store.connection.commit()
        result = await store.plan_and_enqueue_windows(_context("source-gap"), 3)
        assert result.accepted is True
        assert result.queued == 0
        assert result.reason_code.value == "source_incomplete"
        cursor = await store.connection.execute(
            "SELECT status,reason_code FROM summary_jobs WHERE session_id=?",
            ("source-gap",),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert tuple(row) == ("blocked", "source_incomplete")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_manual_enqueue_rejects_existing_blocked_window(
    tmp_db_path: str,
) -> None:
    """当前 epoch 已有阻塞窗口时，手动入口必须返回固定拒绝原因。"""
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.set_summary_clock(lambda: 100.0)
    try:
        for index in range(2):
            await store.add_message(_message("manual-blocked", index))
        assert store.connection is not None
        await store.connection.execute(
            "DELETE FROM messages WHERE session_id=? AND message_seq=?",
            ("manual-blocked", 1),
        )
        await store.connection.commit()
        context = _context("manual-blocked")
        first = await store.plan_and_enqueue_windows(context, 2)
        assert first.reason_code.value == "source_incomplete"

        result = await store.plan_and_enqueue_windows(
            replace(context, triggered_by="manual"),
            2,
        )

        assert result.accepted is False
        assert result.reason_code.value == "blocked"
    finally:
        await store.close()
