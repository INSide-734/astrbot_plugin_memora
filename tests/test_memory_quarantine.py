"""pre-canonical 记忆隔离队列与批准契约。"""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from core.features.quality.application.gate_runtime import (
    GateRuntime,
    build_gate_snapshot,
)
from core.features.quality.application.memory_quality_gate import (
    MemoryQualityGate,
    QuarantineApprovalPendingError,
)
from core.features.quality.domain.gate_config import GateConfig
from core.features.quality.infrastructure.quarantine_store import MemoryQuarantineStore
from core.features.recall.processors.memory_grounding import GroundingResult
from core.shared.contracts.conversation import Message


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


def _candidate(*, quality: str = "low") -> dict[str, Any]:
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


def _needs_judge_result(*, evidence: list | None = None) -> Any:
    """构造重验证返回的 requires_judge 结论。"""

    return GroundingResult(
        allowed=False,
        status="needs_judge",
        reason_codes=("grounding_needs_judge",),
        evidence=evidence or [],
        source_text="我喜欢咖啡。",
        claim_text="用户喜欢咖啡。",
        requires_judge=True,
    )


def _judge_supported_result() -> GroundingResult:
    """构造 Judge 放行后的 grounded 结论。"""

    return GroundingResult(
        allowed=True,
        status="grounded",
        reason_codes=("grounding_judge_supported",),
        evidence=[],
        source_text="我喜欢咖啡。",
        claim_text="用户喜欢咖啡。",
        requires_judge=False,
    )


@pytest.mark.asyncio
async def test_approve_resolves_needs_judge_via_judge(
    quarantine_store: MemoryQuarantineStore,
) -> None:
    """重验证返回 requires_judge 时，批准路径复用同一 Judge 解析。"""

    from core.features.quality.domain.gate_config import GateProfile

    engine = MagicMock()
    engine.add_memory = AsyncMock(return_value=77)
    processor = MagicMock()
    processor.classify_atoms_from_metadata.return_value = []
    processor.resolve_grounding_judge = AsyncMock(
        return_value=_judge_supported_result()
    )
    conversation_manager = MagicMock()
    conversation_manager.get_messages_range = AsyncMock(
        return_value=[_source_message()]
    )
    validator = MagicMock()
    validator.revalidate_stored_evidence = MagicMock(return_value=_needs_judge_result())
    gate_runtime = MagicMock()
    gate_runtime.resolve_profile.return_value = GateProfile(name="p")
    gate = MemoryQualityGate(
        quarantine_store,
        memory_engine=engine,
        memory_processor=processor,
        conversation_manager=conversation_manager,
        grounding_validator=validator,
        gate_runtime=gate_runtime,
    )
    staged = await quarantine_store.stage_candidate(
        candidate_key="needs-judge-approve",
        reason_codes=["grounding_needs_judge"],
        content="用户喜欢咖啡。",
        metadata={"key_facts": ["用户喜欢咖啡。"]},
        importance=0.7,
        session_id="session-1",
        persona_id="persona-1",
        source_window={
            "start_index": 0,
            "end_index": 1,
            "message_count": 1,
            "group_id": "group-1",
        },
        is_group_chat=True,
    )
    approved = await gate.approve(
        staged["candidate_id"],
        expected_revision=staged["revision"],
        actor_id="admin",
    )

    assert approved["status"] == "approved"
    assert approved["canonical_memory_id"] == 77
    engine.add_memory.assert_awaited_once()
    processor.resolve_grounding_judge.assert_awaited_once()
    resolution = processor.resolve_grounding_judge.await_args
    assert resolution.kwargs["is_group_chat"] is True
    assert resolution.kwargs["profile"].name == "p"
    gate_runtime.resolve_profile.assert_called_once_with(
        "group", "group-1", "persona-1"
    )


