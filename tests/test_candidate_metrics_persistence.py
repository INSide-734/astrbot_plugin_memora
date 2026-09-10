"""候选指标 HMAC 记录端口契约测试（P0-3/P0-4）。

覆盖：
- 终态经 ``record_metric_window`` 写入成功（HMAC hash + 聚合行）；
- 同一窗口重复终态被 CAS 拒绝，聚合只计一次；
- DB 任何指标表不出现 scope 原文；
- key sidecar 一次性生成、复用一致、0o600 权限；
- 记录失败不向总结主链抛异常（fail-safe）。
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from core.features.memory.infrastructure.topic_metrics import (
    TopicCandidateMetricsRecorder,
    load_or_create_topic_metrics_key,
)
from core.features.memory.infrastructure.topic_catalog_schema import (
    create_topic_catalog_schema,
    topic_catalog_schema_is_valid,
)
from core.features.memory.infrastructure.topic_catalog_store import TopicCatalogStore
from core.features.reflection.domain.summary_models import (
    CandidateMetrics,
    ClaimedJob,
    SummaryJob,
    TopicCandidateMode,
)

_SCOPE_PLAINTEXT = "scope-plaintext-must-not-leak"
_FIXED_NOW = 1_800_000_000.0


def _make_claim() -> ClaimedJob:
    """构造带完整 scope 快照的 claim（scope 原文仅存在于内存）。"""
    job = SummaryJob(
        job_id="job-1",
        session_id="session-1",
        session_epoch=1,
        start_seq=0,
        end_seq=2,
        expected_count=2,
        source_digest="digest-x",
        scope_key=_SCOPE_PLAINTEXT,
        privacy_level="public",
        chat_type="private",
        resolver_revision="v1",
        scope_reason_code="scope_resolved",
        scope_provenance_complete=True,
    )
    return ClaimedJob(
        job=job,
        claim_token="token-1",
        scheduler_id="sched-1",
        lease_until=1.0,
        worker_generation=1,
    )


def _make_metrics() -> CandidateMetrics:
    """构造一份 top_k 终态指标 DTO。"""
    return CandidateMetrics(
        mode=TopicCandidateMode.TOP_K,
        effective_mode=TopicCandidateMode.TOP_K,
        n_candidates=5,
        n_with_provenance=3,
        n_tokens=None,
        selector_latency_ms=4.25,
        reason="top_k_success",
    )


async def _make_store(tmp_path: Path) -> tuple[aiosqlite.Connection, TopicCatalogStore]:
    """创建带 documents 表与 catalog schema 的内存态测试库。"""
    db = await aiosqlite.connect(tmp_path / "catalog.db")
    await db.execute(
        """
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY,
            text TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    await create_topic_catalog_schema(db)
    await db.commit()
    return db, TopicCatalogStore(db)


@pytest.mark.asyncio
async def test_new_schema_is_valid(tmp_path: Path) -> None:
    """新建库立即通过 catalog schema 校验（P0-3 契约）。"""
    db, _ = await _make_store(tmp_path)
    try:
        assert await topic_catalog_schema_is_valid(db)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_recorder_writes_terminal_metrics(tmp_path: Path) -> None:
    """终态经 record_metric_window 写入 HMAC hash 与聚合行。"""
    db, store = await _make_store(tmp_path)
    try:
        recorder = TopicCandidateMetricsRecorder(
            store, load_or_create_topic_metrics_key(tmp_path), now=lambda: _FIXED_NOW
        )
        assert await recorder.record_success(_make_claim(), _make_metrics()) is True

        window_row = await (
            await db.execute(
                "SELECT window_key_hash, hash_key_version, terminal_state "
                "FROM topic_candidate_metric_windows"
            )
        ).fetchone()
        assert window_row is not None
        assert len(window_row[0]) == 64
        assert window_row[1:] == (1, "success")

        agg_row = await (
            await db.execute(
                "SELECT mode, topic_count_bucket, window_count, candidate_count_sum "
                "FROM topic_candidate_scope_metrics"
            )
        ).fetchone()
        assert agg_row == ("top_k", "unknown", 1, 5)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_recorder_retry_counts_once(tmp_path: Path) -> None:
    """同一窗口重复终态被 CAS 拒绝，聚合不重复计数。"""
    db, store = await _make_store(tmp_path)
    try:
        recorder = TopicCandidateMetricsRecorder(
            store, load_or_create_topic_metrics_key(tmp_path), now=lambda: _FIXED_NOW
        )
        claim = _make_claim()
        assert await recorder.record_success(claim, _make_metrics()) is True
        assert await recorder.record_success(claim, _make_metrics()) is False
        row = await (
            await db.execute("SELECT window_count FROM topic_candidate_scope_metrics")
        ).fetchone()
        assert row == (1,)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_no_plaintext_scope_in_metrics_tables(tmp_path: Path) -> None:
    """两张指标表均不得出现 scope 原文（隐私红线）。"""
    db, store = await _make_store(tmp_path)
    try:
        recorder = TopicCandidateMetricsRecorder(
            store, load_or_create_topic_metrics_key(tmp_path), now=lambda: _FIXED_NOW
        )
        assert await recorder.record_success(_make_claim(), _make_metrics()) is True
        for table in (
            "topic_candidate_metric_windows",
            "topic_candidate_scope_metrics",
        ):
            rows = await (await db.execute(f"SELECT * FROM {table}")).fetchall()
            for row in rows:
                for cell in row:
                    assert not (isinstance(cell, str) and _SCOPE_PLAINTEXT in cell), (
                        f"{table} 出现 scope 原文"
                    )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_recorder_missing_scope_skips(tmp_path: Path) -> None:
    """scope 缺失的 claim 只记固定 reason，不写入任何指标行。"""
    db, store = await _make_store(tmp_path)
    try:
        recorder = TopicCandidateMetricsRecorder(
            store, load_or_create_topic_metrics_key(tmp_path), now=lambda: _FIXED_NOW
        )
        claim = _make_claim()
        object.__setattr__(
            claim.job, "scope_key", ""
        )  # slots dataclass 需绕过冻结写入空 scope
        assert await recorder.record_success(claim, _make_metrics()) is False
        count = await (
            await db.execute("SELECT COUNT(*) FROM topic_candidate_metric_windows")
        ).fetchone()
        assert count == (0,)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_recorder_store_failure_fail_safe(tmp_path: Path) -> None:
    """底层存储失败时不向总结主链抛异常。"""

    class _BrokenStore:
        """record_metric_window 恒抛错的替身。"""

        async def record_metric_window(self, *args: object, **kwargs: object) -> bool:
            raise RuntimeError("boom")

    recorder = TopicCandidateMetricsRecorder(
        _BrokenStore(),
        load_or_create_topic_metrics_key(tmp_path),
        now=lambda: _FIXED_NOW,
    )
    assert await recorder.record_success(_make_claim(), _make_metrics()) is False


def test_key_sidecar_reuse_and_permissions(tmp_path: Path) -> None:
    """key 一次性生成、重复加载一致、sidecar 权限 0o600。"""
    key1 = load_or_create_topic_metrics_key(tmp_path)
    key2 = load_or_create_topic_metrics_key(tmp_path)
    assert key1 == key2
    assert len(key1) == 32
    stat_mode = (tmp_path / "topic_metrics.hmac.key").stat().st_mode & 0o777
    assert stat_mode == 0o600
