"""质量 funnel Page API 的白名单、可用性与只读契约。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
import pytest_asyncio

from core.features.memory.infrastructure.dedup_metrics_store import (
    DedupMetricsStore,
)
from core.features.memory.infrastructure.topic_catalog_schema import (
    create_topic_catalog_schema,
)
from core.features.memory.infrastructure.topic_catalog_store import TopicCatalogStore
from core.features.memory.infrastructure.topic_metrics import (
    load_or_create_topic_metrics_key,
)
from core.features.quality.application.quality_funnel import (
    FUNNEL_STAGE_COUNT_KEYS,
    FUNNEL_STAGE_RATE_KEYS,
    FUNNEL_STAGE_VALUE_KEYS,
    FUNNEL_STAGES,
    TREND_FIELDS,
)
from core.platform.transport.page_api.page_api import (
    PAGE_API_ALIAS_PREFIXES,
    PAGE_API_PREFIX,
    PluginPageApi,
)

ROUTE_SUFFIX = "/metrics/quality-funnel"
_STAGE_KEYS = {"id", "state", "reason", "counts", "values", "rates"}
_TOP_LEVEL_KEYS = {"window", "bucket", "advisory", "stages", "trend"}
#: 任何响应都不得出现的敏感字段名（含逐请求关联键与结论性字段）。
_SENSITIVE_MARKERS = (
    '"scope_key"',
    '"session_id"',
    '"persona_id"',
    '"content"',
    '"query"',
    '"memory_id"',
    '"revision"',
    '"source_mapping"',
    '"threshold"',
    '"pass_fail"',
    '"identity"',
)


class _InjectionStore:
    """按注入决策 Store 的公开摘要形状返回固定载荷。"""

    def __init__(self, payload: Any) -> None:
        self.payload = payload

    async def summary(self, window: str) -> Any:
        return self.payload


class _RaisingInjectionStore:
    async def summary(self, window: str) -> Any:
        raise RuntimeError("injection store offline")


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


def _today() -> str:
    """当前 UTC 日（与后端窗口同一基准）。"""

    return datetime.now(timezone.utc).date().isoformat()


def _make_api(
    *,
    topic_store: Any = None,
    summary_connection: Any = None,
    dedup_store: Any = None,
    injection_store: Any = None,
    data_dir: Any = None,
    ready: bool = True,
) -> PluginPageApi:
    """构造与生产一致的多 mixin 组合 API。"""

    initializer = SimpleNamespace(
        data_dir=str(data_dir) if data_dir else None,
        dedup_metrics_store=dedup_store,
        injection_decision_store=injection_store,
        memory_engine=SimpleNamespace(topic_catalog_store=topic_store),
        conversation_manager=SimpleNamespace(
            store=SimpleNamespace(connection=summary_connection)
        ),
        index_validator=MagicMock(),
    )
    plugin = SimpleNamespace(
        initializer=initializer,
        context=MagicMock(),
        _ensure_plugin_ready=AsyncMock(
            return_value=(True, "") if ready else (False, "插件尚未就绪")
        ),
    )
    return PluginPageApi(plugin)


async def _seed_topic(
    store: TopicCatalogStore,
    *,
    values: dict[str, int],
    bucket_date: str | None = None,
) -> None:
    """写入一条当日候选窗口样本。"""

    assert await store.record_metric_window(
        window_key_hash="a" * 64,
        hash_key_version=1,
        terminal_state="success",
        token_source_available=False,
        scope_key_hash="b" * 64,
        bucket_date=bucket_date or _today(),
        mode="top_k",
        topic_count_bucket="unknown",
        values=values,
    )


async def _seed_summary(connection: Any, *, counts: dict[str, int]) -> None:
    """写入一条当日终态总结窗口。"""

    now = datetime.now(timezone.utc).timestamp()
    await connection.execute(
        """
        INSERT INTO summary_jobs(
            job_id, session_id, session_epoch, start_seq, end_seq, expected_count,
            source_digest, triggered_by, status, reason_code, created_at, updated_at,
            canonical_count, merged_count, quarantine_count, discard_count,
            mark_write_count, failed_count, skipped_count, facts_rejected_count
        ) VALUES ('job-1', 'session-1', 1, 0, 10, 10, 'digest-1', 'auto',
                  'completed', 'completed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now,
            now,
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


def _injection_payload() -> dict[str, Any]:
    """注入摘要样本；附带敏感 canary 证明投影层丢弃未知字段。"""

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return {
        "decision_count": 4,
        "selected_count_total": 5,
        "dropped_count_total": 1,
        "truncated_count_total": 0,
        "memory_present_count": 3,
        "payload_injected_count": 2,
        "budget_utilization_avg": 0.5,
        "cost_trend": [
            {
                "bucket_ms": now_ms,
                "decision_count": 4,
                "selected_count_total": 5,
                "scope_key": "canary-scope",
            }
        ],
        "scope_key": "canary-scope",
        "session_id": "canary-session",
        "content": "canary-content",
        "memory_id": 4242,
        "revision": "canary-revision",
        "threshold": 0.85,
    }


async def _dedup_store(tmp_path: Path) -> DedupMetricsStore:
    """写入当期去重样本：checked=5、hit=1、merged=1、fact_overlap=1。"""

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    store = DedupMetricsStore(str(tmp_path / "dedup.sqlite3"))
    await store.initialize()
    for _ in range(3):
        await store.record("observe", "checked", now_ms=now_ms)
    await store.record("observe", "hit", now_ms=now_ms)
    for _ in range(2):
        await store.record("enforce", "checked", now_ms=now_ms)
    await store.record("enforce", "merged", now_ms=now_ms)
    await store.record("enforce", "fact_overlap", now_ms=now_ms)
    return store


@pytest_asyncio.fixture
async def seeded(tmp_path: Path):
    """构造四个 stage 都有可用数据的 API。"""

    key = load_or_create_topic_metrics_key(tmp_path)
    db, catalog = await _catalog(tmp_path)
    await _seed_topic(
        catalog,
        values={
            "candidate_count_sum": 8,
            "exact_reuse_count": 2,
            "duplicate_topic_count": 1,
            "identity_drop_count": 1,
            "budget_exceeded_count": 1,
            "catalog_degraded_count": 1,
        },
    )
    dedup = await _dedup_store(tmp_path)
    summary = SimpleNamespace(connection=None)
    conn = await aiosqlite.connect(tmp_path / "conversations.db")
    await conn.execute("CREATE TABLE IF NOT EXISTS placeholder (id INTEGER)")
    await conn.close()
    from core.features.conversation.infrastructure.conversation_store import (
        ConversationStore,
    )

    store = ConversationStore(str(tmp_path / "conversations.db"))
    await store.initialize()
    await _seed_summary(
        store.connection,
        counts={
            "canonical_count": 2,
            "merged_count": 1,
            "quarantine_count": 1,
            "discard_count": 1,
            "skipped_count": 1,
            "facts_rejected_count": 2,
        },
    )
    summary.connection = store.connection
    api = _make_api(
        topic_store=catalog,
        summary_connection=store.connection,
        dedup_store=dedup,
        injection_store=_InjectionStore(_injection_payload()),
        data_dir=tmp_path,
    )
    try:
        yield api, key
    finally:
        await dedup.close()
        await store.close()
        await db.close()


@pytest.mark.asyncio
async def test_stage_counts_rates_and_shape_match_contract(seeded) -> None:
    """四阶段计数、派生比率与响应形状等于手算契约。"""

    api, key = seeded
    assert key
    result = await api.get_quality_funnel_payload({"window": "24h"})

    assert result["status"] == "ok"
    data = result["data"]
    assert set(data) == _TOP_LEVEL_KEYS
    assert data["window"] == "24h"
    assert data["bucket"] == "utc_day"
    assert data["advisory"] is True
    assert [stage["id"] for stage in data["stages"]] == list(FUNNEL_STAGES)

    stages = {stage["id"]: stage for stage in data["stages"]}
    for stage_id, stage in stages.items():
        assert set(stage) == _STAGE_KEYS, stage_id
        assert stage["state"] == "available", stage_id
        assert stage["reason"] == "ok", stage_id
        assert set(stage["counts"]) == set(FUNNEL_STAGE_COUNT_KEYS[stage_id])
        assert set(stage["values"]) == set(FUNNEL_STAGE_VALUE_KEYS[stage_id])
        assert set(stage["rates"]) == set(FUNNEL_STAGE_RATE_KEYS[stage_id])

    assert stages["candidates"]["counts"] == {
        "windows": 1,
        "candidates": 8,
        "exact_reuse": 2,
        "duplicate_topics": 1,
        "identity_drops": 1,
        "budget_exceeded": 1,
        "catalog_degraded": 1,
    }
    assert stages["candidates"]["rates"] == {
        "reuse_rate": 2 / 8,
        "degraded_rate": 1.0,
    }
    assert stages["facts"]["counts"] == {
        "windows": 1,
        "canonical": 2,
        "merged": 1,
        "quarantined": 1,
        "discarded": 1,
        "mark_write": 0,
        "failed": 0,
        "skipped": 1,
        "facts_rejected": 2,
    }
    assert stages["facts"]["rates"] == {"merge_rate": 1 / 6, "discard_rate": 1 / 6}
    assert stages["dedup"]["counts"] == {
        "checked": 5,
        "hit": 1,
        "merged": 1,
        "fact_mismatch": 0,
        "fact_overlap": 1,
        "conflict": 0,
        "failed": 0,
    }
    assert stages["dedup"]["rates"] == {
        "hit_rate": 0.2,
        "guard_rate": 0.0,
        "overlap_rate": 0.2,
        "failure_rate": 0.0,
    }
    assert stages["injection"]["counts"] == {
        "decisions": 4,
        "selected": 5,
        "dropped": 1,
        "truncated": 0,
        "memory_present": 3,
        "payload_injected": 2,
    }
    assert stages["injection"]["values"] == {"budget_utilization": 0.5}
    assert stages["injection"]["rates"] == {
        "memory_present_rate": 0.75,
        "payload_injected_rate": 0.5,
    }
    assert data["trend"], "可用的四阶段应产生趋势行"
    for row in data["trend"]:
        assert set(row) == {"day", *TREND_FIELDS}
        assert row["day"] == _today()


@pytest.mark.asyncio
async def test_missing_hmac_key_reports_unavailable_without_zero(
    seeded, tmp_path
) -> None:
    """key 缺失时候选 stage 必须 unavailable 且 counts 为 null。"""

    api, _key = seeded
    (tmp_path / "topic_metrics.hmac.key").unlink()
    result = await api.get_quality_funnel_payload({"window": "24h"})

    assert result["status"] == "ok"
    stages = {stage["id"]: stage for stage in result["data"]["stages"]}
    candidates = stages["candidates"]
    assert candidates["state"] == "unavailable"
    assert candidates["reason"] == "topic_metrics_key_missing"
    assert candidates["counts"] is None
    assert candidates["rates"] is None
    assert candidates["values"] is None
    # 任一 stage 不可用时趋势为空，不得让无证据的零值静默贡献趋势行。
    assert result["data"]["trend"] == []
    assert stages["dedup"]["state"] == "available"


@pytest.mark.asyncio
async def test_store_failures_degrade_or_mark_unavailable(seeded) -> None:
    """Store 失败不得 500、不得回显异常文本，且不伪造可用状态。"""

    api, _key = seeded
    api.plugin.initializer.dedup_metrics_store = _RaisingInjectionStore()
    api.plugin.initializer.injection_decision_store = None
    api.plugin.initializer.memory_engine = SimpleNamespace(topic_catalog_store=object())
    api.plugin.initializer.conversation_manager = SimpleNamespace(store=None)
    result = await api.get_quality_funnel_payload({"window": "24h"})

    assert result["status"] == "ok"
    stages = {stage["id"]: stage for stage in result["data"]["stages"]}
    assert stages["candidates"]["state"] == "unavailable"
    assert stages["candidates"]["reason"] == "topic_metrics_store_unavailable"
    assert stages["facts"]["state"] == "unavailable"
    assert stages["facts"]["reason"] == "summary_store_unavailable"
    assert stages["dedup"] == {
        "id": "dedup",
        "state": "degraded",
        "reason": "dedup_read_failed",
        "counts": {key: 0 for key in FUNNEL_STAGE_COUNT_KEYS["dedup"]},
        "values": {},
        "rates": {key: 0.0 for key in FUNNEL_STAGE_RATE_KEYS["dedup"]},
    }
    assert stages["injection"]["state"] == "degraded"
    assert stages["injection"]["reason"] == "injection_store_missing"
    assert stages["injection"]["values"] == {"budget_utilization": 0.0}
    assert "RuntimeError" not in json.dumps(result)
    assert "offline" not in json.dumps(result)


@pytest.mark.asyncio
async def test_response_allowlist_drops_unknown_and_sensitive_fields(seeded) -> None:
    """响应只含白名单字段，未知/敏感字段一律不出现在任何层级。"""

    api, _key = seeded
    serialized = json.dumps(await api.get_quality_funnel_payload({"window": "24h"}))
    for marker in _SENSITIVE_MARKERS:
        assert marker not in serialized, marker
    for marker in (
        "canary-scope",
        "canary-session",
        "canary-content",
        "canary-revision",
    ):
        assert marker not in serialized, marker


@pytest.mark.asyncio
async def test_malformed_payloads_keep_stable_shape(seeded) -> None:
    """坏载荷只影响自身 stage，响应形状保持稳定。"""

    api, _key = seeded
    api.plugin.initializer.injection_decision_store = _InjectionStore("not-a-mapping")
    api.plugin.initializer.dedup_metrics_store = _InjectionStore({"checked": "bad"})
    result = await api.get_quality_funnel_payload({"window": "24h"})

    stages = {stage["id"]: stage for stage in result["data"]["stages"]}
    assert stages["dedup"]["state"] == "available"
    assert stages["dedup"]["counts"]["checked"] == 0
    assert stages["injection"]["state"] == "degraded"
    assert stages["injection"]["reason"] == "injection_read_failed"


@pytest.mark.asyncio
async def test_unknown_window_returns_stable_error(seeded) -> None:
    """未知窗口返回 ``invalid_window``，且不返回 data。"""

    api, _key = seeded
    result = await api.get_quality_funnel_payload({"window": "12h"})

    assert result["status"] == "error"
    assert result["code"] == "invalid_window"
    assert "data" not in result


@pytest.mark.asyncio
async def test_not_ready_plugin_returns_readiness_error() -> None:
    """插件未就绪时返回就绪错误 envelope，而不是聚合形状。"""

    api = _make_api(ready=False)
    result = await api.get_quality_funnel_payload({"window": "24h"})

    assert result["status"] == "error"


def test_route_registered_as_get_only_read_under_both_prefixes() -> None:
    """路由必须是 GET 只读，并在主前缀与兼容前缀同步注册。"""

    plugin = MagicMock()
    api = PluginPageApi(plugin)
    registered: list[tuple[str, tuple[str, ...]]] = []
    plugin.context.register_web_api.side_effect = (
        lambda path, handler, methods, description: registered.append(
            (path, tuple(methods))
        )
    )
    api.register_routes()

    paths = {path for path, _methods in registered}
    for prefix in (PAGE_API_PREFIX, *PAGE_API_ALIAS_PREFIXES):
        assert f"{prefix}{ROUTE_SUFFIX}" in paths
    assert (f"{PAGE_API_PREFIX}{ROUTE_SUFFIX}", ("GET",)) in registered
    assert (f"{PAGE_API_PREFIX}{ROUTE_SUFFIX}", ("POST",)) not in registered

    metadata = {item["path"]: item for item in api.get_route_metadata()}
    route = metadata[f"{PAGE_API_PREFIX}{ROUTE_SUFFIX}"]
    assert route["methods"] == ["GET"]
    assert route["risk"] == "read"
    assert route["auth"] == "host"
    assert route["requires_ready"] is True
