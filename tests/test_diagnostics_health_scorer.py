"""诊断健康评分和事件存储的行为契约。"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from core.features.diagnostics.application.health_scorer import HealthScorer
from core.features.diagnostics.infrastructure.event_store import DiagnosticEventStore


def test_health_scorer_marks_provider_failure_as_critical():
    """Provider 失败必须把整体健康等级压到 critical。"""
    scorer = HealthScorer()
    result = scorer.score(
        {
            "provider": {"status": "failed", "attempts": 60, "max_attempts": 60},
            "recall": {"sample_count": 10, "p95_total_ms": 120.0},
            "write_coordinator": {"failures_total": 0, "lock_retries_total": 0},
            "background_tasks": {"failed": 0, "active": 0},
            "index": {
                "validator_available": True,
                "last_rebuild_errors": 0,
                "last_rebuild_total": 100,
            },
        }
    )
    provider = _domain(result, "provider")
    assert result["level"] == "critical"
    assert result["score"] < 50
    assert any(item["name"] == "provider" for item in result["domains"])
    assert provider["score"] == 0
    assert provider["status"] == "critical"


def test_health_scorer_scores_waiting_provider_as_watch():
    """Provider 等待重试时应产生 watch 领域并扣十分。"""
    scorer = HealthScorer()
    result = scorer.score(
        {
            "provider": {"status": "waiting", "attempts": 3, "max_attempts": 60},
            "recall": {"sample_count": 10, "p95_total_ms": 120.0},
            "write_coordinator": {"failures_total": 0, "lock_retries_total": 0},
            "background_tasks": {"failed": 0, "active": 0},
            "index": {"last_rebuild_errors": 0, "last_rebuild_total": 100},
        }
    )

    provider = _domain(result, "provider")
    assert result["score"] == 90
    assert result["level"] == "healthy"
    assert provider["score"] == 60
    assert provider["status"] == "watch"
    assert result["recommended_actions"]


def test_health_scorer_treats_explicit_retry_active_as_waiting_provider_evidence():
    """显式 retry_active 应作为 Provider 仍在等待的证据。"""
    result = HealthScorer().score(
        {
            "provider": {
                "status": "waiting",
                "attempts": 0,
                "max_attempts": 0,
                "retry_active": True,
            }
        }
    )

    provider = _domain(result, "provider")
    assert result["score"] == 90
    assert provider["score"] == 60


def test_health_scorer_penalizes_high_recall_p95():
    """召回 P95 超过阈值时应扣除固定分值。"""
    result = HealthScorer().score(
        {"recall": {"sample_count": 3, "p95_total_ms": 1200.5}}
    )

    assert result["score"] == 85
    assert result["level"] == "healthy"
    assert _domain(result, "recall")["score"] == 40


def test_health_scorer_penalizes_write_failures_increased_since_last_event():
    """实例应使用上次累计值检测写失败增长。"""
    scorer = HealthScorer()
    first = scorer.score({"write_coordinator": {"failures_total": 2}})
    second = scorer.score({"write_coordinator": {"failures_total": 5}})

    assert first["score"] == 100
    assert second["score"] == 85
    assert _domain(second, "write")["score"] == 50


def test_health_scorer_explicit_prior_write_failures_detects_increase_on_fresh_scorer():
    """新实例应使用显式历史累计值检测写失败增长。"""
    result = HealthScorer().score(
        {"write_coordinator": {"failures_total": 5}},
        previous_write_failures_total=2,
    )

    assert result["score"] == 85
    assert _domain(result, "write")["score"] == 50


def test_health_scorer_penalizes_background_failures_and_index_error_ratio():
    """后台任务失败和索引错误率过高应分别扣分。"""
    result = HealthScorer().score(
        {
            "background_tasks": {"failed": 2, "active": 1},
            "index": {"last_rebuild_errors": 12, "last_rebuild_total": 100},
        }
    )

    assert result["score"] == 80
    assert result["level"] == "watch"
    assert _domain(result, "scheduler")["score"] == 55
    assert _domain(result, "index")["score"] == 55


def test_health_scorer_treats_prometheus_unavailable_as_informational():
    """Prometheus 不可用只应产生信息领域，不应扣分。"""
    result = HealthScorer().score({"prometheus": {"available": False}})

    assert result["score"] == 100
    assert result["level"] == "healthy"
    prometheus = _domain(result, "prometheus")
    assert prometheus["score"] == 100
    assert prometheus["status"] == "info"


@pytest.mark.parametrize(
    ("score", "level"),
    [
        (100, "healthy"),
        (85, "healthy"),
        (84, "watch"),
        (65, "watch"),
        (64, "degraded"),
        (45, "degraded"),
        (44, "critical"),
        (0, "critical"),
    ],
)
def test_health_scorer_level_boundaries(score: int, level: str):
    """固定分数边界应映射到对应健康等级。"""
    scorer = HealthScorer()

    assert scorer.level_for_score(score) == level


def test_health_scorer_projects_write_availability_and_unresolved_tasks():
    """候选存在但无接受写入时应显示写入不可用并扣分。"""
    result = HealthScorer().score(
        {
            "summary_tasks": {
                "candidate_total": 12,
                "canonical_total": 0,
                "quarantine_total": 0,
                "blocked": 0,
                "unknown": 0,
            }
        }
    )

    projection = result["summary_tasks"]
    assert projection["evidence_status"] == "available"
    assert projection["write_availability"]["candidate_total"] == 12
    assert projection["write_availability"]["status"] == "blocked"
    assert result["score"] == 90
    assert any(item["name"] == "write_availability" for item in result["domains"])


def test_health_scorer_treats_quarantine_as_informational_not_failure():
    """正常隔离属于安全处置，不得按基础设施故障扣分。"""

    result = HealthScorer().score(
        {
            "summary_tasks": {
                "candidate_total": 5,
                "canonical_total": 3,
                "quarantine_total": 2,
            }
        }
    )

    projection = result["summary_tasks"]
    assert projection["write_availability"]["status"] == "available"
    assert projection["write_availability"]["accepted_total"] == 5
    assert result["score"] == 100
    domain = next(
        item for item in result["domains"] if item["name"] == "write_availability"
    )
    assert domain["status"] == "info"


def test_health_scorer_marks_empty_summary_evidence_as_insufficient():
    """无候选、无写入、无未决证据时不得伪造成健康结论。"""

    result = HealthScorer().score({"summary_tasks": {}})

    projection = result["summary_tasks"]
    assert projection["evidence_status"] == "insufficient"
    assert projection["write_availability"]["status"] == "unknown"
    assert projection["unresolved_reason_counts"] == {}


def test_health_scorer_penalizes_unresolved_summary_tasks_with_reason_counts():
    """未决任务应降级并保留闭集原因计数，异常文本不得进入投影。"""

    result = HealthScorer().score(
        {
            "summary_tasks": {
                "blocked": 2,
                "unknown": 1,
                "oldest_unresolved_age_seconds": 3600,
                "unresolved_reason_counts": {
                    "ledger_unresolved": 2,
                    "x" * 200: 1,
                    "negative": -3,
                },
            }
        }
    )

    projection = result["summary_tasks"]
    assert projection["unknown"] == 1
    assert projection["blocked"] == 2
    assert projection["oldest_unresolved_age_seconds"] == 3600
    assert projection["unresolved_reason_counts"] == {"ledger_unresolved": 2}
    assert result["score"] == 90
    assert any(item["name"] == "summary_tasks" for item in result["domains"])


def test_health_scorer_handles_missing_and_malformed_inputs_defensively():
    """缺失或畸形快照片段应安全退化为健康默认值。"""
    result = HealthScorer().score(
        {
            "provider": "not-a-dict",
            "recall": {"p95_total_ms": "slow"},
            "write_coordinator": None,
            "background_tasks": {"failed": "many"},
            "index": {"last_rebuild_errors": "oops", "last_rebuild_total": 0},
        }
    )

    assert result["score"] == 100
    assert result["level"] == "healthy"
    assert isinstance(result["domains"], list)
    assert isinstance(result["recommended_actions"], list)


def test_health_scorer_degrades_on_non_finite_counters():
    """NaN/Inf 计数必须按缺失字段降级，不能让评分整体抛异常。"""

    scorer = HealthScorer()

    result = scorer.score(
        {
            "write_coordinator": {"failures_total": float("nan")},
            "background_tasks": {"failed": float("inf")},
        }
    )

    assert result["score"] == 100
    assert result["level"] == "healthy"
    assert scorer.level_for_score(float("nan")) == "critical"
    assert scorer.level_for_score(float("inf")) == "critical"


@pytest.mark.asyncio
async def test_diagnostic_event_store_add_list_get_resolve_filters_and_payload(
    tmp_path: Path,
):
    """事件 Store 应保存安全标量、支持筛选并返回独立副本。"""
    db_path = tmp_path / "diagnostics.sqlite3"
    store = DiagnosticEventStore(db_path)
    try:
        await store.initialize()

        first = await store.add_event(
            {
                "domain": "provider",
                "severity": "critical",
                "title": "不应保存的自由文本",
                "message": "不应保存的自由文本",
                "source": "health_scorer",
                "payload": {
                    "reason_code": "provider_unavailable",
                    "attempt_count": 60,
                    "nested": {"ok": True},
                },
            }
        )
        second = await store.add_event(
            {
                "event_id": "manual-event",
                "domain": "scheduler",
                "severity": "warning",
                "title": "Backfill failed",
                "message": "One scheduler task failed",
                "source": "scheduler",
                "payload": {"reason_code": "task_error", "failed_count": 1},
            }
        )

        assert first["event_id"]
        assert first["payload"] == {
            "reason_code": "provider_unavailable",
            "attempt_count": 60,
        }
        assert first["title"] == "provider_unavailable"
        assert first["message"] == "provider_unavailable"
        assert second["event_id"] == "manual-event"

        events = await store.list_events()
        assert [event["event_id"] for event in events] == [
            "manual-event",
            first["event_id"],
        ]

        assert [
            event["event_id"] for event in await store.list_events(domain="provider")
        ] == [first["event_id"]]
        assert [
            event["event_id"] for event in await store.list_events(severity="warning")
        ] == ["manual-event"]
        assert len(await store.list_events(limit=1)) == 1

        fetched = await store.get_event(first["event_id"])
        assert fetched is not None
        assert fetched["payload"]["attempt_count"] == 60
        fetched["payload"]["attempt_count"] = 0
        persisted = await store.get_event(first["event_id"])
        assert persisted is not None
        assert persisted["payload"]["attempt_count"] == 60

        resolved = await store.resolve_event(first["event_id"])
        assert resolved is not None
        assert resolved["resolved_at"] is not None
        assert [
            event["event_id"]
            for event in await store.list_events(include_resolved=False)
        ] == ["manual-event"]
        assert await store.resolve_event("missing") is None
        assert await store.get_event("missing") is None

        defensive = await store.add_event(None)
        assert defensive["domain"] == "unknown"
        assert defensive["severity"] == "info"
        assert (
            len(
                await store.list_events(
                    limit=cast(int, "not-a-number"),
                    include_resolved=False,
                )
            )
            == 2
        )
    finally:
        for path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
            path.unlink(missing_ok=True)


def _domain(result: dict, name: str) -> dict:
    """从健康摘要中读取指定领域。"""
    return next(item for item in result["domains"] if item["name"] == name)
