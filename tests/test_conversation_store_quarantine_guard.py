"""会话清理与待处置隔离来源的保护边界（R4-4 回归）。

覆盖生产装配（``conversation_store.quarantine_store = quarantine_store``）下：
待处置隔离候选存在时清空必须 fail-closed 且不改动消息、计数与 epoch；
候选被拒绝或批准终结后清理恢复，并保持 epoch 推进语义。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from core.features.conversation.infrastructure.conversation_store import (
    ConversationStore,
)
from core.features.quality.infrastructure.quarantine_store import (
    MemoryQuarantineStore,
)
from core.shared.contracts.conversation import Message

SESSION = "aiocqhttp:private:10007"


def _message(content: str) -> Message:
    return Message(
        id=0,
        session_id=SESSION,
        role="user",
        content=content,
        sender_id="10007",
        sender_name="Tester",
        timestamp=time.time(),
    )


async def _wired_store(
    tmp_db_path: str,
) -> tuple[ConversationStore, MemoryQuarantineStore]:
    """按生产装配把隔离 Store 挂到会话 Store 上。"""

    quarantine = MemoryQuarantineStore(Path(tmp_db_path).with_suffix(".quarantine.db"))
    await quarantine.initialize()
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.quarantine_store = quarantine
    await store.create_session(SESSION, "aiocqhttp")
    await store.add_message(_message("第一条"))
    await store.add_message(_message("第二条"))
    return store, quarantine


async def _stage(quarantine: MemoryQuarantineStore, key: str) -> dict:
    return await quarantine.stage_candidate(
        candidate_key=key,
        reason_codes=["summary_quality_low"],
        content="候选正文",
        metadata={},
        importance=0.7,
        session_id=SESSION,
        persona_id=None,
        source_window={"start_index": 0, "end_index": 1},
        is_group_chat=False,
    )


async def _session_message_count(store: ConversationStore) -> int:
    session = await store.get_session(SESSION)
    assert session is not None
    return int(session.message_count)


async def _assert_untouched(store: ConversationStore, baseline: dict) -> None:
    messages = await store.get_messages(SESSION, limit=10)
    assert [message.content for message in messages] == baseline["contents"]
    assert await store.get_summary_epoch(SESSION) == baseline["epoch"]
    assert await _session_message_count(store) == baseline["message_count"]


async def _baseline(store: ConversationStore) -> dict:
    """记录清理前的可观察状态，供 fail-closed 断言比对。"""

    messages = await store.get_messages(SESSION, limit=10)
    return {
        "contents": [message.content for message in messages],
        "epoch": await store.get_summary_epoch(SESSION),
        "message_count": await _session_message_count(store),
    }


@pytest.mark.asyncio
async def test_pending_quarantine_blocks_reset_without_mutating_state(tmp_db_path):
    """存在待处置候选时清空 fail-closed，不删消息、不改计数与 epoch。"""

    store, quarantine = await _wired_store(tmp_db_path)
    try:
        baseline = await _baseline(store)
        await _stage(quarantine, "guard-pending")

        with pytest.raises(RuntimeError, match="summary_source_protected"):
            await store.clear_session_atomically(SESSION)

        await _assert_untouched(store, baseline)
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("final_state", ["approving", "blocked"])
async def test_non_terminal_approval_states_still_block_reset(tmp_db_path, final_state):
    """approving/blocked 仍属未处置状态，必须继续保护来源。"""

    store, quarantine = await _wired_store(tmp_db_path)
    try:
        baseline = await _baseline(store)
        staged = await _stage(quarantine, f"guard-{final_state}")
        claimed = await quarantine.claim_approval(
            staged["candidate_id"],
            expected_revision=staged["revision"],
            actor_id="admin",
            approval_token="opaque-token",
        )
        if final_state == "blocked":
            await quarantine.block_approval(
                staged["candidate_id"],
                expected_revision=claimed["revision"],
                actor_id="admin",
                reason_code="grounding_recheck_failed",
            )

        with pytest.raises(RuntimeError, match="summary_source_protected"):
            await store.clear_session_atomically(SESSION)

        await _assert_untouched(store, baseline)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_reset_resumes_after_reject(tmp_db_path):
    """候选被拒绝后清理恢复，并维持删除与 epoch 推进语义。"""

    store, quarantine = await _wired_store(tmp_db_path)
    try:
        baseline = await _baseline(store)
        staged = await _stage(quarantine, "guard-rejected")
        await quarantine.reject(
            staged["candidate_id"],
            expected_revision=staged["revision"],
            actor_id="admin",
        )

        deleted = await store.clear_session_atomically(SESSION)

        assert deleted == 2
        assert await store.get_messages(SESSION, limit=10) == []
        epoch, cursor = await store.get_summary_epoch(SESSION)
        assert (epoch, cursor) == (baseline["epoch"][0] + 1, 0)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_reset_resumes_after_approved_candidate(tmp_db_path):
    """批准终结同样释放保护，清理后 epoch 推进且消息清空。"""

    store, quarantine = await _wired_store(tmp_db_path)
    try:
        staged = await _stage(quarantine, "guard-approved")
        token = "opaque-approval-token"
        claimed = await quarantine.claim_approval(
            staged["candidate_id"],
            expected_revision=staged["revision"],
            actor_id="admin",
            approval_token=token,
        )
        await quarantine.finalize_approval(
            staged["candidate_id"],
            expected_revision=claimed["revision"],
            canonical_memory_id=42,
            actor_id="admin",
            approval_token=token,
        )

        deleted = await store.clear_session_atomically(SESSION)

        assert deleted == 2
        epoch, cursor = await store.get_summary_epoch(SESSION)
        assert (epoch, cursor) == (2, 0)
    finally:
        await store.close()
