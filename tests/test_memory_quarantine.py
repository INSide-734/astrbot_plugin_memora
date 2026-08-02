"""pre-canonical 记忆隔离队列与批准契约。"""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from core.models.conversation_models import Message
from core.review.memory_quality_gate import (
    MemoryQualityGate,
    QuarantineApprovalPendingError,
)
from core.review.quarantine_store import MemoryQuarantineStore


def _source_message(content: str = "我喜欢咖啡。") -> Message:
    """构造批准复核使用的原始会话消息。"""

    return Message(
        id=1,
        session_id="session-1",
        role="user",
        content=content,
        sender_id="user-1",
        sender_name="Alice",
        timestamp=time.time(),
    )


def _candidate(*, quality: str = "low") -> dict[str, object]:
    """构造可进入质量门的记忆候选。"""

    source = "我喜欢咖啡。"
    return {
        "content": "用户喜欢咖啡。",
        "importance": 0.7,
        "atoms": [MagicMock()],
        "metadata": {
            "idempotency_key": "quality-candidate-1",
            "summary_quality": quality,
            "grounding_status": "grounded",
            "grounding_reason_codes": [],
            "key_facts": ["用户喜欢咖啡。"],
            "topics": ["咖啡"],
            "source_evidence": [
                {
                    "message_index": 0,
                    "start": 0,
                    "end": len(source),
                    "message_fingerprint": "placeholder",
                    "inferred": False,
                }
            ],
        },
    }


@pytest_asyncio.fixture
async def quarantine_store(tmp_path) -> MemoryQuarantineStore:
    """创建每个测试独立的隔离队列。"""

    store = MemoryQuarantineStore(tmp_path / "memory_quarantine.sqlite3")
    await store.initialize()
    return store


@pytest.mark.asyncio
async def test_initialize_adds_approval_token_hash_to_legacy_database(tmp_path) -> None:
    """旧隔离库初始化时必须补齐 approval token 摘要列。"""

    db_path = tmp_path / "legacy_memory_quarantine.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            CREATE TABLE memory_quarantine_candidates (
                candidate_id TEXT PRIMARY KEY,
                candidate_key TEXT NOT NULL UNIQUE,
                revision INTEGER NOT NULL,
                status TEXT NOT NULL,
                reason_codes_json TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                importance REAL NOT NULL,
                session_id TEXT NOT NULL,
                persona_id TEXT,
                source_window_json TEXT NOT NULL,
                is_group_chat INTEGER NOT NULL,
                canonical_memory_id INTEGER,
                failure_reason TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )

    store = MemoryQuarantineStore(db_path)
    await store.initialize()

    with sqlite3.connect(db_path) as db:
        columns = {
            row[1]
            for row in db.execute("PRAGMA table_info(memory_quarantine_candidates)")
        }
    assert "approval_token_hash" in columns


@pytest.mark.asyncio
async def test_low_quality_candidate_is_staged_without_canonical_write(
    quarantine_store: MemoryQuarantineStore,
) -> None:
    """低质量候选只进入 quarantine，不调用 canonical 写入。"""

    engine = MagicMock()
    engine.add_memory = AsyncMock(return_value=77)
    processor = MagicMock()
    gate = MemoryQualityGate(
        quarantine_store,
        memory_engine=engine,
        memory_processor=processor,
        conversation_manager=MagicMock(),
    )

    result = await gate.route_candidate(
        _candidate(),
        session_id="session-1",
        persona_id="persona-1",
        source_window={"start_index": 0, "end_index": 1, "message_count": 1},
        is_group_chat=False,
    )

    assert result.action == "quarantined"
    engine.add_memory.assert_not_awaited()
    stored = await quarantine_store.get_candidate(result.candidate_id)
    assert stored is not None
    assert stored["canonical_memory_id"] is None
    assert stored["status"] == "pending"


