"""质量 funnel 聚合器的日桶、可用性与坏载荷契约。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiosqlite
import pytest

from core.features.conversation.infrastructure.conversation_store import (
    ConversationStore,
)
from core.features.memory.infrastructure.dedup_metrics_store import (
    DedupMetricsStore,
)
from core.features.memory.infrastructure.topic_catalog_schema import (
    create_topic_catalog_schema,
)
from core.features.memory.infrastructure.topic_catalog_store import TopicCatalogStore
from core.features.memory.infrastructure.topic_metrics import (
    load_or_create_topic_metrics_key,
    rotate_topic_metrics_key,
)
from core.features.quality.application.quality_funnel import (
    DEDUP_COUNT_KEYS,
    FACT_COUNT_KEYS,
    FUNNEL_STAGES,
    STAGE_AVAILABLE,
    STAGE_DEGRADED,
    STAGE_UNAVAILABLE,
    QualityFunnelSources,
    build_trend,
    collect_quality_funnel,
)

# 2023-11-15T00:00:00Z：恰好落在 UTC 日边界，便于手算日索引。
NOW_MS = 1_700_006_400_000
NOW_SECONDS = NOW_MS / 1000.0
_DAY = 86_400.0


async def _catalog(tmp_path: Path) -> tuple[aiosqlite.Connection, TopicCatalogStore]:
    """创建只含 canonical 表与 topic catalog 的测试库。"""

    db = await aiosqlite.connect(tmp_path / "memora.db")
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


async def _seed_topic_windows(
    store: TopicCatalogStore,
    *,
    hash_key_version: int,
    entries: list[tuple[str, dict[str, int]]],
    mode: str = "top_k",
    start_index: int = 0,
) -> None:
    """按 (bucket_date, values) 写入窗口样本。"""

    for index, (bucket_date, values) in enumerate(entries, start=start_index):
        assert await store.record_metric_window(
            window_key_hash=f"{index + 1:064x}",
            hash_key_version=hash_key_version,
            terminal_state="success",
            token_source_available=False,
            scope_key_hash=f"{index + 100:064x}",
            bucket_date=bucket_date,
            mode=mode,
            topic_count_bucket="unknown",
            values=values,
            now=NOW_SECONDS,
        )


async def _summary_store(tmp_path: Path) -> ConversationStore:
    """创建真实 summary schema 的会话库。"""

    store = ConversationStore(str(tmp_path / "conversations.db"))
    await store.initialize()
    return store


async def _insert_job(
    connection: Any,
    *,
    index: int,
    updated_at: float,
    counts: dict[str, int],
) -> None:
    """直接写入一条未关联会话的终态任务行。"""

    await connection.execute(
        """
        INSERT INTO summary_jobs(
            job_id, session_id, session_epoch, start_seq, end_seq, expected_count,
            source_digest, triggered_by, status, reason_code, created_at, updated_at,
            canonical_count, merged_count, quarantine_count, discard_count,
            mark_write_count, failed_count, skipped_count, facts_rejected_count
        ) VALUES (?, ?, 1, 0, 10, 10, ?, 'auto', 'completed', 'completed', ?, ?,
                  ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"job-{index}",
            f"session-{index}",
            f"digest-{index}",
            updated_at,
            updated_at,
            counts.get("canonical_count", 0),
            counts.get("merged_count", 0),
            counts.get("quarantine_count", 0),
            counts.get("discard_count", 0),
            counts.get("mark_write_count", 0),
            counts.get("failed_count", 0),
            counts.get("skipped_count", 0),
            counts.get("facts_rejected_count", 0),
        ),
    )
    await connection.commit()


class _FakeInjectionStore:
    """固定注入摘要载荷；只暴露 ``summary`` 端口。"""

    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.calls = 0

    async def summary(self, window: str) -> Any:
        self.calls += 1
        return self.payload


class _BrokenStore:
    """任何读取都抛错的 Store 替身。"""

    async def summary(self, window: str) -> Any:
        raise RuntimeError("store offline")


class _BrokenConnection:
    """``execute`` 抛错的连接替身。"""

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("sqlite unavailable")


