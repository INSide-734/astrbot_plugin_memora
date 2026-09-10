"""验证 quarantine 在 canonical 提交结果未知时的持久恢复契约。"""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.quality.application.memory_quality_gate import MemoryQualityGate
from core.features.quality.infrastructure.quarantine_store import MemoryQuarantineStore
from core.features.recall.processors.memory_grounding import MemoryGroundingValidator
from core.shared.contracts.conversation import Message
from tests.stable_approval_source import (
    StableApprovalConversation,
    stable_source_window,
)


def _source_message() -> Message:
    """构造与稳定批准来源一致的测试消息。"""
    return Message(
        id=1,
        session_id="session-1",
        role="user",
        content="我喜欢咖啡。",
        sender_id="user-1",
        sender_name="Alice",
        timestamp=1.0,
    )


def _approval_conversation() -> StableApprovalConversation:
    """构造满足 epoch、message_seq 和来源锁契约的会话替身。"""
    return StableApprovalConversation(_source_message())


async def _claimed_candidate(store: MemoryQuarantineStore) -> tuple[dict, str]:
    """创建 approving 候选并返回其持久记录与原始 token。"""
    candidate = await store.stage_candidate(
        candidate_key="durable-recovery-candidate",
        reason_codes=["summary_quality_low"],
        content="用户喜欢咖啡。",
        metadata={"key_facts": ["用户喜欢咖啡。"]},
        importance=0.7,
        session_id="session-1",
        persona_id="persona-1",
        source_window=stable_source_window(_source_message()),
        is_group_chat=False,
    )
    token = "durable-recovery-token"
    claimed = await store.claim_approval(
        candidate["candidate_id"],
        expected_revision=candidate["revision"],
        actor_id="admin",
        approval_token=token,
    )
    return claimed, token


@pytest.mark.asyncio
async def test_repair_approval_uses_persisted_correlation_without_raw_token(
    tmp_path,
) -> None:
    """重启后只凭两端持久事实即可收口 approving 候选。"""
    store = MemoryQuarantineStore(tmp_path / "memory_quarantine.sqlite3")
    await store.initialize()
    claimed, token = await _claimed_candidate(store)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    engine = MagicMock()
    engine.get_memory = AsyncMock(
        return_value={
            "text": claimed["content"],
            "metadata": {
                "_quarantine_candidate_id": claimed["candidate_id"],
                "_quarantine_approval_token_hash": token_hash,
                "_quarantine_approval_status": "committed",
                "source_epoch": claimed["source_window"]["session_epoch"],
                "source_digest": claimed["source_window"]["source_digest"],
                "source_fence_generation": claimed["source_window"][
                    "worker_generation"
                ],
                "source_fence": claimed["source_window"]["source_fence"],
            },
        }
    )
    gate = MemoryQualityGate(
        store,
        memory_engine=engine,
        memory_processor=MagicMock(),
        conversation_manager=_approval_conversation(),
    )

    repaired = await gate.repair_approval(
        claimed["candidate_id"],
        expected_revision=claimed["revision"],
        canonical_memory_id=77,
        approval_token=None,
        actor_id="admin",
    )

    assert repaired["status"] == "approved"
    assert repaired["canonical_memory_id"] == 77


