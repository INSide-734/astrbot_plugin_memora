"""持久化 SummaryWorker 的质量路由与 canonical 收口契约。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from core.features.conversation.infrastructure.conversation_store import (
    ConversationStore,
)
from core.features.quality.application.gate_runtime import (
    GateRuntime,
    build_gate_snapshot,
    default_gate_snapshot,
    gate_snapshot_from_json,
    gate_snapshot_to_json,
)
from core.features.quality.application.memory_quality_gate import MemoryGateResult
from core.features.quality.domain.gate_config import GateConfig
from core.features.recall.processors.json_parser import SummaryParseError
from core.features.reflection.application.summary_worker import (
    SummaryWorker,
    SummaryWorkerFailure,
)
from core.features.reflection.domain.summary_models import (
    ClaimedJob,
    SummaryReasonCode,
    SummaryWindowContext,
)
from core.shared.contracts.conversation import Message


def _message(session_id: str, index: int, group_id: str | None = None) -> Message:
    """构造具有稳定来源字段的测试消息。"""
    return Message.from_dict(
        {
            "id": 0,
            "session_id": session_id,
            "role": "user",
            "content": f"消息-{index}",
            "sender_id": "user-1",
            "sender_name": "用户",
            "group_id": group_id,
            "platform": "test",
            "timestamp": float(index),
            "metadata": {},
        }
    )


def _candidate(content: str) -> dict[str, Any]:
    """构造最小合法记忆候选。"""
    return {
        "content": content,
        "importance": 0.8,
        "metadata": {},
        "atoms": [],
    }


class _Processor:
    """返回固定候选并记录固化作用域。"""

    def __init__(self, candidates: list[dict[str, Any]]) -> None:
        """保存候选副本。"""
        self.candidates = candidates
        self.calls: list[dict[str, object]] = []

    async def process_conversation(self, **context: object) -> list[dict[str, Any]]:
        """记录调用上下文并返回候选副本。"""
        self.calls.append(context)
        return [dict(candidate) for candidate in self.candidates]


class _FailingProcessor:
    """在 Processor 边界抛出固定异常。"""

    def __init__(self, error: BaseException) -> None:
        """保存待传播或映射的异常。"""

        self.error = error

    async def process_conversation(self, **_context: object) -> list[dict[str, Any]]:
        """抛出固定异常，不返回候选。"""

        raise self.error


class _Gate:
    """按顺序返回固定质量门动作。"""

    def __init__(self, actions: list[str]) -> None:
        """保存动作与调用记录。"""
        self.actions = iter(actions)
        self.calls: list[dict[str, object]] = []

    async def route_candidate(
        self, _candidate_value: dict[str, Any], **context: object
    ) -> MemoryGateResult:
        """记录固化上下文并返回下一个动作。"""
        self.calls.append(context)
        return MemoryGateResult(action=next(self.actions))


class _Engine:
    """实现 SummaryWorker 所需的 canonical 幂等写端口。"""

    def __init__(self, failure: BaseException | None = None) -> None:
        """初始化 owner 映射、写次数与可选失败。"""
        self.failure = failure
        self.owners: dict[str, int] = {}
        self.write_count = 0

    async def find_memory_id_by_idempotency_key(self, key: str) -> int | None:
        """按稳定幂等键查找 owner。"""
        return self.owners.get(key)

    async def add_memory(self, **payload: object) -> int:
        """创建 canonical owner，或传播注入的写入失败。"""
        if self.failure is not None:
            raise self.failure
        metadata = payload.get("metadata")
        assert isinstance(metadata, dict)
        key = metadata.get("idempotency_key")
        assert isinstance(key, str) and key
        existing = self.owners.get(key)
        if existing is not None:
            return existing
        self.write_count += 1
        owner = 100 + self.write_count
        self.owners[key] = owner
        return owner


class _BatchPreparer:
    """保持来源顺序并返回一个基础批次。"""

    async def prepare_batches(
        self, messages: list[Message], _is_group_chat: bool
    ) -> list[list[Message]]:
        """返回完整消息批次。"""
        return [messages]


def _worker(
    store: ConversationStore,
    processor: object,
    gate: object | None,
    engine: _Engine,
) -> SummaryWorker:
    """在测试边界适配真实 Store 与轻量协作替身。"""

    return SummaryWorker(
        cast(Any, store),
        cast(Any, processor),
        cast(Any, gate),
        cast(Any, engine),
        cast(Any, _BatchPreparer()),
    )


async def _claim(
    store: ConversationStore,
    *,
    session_id: str,
    group_id: str | None = None,
    message_count: int = 2,
    window_size: int = 2,
    scope: tuple[str, str, str] | None = None,
) -> ClaimedJob:
    """写入固定来源、规划窗口并返回唯一 claim。"""
    store.set_summary_clock(lambda: 100.0)
    for index in range(message_count):
        await store.add_message(_message(session_id, index, group_id))
    snapshot = default_gate_snapshot()
    scope_key, privacy_level, resolver_revision = scope or ("", "", "")
    context = SummaryWindowContext(
        session_id=session_id,
        session_epoch=1,
        start_seq=0,
        end_seq=0,
        chat_type="group" if group_id else "private",
        group_id=group_id,
        scope_id=group_id or session_id,
        gate_revision=snapshot.revision,
        gate_snapshot_json=gate_snapshot_to_json(snapshot),
        window_size=window_size,
        scope_key=scope_key,
        privacy_level=privacy_level or None,
        resolver_revision=resolver_revision,
        scope_reason_code="scope_resolved" if scope else "scope_unavailable",
        scope_provenance_complete=True if scope else None,
    )
    assert (await store.plan_and_enqueue_windows(context, message_count)).queued == 1
    claims = await store.claim_ready(100.0, "scheduler", 1)
    assert len(claims) == 1
    return claims[0]


@pytest.mark.asyncio
async def test_worker_routes_all_dispositions_without_bypassing_gate(
    tmp_db_path: str,
) -> None:
    """canonical、quarantine、discard 与 mark_write 应形成互斥终态。"""
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    processor = _Processor(
        [
            _candidate("canonical"),
            _candidate("quarantine"),
            _candidate("discard"),
            _candidate("mark-write"),
        ]
    )
    gate = _Gate(["allow", "quarantined", "discard", "mark_write"])
    engine = _Engine()
    worker = _worker(store, processor, gate, engine)
    try:
        claim = await _claim(store, session_id="quality")
        outcome = await worker.execute(claim)
        committed = await store.commit_window(claim, outcome)

        assert outcome.canonical_count == 1
        assert outcome.quarantine_count == 1
        assert outcome.discard_count == 1
        assert outcome.mark_write_count == 1
        assert outcome.failed_count == 0
        assert engine.write_count == 2
        assert len(gate.calls) == 4
        assert committed.accepted is True
        assert committed.cursor == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_worker_failure_keeps_cursor_and_enters_retry_state(
    tmp_db_path: str,
) -> None:
    """真实 canonical 写入失败不得提交窗口或推进连续游标。"""
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    worker = _worker(
        store,
        _Processor([_candidate("failed")]),
        _Gate(["allow"]),
        _Engine(RuntimeError("write_failed")),
    )
    try:
        claim = await _claim(store, session_id="failed")
        outcome = await worker.execute(claim)

        assert outcome.failed_count == 1
        assert outcome.can_advance is False
        assert await store.get_summary_epoch("failed") == (1, 0)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_worker_write_cancellation_propagates(tmp_db_path: str) -> None:
    """canonical 写入取消不得降级为普通候选失败。"""
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    worker = _worker(
        store,
        _Processor([_candidate("cancel")]),
        _Gate(["allow"]),
        _Engine(asyncio.CancelledError()),
    )
    try:
        claim = await _claim(store, session_id="cancel")
        with pytest.raises(asyncio.CancelledError):
            await worker.execute(claim)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_worker_uses_claimed_group_scope_for_processor_and_gate(
    tmp_db_path: str,
) -> None:
    """Processor 和质量门必须共用 claim 固化的群组作用域。"""
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    processor = _Processor([_candidate("group")])
    gate = _Gate(["allow"])
    worker = _worker(store, processor, gate, _Engine())
    try:
        claim = await _claim(store, session_id="group-session", group_id="group-7")
        await worker.execute(claim)

        assert processor.calls[0]["is_group_chat"] is True
        assert processor.calls[0]["group_id"] == "group-7"
        assert gate.calls[0]["group_id"] == "group-7"
        assert gate.calls[0]["scope_id"] == "group-7"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_worker_keeps_claim_snapshot_across_gate_hot_reload(
    tmp_db_path: str,
) -> None:
    """窗口执行中门禁热重载不得改变 job 固化的 revision。"""
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    runtime = GateRuntime(default_gate_snapshot())
    processor = _Processor([_candidate("snapshot")])
    gate = _Gate(["allow"])
    worker = _worker(store, processor, gate, _Engine())
    try:
        claim = await _claim(store, session_id="snapshot")
        original_revision = claim.gate_revision
        runtime.reload(build_gate_snapshot(GateConfig(enabled=False)))

        await worker.execute(claim)

        processor_snapshot = gate_snapshot_from_json(
            str(processor.calls[0]["gate_snapshot_json"])
        )
        gate_snapshot = gate_snapshot_from_json(
            str(gate.calls[0]["gate_snapshot_json"])
        )
        assert processor_snapshot is not None
        assert gate_snapshot is not None
        assert processor_snapshot.revision == original_revision
        assert gate_snapshot.revision == original_revision
        assert runtime.snapshot().revision != original_revision
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_worker_completes_no_facts_without_candidate_intents(
    tmp_db_path: str,
) -> None:
    """空候选必须直接形成 no-facts，推进窗口且不创建 candidate ledger。"""

    store = ConversationStore(tmp_db_path)
    await store.initialize()
    processor = _Processor([])
    engine = _Engine()
    worker = _worker(store, processor, _Gate([]), engine)
    try:
        claim = await _claim(store, session_id="no-facts")
        with patch.object(
            store,
            "begin_candidate_intents",
            new_callable=AsyncMock,
            wraps=store.begin_candidate_intents,
        ) as begin_intents:
            outcome = await worker.execute(claim)
            committed = await store.commit_window(claim, outcome)

        begin_intents.assert_not_awaited()
        assert outcome.reason_code is SummaryReasonCode.NO_FACTS
        assert outcome.can_advance is True
        assert outcome.candidate_slots == ()
        assert committed.status.value == "completed"
        assert committed.cursor == 2
        assert processor.calls[0]["strict_summary"] is True
        assert processor.calls[0]["llm_max_retries"] == 1
        assert engine.write_count == 0
        snapshot = await store.snapshot()
        assert snapshot.canonical_total == 0
        assert snapshot.quarantine_total == 0
        assert snapshot.discard_total == 0
        assert snapshot.mark_write_total == 0
        assert snapshot.failed_candidate_total == 0
        assert snapshot.skipped_idempotent_total == 0
        assert store.connection is not None
        ledger = await store.connection.execute(
            "SELECT COUNT(*) FROM summary_job_candidates WHERE job_id=?",
            (claim.job_id,),
        )
        ledger_row = await ledger.fetchone()
        assert ledger_row is not None
        assert ledger_row[0] == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_worker_skips_twenty_chat_messages_without_projection(
    tmp_db_path: str,
) -> None:
    """二十条闲聊总结应推进完整窗口且不产生 canonical 或派生写入。"""

    store = ConversationStore(tmp_db_path)
    await store.initialize()
    engine = _Engine()
    processor = _Processor([])
    worker = _worker(store, processor, _Gate([]), engine)
    try:
        claim = await _claim(
            store,
            session_id="twenty-chat",
            message_count=20,
            window_size=20,
        )
        outcome = await worker.execute(claim)
        committed = await store.commit_window(claim, outcome)

        assert outcome.reason_code is SummaryReasonCode.NO_FACTS
        assert committed.status.value == "completed"
        assert committed.cursor == 20
        assert engine.write_count == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_worker_maps_strict_parse_error_to_retryable_invalid(
    tmp_db_path: str,
) -> None:
    """严格结构错误必须在 intent 前映射为可重试 summary-invalid。"""

    store = ConversationStore(tmp_db_path)
    await store.initialize()
    worker = _worker(
        store,
        _FailingProcessor(SummaryParseError("secret-response")),
        _Gate([]),
        _Engine(),
    )
    try:
        claim = await _claim(store, session_id="invalid")
        with patch.object(
            store,
            "begin_candidate_intents",
            new_callable=AsyncMock,
            wraps=store.begin_candidate_intents,
        ) as begin_intents:
            with pytest.raises(SummaryWorkerFailure) as raised:
                await worker.execute(claim)

        begin_intents.assert_not_awaited()
        assert raised.value.failed_stage == "memory_extract"
        assert raised.value.reason_code is SummaryReasonCode.SUMMARY_INVALID
        assert raised.value.retryable is True
        assert raised.value.exception_type == "SummaryParseError"
        assert await store.get_summary_epoch("invalid") == (1, 0)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_worker_propagates_processor_cancellation(tmp_db_path: str) -> None:
    """Processor 取消必须传播，不能被映射为 invalid 或普通失败。"""

    store = ConversationStore(tmp_db_path)
    await store.initialize()
    worker = _worker(
        store,
        _FailingProcessor(asyncio.CancelledError()),
        _Gate([]),
        _Engine(),
    )
    try:
        claim = await _claim(store, session_id="processor-cancel")
        with pytest.raises(asyncio.CancelledError):
            await worker.execute(claim)
        assert await store.get_summary_epoch("processor-cancel") == (1, 0)
    finally:
        await store.close()


_OWNER_CONTENT = (
    "项目使用 SQLite 存储会话记录，每周五发布一次版本，发布前必须跑完回归测试"
)
_OWNER_SCOPE_KEY = "group:group-dedup:topic-a"


class _Cursor:
    """返回固定行的最小游标替身。"""

    def __init__(self, rows: list[tuple]) -> None:
        """保存行副本。"""

        self._rows = rows

    async def fetchall(self) -> list[tuple]:
        """返回全部行。"""

        return list(self._rows)

    async def close(self) -> None:
        """关闭游标（无副作用）。"""

        return None


class _Connection:
    """记录 SQL 并返回固定 canonical 行的连接替身。"""

    def __init__(self, rows: list[tuple]) -> None:
        """保存行与查询记录。"""

        self.rows = rows
        self.calls: list[tuple[str, tuple | None]] = []

    async def execute(self, sql: str, params: tuple | None = None) -> _Cursor:
        """记录查询并返回全部行。"""

        self.calls.append((sql, params))
        return _Cursor(self.rows)


class _DedupEngine(_Engine):
    """在幂等写端口之外提供近重复合并端口的引擎替身。"""

    def __init__(self, owner_metadata: dict[str, Any]) -> None:
        """初始化既有 canonical 与查询连接。"""

        super().__init__()
        self.owner_metadata = owner_metadata
        self.owner_revision = 5.0
        self.db_connection = _Connection(
            [(900, _OWNER_CONTENT, json.dumps(owner_metadata))]
        )

    async def get_memory(self, memory_id: int) -> dict[str, Any] | None:
        """返回既有 canonical 记录。"""

        if memory_id != 900:
            return None
        return {
            "id": 900,
            "text": _OWNER_CONTENT,
            "metadata": dict(self.owner_metadata),
            "created_at": 1.0,
            "updated_at": self.owner_revision,
        }

    async def update_memory(
        self,
        memory_id: int,
        updates: dict[str, Any],
        expected_revision: str | None = None,
    ) -> bool:
        """按 revision 校验并原地应用合并增量。"""

        if memory_id != 900 or expected_revision != str(self.owner_revision):
            return False
        metadata = dict(self.owner_metadata)
        metadata.update(updates.get("metadata", {}))
        if "importance" in updates:
            metadata["importance"] = updates["importance"]
        self.owner_metadata = metadata
        self.owner_revision += 1.0
        return True


def _dedup_metadata(session_id: str) -> dict[str, Any]:
    """构造同 scope 的既有 canonical metadata。"""

    return {
        "scope_key": _OWNER_SCOPE_KEY,
        "privacy_level": "public",
        "chat_type": "group",
        "session_id": session_id,
        "persona_id": None,
        "participant_ids": ["user-1", "user-2"],
        "key_facts": ["项目使用 SQLite 存储会话记录", "每周五发布一次版本"],
        "status": "active",
        "importance": 0.3,
    }


def _dedup_candidate(content: str) -> dict[str, Any]:
    """构造带事实证据的近重复候选。"""

    candidate = _candidate(content)
    candidate["metadata"] = {
        "key_facts": ["项目使用 SQLite 存储会话记录"],
        "participant_ids": ["user-1"],
        "source_refs": [{"message_index": 0, "start": 0, "end": 3}],
    }
    return candidate


@pytest.mark.asyncio
async def test_worker_merges_near_duplicate_instead_of_inserting(
    tmp_db_path: str,
) -> None:
    """enforce 模式下同 scope 近重复候选只强化 owner 并正常推进窗口。"""

    store = ConversationStore(tmp_db_path)
    await store.initialize()
    engine = _DedupEngine(_dedup_metadata("dedup-session"))
    worker = SummaryWorker(
        cast(Any, store),
        cast(Any, _Processor([_dedup_candidate(_OWNER_CONTENT + "已")])),
        cast(Any, _Gate(["allow"])),
        cast(Any, engine),
        cast(Any, _BatchPreparer()),
        None,
        SimpleNamespace(
            get_config_snapshot=lambda: ({"memory_dedup": {"mode": "enforce"}}, "rev-1")
        ),
    )
    try:
        claim = await _claim(
            store,
            session_id="dedup-session",
            group_id="group-dedup",
            scope=(_OWNER_SCOPE_KEY, "public", "revision-1"),
        )
        outcome = await worker.execute(claim)
        committed = await store.commit_window(claim, outcome)

        assert engine.write_count == 0
        assert outcome.canonical_count == 1
        assert outcome.failed_count == 0
        assert outcome.unknown_count == 0
        assert outcome.can_advance is True
        assert committed.accepted is True
        assert engine.owner_metadata["merge_count"] == 1
        assert engine.owner_metadata["importance"] == pytest.approx(0.8)
        assert engine.owner_metadata["source_refs"] == [
            {"message_index": 0, "start": 0, "end": 3}
        ]
        assert engine.owner_metadata["merged_idempotency_keys"]
        sql, params = engine.db_connection.calls[0]
        assert "ORDER BY id DESC LIMIT ?" in sql
        assert params == ("dedup-session", 5)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_worker_writes_normally_when_dedup_disabled(tmp_db_path: str) -> None:
    """缺省配置（mode=off）不得发起近重复查询，窗口行为保持不变。"""

    store = ConversationStore(tmp_db_path)
    await store.initialize()
    engine = _DedupEngine(_dedup_metadata("plain-session"))
    worker = SummaryWorker(
        cast(Any, store),
        cast(Any, _Processor([_dedup_candidate(_OWNER_CONTENT + "已")])),
        cast(Any, _Gate(["allow"])),
        cast(Any, engine),
        cast(Any, _BatchPreparer()),
        None,
        SimpleNamespace(get_config_snapshot=lambda: ({}, "rev-1")),
    )
    try:
        claim = await _claim(
            store,
            session_id="plain-session",
            group_id="group-dedup",
            scope=(_OWNER_SCOPE_KEY, "public", "revision-1"),
        )
        outcome = await worker.execute(claim)

        assert engine.write_count == 1
        assert engine.db_connection.calls == []
        assert engine.owner_metadata.get("merge_count") is None
        assert outcome.canonical_count == 1
    finally:
        await store.close()