@pytest.mark.asyncio
async def test_day_bucketed_aggregation_matches_hand_computed_values(
    tmp_path: Path,
) -> None:
    """四个 stage 按 UTC 日聚合后的计数、比率与趋势等于手算值。"""

    key = load_or_create_topic_metrics_key(tmp_path)
    db, catalog = await _catalog(tmp_path)
    summary = await _summary_store(tmp_path)
    dedup = DedupMetricsStore(
        str(tmp_path / "dedup.sqlite3"), clock=lambda: NOW_SECONDS
    )
    await dedup.initialize()
    try:
        await _seed_topic_windows(
            catalog,
            hash_key_version=1,
            entries=[
                (
                    "2023-11-14",
                    {
                        "candidate_count_sum": 6,
                        "exact_reuse_count": 2,
                        "duplicate_topic_count": 1,
                        "identity_drop_count": 1,
                        "catalog_degraded_count": 1,
                    },
                ),
                (
                    "2023-11-14",
                    {
                        "candidate_count_sum": 4,
                        "exact_reuse_count": 1,
                        "duplicate_topic_count": 1,
                    },
                ),
                (
                    "2023-11-15",
                    {
                        "candidate_count_sum": 5,
                        "exact_reuse_count": 1,
                        "budget_exceeded_count": 2,
                    },
                ),
            ],
        )
        # mode='off' 的窗口样本不得进入候选分母。
        await _seed_topic_windows(
            catalog,
            hash_key_version=1,
            entries=[("2023-11-15", {"candidate_count_sum": 99})],
            mode="off",
            start_index=4,
        )
        await _insert_job(
            summary.connection,
            index=1,
            updated_at=NOW_SECONDS - _DAY,
            counts={
                "canonical_count": 2,
                "merged_count": 1,
                "quarantine_count": 1,
                "discard_count": 1,
                "skipped_count": 1,
                "facts_rejected_count": 3,
            },
        )
        await _insert_job(
            summary.connection,
            index=2,
            updated_at=NOW_SECONDS - _DAY + 60,
            counts={"canonical_count": 1, "facts_rejected_count": 1},
        )
        await _insert_job(
            summary.connection,
            index=3,
            updated_at=NOW_SECONDS,
            counts={
                "canonical_count": 3,
                "merged_count": 2,
                "failed_count": 1,
            },
        )
        for _ in range(4):
            await dedup.record("observe", "checked", now_ms=NOW_MS)
        for _ in range(2):
            await dedup.record("observe", "hit", now_ms=NOW_MS)
        await dedup.record("observe", "fact_overlap", now_ms=NOW_MS)
        for _ in range(2):
            await dedup.record("enforce", "checked", now_ms=NOW_MS)
        await dedup.record("enforce", "hit", now_ms=NOW_MS)
        await dedup.record("enforce", "merged", now_ms=NOW_MS)
        await dedup.record("enforce", "conflict", now_ms=NOW_MS)
        await dedup.record("enforce", "failed", now_ms=NOW_MS)
        injection = _FakeInjectionStore(
            {
                "decision_count": 10,
                "selected_count_total": 12,
                "dropped_count_total": 3,
                "truncated_count_total": 1,
                "memory_present_count": 7,
                "payload_injected_count": 6,
                "budget_utilization_avg": 0.42,
                "cost_trend": [
                    {
                        "bucket_ms": NOW_MS,
                        "decision_count": 10,
                        "selected_count_total": 12,
                        "dropped_count_total": 3,
                    }
                ],
                "scope_key": "canary-scope",
            }
        )
        stages = await collect_quality_funnel(
            QualityFunnelSources(
                topic_store=catalog,
                topic_key_state=SimpleNamespace(version=1, key=key),
                summary_connection=summary.connection,
                dedup_store=dedup,
                injection_store=injection,
            ),
            window="24h",
            now_ms=NOW_MS,
        )

        assert set(stages) == set(FUNNEL_STAGES)
        candidates = stages["candidates"]
        assert (candidates.state, candidates.reason) == (STAGE_AVAILABLE, "ok")
        assert dict(candidates.counts) == {
            "windows": 3,
            "candidates": 15,
            "exact_reuse": 4,
            "duplicate_topics": 2,
            "identity_drops": 1,
            "budget_exceeded": 2,
            "catalog_degraded": 1,
        }

        facts = stages["facts"]
        assert (facts.state, facts.reason) == (STAGE_AVAILABLE, "ok")
        assert dict(facts.counts) == {
            "windows": 3,
            "canonical": 6,
            "merged": 3,
            "quarantined": 1,
            "discarded": 1,
            "mark_write": 0,
            "failed": 1,
            "skipped": 1,
            "facts_rejected": 4,
        }
        assert set(facts.counts) == set(FACT_COUNT_KEYS)

        dedup_stage = stages["dedup"]
        assert (dedup_stage.state, dedup_stage.reason) == (STAGE_AVAILABLE, "ok")
        assert dict(dedup_stage.counts) == {
            "checked": 6,
            "hit": 3,
            "merged": 1,
            "fact_mismatch": 0,
            "fact_overlap": 1,
            "conflict": 1,
            "failed": 1,
        }
        assert set(dedup_stage.counts) == set(DEDUP_COUNT_KEYS)

        injection_stage = stages["injection"]
        assert (injection_stage.state, injection_stage.reason) == (
            STAGE_AVAILABLE,
            "ok",
        )
        assert dict(injection_stage.counts) == {
            "decisions": 10,
            "selected": 12,
            "dropped": 3,
            "truncated": 1,
            "memory_present": 7,
            "payload_injected": 6,
        }
        assert dict(injection_stage.values) == {"budget_utilization": 0.42}

        trend = build_trend(stages)
        assert [row["day"] for row in trend] == ["2023-11-14", "2023-11-15"]
        assert trend[0] == {
            "day": "2023-11-14",
            "candidates": 10,
            "canonical": 3,
            "merged": 1,
            "facts_rejected": 4,
            "dedup_checked": 0,
            "dedup_hit": 0,
            "decisions": 0,
            "selected": 0,
        }
        assert trend[1] == {
            "day": "2023-11-15",
            "candidates": 5,
            "canonical": 3,
            "merged": 2,
            "facts_rejected": 0,
            "dedup_checked": 6,
            "dedup_hit": 3,
            "decisions": 10,
            "selected": 12,
        }
        short = await collect_quality_funnel(
            QualityFunnelSources(
                topic_store=catalog,
                topic_key_state=SimpleNamespace(version=1, key=key),
                summary_connection=summary.connection,
                dedup_store=dedup,
                injection_store=injection,
            ),
            window="1h",
            now_ms=NOW_MS,
        )
        # 短窗口按「窗口触及的 UTC 日」聚合：topic 源数据只有日粒度，跨日边界时
        # 1h 窗口会带上前一日样本，而不是伪造小时级精度；facts/dedup/injection
        # 仍按毫秒窗口精确过滤。
        assert short["candidates"].counts["candidates"] == 15
        assert short["facts"].counts["canonical"] == 3
    finally:
        await dedup.close()
        await summary.close()
        await db.close()