@pytest.mark.asyncio
async def test_quarantine_stage_is_idempotent_by_candidate_key(
    quarantine_store: MemoryQuarantineStore,
) -> None:
    """窗口重试不得重复创建相同隔离候选。"""

    gate = MemoryQualityGate(
        quarantine_store,
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=MagicMock(),
    )
    context = {
        "session_id": "session-1",
        "persona_id": None,
        "source_window": {"start_index": 0, "end_index": 1, "message_count": 1},
        "is_group_chat": False,
    }

    first = await gate.route_candidate(_candidate(), **context)
    second = await gate.route_candidate(_candidate(), **context)

    assert first.candidate_id == second.candidate_id
    assert len(await quarantine_store.list_candidates()) == 1


@pytest.mark.asyncio
async def test_approval_claim_persists_only_token_digest(
    quarantine_store: MemoryQuarantineStore,
) -> None:
    """隔离 Store 只能持久化 repair token 摘要，不能保存明文能力凭据。"""

    token = "opaque-approval-token"
    staged = await quarantine_store.stage_candidate(
        candidate_key="approval-token-digest",
        reason_codes=["summary_quality_low"],
        content="用户喜欢咖啡。",
        metadata={},
        importance=0.7,
        session_id="session-1",
        persona_id=None,
        source_window={"start_index": 0, "end_index": 1},
        is_group_chat=False,
    )
    await quarantine_store.claim_approval(
        staged["candidate_id"],
        expected_revision=staged["revision"],
        actor_id="admin",
        approval_token=token,
    )

    with sqlite3.connect(quarantine_store.db_path) as db:
        row = db.execute(
            """
            SELECT approval_token_hash
            FROM memory_quarantine_candidates
            WHERE candidate_id = ?
            """,
            (staged["candidate_id"],),
        ).fetchone()
    assert row == (hashlib.sha256(token.encode("utf-8")).hexdigest(),)
    assert token not in row[0]


@pytest.mark.asyncio
async def test_approve_creates_one_real_canonical_memory(
    quarantine_store: MemoryQuarantineStore,
) -> None:
    """批准候选只能通过正常引擎入口生成一个 canonical 记录。"""

    message = _source_message()
    engine = MagicMock()
    engine.add_memory = AsyncMock(return_value=77)
    processor = MagicMock()
    processor.classify_atoms_from_metadata.return_value = ["rebuilt-atom"]
    conversation_manager = MagicMock()
    conversation_manager.get_messages_range = AsyncMock(return_value=[message])
    gate = MemoryQualityGate(
        quarantine_store,
        memory_engine=engine,
        memory_processor=processor,
        conversation_manager=conversation_manager,
    )
    candidate = _candidate()
    candidate["metadata"]["source_evidence"] = gate.grounding_validator.validate(
        {
            "summary": candidate["content"],
            "key_facts": candidate["metadata"]["key_facts"],
            "source_refs": [
                {"message_index": 0, "start": 0, "end": len(message.content)}
            ],
        },
        [message],
        is_group_chat=False,
    ).evidence
    staged = await gate.route_candidate(
        candidate,
        session_id="session-1",
        persona_id="persona-1",
        source_window={"start_index": 0, "end_index": 1, "message_count": 1},
        is_group_chat=False,
    )
    pending = await quarantine_store.get_candidate(staged.candidate_id)

    approved = await gate.approve(
        staged.candidate_id,
        expected_revision=pending["revision"],
        actor_id="admin",
    )
    approved_again = await gate.approve(
        staged.candidate_id,
        expected_revision=approved["revision"],
        actor_id="admin",
    )

    assert approved["status"] == "approved"
    assert approved["canonical_memory_id"] == 77
    assert approved_again["canonical_memory_id"] == 77
    engine.add_memory.assert_awaited_once()
    assert engine.add_memory.await_args.kwargs["atoms"] == ["rebuilt-atom"]


