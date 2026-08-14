"""隔离反馈事件 Store 的事务、幂等和重建契约。"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import pytest

import core.features.learning.infrastructure.feedback_signal_store as feedback_store_module
from core.features.learning.domain.models import (
    FeedbackAdapterKind,
    FeedbackOutcome,
    FeedbackSignalAggregate,
    TrustedFeedbackEvent,
    build_trusted_feedback_event,
)
from core.features.learning.infrastructure import FeedbackSignalStore

_WINDOWS_TEXT_SENSITIVE_KEY = b"A" * 8 + b"\r\n" + b"B" * 22


class _WindowsOsProxy:
    """模拟 Windows 文件 API、二进制标志与文本模式转换。"""

    name = "nt"
    O_BINARY = 0x8000

    def __init__(self) -> None:
        """记录以二进制模式打开的文件描述符。"""

        self._binary_descriptors: set[int] = set()

    def __getattr__(self, name: str):
        """转发通用 os API，并隐藏 Windows 不提供的 POSIX 能力。"""

        if name in {"fchmod", "O_NOFOLLOW", "O_DIRECTORY"}:
            raise AttributeError(name)
        return getattr(os, name)

    def urandom(self, size: int) -> bytes:
        """返回包含 CRLF 的确定性 32 字节密钥以覆盖文本转换风险。"""

        assert size == len(_WINDOWS_TEXT_SENSITIVE_KEY)
        return _WINDOWS_TEXT_SENSITIVE_KEY

    def open(self, path, flags: int) -> int:
        """模拟 Windows 不支持目录句柄，并记录 O_BINARY。"""

        if Path(path).is_dir():
            raise OSError("directory handles are unavailable")
        binary = bool(flags & self.O_BINARY)
        native_binary_flag = getattr(os, "O_BINARY", 0)
        file_descriptor = os.open(
            path,
            flags if native_binary_flag else flags & ~self.O_BINARY,
        )
        if binary:
            self._binary_descriptors.add(file_descriptor)
        return file_descriptor

    def read(self, file_descriptor: int, size: int) -> bytes:
        """二进制模式原样读取，否则模拟 Windows CRLF 文本转换。"""

        value = os.read(file_descriptor, size)
        if file_descriptor in self._binary_descriptors:
            return value
        return value.replace(b"\r\n", b"\n")

    def close(self, file_descriptor: int) -> None:
        """关闭文件描述符并清理二进制模式记录。"""

        self._binary_descriptors.discard(file_descriptor)
        os.close(file_descriptor)


def _event(
    decision: str = "decision-1",
    *,
    observed_at: datetime | None = None,
):
    """构造匿名可信事件。"""

    return build_trusted_feedback_event(
        adapter_kind=FeedbackAdapterKind.RETRIEVAL_RESULT,
        decision_key=decision,
        variant_key="document_route",
        outcome=FeedbackOutcome.POSITIVE,
        scope_domain="scope-synthetic",
        persona_domain=None,
        observed_at=observed_at or datetime(2026, 7, 21, 10, tzinfo=timezone.utc),
        window_seconds=3600,
    )


def test_store_deduplicates_and_persists_committed_events(tmp_path) -> None:
    """相同 dedupe key 重放只产生一条已提交事件。"""

    path = tmp_path / "feedback.db"
    store = FeedbackSignalStore(path)
    store.initialize()

    first = store.insert_events([_event()])
    replay = store.insert_events([_event()])
    summary = store.safe_summary()
    store.close()

    reopened = FeedbackSignalStore(path)
    reopened.initialize()
    try:
        assert first == {"accepted": 1, "duplicate_event": 0}
        assert replay == {"accepted": 0, "duplicate_event": 1}
        assert summary["event_count"] == 1
        assert len(reopened.list_events()) == 1
    finally:
        reopened.close()


def test_opaque_token_is_keyed_stable_across_restart_and_install_isolated(
    tmp_path,
) -> None:
    """token 必须跨重启稳定、跨安装不同，且不能退回固定 SHA-256。"""

    first_path = tmp_path / "first.db"
    first = FeedbackSignalStore(first_path)
    first.initialize()
    token = first.opaque_token("decision", "forget:7")
    first.close()
    key_path = Path(f"{first_path}.hmac.key")
    key_material = key_path.read_bytes()

    reopened = FeedbackSignalStore(first_path)
    reopened.initialize()
    restarted_token = reopened.opaque_token("decision", "forget:7")
    reopened.close()

    second = FeedbackSignalStore(tmp_path / "second.db")
    second.initialize()
    other_install_token = second.opaque_token("decision", "forget:7")
    second.close()

    legacy_digest = hashlib.sha256(b"memora-feedback-v1|decision|forget:7").hexdigest()
    assert token == restarted_token
    assert token != other_install_token
    assert token != f"decision:{legacy_digest}"
    assert len(key_material) == 32
    if os.name != "nt":
        assert key_path.stat().st_mode & 0o777 == 0o600
    assert key_material not in first_path.read_bytes()


def test_store_initializes_with_windows_file_api_semantics(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows 文本敏感密钥仍应保持 32 字节并跨重启稳定。"""

    monkeypatch.setattr(feedback_store_module, "os", _WindowsOsProxy())
    path = tmp_path / "windows-key.db"
    store = FeedbackSignalStore(path)
    store.initialize()
    token = store.opaque_token("decision", "forget:7")
    store.close()

    reopened = FeedbackSignalStore(path)
    reopened.initialize()
    try:
        assert reopened.opaque_token("decision", "forget:7") == token
    finally:
        reopened.close()