@pytest.mark.asyncio
async def test_approve_blocks_when_judge_unavailable(
    quarantine_store: MemoryQuarantineStore,
) -> None:
    """Judge 不可用时批准必须 fail-closed 阻塞为 grounding_judge_unavailable。"""

    from core.features.quality.domain.gate_config import GateProfile

    engine = MagicMock()
    engine.add_memory = AsyncMock()
    processor = MagicMock()
    processor.classify_atoms_from_metadata.return_value = []
    processor.resolve_grounding_judge = AsyncMock(
        return_value=_needs_judge_result().with_unavailable_judge()
    )
    conversation_manager = MagicMock()
    conversation_manager.get_messages_range = AsyncMock(
        return_value=[_source_message()]
    )
    validator = MagicMock()
    validator.revalidate_stored_evidence = MagicMock(return_value=_needs_judge_result())
    gate_runtime = MagicMock()
    gate_runtime.resolve_profile.return_value = GateProfile(name="p")
    gate = MemoryQualityGate(
        quarantine_store,
        memory_engine=engine,
        memory_processor=processor,
        conversation_manager=conversation_manager,
        grounding_validator=validator,
        gate_runtime=gate_runtime,
    )
    staged = await quarantine_store.stage_candidate(
        candidate_key="needs-judge-unavailable",
        reason_codes=["grounding_needs_judge"],
        content="用户喜欢咖啡。",
        metadata={"key_facts": ["用户喜欢咖啡。"]},
        importance=0.7,
        session_id="session-1",
        persona_id=None,
        source_window={"start_index": 0, "end_index": 1, "message_count": 1},
        is_group_chat=False,
    )
    blocked = await gate.approve(
        staged["candidate_id"],
        expected_revision=staged["revision"],
        actor_id="admin",
    )

    assert blocked["status"] == "blocked"
    assert blocked["failure_reason"] == "grounding_judge_unavailable"
    engine.add_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_approve_judge_cancellation_blocks_and_propagates(
    quarantine_store: MemoryQuarantineStore,
) -> None:
    """Judge 取消必须先恢复 blocked 再传播，不得遗留 approving 状态。"""

    from core.features.quality.domain.gate_config import GateProfile

    engine = MagicMock()
    engine.add_memory = AsyncMock()
    processor = MagicMock()
    processor.classify_atoms_from_metadata.return_value = []
    processor.resolve_grounding_judge = AsyncMock(side_effect=asyncio.CancelledError)
    conversation_manager = MagicMock()
    conversation_manager.get_messages_range = AsyncMock(
        return_value=[_source_message()]
    )
    validator = MagicMock()
    validator.revalidate_stored_evidence = MagicMock(return_value=_needs_judge_result())
    gate_runtime = MagicMock()
    gate_runtime.resolve_profile.return_value = GateProfile(name="p")
    gate = MemoryQualityGate(
        quarantine_store,
        memory_engine=engine,
        memory_processor=processor,
        conversation_manager=conversation_manager,
        grounding_validator=validator,
        gate_runtime=gate_runtime,
    )
    staged = await quarantine_store.stage_candidate(
        candidate_key="needs-judge-cancel",
        reason_codes=["grounding_needs_judge"],
        content="用户喜欢咖啡。",
        metadata={"key_facts": ["用户喜欢咖啡。"]},
        importance=0.7,
        session_id="session-1",
        persona_id=None,
        source_window={"start_index": 0, "end_index": 1, "message_count": 1},
        is_group_chat=False,
    )

    with pytest.raises(asyncio.CancelledError):
        await gate.approve(
            staged["candidate_id"],
            expected_revision=staged["revision"],
            actor_id="admin",
        )

    blocked = await quarantine_store.get_candidate(staged["candidate_id"])
    assert blocked is not None
    assert blocked["status"] == "blocked"
    assert blocked["failure_reason"] == "approval_cancelled_before_write"
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
@pytest.mark.parametrize(
    ("write_error", "expected_error"),
    [
        (asyncio.CancelledError(), asyncio.CancelledError),
        (RuntimeError("post_commit_failure"), QuarantineApprovalPendingError),
    ],
)
async def test_unknown_canonical_write_result_prevents_automatic_retry(
    quarantine_store: MemoryQuarantineStore,
    write_error: BaseException,
    expected_error: type[BaseException],
) -> None:
    """canonical 写入取消或失败且结果未知时保留 approving，防止重复写入。"""

    message = _source_message()
    candidate = _candidate()
    processor = MagicMock()
    processor.classify_atoms_from_metadata.return_value = []
    engine = MagicMock()
    engine.add_memory = AsyncMock(side_effect=write_error)
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

    with pytest.raises(expected_error):
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


def _gate_runtime_with(
    disposition: str,
    rules: list[dict[str, object]] | None = None,
) -> GateRuntime:
    """构造默认 profile 处置与可选规则的独立门禁快照。"""
    payload: dict[str, object] = {
        "profiles": [
            {
                "name": "private",
                "disposition": disposition,
                "rules": rules or [],
            }
        ],
        "bindings": [],
    }
    return GateRuntime(build_gate_snapshot(GateConfig.model_validate(payload)))


