"""来源可重放状态评估（O1 方向 4）行为测试。

覆盖完整引用、部分缺失、全部缺失、Store 缺失/异常、格式不明、跨会话，以及
``/new``、TTL、trim 三条清理路径后的判定，并断言投影只含聚合状态。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from core.features.conversation.infrastructure.conversation_store import (
    ConversationStore,
)
from core.features.memory.application.source_replayability import (
    STATUS_PARTIAL,
    STATUS_REPLAYABLE,
    STATUS_UNAVAILABLE,
    STATUS_UNKNOWN,
    SourceReplayabilityAssessor,
)
from core.shared.contracts.conversation import (
    Message,
    message_evidence_fingerprint,
)

SESSION = "sess-source"
USER_TEXT = "第一段用户来源"
ASSISTANT_TEXT = "第二段助手来源"


def _message(content: str, role: str = "user", session_id: str = SESSION) -> Message:
    return Message(
        id=0,
        session_id=session_id,
        role=role,
        content=content,
        sender_id="u-1",
        sender_name="Tester",
        platform="qq",
    )


def _reference(
    message_id: int,
    message_seq: int,
    role: str,
    content: str,
    *,
    end: int | None = None,
) -> dict[str, Any]:
    return {
        "message_index": message_seq - 1,
        "message_id": message_id,
        "message_seq": message_seq,
        "role": role,
        "start": 0,
        "end": len(content) if end is None else end,
        "message_fingerprint": message_evidence_fingerprint(role, content),
        "inferred": False,
    }


async def _populated_store(tmp_db_path: str) -> tuple[ConversationStore, int, int]:
    store = ConversationStore(tmp_db_path)
    await store.initialize()
    first = await store.add_message(_message(USER_TEXT))
    second = await store.add_message(_message(ASSISTANT_TEXT, role="assistant"))
    return store, first, second


def _metadata(*references: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "session_id": SESSION,
        "source_epoch": 1,
        "source_evidence": list(references),
        **extra,
    }


class TestReplayabilityStatuses:
    """四种状态只由当前消息事实与引用格式决定。"""

    @pytest.mark.asyncio
    async def test_complete_references_are_replayable(self, tmp_db_path):
        store, first, second = await _populated_store(tmp_db_path)
        try:
            metadata = _metadata(
                _reference(first, 1, "user", USER_TEXT),
                _reference(second, 2, "assistant", ASSISTANT_TEXT),
                fact_source_evidence=[[_reference(first, 1, "user", USER_TEXT)]],
            )
            result = await SourceReplayabilityAssessor(store).assess(metadata)
        finally:
            await store.close()

        assert result.status == STATUS_REPLAYABLE
        assert (result.total, result.verified, result.absent) == (3, 3, 0)
        assert result.unverifiable == 0
        assert result.reason_codes == ("source_replayable",)

    @pytest.mark.asyncio
    async def test_trim_of_one_message_is_partial(self, tmp_db_path):
        store, first, second = await _populated_store(tmp_db_path)
        try:
            conn = store.connection
            assert conn is not None
            await conn.execute(
                """
                INSERT INTO session_epochs(session_id, epoch, cursor_seq, updated_at)
                VALUES (?, 1, 1, 0)
                ON CONFLICT(session_id) DO UPDATE SET cursor_seq = 1
                """,
                (SESSION,),
            )
            await conn.commit()
            trimmed = await store.trim_if_safe(SESSION, 1, 1)
            result = await SourceReplayabilityAssessor(store).assess(
                _metadata(
                    _reference(first, 1, "user", USER_TEXT),
                    _reference(second, 2, "assistant", ASSISTANT_TEXT),
                )
            )
        finally:
            await store.close()

        assert trimmed.deleted_count == 1
        assert result.status == STATUS_PARTIAL
        assert (result.total, result.verified, result.absent) == (2, 1, 1)
        assert result.reason_codes == ("source_partial",)

    @pytest.mark.asyncio
    async def test_all_references_removed_within_epoch_is_unavailable(
        self, tmp_db_path
    ):
        store, first, second = await _populated_store(tmp_db_path)
        try:
            conn = store.connection
            assert conn is not None
            await conn.execute("DELETE FROM messages WHERE session_id=?", (SESSION,))
            await conn.commit()
            result = await SourceReplayabilityAssessor(store).assess(
                _metadata(
                    _reference(first, 1, "user", USER_TEXT),
                    _reference(second, 2, "assistant", ASSISTANT_TEXT),
                )
            )
        finally:
            await store.close()

        assert result.status == STATUS_UNAVAILABLE
        assert (result.total, result.verified, result.absent) == (2, 0, 2)
        assert result.reason_codes == ("source_unavailable",)

    @pytest.mark.asyncio
    async def test_new_rotation_reports_epoch_mismatch(self, tmp_db_path):
        store, first, _second = await _populated_store(tmp_db_path)
        try:
            deleted = await store.clear_session_atomically(SESSION)
            result = await SourceReplayabilityAssessor(store).assess(
                _metadata(_reference(first, 1, "user", USER_TEXT))
            )
        finally:
            await store.close()

        assert deleted == 2
        assert result.status == STATUS_UNAVAILABLE
        assert result.reason_codes == ("source_epoch_mismatch",)

    @pytest.mark.asyncio
    async def test_ttl_rotation_reports_epoch_mismatch(self, tmp_db_path):
        store, first, _second = await _populated_store(tmp_db_path)
        try:
            conn = store.connection
            assert conn is not None
            await conn.execute(
                "UPDATE sessions SET last_active_at=0 WHERE session_id=?",
                (SESSION,),
            )
            await conn.commit()
            deleted = await store.delete_old_sessions(ttl_seconds=1)
            result = await SourceReplayabilityAssessor(store).assess(
                _metadata(_reference(first, 1, "user", USER_TEXT))
            )
        finally:
            await store.close()

        assert deleted == 1
        assert result.status == STATUS_UNAVAILABLE
        assert result.reason_codes == ("source_epoch_mismatch",)

    @pytest.mark.asyncio
    async def test_missing_store_is_unknown(self):
        result = await SourceReplayabilityAssessor(None).assess(
            _metadata(_reference(1, 1, "user", USER_TEXT))
        )

        assert result.status == STATUS_UNKNOWN
        assert result.unverifiable == 1
        assert result.reason_codes == ("source_store_unavailable",)

    @pytest.mark.asyncio
    async def test_store_error_is_unknown(self):
        class BrokenStore:
            async def get_message_identity_rows(self, message_ids):
                raise RuntimeError("db locked")

            async def get_summary_epoch(self, session_id):
                raise RuntimeError("db locked")

        result = await SourceReplayabilityAssessor(BrokenStore()).assess(
            _metadata(_reference(1, 1, "user", USER_TEXT))
        )

        assert result.status == STATUS_UNKNOWN
        assert (result.verified, result.absent) == (0, 0)
        assert result.unverifiable == 1
        assert result.reason_codes == ("source_store_error",)

    @pytest.mark.asyncio
    async def test_unresolved_reference_is_unknown_not_replayable(self, tmp_db_path):
        store, _first, _second = await _populated_store(tmp_db_path)
        try:
            malformed = {
                "message_index": 0,
                "message_id": 1,
                "message_seq": 1,
                "role": "user",
                "start": 0,
                "end": 3,
                "message_fingerprint": "not-a-sha256",
                "inferred": False,
            }
            result = await SourceReplayabilityAssessor(store).assess(
                _metadata(malformed)
            )
        finally:
            await store.close()

        assert result.status == STATUS_UNKNOWN
        assert (result.total, result.verified, result.unverifiable) == (1, 0, 1)

    @pytest.mark.asyncio
    async def test_cross_session_reference_is_not_replayable(self, tmp_db_path):
        store = ConversationStore(tmp_db_path)
        await store.initialize()
        try:
            other_id = await store.add_message(
                _message(USER_TEXT, session_id="sess-other")
            )
            result = await SourceReplayabilityAssessor(store).assess(
                _metadata(_reference(other_id, 1, "user", USER_TEXT))
            )
        finally:
            await store.close()

        assert result.status == STATUS_UNAVAILABLE
        assert (result.total, result.verified, result.absent) == (1, 0, 1)

    @pytest.mark.asyncio
    async def test_missing_session_identity_is_unknown(self, tmp_db_path):
        store, first, _second = await _populated_store(tmp_db_path)
        try:
            metadata = {
                "source_evidence": [_reference(first, 1, "user", USER_TEXT)],
            }
            result = await SourceReplayabilityAssessor(store).assess(metadata)
        finally:
            await store.close()

        assert result.status == STATUS_UNKNOWN
        assert result.reason_codes == ("source_session_unavailable",)

    @pytest.mark.asyncio
    async def test_no_recorded_evidence_is_unavailable(self, tmp_db_path):
        store, _first, _second = await _populated_store(tmp_db_path)
        try:
            result = await SourceReplayabilityAssessor(store).assess(
                {"session_id": SESSION, "key_facts": ["无证据旧记录"]}
            )
        finally:
            await store.close()

        assert result.status == STATUS_UNAVAILABLE
        assert result.total == 0
        assert result.reason_codes == ("source_not_recorded",)

    @pytest.mark.asyncio
    async def test_missing_epoch_provenance_is_unknown(self, tmp_db_path):
        store, first, _second = await _populated_store(tmp_db_path)
        try:
            metadata = {
                "session_id": SESSION,
                "source_evidence": [_reference(first, 1, "user", USER_TEXT)],
            }
            result = await SourceReplayabilityAssessor(store).assess(metadata)
        finally:
            await store.close()

        assert result.status == STATUS_UNKNOWN
        assert result.reason_codes == ("source_epoch_unrecorded",)

    @pytest.mark.asyncio
    async def test_unreadable_current_epoch_is_unknown(self, tmp_db_path):
        store, first, second = await _populated_store(tmp_db_path)
        try:
            rows = await store.get_message_identity_rows([first, second])
        finally:
            await store.close()

        class EpochlessStore:
            async def get_message_identity_rows(self, message_ids):
                return rows

            async def get_summary_epoch(self, session_id):
                raise RuntimeError("epoch unavailable")

        result = await SourceReplayabilityAssessor(EpochlessStore()).assess(
            _metadata(
                _reference(first, rows[first]["message_seq"], "user", USER_TEXT),
                _reference(
                    second,
                    rows[second]["message_seq"],
                    "assistant",
                    ASSISTANT_TEXT,
                ),
            )
        )

        assert result.status == STATUS_UNKNOWN
        assert result.reason_codes == ("source_epoch_unavailable",)

    @pytest.mark.asyncio
    async def test_invalid_window_bounds_are_unknown(self, tmp_db_path):
        store, first, _second = await _populated_store(tmp_db_path)
        try:
            metadata = {
                "session_id": SESSION,
                "source_window": {"session_epoch": 1, "start_seq": "bad"},
                "source_evidence": [_reference(first, 1, "user", USER_TEXT)],
            }
            result = await SourceReplayabilityAssessor(store).assess(metadata)
        finally:
            await store.close()

        assert result.status == STATUS_UNKNOWN
        assert result.reason_codes == ("source_window_invalid",)

    @pytest.mark.asyncio
    async def test_window_epoch_fallback_and_boundaries(self, tmp_db_path):
        store, first, second = await _populated_store(tmp_db_path)
        try:
            inside = await SourceReplayabilityAssessor(store).assess(
                {
                    "session_id": SESSION,
                    "source_window": {
                        "session_epoch": 1,
                        "start_seq": 0,
                        "end_seq": 2,
                    },
                    "source_evidence": [
                        _reference(first, 1, "user", USER_TEXT),
                        _reference(second, 2, "assistant", ASSISTANT_TEXT),
                    ],
                }
            )
            outside = await SourceReplayabilityAssessor(store).assess(
                {
                    "session_id": SESSION,
                    "source_window": {
                        "session_epoch": 1,
                        "start_seq": 0,
                        "end_seq": 1,
                    },
                    "source_evidence": [
                        _reference(second, 2, "assistant", ASSISTANT_TEXT),
                    ],
                }
            )
        finally:
            await store.close()

        assert inside.status == STATUS_REPLAYABLE
        assert outside.status == STATUS_UNAVAILABLE
        assert outside.absent == 1

    @pytest.mark.asyncio
    async def test_top_level_source_bounds_are_enforced(self, tmp_db_path):
        store, first, second = await _populated_store(tmp_db_path)
        try:
            inside = await SourceReplayabilityAssessor(store).assess(
                {
                    "session_id": SESSION,
                    "source_epoch": 1,
                    "source_start_seq": 0,
                    "source_end_seq": 2,
                    "source_evidence": [
                        _reference(second, 2, "assistant", ASSISTANT_TEXT),
                    ],
                }
            )
            outside = await SourceReplayabilityAssessor(store).assess(
                {
                    "session_id": SESSION,
                    "source_epoch": 1,
                    "source_start_seq": 0,
                    "source_end_seq": 1,
                    "source_evidence": [
                        _reference(second, 2, "assistant", ASSISTANT_TEXT),
                    ],
                }
            )
        finally:
            await store.close()

        assert inside.status == STATUS_REPLAYABLE
        assert outside.status == STATUS_UNAVAILABLE
        assert outside.absent == 1

    @pytest.mark.asyncio
    async def test_conflicting_window_bounds_are_unknown(self, tmp_db_path):
        store, first, _second = await _populated_store(tmp_db_path)
        try:
            result = await SourceReplayabilityAssessor(store).assess(
                {
                    "session_id": SESSION,
                    "source_epoch": 1,
                    "source_start_seq": 0,
                    "source_end_seq": 1,
                    "source_window": {
                        "session_epoch": 1,
                        "start_seq": 0,
                        "end_seq": 2,
                    },
                    "source_evidence": [
                        _reference(first, 1, "user", USER_TEXT),
                    ],
                }
            )
        finally:
            await store.close()

        assert result.status == STATUS_UNKNOWN
        assert result.reason_codes == ("source_window_invalid",)

    @pytest.mark.asyncio
    async def test_source_window_epoch_rotates_with_new(self, tmp_db_path):
        store, first, _second = await _populated_store(tmp_db_path)
        try:
            await store.clear_session_atomically(SESSION)
            metadata = {
                "session_id": SESSION,
                "source_window": {
                    "session_epoch": 1,
                    "start_seq": 0,
                    "end_seq": 1,
                },
                "source_evidence": [_reference(first, 1, "user", USER_TEXT)],
            }
            result = await SourceReplayabilityAssessor(store).assess(metadata)
        finally:
            await store.close()

        assert result.status == STATUS_UNAVAILABLE
        assert result.reason_codes == ("source_epoch_mismatch",)


class TestReplayabilityProjection:
    """投影只允许聚合状态、计数与固定原因码。"""

    @pytest.mark.asyncio
    async def test_payload_exposes_only_aggregate_status(self, tmp_db_path):
        store, first, _second = await _populated_store(tmp_db_path)
        try:
            reference = _reference(first, 1, "user", USER_TEXT)
            result = await SourceReplayabilityAssessor(store).assess(
                _metadata(reference)
            )
        finally:
            await store.close()

        payload = result.to_payload()
        assert set(payload) == {"status", "references", "reason_codes"}
        assert set(payload["references"]) == {
            "total",
            "verified",
            "absent",
            "unverifiable",
        }
        assert payload["status"] == STATUS_REPLAYABLE
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        for secret in (
            SESSION,
            USER_TEXT,
            reference["message_fingerprint"],
            "message_seq",
            "scope_key",
            "privacy_level",
            "revision",
        ):
            assert secret not in serialized


class TestMessageIdentityReadPort:
    """只读身份查询：忽略非法 ID，连接缺失时显式失败。"""

    @pytest.mark.asyncio
    async def test_ignores_invalid_ids_and_reads_rows(self, tmp_db_path):
        store, first, _second = await _populated_store(tmp_db_path)
        try:
            ids: list[Any] = [True, 0, -1, "1", first, first, 999]
            rows = await store.get_message_identity_rows(ids)
        finally:
            await store.close()

        assert set(rows) == {first}
        assert rows[first]["session_id"] == SESSION
        assert rows[first]["role"] == "user"
        assert rows[first]["content"] == USER_TEXT
        assert rows[first]["message_seq"] == 1

    @pytest.mark.asyncio
    async def test_uninitialized_connection_raises(self, tmp_db_path):
        store = ConversationStore(tmp_db_path)
        with pytest.raises(RuntimeError):
            await store.get_message_identity_rows([1])