@pytest.mark.parametrize("corruption", [None, b"short", b"x" * 32])
def test_existing_store_rejects_missing_or_malformed_token_key(
    tmp_path,
    corruption: bytes | None,
) -> None:
    """已初始化 Store 的密钥缺失或损坏时必须拒绝静默轮换。"""

    path = tmp_path / "corrupt-key.db"
    store = FeedbackSignalStore(path)
    store.initialize()
    store.close()
    key_path = Path(f"{path}.hmac.key")
    if corruption is None:
        key_path.unlink()
    else:
        key_path.write_bytes(corruption)
        key_path.chmod(0o600)

    reopened = FeedbackSignalStore(path)
    with pytest.raises(RuntimeError, match="feedback_token_key_(missing|invalid)"):
        reopened.initialize()
    reopened.close()


@pytest.mark.skipif(os.name == "nt", reason="Windows 不提供 POSIX 文件权限位")
def test_existing_store_rejects_overly_permissive_token_key(tmp_path) -> None:
    """密钥文件出现 group/other 权限时不得继续使用。"""

    path = tmp_path / "permissive-key.db"
    store = FeedbackSignalStore(path)
    store.initialize()
    store.close()
    Path(f"{path}.hmac.key").chmod(0o644)

    reopened = FeedbackSignalStore(path)
    with pytest.raises(RuntimeError, match="feedback_token_key_invalid"):
        reopened.initialize()
    reopened.close()


def test_store_rolls_back_partial_batch_on_non_sql_error(tmp_path) -> None:
    """批次中途异常时事务必须回滚，不保留半批事件。"""

    store = FeedbackSignalStore(tmp_path / "rollback.db")
    store.initialize()

    with pytest.raises(AttributeError):
        store.insert_events(
            [_event("decision-a"), cast(TrustedFeedbackEvent, object())]
        )

    try:
        assert store.safe_summary()["event_count"] == 0
    finally:
        store.close()


def test_store_replaces_and_rebuilds_aggregates_without_event_loss(tmp_path) -> None:
    """删除 aggregate 后可从保留事件重建，事件计数保持不变。"""

    store = FeedbackSignalStore(tmp_path / "aggregate.db")
    store.initialize()
    store.insert_events([_event()])
    aggregate = FeedbackSignalAggregate(
        scope_domain="scope-synthetic",
        persona_domain=None,
        window_start=datetime(2026, 7, 21, 10, tzinfo=timezone.utc),
        window_end=datetime(2026, 7, 21, 11, tzinfo=timezone.utc),
        accepted_count=1,
        independent_window_count=1,
        decayed_support=1.0,
        proposed_document_weight=0.7,
        proposed_graph_weight=0.3,
        delta_from_baseline=0.0,
        status="baseline_retained",
        policy_version=1,
    )
    store.replace_aggregates([aggregate])
    before = store.safe_summary()
    store.replace_aggregates([])
    after = store.safe_summary()

    try:
        assert before == {"event_count": 1, "aggregate_count": 1}
        assert after == {"event_count": 1, "aggregate_count": 0}
    finally:
        store.close()