@pytest.mark.asyncio
async def test_empty_window_stays_available_with_zero_counts(tmp_path: Path) -> None:
    """空窗口是可用零值，不得伪装成不可用或降级。"""

    key = load_or_create_topic_metrics_key(tmp_path)
    db, catalog = await _catalog(tmp_path)
    summary = await _summary_store(tmp_path)
    dedup = DedupMetricsStore(
        str(tmp_path / "dedup.sqlite3"), clock=lambda: NOW_SECONDS
    )
    await dedup.initialize()
    try:
        stages = await collect_quality_funnel(
            QualityFunnelSources(
                topic_store=catalog,
                topic_key_state=SimpleNamespace(version=1, key=key),
                summary_connection=summary.connection,
                dedup_store=dedup,
                injection_store=_FakeInjectionStore({}),
            ),
            window="24h",
            now_ms=NOW_MS,
        )
        for stage in FUNNEL_STAGES:
            read = stages[stage]
            assert read.state == STAGE_AVAILABLE, stage
            assert read.reason == "ok", stage
            assert all(value == 0 for value in read.counts.values()), stage
        assert build_trend(stages) == []
    finally:
        await dedup.close()
        await summary.close()
        await db.close()


@pytest.mark.asyncio
async def test_missing_hmac_key_is_unavailable_not_zero(tmp_path: Path) -> None:
    """key 缺失时候选 stage 必须不可用，且不返回零值计数。"""

    db, catalog = await _catalog(tmp_path)
    try:
        from core.features.memory.infrastructure.topic_metrics import (
            load_topic_metrics_key_state,
        )

        error: BaseException | None = None
        try:
            load_topic_metrics_key_state(tmp_path, create=False)
        except Exception as exc:  # noqa: BLE001 - 测试断言闭集 reason 映射
            error = exc
        assert error is not None
        stages = await collect_quality_funnel(
            QualityFunnelSources(
                topic_store=catalog,
                topic_key_state=None,
                topic_key_error=error,
            ),
            window="24h",
            now_ms=NOW_MS,
        )
        candidates = stages["candidates"]
        assert candidates.state == STAGE_UNAVAILABLE
        assert candidates.reason == "topic_metrics_key_missing"
        assert dict(candidates.counts) == {}
        assert build_trend(stages) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_key_rotation_gap_is_unavailable_not_zero(tmp_path: Path) -> None:
    """轮换后当前版本在窗口内没有样本时不得回落零值。"""

    key = load_or_create_topic_metrics_key(tmp_path)
    db, catalog = await _catalog(tmp_path)
    try:
        await _seed_topic_windows(
            catalog,
            hash_key_version=1,
            entries=[("2023-11-15", {"candidate_count_sum": 4})],
        )
        rotated = rotate_topic_metrics_key(tmp_path)
        assert rotated.version == 2
        assert rotated.key != key
        stages = await collect_quality_funnel(
            QualityFunnelSources(
                topic_store=catalog,
                topic_key_state=rotated,
                summary_connection=None,
                dedup_store=None,
                injection_store=None,
            ),
            window="24h",
            now_ms=NOW_MS,
        )
        candidates = stages["candidates"]
        assert (candidates.state, candidates.reason) == (
            STAGE_UNAVAILABLE,
            "topic_metrics_key_rotation_gap",
        )
        assert dict(candidates.counts) == {}
        # 其它 stage 缺失沿零值契约，但状态必须降级。
        assert stages["facts"].state == STAGE_UNAVAILABLE
        assert stages["dedup"].state == STAGE_DEGRADED
        assert stages["dedup"].reason == "dedup_store_missing"
        assert stages["injection"].state == STAGE_DEGRADED
        assert stages["injection"].reason == "injection_store_missing"
        assert set(stages["dedup"].counts) == set(DEDUP_COUNT_KEYS)
        assert build_trend(stages) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_store_failures_do_not_fake_zero_or_raise(tmp_path: Path) -> None:
    """Store 抛错时：总结不可用、去重/注入降级零值，且不冒泡异常。"""

    stages = await collect_quality_funnel(
        QualityFunnelSources(
            topic_store=_BrokenStore(),
            summary_connection=_BrokenConnection(),
            dedup_store=_BrokenStore(),
            injection_store=_BrokenStore(),
        ),
        window="24h",
        now_ms=NOW_MS,
    )
    assert stages["candidates"].state == STAGE_UNAVAILABLE
    assert stages["candidates"].reason == "topic_metrics_store_unavailable"
    assert stages["facts"].state == STAGE_UNAVAILABLE
    assert stages["facts"].reason == "summary_read_failed"
    assert stages["dedup"].state == STAGE_DEGRADED
    assert stages["dedup"].reason == "dedup_read_failed"
    assert stages["injection"].state == STAGE_DEGRADED
    assert stages["injection"].reason == "injection_read_failed"
    assert all(count == 0 for count in stages["dedup"].counts.values())


