"""群捕获、可信规划与 canonical 消费之间的 scope 快照契约。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from core.features.conversation.infrastructure.conversation_store import (
    ConversationStore,
)
from core.features.identity.domain.models import IdentityTrust, ResolvedIdentity
from core.features.quality.application.gate_runtime import (
    default_gate_snapshot,
    gate_snapshot_to_json,
)
from core.features.reflection.application.candidate_writer import (
    store_reflection_candidates,
)
from core.features.reflection.application.reflection_handler import ReflectionHandler
from core.features.reflection.application.summary_worker import SummaryWorker
from core.features.reflection.domain.storage_outcomes import ReflectionStoreOutcome
from core.features.reflection.domain.summary_models import (
    SummaryReasonCode,
    SummaryWindowContext,
)
from core.shared.contracts.conversation import Message

_SESSION = "synthetic-session"
_GROUP = "synthetic-group"
_SCOPE_FIELDS = (
    "scope_key",
    "chat_type",
    "privacy_level",
    "resolver_revision",
    "scope_id",
)


async def _capture(store: ConversationStore, start: int = 0) -> None:
    """通过真实 capture Store 写入两条纯合成群消息。"""
    for index in range(start, start + 2):
        await store.add_message(
            Message.from_dict(
                {
                    "id": 0,
                    "session_id": _SESSION,
                    "role": "user",
                    "content": "synthetic fact",
                    "sender_id": "synthetic-user",
                    "sender_name": None,
                    "group_id": _GROUP,
                    "platform": "test",
                    "timestamp": float(index),
                    "metadata": {},
                }
            )
        )


@pytest_asyncio.fixture
async def captured_store(tmp_db_path: str) -> AsyncIterator[ConversationStore]:
    """每个场景独占临时 SQLite，关闭连接不依赖测试成功。"""
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    store.set_summary_clock(lambda: 100.0)
    try:
        await _capture(store)
        yield store
    finally:
        await store.close()


def _handler(store: ConversationStore) -> ReflectionHandler:
    """只装配 scope 解析所需 Store；不接入 Provider。"""
    return ReflectionHandler(
        context=None,
        config_manager=cast(Any, None),
        memory_engine=cast(Any, None),
        memory_processor=cast(Any, None),
        conversation_manager=cast(Any, SimpleNamespace(store=store)),
        enforce_limit_cb=AsyncMock(),
    )


async def _context(store: ConversationStore) -> SummaryWindowContext:
    """由可信协议身份经 ReflectionHandler 的真实 resolver 构造规划上下文。"""
    identity = ResolvedIdentity(
        protocol="test",
        identity_namespace="test",
        stable_user_id="synthetic-user",
        canonical_user_id="synthetic-user",
        scope_type="group",
        scope_id=_GROUP,
        global_name=None,
        scope_name=None,
        display_name=None,
        observed_at=100.0,
        trust_status=IdentityTrust.TRUSTED,
        name_field_states={},
    )
    legacy_scope = await store.get_summary_scope(_SESSION)
    resolution = await _handler(store)._resolve_summary_scope(
        _SESSION, identity, legacy_scope
    )
    assert resolution.available is True
    epoch, cursor = await store.get_summary_epoch(_SESSION)
    gate = default_gate_snapshot()
    return SummaryWindowContext(
        session_id=_SESSION,
        session_epoch=epoch,
        start_seq=cursor,
        chat_type=resolution.chat_type,
        group_id=_GROUP,
        scope_id=resolution.scope_id,
        gate_revision=gate.revision,
        gate_snapshot_json=gate_snapshot_to_json(gate),
        scope_key=resolution.scope_key,
        privacy_level=resolution.privacy_level,
        resolver_revision=resolution.resolver_revision,
        scope_reason_code=resolution.reason_code,
        scope_provenance_complete=resolution.available,
    )


async def _metadata(store: ConversationStore) -> dict[str, Any]:
    """读取 session 原始持久化值，不输出正文或身份日志。"""
    assert store.connection is not None
    cursor = await store.connection.execute(
        "SELECT metadata FROM sessions WHERE session_id=?", (_SESSION,)
    )
    row = await cursor.fetchone()
    assert row is not None
    return json.loads(row[0])


async def _job_scopes(store: ConversationStore) -> list[tuple[object, ...]]:
    """仅投影任务的固定 scope 字段，避免全行敏感内容断言。"""
    assert store.connection is not None
    cursor = await store.connection.execute(
        "SELECT scope_key,chat_type,privacy_level,resolver_revision,scope_id,"
        "scope_provenance_complete FROM summary_jobs WHERE session_id=? "
        "ORDER BY start_seq",
        (_SESSION,),
    )
    return [tuple(row) for row in await cursor.fetchall()]


class _CanonicalSink:
    """canonical 端口替身只保留 scope 投影与幂等 owner，不调用外部服务。"""

    def __init__(self) -> None:
        self.scopes: list[dict[str, object]] = []
        self.owners: dict[str, int] = {}

    async def find_memory_id_by_idempotency_key(self, key: str) -> int | None:
        return self.owners.get(key)

    async def add_memory(self, **payload: Any) -> int:
        metadata = payload["metadata"]
        self.scopes.append(
            {
                key: metadata.get(key)
                for key in (*_SCOPE_FIELDS[:-1], "source_provenance_complete")
            }
        )
        owner = len(self.scopes)
        self.owners[metadata["idempotency_key"]] = owner
        return owner


@pytest.mark.asyncio
async def test_capture_then_planning_freezes_scope_through_worker_and_restart(
    captured_store: ConversationStore,
) -> None:
    """捕获不足以推导 scope；可信规划后重启、DTO 与 canonical 使用同一快照。"""
    store = captured_store
    before = await _metadata(store)
    assert before == {"chat_type": "group", "group_id": _GROUP, "scope_id": _GROUP}
    assert await store.get_summary_scope_snapshot(_SESSION) is None
    handler = _handler(store)
    legacy = await store.get_summary_scope(_SESSION)
    assert (
        await handler._resolve_summary_scope(_SESSION, None, legacy)
    ).available is False

    context = await _context(store)
    expected = {key: getattr(context, key) for key in _SCOPE_FIELDS}
    planned = await store.plan_and_enqueue_windows(context, 2)
    assert (planned.accepted, planned.queued) == (True, 1)
    duplicate = await store.plan_and_enqueue_windows(context, 2)
    assert (duplicate.accepted, duplicate.queued, duplicate.duplicates) == (True, 0, 1)
    assert duplicate.reason_code is SummaryReasonCode.DUPLICATE
    assert await _job_scopes(store) == [(*expected.values(), 1)]
    assert (await _metadata(store))["scope_provenance_complete"] is True
    assert await store.get_summary_scope_snapshot(_SESSION) == expected

    await store.close()
    await store.initialize()
    assert await store.get_summary_scope_snapshot(_SESSION) == expected
    persisted = await handler._resolve_summary_scope(_SESSION, None, legacy)
    assert persisted.available is True
    assert {key: getattr(persisted, key) for key in _SCOPE_FIELDS} == expected
    claims = await store.claim_ready(100.0, "synthetic-worker", 1)
    assert len(claims) == 1
    claim = claims[0]
    assert claim.scope_available is True
    assert {key: getattr(claim, key) for key in _SCOPE_FIELDS} == expected

    async def prepare(messages: list[Message], _is_group: bool) -> list[list[Message]]:
        return [messages]

    processor = SimpleNamespace(
        process_conversation=AsyncMock(
            return_value=[
                {"content": "synthetic fact", "importance": 0.8, "metadata": {}}
            ]
        )
    )
    sink = _CanonicalSink()
    worker = SummaryWorker(
        cast(Any, store),
        cast(Any, processor),
        None,
        cast(Any, sink),
        cast(Any, SimpleNamespace(prepare_batches=prepare)),
    )
    outcome = await worker.execute(claim)
    assert (outcome.canonical_count, outcome.failed_count, outcome.unknown_count) == (
        1,
        0,
        0,
    )
    assert sink.scopes == [
        {
            **{key: expected[key] for key in _SCOPE_FIELDS[:-1]},
            "source_provenance_complete": True,
        }
    ]
    committed = await store.commit_window(claim, outcome)
    assert (committed.accepted, committed.cursor) == (True, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", _SCOPE_FIELDS)
async def test_conflicting_planning_never_overwrites_or_enqueues(
    captured_store: ConversationStore, field: str
) -> None:
    """已固化任一 scope 字段冲突时，既有任务和新消息 frontier 均保持不变。"""
    store = captured_store
    context = await _context(store)
    assert (await store.plan_and_enqueue_windows(context, 2)).queued == 1
    await _capture(store, 2)
    original = await store.get_summary_scope_snapshot(_SESSION)
    original_jobs = await _job_scopes(store)
    changes: dict[str, Any] = {
        field: "shared" if field == "privacy_level" else "conflicting-scope"
    }
    if field == "chat_type":
        changes.update(chat_type="private", group_id=None)
    conflict = replace(context, **changes)
    failed = await store.plan_and_enqueue_windows(conflict, 4)
    assert (failed.accepted, failed.queued) == (False, 0)
    assert failed.reason_code is SummaryReasonCode.STORE_UNAVAILABLE
    assert await store.get_summary_scope_snapshot(_SESSION) == original
    assert await _job_scopes(store) == original_jobs
    assert await store.get_summary_epoch(_SESSION) == (1, 0)
    assert store.connection is not None and not store.connection.in_transaction
    # 同一连接仍可规划剩余来源，失败没有留下锁或消耗 frontier。
    assert (await store.plan_and_enqueue_windows(context, 4)).queued == 1
    assert await _job_scopes(store) == original_jobs * 2


@pytest.mark.asyncio
async def test_cancelled_planning_rolls_back_new_scope_snapshot(
    captured_store: ConversationStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """快照 UPDATE 之后取消也必须撤销整个规划事务，并向上传播取消。"""
    store = captured_store
    context = await _context(store)
    before = await _metadata(store)
    with monkeypatch.context() as patch:
        patch.setattr(
            store,
            "_validated_frontier",
            AsyncMock(side_effect=asyncio.CancelledError()),
        )
        with pytest.raises(asyncio.CancelledError):
            await store.plan_and_enqueue_windows(context, 2)
    assert await _metadata(store) == before
    assert await store.get_summary_scope_snapshot(_SESSION) is None
    assert await _job_scopes(store) == []
    assert store.connection is not None and not store.connection.in_transaction
    assert (await store.plan_and_enqueue_windows(context, 2)).queued == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["scope_key", "privacy_level", "resolver_revision"])
async def test_canonical_writer_rejects_partial_trusted_snapshot(
    captured_store: ConversationStore, missing: str
) -> None:
    """部分可信快照不能由模型 metadata 补齐，更不能抵达 canonical 端口。"""
    store = captured_store
    context = await _context(store)
    assert (await store.plan_and_enqueue_windows(context, 2)).queued == 1
    claims = await store.claim_ready(100.0, "synthetic-worker", 1)
    assert len(claims) == 1
    claim = claims[0]
    scope = {key: getattr(claim, key) for key in _SCOPE_FIELDS}
    scope[missing] = None
    sink = _CanonicalSink()
    results = await store_reflection_candidates(
        [
            {
                "content": "synthetic fact",
                "importance": 0.8,
                "metadata": {key: getattr(claim, key) for key in _SCOPE_FIELDS},
            }
        ],
        completed_idempotency_keys=set(),
        session_id=_SESSION,
        persona_id=None,
        start_index=0,
        end_index=2,
        is_group_chat=True,
        group_id=_GROUP,
        source_provenance_complete=True,
        memory_engine=sink,
        memory_quality_gate=None,
        schedule_evolution_after_write=AsyncMock(),
        **scope,
    )
    assert [result.outcome for result in results] == [ReflectionStoreOutcome.FAILED]
    assert sink.scopes == []
    assert await store.get_summary_epoch(_SESSION) == (1, 0)


@pytest.mark.asyncio
async def test_job_fallback_requires_persisted_provenance(
    captured_store: ConversationStore,
) -> None:
    """session 快照缺失时只接受完整 job 快照；provenance 缺失不按 ID 补猜。"""
    store = captured_store
    context = await _context(store)
    assert (await store.plan_and_enqueue_windows(context, 2)).queued == 1
    expected = {key: getattr(context, key) for key in _SCOPE_FIELDS}
    assert store.connection is not None
    await store.connection.execute(
        "UPDATE sessions SET metadata='{}' WHERE session_id=?", (_SESSION,)
    )
    await store.connection.commit()
    assert await store.get_summary_scope_snapshot(_SESSION) == expected
    legacy = await store.get_summary_scope(_SESSION)
    handler = _handler(store)
    assert (
        await handler._resolve_summary_scope(_SESSION, None, legacy)
    ).available is True

    await store.connection.execute(
        "UPDATE summary_jobs SET scope_provenance_complete=0 WHERE session_id=?",
        (_SESSION,),
    )
    await store.connection.commit()
    assert await store.get_summary_scope_snapshot(_SESSION) is None
    resolution = await handler._resolve_summary_scope(_SESSION, None, legacy)
    assert resolution.available is False
    assert resolution.scope_key == ""


@pytest.mark.asyncio
async def test_snapshot_read_cancellation_reaches_reflection_handler(
    captured_store: ConversationStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """持久化读取取消不能被当作缺失快照吞掉。"""
    store = captured_store
    context = await _context(store)
    assert (await store.plan_and_enqueue_windows(context, 2)).queued == 1
    legacy = await store.get_summary_scope(_SESSION)
    assert store.connection is not None
    with monkeypatch.context() as patch:
        patch.setattr(
            store.connection, "execute", AsyncMock(side_effect=asyncio.CancelledError())
        )
        with pytest.raises(asyncio.CancelledError):
            await _handler(store)._resolve_summary_scope(_SESSION, None, legacy)
    assert await store.get_summary_scope_snapshot(_SESSION) == {
        key: getattr(context, key) for key in _SCOPE_FIELDS
    }