@pytest.mark.asyncio
async def test_route_discard_disposition(
    quarantine_store: MemoryQuarantineStore,
) -> None:
    """profile 默认处置为 discard 时候选不进入隔离库。"""

    engine = MagicMock()
    engine.add_memory = AsyncMock(return_value=77)
    gate = MemoryQualityGate(
        quarantine_store,
        memory_engine=engine,
        memory_processor=MagicMock(),
        conversation_manager=MagicMock(),
        gate_runtime=_gate_runtime_with("discard"),
    )
    candidate = _candidate()
    candidate["metadata"]["grounding_status"] = "unverified"
    candidate["metadata"]["grounding_reason_codes"] = ["grounding_numeric_conflict"]

    result = await gate.route_candidate(
        candidate,
        session_id="session-1",
        persona_id="persona-1",
        source_window={"start_index": 0, "end_index": 1, "message_count": 1},
        is_group_chat=False,
    )

    assert result.action == "discard"
    engine.add_memory.assert_not_awaited()
    assert await quarantine_store.list_candidates() == []


@pytest.mark.asyncio
async def test_route_mark_write_tags_metadata_and_builds_atoms(
    quarantine_store: MemoryQuarantineStore,
) -> None:
    """mark_write 候选带低置信标记并经 processor 重建原子。"""

    engine = MagicMock()
    engine.add_memory = AsyncMock(return_value=77)
    processor = MagicMock()
    processor.classify_atoms_from_metadata = MagicMock(return_value=["rebuilt-atom"])
    gate = MemoryQualityGate(
        quarantine_store,
        memory_engine=engine,
        memory_processor=processor,
        conversation_manager=MagicMock(),
        gate_runtime=_gate_runtime_with("mark_write"),
    )
    candidate = _candidate()
    candidate["metadata"]["grounding_status"] = "unverified"
    candidate["metadata"]["grounding_reason_codes"] = ["grounding_numeric_conflict"]

    result = await gate.route_candidate(
        candidate,
        session_id="session-1",
        persona_id="persona-1",
        source_window={"start_index": 0, "end_index": 1, "message_count": 1},
        is_group_chat=False,
    )

    assert result.action == "mark_write"
    assert result.atoms == ["rebuilt-atom"]
    assert candidate["metadata"]["gate_disposition"] == "mark_write"
    assert candidate["metadata"]["quality_gate_action"] == "mark_write"
    assert "grounding_numeric_conflict" in candidate["metadata"]["gate_reason_codes"]
    processor.classify_atoms_from_metadata.assert_called_once_with(
        metadata=candidate["metadata"],
        parent_importance=candidate["importance"],
        session_id="session-1",
        persona_id="persona-1",
    )
    assert await quarantine_store.list_candidates() == []


@pytest.mark.asyncio
async def test_rule_force_overrides_default(
    quarantine_store: MemoryQuarantineStore,
) -> None:
    """规则 force_disposition 覆盖 profile 默认处置。"""

    engine = MagicMock()
    engine.add_memory = AsyncMock(return_value=77)
    processor = MagicMock()
    processor.classify_atoms_from_metadata = MagicMock(return_value=[])
    gate = MemoryQualityGate(
        quarantine_store,
        memory_engine=engine,
        memory_processor=processor,
        conversation_manager=MagicMock(),
        gate_runtime=_gate_runtime_with(
            "discard",
            rules=[
                {
                    "id": "r1",
                    "when": {"op": "exists", "field": "content"},
                    "action": {"kind": "force_disposition", "value": "mark_write"},
                }
            ],
        ),
    )

    result = await gate.route_candidate(
        _candidate(),
        session_id="session-1",
        persona_id="persona-1",
        source_window={"start_index": 0, "end_index": 1, "message_count": 1},
        is_group_chat=False,
    )

    assert result.action == "mark_write"
    assert result.atoms == []
    engine.add_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_rule_importance_actions_apply_and_clamp(
    quarantine_store: MemoryQuarantineStore,
) -> None:
    """规则重要性动作落在候选 importance 上，set_importance 覆盖 delta 且 clamp [0,1]。"""

    processor = MagicMock()
    processor.classify_atoms_from_metadata = MagicMock(return_value=[])
    gate = MemoryQualityGate(
        quarantine_store,
        memory_engine=MagicMock(),
        memory_processor=processor,
        conversation_manager=MagicMock(),
        gate_runtime=_gate_runtime_with(
            "mark_write",
            rules=[
                {
                    "id": "r1",
                    "when": {"op": "exists", "field": "content"},
                    "action": {"kind": "importance_delta", "delta": 0.9},
                },
                {
                    "id": "r2",
                    "when": {"op": "exists", "field": "content"},
                    "action": {"kind": "set_importance", "value": 0.3},
                },
            ],
        ),
    )
    candidate = _candidate()  # importance=0.7

    await gate.route_candidate(
        candidate,
        session_id="session-1",
        persona_id="persona-1",
        source_window={"start_index": 0, "end_index": 1, "message_count": 1},
        is_group_chat=False,
    )

    assert candidate["importance"] == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_rule_metadata_actions_dedupe_truncate_and_privacy(
    quarantine_store: MemoryQuarantineStore,
) -> None:
    """add_topics 去重截断、set_privacy 与 drop_atoms 跳过原子构建。"""

    processor = MagicMock()
    processor.classify_atoms_from_metadata = MagicMock(return_value=["atom"])
    gate = MemoryQualityGate(
        quarantine_store,
        memory_engine=MagicMock(),
        memory_processor=processor,
        conversation_manager=MagicMock(),
        gate_runtime=_gate_runtime_with(
            "mark_write",
            rules=[
                {
                    "id": "r1",
                    "when": {"op": "exists", "field": "content"},
                    "action": {"kind": "add_topics", "values": ["猫", "咖啡", "猫"]},
                },
                {
                    "id": "r2",
                    "when": {"op": "exists", "field": "content"},
                    "action": {"kind": "set_privacy", "value": "public"},
                },
                {
                    "id": "r3",
                    "when": {"op": "exists", "field": "content"},
                    "action": {"kind": "drop_atoms", "value": True},
                },
            ],
        ),
    )
    candidate = _candidate()  # topics=["咖啡"]

    result = await gate.route_candidate(
        candidate,
        session_id="session-1",
        persona_id="persona-1",
        source_window={"start_index": 0, "end_index": 1, "message_count": 1},
        is_group_chat=False,
    )

    assert result.action == "mark_write"
    assert result.atoms == []
    assert candidate["metadata"]["topics"] == ["咖啡", "猫"]
    assert candidate["metadata"]["privacy_level"] == "public"
    processor.classify_atoms_from_metadata.assert_not_called()


