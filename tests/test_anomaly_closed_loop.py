"""LIFE-03 异常检测全链路闭环：装配、日聚合、幂等、告警事件与健康可见性。"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from core.features.decay.application import DecayScheduler
from core.features.diagnostics.application.health_scorer import HealthScorer
from core.features.memory.application.anomaly_detector import AnomalyDetector
from core.features.memory.application.memory_engine import MemoryEngine


def _day_ts(days_ago: int = 0) -> int:
    """返回 N 天前的 UTC 零点时间戳。"""

    return (int(time.time()) // 86400 - days_ago) * 86400


def _engine_config(
    data_dir: str,
    *,
    enabled: bool,
    window_days: int = 7,
    sigma_threshold: float = 3.0,
) -> dict[str, object]:
    """构造最小真实引擎配置。"""

    return {
        "graph_memory_enabled": False,
        "recall_engine.stopwords_path": "",
        "write_reliability.repair_enabled": False,
        "user_profile.enabled": False,
        "auto_learning.enabled": False,
        "knowledge_base.enabled": False,
        "notes.enabled": False,
        "reranker.enabled": False,
        "export.enabled": False,
        "continuity_tracking.enabled": False,
        "anomaly_detection.enabled": enabled,
        "anomaly_detection.window_days": window_days,
        "anomaly_detection.sigma_threshold": sigma_threshold,
        "data_dir": data_dir,
    }


@pytest.mark.asyncio
async def test_real_engine_builds_anomaly_detector_from_runtime_config(
    tmp_path: Path,
) -> None:
    """真实引擎应按运行时配置构造检测器，并在关闭时同步保存状态。"""

    engine = MemoryEngine(
        db_path=str(tmp_path / "memora.db"),
        faiss_db=MagicMock(),
        config=_engine_config(
            str(tmp_path),
            enabled=True,
            window_days=9,
            sigma_threshold=2.5,
        ),
    )
    engine._schema.create_tables = AsyncMock()
    try:
        with patch(
            "core.features.memory.application.memory_engine_lifecycle.BM25Retriever"
        ) as bm25_cls:
            bm25_cls.return_value.initialize = AsyncMock()
            await engine.initialize()

        detector = engine.anomaly_detector
        assert isinstance(detector, AnomalyDetector)
        assert detector.window_days == 9
        assert detector._sigma_threshold == pytest.approx(2.5)
    finally:
        await engine.close()

    assert (tmp_path / "anomaly_state.json").exists()


@pytest.mark.asyncio
async def test_disabled_anomaly_never_feeds_or_writes_state(tmp_path: Path) -> None:
    """关闭异常检测时不得创建检测器、不得执行日聚合、不得写状态或事件。"""

    engine = MemoryEngine(
        db_path=str(tmp_path / "memora.db"),
        faiss_db=MagicMock(),
        config=_engine_config(str(tmp_path), enabled=False),
    )
    engine._schema.create_tables = AsyncMock()
    try:
        with patch(
            "core.features.memory.application.memory_engine_lifecycle.BM25Retriever"
        ) as bm25_cls:
            bm25_cls.return_value.initialize = AsyncMock()
            await engine.initialize()
        assert engine.anomaly_detector is None

        scheduler = DecayScheduler(
            memory_engine=engine,
            decay_rate=0.0,
            data_dir=str(tmp_path),
        )
        await scheduler._run_anomaly_feed()
    finally:
        await engine.close()

    assert not (tmp_path / "anomaly_state.json").exists()
    assert not (tmp_path / "diagnostics_events.db").exists()


@pytest.mark.asyncio
async def test_daily_feed_is_idempotent_and_emits_sanitized_event(
    tmp_path: Path,
) -> None:
    """同一天重复调度只投喂一次，spike 告警写入脱敏诊断事件。"""

    detector = AnomalyDetector(data_dir=str(tmp_path), window_days=14)
    engine = SimpleNamespace(
        anomaly_detector=detector,
    )
    engine.count_canonical_created_on = AsyncMock(
        side_effect=lambda day_ts: (
            500 if day_ts == _day_ts(0) else (99 if (day_ts // 86400) % 2 == 0 else 101)
        )
    )
    scheduler = DecayScheduler(
        memory_engine=engine,
        decay_rate=0.0,
        data_dir=str(tmp_path),
    )

    await scheduler._run_anomaly_feed()
    first_calls = engine.count_canonical_created_on.await_count
    await scheduler._run_anomaly_feed()

    assert engine.count_canonical_created_on.await_count == first_calls
    assert len(detector._window) == 14

    from core.features.diagnostics.infrastructure.event_store import (
        DiagnosticEventStore,
    )

    store = DiagnosticEventStore(tmp_path / "diagnostics_events.db")
    await store.initialize()
    events = await store.list_events()
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["reason_code"] == "memory_rate_anomaly"
    assert payload["direction"] == "spike"
    assert payload["count"] == 500


@pytest.mark.asyncio
async def test_sqlite_count_aggregates_canonical_created_at(tmp_path: Path) -> None:
    """canonical created_at 应按 UTC 日聚合，不依赖内存计数。"""

    db_path = tmp_path / "memora.db"
    connection = await aiosqlite.connect(db_path)
    try:
        await connection.execute(
            "CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT, "
            "text TEXT, metadata TEXT, created_at TEXT, updated_at TEXT)"
        )
        today_iso = datetime.fromtimestamp(_day_ts(0), tz=timezone.utc).isoformat()
        yesterday_iso = datetime.fromtimestamp(_day_ts(1), tz=timezone.utc).isoformat()
        await connection.executemany(
            "INSERT INTO documents(doc_id, text, metadata, created_at, updated_at) "
            "VALUES (?, ?, '{}', ?, ?)",
            [
                ("d1", "今日一", today_iso, today_iso),
                ("d2", "今日二", today_iso, today_iso),
                ("d3", "昨日", yesterday_iso, yesterday_iso),
                ("d4", "旧格式", "2020-01-02 08:00:00", "2020-01-02 08:00:00"),
            ],
        )
        await connection.commit()
        host = SimpleNamespace(db_connection=connection)

        from core.features.memory.application.stats_operations import (
            StatsOperationsMixin,
        )

        count_today = await StatsOperationsMixin.count_canonical_created_on(
            host, _day_ts(0)
        )
        count_yesterday = await StatsOperationsMixin.count_canonical_created_on(
            host, _day_ts(1)
        )
    finally:
        await connection.close()

    assert count_today == 2
    assert count_yesterday == 1


def test_alert_and_stats_carry_stable_reason_codes() -> None:
    """告警与最近状态必须输出稳定 reason code。"""

    detector = AnomalyDetector(window_days=14)
    base = _day_ts(0)
    for i in range(12):
        detector.record_daily_count(base - (12 - i) * 86400, 99 if i % 2 == 0 else 101)

    alert = detector.record_daily_count(base, 500)

    assert alert is not None
    assert alert["reason_code"] == "memory_rate_anomaly"
    assert detector.stats["reason_code"] == "memory_rate_anomaly"

    fresh = AnomalyDetector(window_days=14)
    fresh.record_daily_count(base, 10)
    assert fresh.stats["reason_code"] == "insufficient_history"


def test_default_window_compares_spike_with_prior_days_only() -> None:
    """默认七日窗口必须用此前历史作为基线，极端当天值不得污染自身基线。"""

    detector = AnomalyDetector()
    base = _day_ts(0)
    for offset in range(7, 0, -1):
        detector.record_daily_count(
            base - offset * 86400,
            99 if offset % 2 == 0 else 101,
        )

    alert = detector.record_daily_count(base, 1_000_000_000)

    assert alert is not None
    assert alert["reason_code"] == "memory_rate_anomaly"
    assert alert["window_size"] == 7


@pytest.mark.asyncio
async def test_failed_anomaly_event_is_retried_before_day_is_marked(
    tmp_path: Path,
) -> None:
    """诊断事件首次写失败时保留待投递告警，下一轮成功后才标记日期。"""

    detector = AnomalyDetector(data_dir=str(tmp_path))
    engine = SimpleNamespace(anomaly_detector=detector)
    engine.count_canonical_created_on = AsyncMock(
        side_effect=lambda day_ts: (
            500 if day_ts == _day_ts(0) else (99 if (day_ts // 86400) % 2 == 0 else 101)
        )
    )
    scheduler = DecayScheduler(
        memory_engine=engine,
        decay_rate=0.0,
        data_dir=str(tmp_path),
    )
    scheduler._emit_anomaly_event = AsyncMock(
        side_effect=[OSError("diagnostic store unavailable"), None]
    )

    with pytest.raises(OSError, match="diagnostic store unavailable"):
        await scheduler._run_anomaly_feed()

    assert detector.has_fed(_day_ts(0)) is False
    await scheduler._run_anomaly_feed()

    assert detector.has_fed(_day_ts(0)) is True
    assert scheduler._emit_anomaly_event.await_count == 2


@pytest.mark.asyncio
async def test_anomaly_event_uses_stable_daily_idempotency_key(tmp_path: Path) -> None:
    """同一 UTC 日重复投递只能持久化一条诊断事件。"""

    scheduler = DecayScheduler(
        memory_engine=SimpleNamespace(),
        decay_rate=0.0,
        data_dir=str(tmp_path),
    )
    alert = {
        "day_ts": _day_ts(0),
        "direction": "spike",
        "count": 500,
        "mean_7d": 100.0,
        "stdev_7d": 1.0,
        "z_score": 400.0,
        "window_size": 7,
    }

    await scheduler._emit_anomaly_event(alert)
    await scheduler._emit_anomaly_event(alert)

    store = await scheduler._get_diagnostic_event_store()
    events = await store.list_events()
    assert len(events) == 1
    assert events[0]["event_id"] == f"anomaly-{_day_ts(0)}"


def test_pending_anomaly_alert_survives_restart_until_marked(tmp_path: Path) -> None:
    """待投递告警必须随状态恢复，并在事件成功后由 mark_fed 清除。"""

    detector = AnomalyDetector(data_dir=str(tmp_path))
    base = _day_ts(0)
    for offset in range(7, 0, -1):
        detector.record_daily_count(
            base - offset * 86400,
            99 if offset % 2 == 0 else 101,
        )
    alert = detector.record_daily_count(base, 500)
    assert alert is not None
    detector.save_state()

    restarted = AnomalyDetector(data_dir=str(tmp_path))
    restarted.load_state()

    assert restarted.pending_alert(base) == alert
    restarted.mark_fed(base)
    assert restarted.pending_alert(base) is None
    assert restarted.has_fed(base) is True


def test_health_scorer_reports_anomaly_domain() -> None:
    """健康评分应暴露异常检测域与稳定 reason code。"""

    health = HealthScorer().score(
        {
            "anomaly": {
                "available": True,
                "reason_code": "memory_rate_anomaly",
                "alerts": 1,
            }
        }
    )

    assert any(domain["name"] == "anomaly" for domain in health["domains"])
    assert health["score"] < 100


def test_diagnostics_snapshot_includes_anomaly_summary() -> None:
    """诊断快照必须包含异常检测的最近状态与 reason code。"""

    detector = AnomalyDetector()
    initializer = SimpleNamespace(
        memory_engine=SimpleNamespace(anomaly_detector=detector)
    )

    from core.platform.transport.page_api.diagnostics_api import DiagnosticsApiMixin
    from core.platform.transport.page_api.metrics_api import MetricsApiMixin

    class _DiagnosticsStub(DiagnosticsApiMixin, MetricsApiMixin):
        """组合诊断与指标快照构造器的测试替身。"""

    api = _DiagnosticsStub()
    api.plugin = SimpleNamespace(initializer=initializer)

    snapshot = api._build_diagnostics_snapshot()

    assert "anomaly" in snapshot
    assert snapshot["anomaly"]["available"] is True
    assert snapshot["anomaly"]["reason_code"] == "insufficient_history"