@pytest.mark.asyncio
async def test_repair_without_raw_token_rejects_wrong_correlation(tmp_path) -> None:
    """持久 digest 匹配但 candidate correlation 不匹配时必须拒绝收口。"""
    store = MemoryQuarantineStore(tmp_path / "memory_quarantine.sqlite3")
    await store.initialize()
    claimed, token = await _claimed_candidate(store)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    engine = MagicMock()
    engine.get_memory = AsyncMock(
        return_value={
            "text": claimed["content"],
            "metadata": {
                "_quarantine_candidate_id": "qc-other",
                "_quarantine_approval_token_hash": token_hash,
                "_quarantine_approval_status": "committed",
                "source_epoch": claimed["source_window"]["session_epoch"],
                "source_digest": claimed["source_window"]["source_digest"],
                "source_fence_generation": claimed["source_window"][
                    "worker_generation"
                ],
                "source_fence": claimed["source_window"]["source_fence"],
            },
        }
    )
    gate = MemoryQualityGate(
        store,
        memory_engine=engine,
        memory_processor=MagicMock(),
        conversation_manager=_approval_conversation(),
    )

    with pytest.raises(ValueError, match="quarantine_candidate_correlation_invalid"):
        await gate.repair_approval(
            claimed["candidate_id"],
            expected_revision=claimed["revision"],
            canonical_memory_id=77,
            approval_token=None,
            actor_id="admin",
        )


@pytest.mark.asyncio
async def test_approved_candidate_missing_canonical_is_not_returned_as_valid(
    tmp_path,
) -> None:
    """approved 指向缺失 canonical 时不能走无校验的幂等返回。"""
    store = MemoryQuarantineStore(tmp_path / "memory_quarantine.sqlite3")
    await store.initialize()
    candidate, token = await _claimed_candidate(store)
    approved = await store.finalize_approval(
        candidate["candidate_id"],
        expected_revision=candidate["revision"],
        canonical_memory_id=77,
        actor_id="admin",
        approval_token=token,
    )
    engine = MagicMock()
    engine.get_memory = AsyncMock(return_value=None)
    gate = MemoryQualityGate(
        store,
        memory_engine=engine,
        memory_processor=MagicMock(),
        conversation_manager=_approval_conversation(),
    )

    with pytest.raises(ValueError, match="quarantine_canonical_not_found"):
        await gate.approve(
            candidate["candidate_id"],
            expected_revision=approved["revision"],
            actor_id="admin",
        )


@pytest.mark.asyncio
async def test_approve_persists_candidate_correlation_in_canonical_metadata(
    tmp_path,
) -> None:
    """canonical metadata 必须携带可用于崩溃恢复的 quarantine 关联。"""
    store = MemoryQuarantineStore(tmp_path / "memory_quarantine.sqlite3")
    await store.initialize()
    message = _source_message()
    grounding = MemoryGroundingValidator()
    source_evidence = grounding.validate(
        {
            "summary": "用户喜欢咖啡。",
            "key_facts": ["用户喜欢咖啡。"],
            "source_refs": [
                {"message_index": 0, "start": 0, "end": len(message.content)}
            ],
        },
        [message],
        is_group_chat=False,
    ).evidence
    candidate = await store.stage_candidate(
        candidate_key="durable-metadata-candidate",
        reason_codes=["summary_quality_low"],
        content="用户喜欢咖啡。",
        metadata={
            "key_facts": ["用户喜欢咖啡。"],
            "source_evidence": source_evidence,
            "grounding_status": "grounded",
            "summary_quality": "low",
        },
        importance=0.7,
        session_id="session-1",
        persona_id="persona-1",
        source_window=stable_source_window(message),
        is_group_chat=False,
    )
    engine = MagicMock()
    engine.add_memory = AsyncMock(return_value=77)
    processor = MagicMock()
    processor.classify_atoms_from_metadata.return_value = []
    conversation = _approval_conversation()
    conversation.get_messages_seq_range = AsyncMock(return_value=[message])
    gate = MemoryQualityGate(
        store,
        memory_engine=engine,
        memory_processor=processor,
        conversation_manager=conversation,
    )
    pending = await store.get_candidate(candidate["candidate_id"])

    await gate.approve(
        candidate["candidate_id"],
        expected_revision=pending["revision"],
        actor_id="admin",
    )

    metadata = engine.add_memory.await_args.kwargs["metadata"]
    assert metadata["_quarantine_candidate_id"] == candidate["candidate_id"]
    assert metadata["_quarantine_approval_status"] == "committed"
