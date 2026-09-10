"""P1 指标 key、canonical 读取与候选观测契约测试。"""

from __future__ import annotations

import json
import stat
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import pytest

from core.features.memory.infrastructure.topic_catalog_schema import (
    create_topic_catalog_schema,
)
from core.features.memory.infrastructure.topic_catalog_store import TopicCatalogStore
from core.features.memory.infrastructure.topic_metrics import (
    load_topic_metrics_key_state,
    read_topic_metrics_summary,
    rotate_topic_metrics_key,
)
from core.features.recall.processors import reflection_generation_observability as topic_obs
from core.platform.transport.page_api import topic_segmentation_api
from core.platform.transport.page_api.topic_segmentation_api import (
    TopicSegmentationApiMixin,
)


async def _catalog(tmp_path: Path) -> tuple[aiosqlite.Connection, TopicCatalogStore]:
    """创建只含 canonical 表和 topic catalog 的测试库。"""

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


@pytest.mark.parametrize("contents", [b"bad", b"00" * 31])
def test_invalid_metric_key_fails_closed(tmp_path: Path, contents: bytes) -> None:
    """缺失或损坏的 key 不得静默生成替代 key。"""

    path = tmp_path / "topic_metrics.hmac.key"
    path.write_bytes(contents)
    path.chmod(0o600)
    with pytest.raises(RuntimeError, match="topic_metrics_key_invalid"):
        load_topic_metrics_key_state(tmp_path, create=True)


