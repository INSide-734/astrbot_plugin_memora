"""Behavioral and security contracts for the SQLite injection-decision store."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import FrozenInstanceError

import pytest

from core.features.injection.domain.models import InjectionDecisionRecord
from core.features.injection.infrastructure.injection_decision_store import (
    INJECTION_DECISION_SORT_COLUMNS,
    DecisionQuery,
    InjectionDecisionStore,
)


def record(
    decision_id: str, created_at_ms: int, **overrides
) -> InjectionDecisionRecord:
    values = {
        "decision_id": decision_id,
        "created_at_ms": created_at_ms,
        "routing_mode": "manual",
        "configured_preset": "balanced",
        "recommended_preset": "balanced",
        "resolved_preset": "balanced",
        "preferred_delivery": "extra_user_content",
        "resolved_delivery": "extra_user_content",
        "fallback_applied": False,
        "outcome": "injected",
        "primary_reason": "MANUAL_SELECTED",
        "actual_payload_chars": 600,
    }
    values.update(overrides)
    return InjectionDecisionRecord(**values)


SAFE_COLUMNS = {
    "decision_id",
    "created_at_ms",
    "trace_id",
    "routing_mode",
    "configured_preset",
    "recommended_preset",
    "resolved_preset",
    "preferred_delivery",
    "resolved_delivery",
    "fallback_applied",
    "outcome",
    "error_code",
    "primary_reason",
    "reason_codes_json",
    "provider_type",
    "provider_model",
    "candidate_count",
    "selected_count",
    "dropped_count",
    "truncated_count",
    "configured_budget_chars",
    "effective_budget_chars",
    "actual_payload_chars",
    "context_headroom_chars",
    "decision_ms",
    "format_ms",
    "inject_ms",
}

FORBIDDEN_COLUMNS = {
    "query",
    "prompt",
    "content",
    "memory_content",
    "memory_id",
    "memory_ids",
    "user_id",
    "group_id",
    "persona_id",
    "session_id",
    "stack_trace",
    "headers",
    "api_endpoint",
    "api_key",
    "secret",
}


@pytest.mark.asyncio
async def test_file_database_uses_wal_and_exact_safe_schema(tmp_path) -> None:
    db_path = tmp_path / "memora.db"
    store = InjectionDecisionStore(db_path)
    await store.initialize()
    try:
        with sqlite3.connect(db_path) as connection:
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(injection_decisions)"
                ).fetchall()
            }
        assert journal_mode.lower() == "wal"
        assert columns == SAFE_COLUMNS
        assert columns.isdisjoint(FORBIDDEN_COLUMNS)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_summary_and_filtered_page_use_expected_indexes(tmp_path) -> None:
    store = InjectionDecisionStore(tmp_path / "memora.db")
    await store.initialize()
    try:
        index_rows = await store._fetch_all("PRAGMA index_list('injection_decisions')")
        names = {row["name"] for row in index_rows}
        assert {
            "idx_injection_decisions_created",
            "idx_injection_decisions_preset",
            "idx_injection_decisions_provider",
            "idx_injection_decisions_outcome",
        } <= names
        plan = await store._fetch_all(
            """
            EXPLAIN QUERY PLAN
            SELECT decision_id
            FROM injection_decisions
            WHERE resolved_preset = ?
            ORDER BY created_at_ms DESC, decision_id DESC
            LIMIT ? OFFSET ?
            """,
            ("balanced", 50, 0),
        )
        assert any("idx_injection_decisions_preset" in row["detail"] for row in plan)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_initialize_is_idempotent_on_same_file(tmp_path) -> None:
    db_path = tmp_path / "memora.db"
    first = InjectionDecisionStore(db_path)
    await first.initialize()
    await first.close()
    second = InjectionDecisionStore(db_path)
    await second.initialize()
    try:
        assert await second.insert_many([record("one", 1)]) == 1
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_batch_is_idempotent_and_list_is_stable(tmp_path) -> None:
    store = InjectionDecisionStore(tmp_path / "memora.db")
    await store.initialize()
    try:
        now = int(time.time() * 1000)
        rows = [record("b", now), record("a", now)]
        assert await store.insert_many(rows) == 2
        assert await store.insert_many(rows) == 0
        page = await store.list_decisions(DecisionQuery(offset=0, limit=100))
        assert page.total == 2
        assert [item["decision_id"] for item in page.items] == ["a", "b"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_empty_batch_is_a_noop(tmp_path) -> None:
    store = InjectionDecisionStore(tmp_path / "memora.db")
    await store.initialize()
    try:
        assert await store.insert_many([]) == 0
        assert (await store.list_decisions(DecisionQuery())).total == 0
    finally:
        await store.close()


def test_decision_query_is_frozen_and_validates_pagination_and_window() -> None:
    query = DecisionQuery()
    with pytest.raises(FrozenInstanceError):
        query.offset = 1  # type: ignore[misc]
    for values in (
        {"offset": -1},
        {"limit": 0},
        {"limit": 101},
        {"from_ms": 20, "to_ms": 10},
        {"sort_by": "query"},
        {"sort_order": "sideways"},
    ):
        with pytest.raises(ValueError):
            DecisionQuery(**values)


@pytest.mark.asyncio
async def test_list_pagination_is_stable_and_reports_unpaged_total(tmp_path) -> None:
    store = InjectionDecisionStore(tmp_path / "memora.db")
    await store.initialize()
    try:
        await store.insert_many(
            [record("c", 20), record("b", 20), record("a", 20), record("old", 10)]
        )
        first = await store.list_decisions(DecisionQuery(offset=0, limit=2))
        second = await store.list_decisions(DecisionQuery(offset=2, limit=2))
        assert first.total == second.total == 4
        assert first.offset == 0 and first.limit == 2
        assert second.offset == 2 and second.limit == 2
        assert [item["decision_id"] for item in first.items] == ["a", "b"]
        assert [item["decision_id"] for item in second.items] == ["c", "old"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_sort_happens_before_limit_and_uses_public_allowlist(tmp_path) -> None:
    store = InjectionDecisionStore(tmp_path / "memora.db")
    await store.initialize()
    try:
        await store.insert_many(
            [
                record("high", 1, actual_payload_chars=900),
                record("low", 2, actual_payload_chars=100),
                record("middle", 3, actual_payload_chars=500),
            ]
        )
        page = await store.list_decisions(
            DecisionQuery(
                offset=0,
                limit=2,
                sort_by="actual_payload_chars",
                sort_order="asc",
            )
        )
        assert [item["decision_id"] for item in page.items] == ["low", "middle"]
    finally:
        await store.close()


def test_injection_decision_sort_columns_are_fixed() -> None:
    assert INJECTION_DECISION_SORT_COLUMNS == {
        "created_at_ms": "created_at_ms",
        "routing_mode": "routing_mode COLLATE NOCASE",
        "resolved_preset": "resolved_preset COLLATE NOCASE",
        "provider_type": "provider_type COLLATE NOCASE",
        "outcome": "outcome COLLATE NOCASE",
        "actual_payload_chars": "actual_payload_chars",
        "decision_ms": "decision_ms",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query_values", "matching_overrides"),
    [
        ({"routing_mode": "hybrid"}, {"routing_mode": "hybrid"}),
        ({"resolved_preset": "quality"}, {"resolved_preset": "quality"}),
        ({"provider_type": "openai"}, {"provider_type": "openai"}),
        (
            {"primary_reason": "AUTO_HISTORY_INTENT"},
            {"primary_reason": "AUTO_HISTORY_INTENT"},
        ),
        ({"fallback_applied": True}, {"fallback_applied": True}),
        ({"outcome": "error"}, {"outcome": "error", "error_code": "FORMAT_FAILED"}),
    ],
)
async def test_every_scalar_filter_is_applied(
    tmp_path, query_values, matching_overrides
) -> None:
    store = InjectionDecisionStore(tmp_path / "memora.db")
    await store.initialize()
    try:
        await store.insert_many(
            [record("match", 20, **matching_overrides), record("other", 10)]
        )
        page = await store.list_decisions(DecisionQuery(**query_values))
        assert page.total == 1
        assert [item["decision_id"] for item in page.items] == ["match"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_time_filters_are_inclusive_and_composable(tmp_path) -> None:
    store = InjectionDecisionStore(tmp_path / "memora.db")
    await store.initialize()
    try:
        await store.insert_many(
            [
                record("before", 9),
                record("from", 10),
                record("to", 20),
                record("after", 21),
            ]
        )
        page = await store.list_decisions(DecisionQuery(from_ms=10, to_ms=20))
        assert page.total == 2
        assert [item["decision_id"] for item in page.items] == ["to", "from"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_filters_use_parameter_binding_for_hostile_values(tmp_path) -> None:
    store = InjectionDecisionStore(tmp_path / "memora.db")
    await store.initialize()
    hostile = "openai' OR 1=1; DROP TABLE injection_decisions; --"
    try:
        await store.insert_many(
            [record("hostile", 2, provider_type=hostile), record("safe", 1)]
        )
        page = await store.list_decisions(DecisionQuery(provider_type=hostile))
        assert page.total == 1
        assert page.items[0]["decision_id"] == "hostile"
        assert (await store.list_decisions(DecisionQuery())).total == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_detail_decodes_reason_json_and_preserves_opaque_ids(tmp_path) -> None:
    store = InjectionDecisionStore(tmp_path / "memora.db")
    await store.initialize()
    opaque_id = "550e8400-e29b-41d4-a716-446655440000/opaque?x=1"
    try:
        await store.insert_many(
            [
                record(
                    opaque_id,
                    100,
                    trace_id="trace-opaque",
                    reason_codes=("MANUAL_SELECTED", "提供者降级"),
                )
            ]
        )
        detail = await store.get_decision(opaque_id)
        assert detail is not None
        assert detail["decision_id"] == opaque_id
        assert detail["trace_id"] == "trace-opaque"
        assert detail["reason_codes"] == ["MANUAL_SELECTED", "提供者降级"]
        assert "reason_codes_json" not in detail
        assert await store.get_decision("missing") is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_detail_lookup_binds_opaque_hostile_id(tmp_path) -> None:
    store = InjectionDecisionStore(tmp_path / "memora.db")
    await store.initialize()
    hostile_id = "x' OR decision_id <> 'x"
    try:
        await store.insert_many([record(hostile_id, 2), record("other", 1)])
        detail = await store.get_decision(hostile_id)
        assert detail is not None and detail["decision_id"] == hostile_id
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_empty_summary_has_complete_zero_shape(tmp_path) -> None:
    store = InjectionDecisionStore(tmp_path / "memora.db")
    await store.initialize()
    try:
        summary = await store.summary(window="24h", now_ms=1_000)
        assert summary == {
            "window": "24h",
            "decision_count": 0,
            "payload_chars_p95": 0,
            "provider_fallback_rate": 0.0,
            "preset_distribution": {},
            "cost_trend": [],
            "recent_events": [],
        }
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_summary_reports_deterministic_p95_distribution_fallback_and_events(
    tmp_path,
) -> None:
    store = InjectionDecisionStore(tmp_path / "memora.db")
    await store.initialize()
    now = 1_000_000
    try:
        rows = [
            record(
                f"d-{index:02d}",
                now - index,
                actual_payload_chars=index,
                resolved_preset="quality" if index % 2 else "balanced",
                fallback_applied=index in {1, 2, 3, 4},
            )
            for index in range(1, 21)
        ]
        await store.insert_many(rows)
        summary = await store.summary(window="24h", now_ms=now)
        assert summary["window"] == "24h"
        assert summary["decision_count"] == 20
        assert summary["payload_chars_p95"] == 19
        assert summary["provider_fallback_rate"] == pytest.approx(4 / 20)
        assert summary["preset_distribution"] == {"balanced": 10, "quality": 10}
        assert summary["cost_trend"] == [
            {
                "bucket_ms": 0,
                "decision_count": 20,
                "payload_chars_p95": 19,
                "provider_fallback_rate": pytest.approx(4 / 20),
            }
        ]
        assert summary["recent_events"]
        assert summary["recent_events"][0]["decision_id"] == "d-01"
        assert all(
            "query" not in event and "content" not in event
            for event in summary["recent_events"]
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_summary_window_excludes_older_rows(tmp_path) -> None:
    store = InjectionDecisionStore(tmp_path / "memora.db")
    await store.initialize()
    now = 40 * 86_400_000
    try:
        await store.insert_many(
            [record("recent", now), record("old", now - 2 * 86_400_000)]
        )
        summary = await store.summary(window="24h", now_ms=now)
        assert summary["decision_count"] == 1
        assert summary["recent_events"][0]["decision_id"] == "recent"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_retention_zero_disables_age_deletion(tmp_path) -> None:
    store = InjectionDecisionStore(tmp_path / "memora.db")
    await store.initialize()
    try:
        now = int(time.time() * 1000)
        await store.insert_many([record("ancient", 1), record("new", now)])
        result = await store.cleanup(retention_days=0, max_rows=10, now_ms=now)
        assert result.deleted_expired == 0
        assert result.deleted_overflow == 0
        assert (await store.list_decisions(DecisionQuery())).total == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_retention_runs_before_row_cap(tmp_path) -> None:
    store = InjectionDecisionStore(tmp_path / "memora.db")
    await store.initialize()
    try:
        now = int(time.time() * 1000)
        day = 86_400_000
        await store.insert_many(
            [
                record("expired", now - 31 * day),
                record("oldest-live", now - 2 * day),
                record("newest", now),
            ]
        )
        result = await store.cleanup(retention_days=30, max_rows=1, now_ms=now)
        assert result.deleted_expired == 1
        assert result.deleted_overflow == 1
        assert (await store.list_decisions(DecisionQuery())).items[0][
            "decision_id"
        ] == "newest"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_row_cap_uses_stable_created_at_and_id_order(tmp_path) -> None:
    store = InjectionDecisionStore(tmp_path / "memora.db")
    await store.initialize()
    try:
        await store.insert_many([record("a", 10), record("c", 10), record("b", 10)])
        result = await store.cleanup(retention_days=0, max_rows=2, now_ms=10)
        assert result.deleted_expired == 0
        assert result.deleted_overflow == 1
        page = await store.list_decisions(DecisionQuery())
        assert [item["decision_id"] for item in page.items] == ["b", "c"]
    finally:
        await store.close()