@pytest.mark.asyncio
async def test_approval_repair_finalizes_after_canonical_write_before_store_finalize(
    quarantine_store: MemoryQuarantineStore,
) -> None:
    """canonical 已写入但 finalize 失败时 token repair 只能收口同一条候选。"""

    message = _source_message()
    engine = MagicMock()
    engine.add_memory = AsyncMock(return_value=77)
    processor = MagicMock()
    processor.classify_atoms_from_metadata.return_value = []
    conversation_manager = MagicMock()
    conversation_manager.get_messages_range = AsyncMock(return_value=[message])
    gate = MemoryQualityGate(
        quarantine_store,
        memory_engine=engine,
        memory_processor=processor,
        conversation_manager=conversation_manager,
    )
    candidate = _candidate()
    candidate["metadata"]["source_evidence"] = gate.grounding_validator.validate(
        {
            "summary": candidate["content"],
            "key_facts": candidate["metadata"]["key_facts"],
            "source_refs": [
                {"message_index": 0, "start": 0, "end": len(message.content)}
            ],
        },
        [message],
        is_group_chat=False,
    ).evidence
    staged = await gate.route_candidate(
        candidate,
        session_id="session-1",
        persona_id="persona-1",
        source_window={"start_index": 0, "end_index": 1, "message_count": 1},
        is_group_chat=False,
    )
    pending = await quarantine_store.get_candidate(staged.candidate_id)
    captured_metadata: dict[str, object] = {}

    async def capture_canonical(**kwargs: object) -> int:
        """捕获 canonical metadata 并模拟已提交的整数 ID。"""

        captured_metadata.update(kwargs["metadata"])
        return 77

    engine.add_memory.side_effect = capture_canonical
    original_finalize = quarantine_store.finalize_approval
    quarantine_store.finalize_approval = AsyncMock(
        side_effect=RuntimeError("simulated finalize failure")
    )

    with pytest.raises(QuarantineApprovalPendingError) as error:
        await gate.approve(
            staged.candidate_id,
            expected_revision=pending["revision"],
            actor_id="admin",
        )

    assert error.value.revision == pending["revision"] + 1
    token = error.value.approval_token
    assert (
        captured_metadata["_quarantine_approval_token_hash"]
        == hashlib.sha256(token.encode("utf-8")).hexdigest()
    )
    assert token not in str(captured_metadata)
    assert captured_metadata["_quarantine_approval_status"] == "committed"
    approving = await quarantine_store.get_candidate(staged.candidate_id)
    assert approving["status"] == "approving"

    quarantine_store.finalize_approval = original_finalize
    restarted_store = MemoryQuarantineStore(quarantine_store.db_path)
    await restarted_store.initialize()
    engine.get_memory = AsyncMock(
        return_value={
            "text": candidate["content"],
            "metadata": captured_metadata,
        }
    )
    restarted_gate = MemoryQualityGate(
        restarted_store,
        memory_engine=engine,
        memory_processor=processor,
        conversation_manager=conversation_manager,
    )
    repaired = await restarted_gate.repair_approval(
        staged.candidate_id,
        expected_revision=error.value.revision,
        canonical_memory_id=77,
        approval_token=token,
        actor_id="admin",
    )

    assert repaired["status"] == "approved"
    assert repaired["canonical_memory_id"] == 77
    assert await restarted_store.list_actions(staged.candidate_id)