def test_metric_key_rotation_increments_version_and_preserves_permissions(
    tmp_path: Path,
) -> None:
    """显式轮换生成新 key 和新版本，不复用旧版本。"""

    from core.features.memory.infrastructure.topic_metrics import (
        load_or_create_topic_metrics_key,
    )

    first = load_or_create_topic_metrics_key(tmp_path)
    rotated = rotate_topic_metrics_key(tmp_path)
    assert rotated.version == 2
    assert rotated.key != first
    assert load_topic_metrics_key_state(tmp_path).version == 2
    assert stat.S_IMODE(
        (tmp_path / "topic_metrics.hmac.key").stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_metric_summary_uses_current_version_and_nearest_rank_p95(
    tmp_path: Path,
) -> None:
    """canonical 窗口样本按 UTC Unix 时间过滤并计算真实 nearest-rank P95。"""

    db, store = await _catalog(tmp_path)
    fixed_now = 1_800_000_000.0
    try:
        for index in range(20):
            observed = fixed_now - index * 60
            assert await store.record_metric_window(
                window_key_hash=f"{index + 1:064x}",
                hash_key_version=1,
                terminal_state="success",
                token_source_available=True,
                scope_key_hash="a" * 64,
                bucket_date=datetime.fromtimestamp(observed, timezone.utc)
                .date()
                .isoformat(),
                mode="top_k",
                topic_count_bucket="unknown",
                values={
                    "candidate_count_sum": index + 1,
                    "selector_duration_ms": index + 1,
                    "prompt_tokens": index + 1,
                },
                now=observed,
            )
        summary = await store.read_metric_summary(
            hash_key_version=1,
            since=fixed_now - 7 * 86400,
            until=fixed_now,
        )
        assert summary == {
            "p95_latency_ms": 19.0,
            "p95_candidates": 19.0,
            "p95_tokens": 19.0,
        }
        assert (
            await store.read_metric_summary(
                hash_key_version=2,
                since=fixed_now - 7 * 86400,
                until=fixed_now,
            )
            is None
        )
    finally:
        await db.close()


class _ApiStub(TopicSegmentationApiMixin):
    """只提供 topic metrics API 所需的插件字段。"""

    def __init__(self, store: TopicCatalogStore, data_dir: Path) -> None:
        self.plugin = SimpleNamespace(
            initializer=SimpleNamespace(data_dir=data_dir),
        )
        self._store = store


@pytest.mark.asyncio
async def test_topic_api_reads_canonical_store_with_utc_wall_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Page API 只读 canonical Store，不读 conversations.db 或单调时钟。"""

    db, store = await _catalog(tmp_path)
    fixed_now = 1_800_000_000.0
    try:
        for index in range(20):
            observed = fixed_now - index * 60
            assert await store.record_metric_window(
                window_key_hash=f"{index + 100:064x}",
                hash_key_version=1,
                terminal_state="success",
                token_source_available=False,
                scope_key_hash="b" * 64,
                bucket_date=datetime.fromtimestamp(observed, timezone.utc)
                .date()
                .isoformat(),
                mode="observe",
                topic_count_bucket="unknown",
                values={"candidate_count_sum": index + 1,
                        "selector_duration_ms": index + 1},
                now=observed,
            )
        from core.features.memory.infrastructure.topic_metrics import (
            load_or_create_topic_metrics_key,
        )

        load_or_create_topic_metrics_key(tmp_path)
        monkeypatch.setattr(topic_segmentation_api.time,
                            "time", lambda: fixed_now)
        api = _ApiStub(store, tmp_path)
        summary = await api._query_aggregated_metrics(
            {"memory_engine": SimpleNamespace(topic_catalog_store=store)}
        )
        assert summary == {
            "p95_latency_ms": 19.0,
            "p95_candidates": 19.0,
            "p95_tokens": None,
        }
    finally:
        await db.close()


def test_topic_event_accepts_only_typed_safe_scalars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """候选事件只发射闭集枚举和非负标量，拒绝敏感或恶意自由文本。"""

    events: list[dict[str, object]] = []
    monkeypatch.setattr(
        topic_obs,
        "report_debug_event",
        lambda event_name, **fields: events.append(
            {"event": event_name, **fields}),
    )
    assert topic_obs.report_topic_candidate_event(
        mode="top_k",
        effective_mode="top_k",
        catalog_status="ready",
        topic_count_bucket="17-32",
        reason_code="top_k_success",
        candidate_count=4,
        selector_duration_ms=2.5,
        token_source_available=False,
    )
    assert events and events[0]["event"] == "reflection_state"
    serialized = json.dumps(events, ensure_ascii=False)
    assert "scope" not in serialized
    assert "query" not in serialized
    assert not topic_obs.report_topic_candidate_event(
        mode="top_k",
        effective_mode="top_k",
        catalog_status="ready",
        topic_count_bucket="17-32",
        reason_code="top_k_success",
        query="不要进入事件",
    )
    assert len(events) == 1
    assert not topic_obs.report_topic_candidate_event(
        mode="arbitrary",
        effective_mode="top_k",
        catalog_status="ready",
        topic_count_bucket="17-32",
        reason_code="top_k_success",
    )
    assert len(events) == 1


def test_topic_metrics_reader_keeps_missing_key_unavailable(tmp_path: Path) -> None:
    """API 读路径缺 key 时保持 unavailable，不为读取动作创建 key。"""

    class _Store:
        async def read_metric_summary(self, **_kwargs: object) -> dict[str, float]:
            raise AssertionError("missing key must short-circuit")

    import asyncio

    assert asyncio.run(read_topic_metrics_summary(_Store(), tmp_path)) is None
    assert not (tmp_path / "topic_metrics.hmac.key").exists()


@pytest.mark.asyncio
async def test_startup_does_not_recreate_lost_key_over_metric_history(tmp_path: Path) -> None:
    """已有指标但 sidecar 丢失时停聚；不能新建版本一合并旧摘要。"""
    from core.features.memory.infrastructure.topic_metrics import build_metrics_recorder

    db, store = await _catalog(tmp_path)
    engine = SimpleNamespace(topic_catalog_store=store)
    try:
        assert await build_metrics_recorder(engine, tmp_path) is not None
        assert await store.record_metric_window(
            window_key_hash="a" * 64,
            hash_key_version=1,
            terminal_state="success",
            token_source_available=False,
            scope_key_hash="b" * 64,
            bucket_date="2026-09-08",
            mode="observe",
            topic_count_bucket="unknown",
            values={"selector_duration_ms": 10.0},
            now=1_800_000_000.0,
        )
        key_path = tmp_path / "topic_metrics.hmac.key"
        key_path.unlink()
        assert await build_metrics_recorder(engine, tmp_path) is None
        assert not key_path.exists()
        assert await read_topic_metrics_summary(store, tmp_path) is None
        assert await store.has_metric_history()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_rotation_during_metric_read_discards_previous_version(tmp_path: Path) -> None:
    """读取期间发生轮换时丢弃旧版本摘要，不把它当作当前数据返回。"""
    from core.features.memory.infrastructure.topic_metrics import load_or_create_topic_metrics_key

    load_or_create_topic_metrics_key(tmp_path)

    class RotatingStore:
        """在读事务异步边界模拟管理员轮换。"""

        async def read_metric_summary(self, **_kwargs):
            """旧版本查询结束前发布新 key。"""
            rotate_topic_metrics_key(tmp_path)
            return {"p95_latency_ms": 10.0}

    assert await read_topic_metrics_summary(RotatingStore(), tmp_path) is None