def test_store_prunes_only_events_before_cutoff(tmp_path) -> None:
    """保留期清理只删除 cutoff 之前的事件并保留边界后的事件。"""

    store = FeedbackSignalStore(tmp_path / "retention.db")
    store.initialize()
    store.insert_events(
        [
            _event(
                "expired-decision",
                observed_at=datetime(2026, 7, 21, 10, tzinfo=timezone.utc),
            ),
            _event(
                "current-decision",
                observed_at=datetime(2026, 7, 21, 12, tzinfo=timezone.utc),
            ),
        ]
    )

    deleted = store.delete_events_before(datetime(2026, 7, 21, 11, tzinfo=timezone.utc))

    try:
        assert deleted == 1
        remaining = store.list_events()
        assert [item.decision_key for item in remaining] == ["current-decision"]
    finally:
        store.close()


def test_store_revokes_only_exact_anonymous_decision_domain(tmp_path) -> None:
    """决策撤销必须同时匹配适配器、variant、scope 和 persona。"""

    store = FeedbackSignalStore(tmp_path / "revoke.db")
    store.initialize()
    store.insert_events([_event("decision-a"), _event("decision-b")])

    deleted = store.delete_decision_events(
        adapter_kind=FeedbackAdapterKind.RETRIEVAL_RESULT,
        decision_key="decision-a",
        variant_key="document_route",
        scope_domain="scope-synthetic",
        persona_domain=None,
    )

    try:
        assert deleted == 1
        remaining = store.list_events()
        assert [item.decision_key for item in remaining] == ["decision-b"]
    finally:
        store.close()


def test_transactional_revoke_rolls_back_callback_failure(tmp_path) -> None:
    """聚合构建失败时撤销、保留期清理和旧聚合必须一起回滚。"""

    store = FeedbackSignalStore(tmp_path / "revoke-rollback.db")
    store.initialize()
    event = _event("decision-a")
    store.insert_events([event])
    aggregate = FeedbackSignalAggregate(
        scope_domain="scope-synthetic",
        persona_domain=None,
        window_start=datetime(2026, 7, 21, 10, tzinfo=timezone.utc),
        window_end=datetime(2026, 7, 21, 11, tzinfo=timezone.utc),
        accepted_count=1,
        independent_window_count=1,
        decayed_support=1.0,
        proposed_document_weight=0.7,
        proposed_graph_weight=0.3,
        delta_from_baseline=0.0,
        status="baseline_retained",
        policy_version=1,
    )
    store.replace_aggregates([aggregate])

    def fail_build(_events):
        raise RuntimeError("injected_rebuild_failure")

    with pytest.raises(RuntimeError, match="injected_rebuild_failure"):
        store.revoke_and_replace_aggregates(
            adapter_kind=FeedbackAdapterKind.RETRIEVAL_RESULT,
            decision_key="decision-a",
            variant_key="document_route",
            scope_domain="scope-synthetic",
            persona_domain=None,
            retention_cutoff=datetime(2026, 7, 20, tzinfo=timezone.utc),
            aggregate_builder=fail_build,
        )

    try:
        assert [item.decision_key for item in store.list_events()] == ["decision-a"]
        assert store.safe_summary() == {"event_count": 1, "aggregate_count": 1}
    finally:
        store.close()


def test_safe_summary_omits_event_and_domain_canaries(tmp_path) -> None:
    """Store 状态不得暴露 decision、domain 或 dedupe 原值。"""

    store = FeedbackSignalStore(tmp_path / "safe.db")
    store.initialize()
    event = build_trusted_feedback_event(
        adapter_kind=FeedbackAdapterKind.RETRIEVAL_RESULT,
        decision_key="DECISION-SECRET-CANARY",
        variant_key="document_route",
        outcome=FeedbackOutcome.POSITIVE,
        scope_domain="SCOPE-SECRET-CANARY",
        persona_domain="PERSONA-SECRET-CANARY",
        observed_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
        window_seconds=3600,
    )
    store.insert_events([event])
    serialized = json.dumps(store.safe_summary())

    try:
        for canary in (
            "DECISION-SECRET-CANARY",
            "SCOPE-SECRET-CANARY",
            "PERSONA-SECRET-CANARY",
            event.dedupe_key,
        ):
            assert canary not in serialized
    finally:
        store.close()