@pytest.mark.asyncio
async def test_approval_repair_fails_closed_for_wrong_token_or_canonical(
    quarantine_store: MemoryQuarantineStore,
) -> None:
    """错误 token、canonical 正文或状态不得批准 approving 候选。"""

    token = "opaque-approval-token"
    staged = await quarantine_store.stage_candidate(
        candidate_key="repair-fail-closed",
        reason_codes=["summary_quality_low"],
        content="用户喜欢咖啡。",
        metadata={},
        importance=0.7,
        session_id="session-1",
        persona_id=None,
        source_window={"start_index": 0, "end_index": 1},
        is_group_chat=False,
    )
    claimed = await quarantine_store.claim_approval(
        staged["candidate_id"],
        expected_revision=staged["revision"],
        actor_id="admin",
        approval_token=token,
    )
    engine = MagicMock()
    engine.get_memory = AsyncMock(
        return_value={
            "text": "用户喜欢咖啡。",
            "metadata": {
                "_quarantine_approval_token_hash": hashlib.sha256(
                    token.encode("utf-8")
                ).hexdigest(),
                "_quarantine_approval_status": "committed",
            },
        }
    )
    gate = MemoryQualityGate(
        quarantine_store,
        memory_engine=engine,
        memory_processor=MagicMock(),
        conversation_manager=MagicMock(),
    )

    with pytest.raises(ValueError, match="quarantine_approval_token_invalid"):
        await gate.repair_approval(
            staged["candidate_id"],
            expected_revision=claimed["revision"],
            canonical_memory_id=77,
            approval_token="wrong-token",
            actor_id="admin",
        )
    engine.get_memory.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await gate.repair_approval(
            staged["candidate_id"],
            expected_revision=claimed["revision"],
            canonical_memory_id=77,
            approval_token=token,
            actor_id="admin",
        )
    engine.get_memory.side_effect = None
    engine.get_memory.return_value = {
        "text": "另一条正文",
        "metadata": {
            "_quarantine_approval_token_hash": hashlib.sha256(
                token.encode("utf-8")
            ).hexdigest(),
            "_quarantine_approval_status": "committed",
        },
    }
    with pytest.raises(ValueError, match="quarantine_canonical_mismatch"):
        await gate.repair_approval(
            staged["candidate_id"],
            expected_revision=claimed["revision"],
            canonical_memory_id=77,
            approval_token=token,
            actor_id="admin",
        )
    current = await quarantine_store.get_candidate(staged["candidate_id"])
    assert current["status"] == "approving"

    engine.get_memory.return_value = {
        "text": "用户喜欢咖啡。",
        "metadata": {
            "_quarantine_approval_token_hash": hashlib.sha256(
                token.encode("utf-8")
            ).hexdigest(),
            "_quarantine_approval_status": "committed",
        },
    }
    approved = await gate.repair_approval(
        staged["candidate_id"],
        expected_revision=claimed["revision"],
        canonical_memory_id=77,
        approval_token=token,
        actor_id="admin",
    )
    with pytest.raises(ValueError, match="quarantine_status_conflict"):
        await gate.repair_approval(
            staged["candidate_id"],
            expected_revision=approved["revision"],
            canonical_memory_id=999,
            approval_token="wrong-token",
            actor_id="admin",
        )


@pytest.mark.asyncio
async def test_repair_can_explicitly_confirm_canonical_absence(
    quarantine_store: MemoryQuarantineStore,
) -> None:
    """明确确认 canonical 未写入时，approving 可安全回到 blocked。"""

    staged = await quarantine_store.stage_candidate(
        candidate_key="repair-blocked",
        reason_codes=["summary_quality_low"],
        content="用户喜欢咖啡。",
        metadata={},
        importance=0.7,
        session_id="session-1",
        persona_id=None,
        source_window={"start_index": 0, "end_index": 1},
        is_group_chat=False,
    )
    claimed = await quarantine_store.claim_approval(
        staged["candidate_id"],
        expected_revision=staged["revision"],
        actor_id="admin",
        approval_token="opaque-approval-token",
    )
    gate = MemoryQualityGate(
        quarantine_store,
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=MagicMock(),
    )

    with pytest.raises(
        ValueError,
        match="quarantine_canonical_absence_confirmation_required",
    ):
        await gate.repair_blocked(
            staged["candidate_id"],
            expected_revision=claimed["revision"],
            actor_id="admin",
            confirm_canonical_absent=False,
        )
    blocked = await gate.repair_blocked(
        staged["candidate_id"],
        expected_revision=claimed["revision"],
        actor_id="admin",
        confirm_canonical_absent=True,
    )

    assert blocked["status"] == "blocked"
    assert blocked["failure_reason"] == "canonical_write_not_found_confirmed"


