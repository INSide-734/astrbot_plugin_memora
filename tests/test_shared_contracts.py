"""P1a shared contracts 的 fail-closed 契约测试。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, cast

import pytest

from core.models.derived_metadata import DerivedMetadataSourceRef
from core.shared.contracts import (
    CanonicalMemoryCommitted,
    MemorySourceRef,
    SourceReadDenyReason,
    SourceReadRequest,
    SourceReadResult,
    raise_if_cancelled,
    to_derived_metadata_source,
)


def _source(**overrides: object) -> MemorySourceRef:
    """构造一条最小合法 canonical 来源。"""

    values: dict[str, object] = {
        "memory_id": 7,
        "revision_token": "rev-7",
        "scope_key": "private:session-1",
        "privacy_level": "shared",
        "occurred_at": datetime(2026, 8, 9, tzinfo=timezone.utc),
        "stable_user_id": "user-1",
    }
    values.update(overrides)
    return cast(Any, MemorySourceRef)(**values)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("scope_key", "", SourceReadDenyReason.INVALID_REQUEST.value),
        ("privacy_clearance", "", SourceReadDenyReason.INVALID_REQUEST.value),
        ("stable_user_id", "", SourceReadDenyReason.INVALID_REQUEST.value),
        ("user_role", "member", SourceReadDenyReason.ROLE_DENIED.value),
        ("max_content_chars", 0, SourceReadDenyReason.CONTENT_LIMIT.value),
        (
            "expected_revisions",
            {True: "rev"},
            SourceReadDenyReason.REVISION_MISMATCH.value,
        ),
    ],
)
def test_source_request_rejects_missing_or_invalid_authorization(
    field: str, value: object, reason: str
) -> None:
    """缺失 scope、身份、角色、预算或 revision 时不得构造读取请求。"""

    values: dict[str, object] = {
        "scope_key": "private:session-1",
        "privacy_clearance": "shared",
        "stable_user_id": "user-1",
        "user_role": "user",
        "expected_revisions": {7: "rev-7"},
    }
    values[field] = value
    with pytest.raises(ValueError, match=reason):
        cast(Any, SourceReadRequest)(**values)


def test_denied_result_cannot_carry_source_body() -> None:
    """拒绝结果与正文互斥，避免错误 envelope 泄露 canonical 内容。"""

    with pytest.raises(ValueError, match="denied_result"):
        SourceReadResult(
            sources=(_source(content="secret canary"),),
            deny_reason=SourceReadDenyReason.PRIVACY_DENIED,
        )
    result = SourceReadResult.denied(SourceReadDenyReason.PRIVACY_DENIED)
    assert result.sources == ()
    assert result.allowed is False


def test_source_to_derived_metadata_preserves_scope_revision_role_and_stale() -> None:
    """派生 metadata 只能得到受限来源证据，不携带正文。"""

    source = _source(content="must stay local", source_role="supporting")
    derived = to_derived_metadata_source(source, stale=True)

    assert isinstance(derived, DerivedMetadataSourceRef)
    assert derived.memory_id == source.memory_id
    assert derived.revision_token == source.revision_token
    assert derived.trusted_scope == source.scope_key
    assert derived.privacy_level == source.privacy_level
    assert derived.source_role == source.source_role
    assert derived.stale is True
    assert not hasattr(derived, "content")


def test_committed_event_contains_digest_but_not_content() -> None:
    """canonical 提交事件只保存摘要和标量 allowlist。"""

    event = CanonicalMemoryCommitted(
        event_id="event-1",
        op_id="op-1",
        memory_id=7,
        revision="rev-7",
        scope_key="private:session-1",
        privacy="shared",
        stable_user_id="user-1",
        source_role="primary",
        changed_fields=("content", "metadata"),
        content_digest=CanonicalMemoryCommitted.digest_content("secret"),
        occurred_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
    )

    assert event.event_revision_key == "event-1:rev-7"
    assert len(event.content_digest) == 64
    assert not hasattr(event, "content")


@pytest.mark.asyncio
async def test_cancelled_task_is_not_converted_to_empty_success() -> None:
    """取消必须沿边界传播，不能被降级成空的成功读取。"""

    async def wait_for_cancel() -> None:
        await asyncio.sleep(60)

    task = asyncio.create_task(wait_for_cancel())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_explicit_cancel_token_is_propagated() -> None:
    """显式取消令牌触发稳定的 CancelledError。"""

    class _Token:
        def is_set(self) -> bool:
            return True

    with pytest.raises(asyncio.CancelledError):
        raise_if_cancelled(_Token())
