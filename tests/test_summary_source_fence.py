"""总结来源 fence 到 canonical 写入入口的回归契约。"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.conversation.infrastructure.conversation_store import (
    ConversationStore,
)
from core.features.memory.application.memory_engine import MemoryEngine
from core.features.quality.application.gate_runtime import (
    default_gate_snapshot,
    gate_snapshot_to_json,
)
from core.features.reflection.domain.summary_models import SummaryWindowContext
from core.shared.contracts.conversation import Message
from core.shared.summary_source_fence import SummarySourceFence


def _fence() -> SummarySourceFence:
    """构造不含正文的固定总结来源 fence。"""

    return SummarySourceFence(
        job_id="job-1",
        session_id="session-1",
        session_epoch=1,
        start_seq=0,
        end_seq=2,
        expected_count=2,
        source_digest="digest",
        worker_generation=1,
        claim_token="claim-token",
    )


@pytest.mark.asyncio
async def test_fenced_write_rejects_before_canonical_when_source_is_invalid() -> None:
    """来源已失效时不得开始可见 canonical 写入。"""

    engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
    engine._add_memory_unchecked = AsyncMock(
        return_value=17)  # type: ignore[method-assign]
    engine.set_summary_source_validator(AsyncMock(return_value=False))

    with pytest.raises(RuntimeError, match="summary_source_fenced"):
        await engine.add_memory(
            "候选正文",
            metadata={"idempotency_key": "summary-key"},
            source_fence=_fence(),
        )

    # type: ignore[attr-defined]
    engine._add_memory_unchecked.assert_not_awaited()


@pytest.mark.asyncio
async def test_fenced_write_is_left_nonrecallable_when_source_expires_mid_write() -> (
    None
):
    """写入途中失去 fence 时，canonical 只能保留为不可召回 orphan。"""

    engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
    engine._add_memory_unchecked = AsyncMock(
        return_value=17)  # type: ignore[method-assign]
    # type: ignore[method-assign]
    engine._set_summary_source_orphan = AsyncMock()
    engine.set_summary_source_validator(AsyncMock(side_effect=(True, False)))

    with pytest.raises(RuntimeError, match="summary_source_fenced"):
        await engine.add_memory(
            "候选正文",
            metadata={"idempotency_key": "summary-key"},
            source_fence=_fence(),
        )

    # type: ignore[attr-defined]
    engine._add_memory_unchecked.assert_awaited_once()
    engine._set_summary_source_orphan.assert_awaited_once_with(
        17, True)  # type: ignore[attr-defined]


def _message(session_id: str, index: int) -> Message:
    """构造具备稳定序号的最小测试消息。"""

    return Message.from_dict(
        {
            "id": 0,
            "session_id": session_id,
            "role": "user",
            "content": f"消息-{index}",
            "sender_id": "user-1",
            "sender_name": None,
            "group_id": None,
            "platform": "test",
            "timestamp": float(index),
            "metadata": {},
        }
    )


@pytest.mark.asyncio
async def test_conversation_store_validates_only_matching_running_claim(
    tmp_db_path: str,
) -> None:
    """持久化 fence 必须绑定同一运行中 claim，令牌变化立即拒绝。"""

    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.set_summary_clock(lambda: 100.0)
    try:
        for index in range(2):
            await store.add_message(_message("session-1", index))
        snapshot = default_gate_snapshot()
        context = SummaryWindowContext(
            session_id="session-1",
            session_epoch=1,
            start_seq=0,
            end_seq=0,
            chat_type="private",
            scope_id="session-1",
            gate_revision=snapshot.revision,
            gate_snapshot_json=gate_snapshot_to_json(snapshot),
            window_size=2,
            scope_key="scope-1",
            privacy_level="shared",
            resolver_revision="resolver-1",
            scope_provenance_complete=True,
        )
        assert (await store.plan_and_enqueue_windows(context, 2)).queued == 1
        claims = await store.claim_ready(100.0, "scheduler", 1)
        assert len(claims) == 1
        claim = claims[0]
        fence = SummarySourceFence(
            job_id=claim.job_id,
            session_id=claim.session_id,
            session_epoch=claim.session_epoch,
            start_seq=claim.start_seq,
            end_seq=claim.end_seq,
            expected_count=claim.expected_count,
            source_digest=claim.source_digest,
            worker_generation=claim.worker_generation,
            claim_token=claim.claim_token,
            scope_key=claim.scope_key,
            privacy_level=claim.privacy_level,
            resolver_revision=claim.resolver_revision,
            scope_provenance_complete=claim.scope_provenance_complete,
        )

        assert await store.summary_source_fence_is_active(fence)
        assert not await store.summary_source_fence_is_active(
            replace(fence, claim_token="other-token")
        )
    finally:
        await store.close()