@pytest.mark.asyncio
async def test_reject_preserves_original_conversation_evidence(
    quarantine_store: MemoryQuarantineStore,
) -> None:
    """拒绝候选不得调用任何会话删除入口。"""

    conversation_manager = MagicMock()
    conversation_manager.delete_message = AsyncMock()
    conversation_manager.clear_session = AsyncMock()
    gate = MemoryQualityGate(
        quarantine_store,
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=conversation_manager,
    )
    staged = await gate.route_candidate(
        _candidate(),
        session_id="session-1",
        persona_id=None,
        source_window={"start_index": 0, "end_index": 1, "message_count": 1},
        is_group_chat=False,
    )
    pending = await quarantine_store.get_candidate(staged.candidate_id)

    rejected = await gate.reject(
        staged.candidate_id,
        expected_revision=pending["revision"],
        actor_id="admin",
    )

    assert rejected["status"] == "rejected"
    conversation_manager.delete_message.assert_not_awaited()
    conversation_manager.clear_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_source_evidence_blocks_approval(
    quarantine_store: MemoryQuarantineStore,
) -> None:
    """批准时缺少原始证据不得回退为新的本地推断。"""

    candidate = _candidate()
    candidate["metadata"]["source_evidence"] = []
    engine = MagicMock()
    engine.add_memory = AsyncMock()
    conversation_manager = MagicMock()
    conversation_manager.get_messages_range = AsyncMock(
        return_value=[_source_message()]
    )
    gate = MemoryQualityGate(
        quarantine_store,
        memory_engine=engine,
        memory_processor=MagicMock(),
        conversation_manager=conversation_manager,
    )
    staged = await gate.route_candidate(
        candidate,
        session_id="session-1",
        persona_id=None,
        source_window={"start_index": 0, "end_index": 1, "message_count": 1},
        is_group_chat=False,
    )
    pending = await quarantine_store.get_candidate(staged.candidate_id)

    blocked = await gate.approve(
        staged.candidate_id,
        expected_revision=pending["revision"],
        actor_id="admin",
    )

    assert blocked["status"] == "blocked"
    assert blocked["failure_reason"] == "grounding_source_evidence_missing"
    engine.add_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejected_candidate_cannot_be_approved(
    quarantine_store: MemoryQuarantineStore,
) -> None:
    """rejected 是终态，后续批准不得重新生成 canonical。"""

    engine = MagicMock()
    engine.add_memory = AsyncMock()
    gate = MemoryQualityGate(
        quarantine_store,
        memory_engine=engine,
        memory_processor=MagicMock(),
        conversation_manager=MagicMock(),
    )
    staged = await gate.route_candidate(
        _candidate(),
        session_id="session-1",
        persona_id=None,
        source_window={"start_index": 0, "end_index": 1, "message_count": 1},
        is_group_chat=False,
    )
    pending = await quarantine_store.get_candidate(staged.candidate_id)
    rejected = await gate.reject(
        staged.candidate_id,
        expected_revision=pending["revision"],
        actor_id="admin",
    )

    with pytest.raises(ValueError, match="quarantine_status_conflict"):
        await gate.approve(
            staged.candidate_id,
            expected_revision=rejected["revision"],
            actor_id="admin",
        )

    engine.add_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_approval_correction_rejects_oversized_content_before_claim(
    quarantine_store: MemoryQuarantineStore,
) -> None:
    """管理员修正正文必须服从与抽取 Schema 一致的长度上限。"""

    gate = MemoryQualityGate(
        quarantine_store,
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=MagicMock(),
    )
    staged = await gate.route_candidate(
        _candidate(),
        session_id="session-1",
        persona_id=None,
        source_window={"start_index": 0, "end_index": 1, "message_count": 1},
        is_group_chat=False,
    )
    pending = await quarantine_store.get_candidate(staged.candidate_id)

    with pytest.raises(ValueError, match="quarantine_content_too_long"):
        await gate.approve(
            staged.candidate_id,
            expected_revision=pending["revision"],
            actor_id="admin",
            content="x" * 2001,
        )

    unchanged = await quarantine_store.get_candidate(staged.candidate_id)
    assert unchanged["status"] == "pending"
    assert unchanged["revision"] == pending["revision"]