@pytest.mark.asyncio
async def test_malformed_store_payloads_are_normalized() -> None:
    """坏载荷只影响自身 stage：非法字段归零，未知字段被丢弃。"""

    class _GarbageDedupStore:
        async def summary(self, window: str) -> Any:
            return {
                "checked": "6",
                "hit": -3,
                "merged": True,
                "fact_mismatch": 1.5,
                "fact_overlap": 2,
                "conflict": None,
                "failed": 1,
                "scope_key": "canary-scope",
                "trend": [
                    {
                        "bucket_ms": NOW_MS,
                        "checked": 2,
                        "hit": 1,
                        "session_id": "canary",
                    },
                    {"bucket_ms": "bad", "checked": 9},
                    "not-a-row",
                ],
            }

    stages = await collect_quality_funnel(
        QualityFunnelSources(
            topic_store=object(),
            summary_connection=None,
            dedup_store=_GarbageDedupStore(),
            injection_store=_FakeInjectionStore(["not", "a", "mapping"]),
        ),
        window="24h",
        now_ms=NOW_MS,
    )
    dedup = stages["dedup"]
    assert dedup.state == STAGE_AVAILABLE
    assert dict(dedup.counts) == {
        "checked": 0,
        "hit": 0,
        "merged": 0,
        "fact_mismatch": 0,
        "fact_overlap": 2,
        "conflict": 0,
        "failed": 1,
    }
    assert dedup.trend == ({"day": "2023-11-15", "checked": 2, "hit": 1},)
    injection = stages["injection"]
    assert (injection.state, injection.reason) == (
        STAGE_DEGRADED,
        "injection_read_failed",
    )
    assert all(count == 0 for count in injection.counts.values())
    assert injection.values == {"budget_utilization": 0.0}


@pytest.mark.asyncio
async def test_unknown_window_returns_no_stages() -> None:
    """非法窗口不产生 stage 结果（由 API 层返回稳定错误码）。"""

    assert await collect_quality_funnel(QualityFunnelSources(), window="12h") == {}


@pytest.mark.asyncio
async def test_cancellation_propagates_from_store_ports() -> None:
    """取消信号必须传播，不能被收敛为降级结果。"""

    class _CancellingStore:
        async def summary(self, window: str) -> Any:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await collect_quality_funnel(
            QualityFunnelSources(dedup_store=_CancellingStore()),
            window="24h",
            now_ms=NOW_MS,
        )