@pytest.mark.asyncio
async def test_route_mark_write_classify_failure_degrades_to_empty_atoms(
    quarantine_store: MemoryQuarantineStore,
) -> None:
    """原子派生普通异常不阻断 mark_write，降级为空原子列表。"""

    processor = MagicMock()
    processor.classify_atoms_from_metadata = MagicMock(side_effect=RuntimeError("boom"))
    gate = MemoryQualityGate(
        quarantine_store,
        memory_engine=MagicMock(),
        memory_processor=processor,
        conversation_manager=MagicMock(),
        gate_runtime=_gate_runtime_with("mark_write"),
    )

    result = await gate.route_candidate(
        _candidate(),
        session_id="session-1",
        persona_id="persona-1",
        source_window={"start_index": 0, "end_index": 1, "message_count": 1},
        is_group_chat=False,
    )

    assert result.action == "mark_write"
    assert result.atoms == []


@pytest.mark.asyncio
async def test_route_mark_write_cancellation_propagates(
    quarantine_store: MemoryQuarantineStore,
) -> None:
    """原子派生被取消必须向上传播，不得吞成普通降级。"""

    processor = MagicMock()
    processor.classify_atoms_from_metadata = MagicMock(
        side_effect=asyncio.CancelledError()
    )
    gate = MemoryQualityGate(
        quarantine_store,
        memory_engine=MagicMock(),
        memory_processor=processor,
        conversation_manager=MagicMock(),
        gate_runtime=_gate_runtime_with("mark_write"),
    )

    with pytest.raises(asyncio.CancelledError):
        await gate.route_candidate(
            _candidate(),
            session_id="session-1",
            persona_id="persona-1",
            source_window={"start_index": 0, "end_index": 1, "message_count": 1},
            is_group_chat=False,
        )


@pytest.mark.asyncio
async def test_default_quarantine_profile_still_stages(
    quarantine_store: MemoryQuarantineStore,
) -> None:
    """默认配置（disposition=quarantine）仍走隔离库，行为与未启用门禁一致。"""

    gate = MemoryQualityGate(
        quarantine_store,
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=MagicMock(),
        gate_runtime=GateRuntime(build_gate_snapshot(GateConfig())),
    )

    result = await gate.route_candidate(
        _candidate(),
        session_id="session-1",
        persona_id="persona-1",
        source_window={"start_index": 0, "end_index": 1, "message_count": 1},
        is_group_chat=False,
    )

    assert result.action == "quarantined"
    stored = await quarantine_store.get_candidate(result.candidate_id)
    assert stored is not None
    assert stored["status"] == "pending"