@pytest.mark.asyncio
async def test_approval_cancellation_before_write_blocks_and_propagates(
    quarantine_store: MemoryQuarantineStore,
) -> None:
    """取证阶段取消必须传播，并把未写入候选恢复为可处置 blocked。"""

    message = _source_message()
    candidate = _candidate()
    validator = MemoryQualityGate(
        quarantine_store,
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=MagicMock(),
    ).grounding_validator
    candidate["metadata"]["source_evidence"] = validator.validate(
        {
            "summary": candidate["content"],
            "key_facts": candidate["metadata"]["key_facts"],
            "source_refs": [
                {"message_index": 0, "start": 0, "end": len(message.content)}
            ],
        },
        [message],
        is_group_chat=False,
    ).evidence
    engine = MagicMock()
    engine.add_memory = AsyncMock()
    conversation_manager = MagicMock()
    conversation_manager.get_messages_range = AsyncMock(
        side_effect=asyncio.CancelledError
    )
    gate = MemoryQualityGate(
        quarantine_store,
        memory_engine=engine,
        memory_processor=MagicMock(),
        conversation_manager=conversation_manager,
    )
    staged = await gate.route_candidate(
        candidate,
        session_id="session-1",
        persona_id=None,
        source_window={"start_index": 0, "end_index": 1, "message_count": 1},
        is_group_chat=False,
    )
    pending = await quarantine_store.get_candidate(staged.candidate_id)

    with pytest.raises(asyncio.CancelledError):
        await gate.approve(
            staged.candidate_id,
            expected_revision=pending["revision"],
            actor_id="admin",
        )

    blocked = await quarantine_store.get_candidate(staged.candidate_id)
    assert blocked["status"] == "blocked"
    assert blocked["failure_reason"] == "approval_cancelled_before_write"
    engine.add_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_during_canonical_write_prevents_automatic_retry(
    quarantine_store: MemoryQuarantineStore,
) -> None:
    """canonical 提交结果未知时保留 approving，防止自动重复写入。"""

    message = _source_message()
    candidate = _candidate()
    processor = MagicMock()
    processor.classify_atoms_from_metadata.return_value = []
    engine = MagicMock()
    engine.add_memory = AsyncMock(side_effect=asyncio.CancelledError)
    conversation_manager = MagicMock()
    conversation_manager.get_messages_range = AsyncMock(return_value=[message])
    gate = MemoryQualityGate(
        quarantine_store,
        memory_engine=engine,
        memory_processor=processor,
        conversation_manager=conversation_manager,
    )
    candidate["metadata"]["source_evidence"] = gate.grounding_validator.validate(
        {
            "summary": candidate["content"],
            "key_facts": candidate["metadata"]["key_facts"],
            "source_refs": [
                {"message_index": 0, "start": 0, "end": len(message.content)}
            ],
        },
        [message],
        is_group_chat=False,
    ).evidence
    staged = await gate.route_candidate(
        candidate,
        session_id="session-1",
        persona_id=None,
        source_window={"start_index": 0, "end_index": 1, "message_count": 1},
        is_group_chat=False,
    )
    pending = await quarantine_store.get_candidate(staged.candidate_id)

    with pytest.raises(asyncio.CancelledError):
        await gate.approve(
            staged.candidate_id,
            expected_revision=pending["revision"],
            actor_id="admin",
        )

    unknown = await quarantine_store.get_candidate(staged.candidate_id)
    assert unknown["status"] == "approving"
    with pytest.raises(ValueError, match="quarantine_status_conflict"):
        await gate.approve(
            staged.candidate_id,
            expected_revision=unknown["revision"],
            actor_id="admin",
        )
    engine.add_memory.assert_awaited_once()
