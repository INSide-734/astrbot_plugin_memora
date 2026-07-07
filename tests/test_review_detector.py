from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from core.review.models import (
    ReviewAction,
    ReviewActionResult,
    ReviewItem,
    ReviewReason,
    ReviewSeverity,
    ReviewStatus,
)
from core.review.review_detector import ReviewDetector
from core.review.review_store import ReviewStore


@asynccontextmanager
async def _review_store(tmp_path: Path):
    db_path = tmp_path / "reviews.db"
    store = ReviewStore(db_path)
    await store.initialize()
    try:
        yield store
    finally:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{db_path}{suffix}")
            if candidate.exists():
                candidate.unlink()


def test_review_detector_flags_duplicate_and_stale_memory():
    detector = ReviewDetector()
    items = detector.detect(
        memories=[
            {
                "id": "mem-a",
                "content": "用户喜欢拿铁",
                "importance": 7,
                "metadata": {"last_accessed_days": 180},
            },
            {
                "id": "mem-b",
                "content": "用户喜欢拿铁咖啡",
                "importance": 6,
                "metadata": {"last_accessed_days": 3},
            },
        ],
        quality_stats={"low_confidence_ids": []},
    )
    reasons = {reason for item in items for reason in item.reasons}
    assert "duplicate" in reasons
    assert "stale" in reasons


def test_review_detector_flags_low_confidence_memory():
    detector = ReviewDetector()

    items = detector.detect(
        memories=[
            {
                "id": "mem-low",
                "content": "用户喜欢冷萃",
                "metadata": {"session_id": "s-1"},
            }
        ],
        quality_stats={"low_confidence_ids": ["mem-low"]},
    )

    assert len(items) == 1
    assert items[0].memory_id == "mem-low"
    assert ReviewReason.LOW_CONFIDENCE.value in items[0].reasons
    assert items[0].severity == ReviewSeverity.MEDIUM.value


def test_review_detector_uses_configured_sensitive_markers():
    detector = ReviewDetector(sensitive_markers=["身份证", "token="])

    items = detector.detect(
        memories=[
            {
                "id": "mem-sensitive",
                "content": "用户身份证是 110101199003070011",
                "metadata": {"session_id": "s-1"},
            },
            {
                "id": "mem-token",
                "content": "service token=secret",
                "metadata": {"session_id": "s-1"},
            },
        ],
        quality_stats={"low_confidence_ids": []},
    )

    assert [item.memory_id for item in items] == ["mem-sensitive", "mem-token"]
    assert all(ReviewReason.SENSITIVE.value in item.reasons for item in items)
    assert all(item.severity == ReviewSeverity.HIGH.value for item in items)


def test_review_detector_matches_english_sensitive_markers_case_insensitively():
    detector = ReviewDetector(sensitive_markers=["token="])

    items = detector.detect(
        memories=[
            {
                "id": "mem-token",
                "content": "service TOKEN=secret",
                "metadata": {"session_id": "s-1"},
            }
        ],
        quality_stats={"low_confidence_ids": []},
    )

    assert len(items) == 1
    assert ReviewReason.SENSITIVE.value in items[0].reasons


def test_review_detector_flags_noisy_and_provenance_missing_memories():
    detector = ReviewDetector()

    items = detector.detect(
        memories=[
            {"id": "mem-noisy", "content": "!!!", "metadata": {}},
            {
                "id": "mem-provenance",
                "content": "用户喜欢晨跑",
                "metadata": {},
            },
        ],
        quality_stats={"low_confidence_ids": []},
    )

    by_id = {item.memory_id: item for item in items}
    assert ReviewReason.NOISY.value in by_id["mem-noisy"].reasons
    assert ReviewReason.PROVENANCE_MISSING.value in by_id["mem-noisy"].reasons
    assert ReviewReason.PROVENANCE_MISSING.value in by_id["mem-provenance"].reasons


def test_review_models_serialize_to_json_safe_dicts():
    item = ReviewItem(
        memory_id="mem-1",
        reasons=[ReviewReason.DUPLICATE, ReviewReason.STALE],
        severity=ReviewSeverity.HIGH,
        status=ReviewStatus.OPEN,
        content_preview="用户喜欢拿铁",
        metadata={"tags": {"coffee", "profile"}},
        item_id="review-1",
        created_at=100.0,
        updated_at=101.0,
    )
    action = ReviewAction(
        item_id="review-1",
        action=ReviewStatus.MERGED,
        actor_id="operator",
        payload={"merged_into": "mem-2"},
        created_at=102.0,
        action_id="action-1",
    )
    result = ReviewActionResult(
        item=item,
        action=action,
        success=True,
        message="merged",
    )

    payload = result.to_dict()

    assert payload["item"]["status"] == "open"
    assert payload["item"]["reasons"] == ["duplicate", "stale"]
    assert payload["item"]["severity"] == "high"
    assert payload["item"]["metadata"]["tags"] == ["coffee", "profile"]
    assert payload["action"]["action"] == "merged"
    json.dumps(payload, ensure_ascii=False)


@pytest.mark.asyncio
async def test_review_store_upsert_deduplicates_open_items_by_memory_id_and_reason(
    tmp_path,
):
    async with _review_store(tmp_path) as store:
        first = ReviewItem(
            memory_id="mem-1",
            reasons=[ReviewReason.DUPLICATE],
            severity=ReviewSeverity.MEDIUM,
            content_preview="用户喜欢拿铁",
            metadata={"match": "mem-2"},
            created_at=100.0,
            updated_at=100.0,
        )
        second = ReviewItem(
            memory_id="mem-1",
            reasons=[ReviewReason.DUPLICATE],
            severity=ReviewSeverity.HIGH,
            content_preview="用户喜欢拿铁咖啡",
            metadata={"match": "mem-3"},
            created_at=120.0,
            updated_at=120.0,
        )

        inserted = await store.upsert_item(first)
        updated = await store.upsert_item(second)
        listed = await store.list_items()

    assert inserted["item_id"] == updated["item_id"]
    assert len(listed) == 1
    assert listed[0]["severity"] == "high"
    assert listed[0]["content_preview"] == "用户喜欢拿铁咖啡"
    assert listed[0]["metadata"] == {"match": "mem-3"}


@pytest.mark.asyncio
async def test_review_store_deduplicates_when_matching_reason_is_not_first(tmp_path):
    async with _review_store(tmp_path) as store:
        first = await store.upsert_item(
            ReviewItem(
                memory_id="mem-overlap",
                reasons=[ReviewReason.STALE, ReviewReason.DUPLICATE],
                severity=ReviewSeverity.MEDIUM,
                content_preview="用户喜欢拿铁",
                created_at=100.0,
                updated_at=100.0,
            )
        )
        second = await store.upsert_item(
            ReviewItem(
                memory_id="mem-overlap",
                reasons=[ReviewReason.DUPLICATE],
                severity=ReviewSeverity.HIGH,
                content_preview="用户喜欢拿铁咖啡",
                created_at=120.0,
                updated_at=120.0,
            )
        )
        open_items = await store.list_items(status=ReviewStatus.OPEN)

    assert second["item_id"] == first["item_id"]
    assert [item["item_id"] for item in open_items] == [first["item_id"]]
    assert open_items[0]["reasons"] == ["duplicate"]
    assert open_items[0]["severity"] == "high"


@pytest.mark.asyncio
async def test_review_store_consolidates_multiple_open_items_with_overlapping_reasons(
    tmp_path,
):
    async with _review_store(tmp_path) as store:
        stale = await store.upsert_item(
            ReviewItem(
                memory_id="mem-consolidate",
                reasons=[ReviewReason.STALE],
                severity=ReviewSeverity.LOW,
                content_preview="old stale",
                created_at=100.0,
                updated_at=100.0,
            )
        )
        duplicate = await store.upsert_item(
            ReviewItem(
                memory_id="mem-consolidate",
                reasons=[ReviewReason.DUPLICATE],
                severity=ReviewSeverity.MEDIUM,
                content_preview="newer duplicate",
                created_at=200.0,
                updated_at=200.0,
            )
        )
        merged = await store.upsert_item(
            ReviewItem(
                memory_id="mem-consolidate",
                reasons=[ReviewReason.STALE, ReviewReason.DUPLICATE],
                severity=ReviewSeverity.HIGH,
                content_preview="merged review",
                created_at=300.0,
                updated_at=300.0,
            )
        )
        open_items = await store.list_items(status=ReviewStatus.OPEN)
        safe_items = await store.list_items(status=ReviewStatus.SAFE)

    assert merged["item_id"] == duplicate["item_id"]
    assert [item["item_id"] for item in open_items] == [duplicate["item_id"]]
    assert open_items[0]["reasons"] == ["stale", "duplicate"]
    assert open_items[0]["severity"] == "high"
    assert [item["item_id"] for item in safe_items] == [stale["item_id"]]


@pytest.mark.asyncio
async def test_review_store_records_action_history_and_keeps_closed_item(tmp_path):
    async with _review_store(tmp_path) as store:
        item = await store.upsert_item(
            ReviewItem(
                memory_id="mem-1",
                reasons=[ReviewReason.STALE],
                severity=ReviewSeverity.LOW,
                content_preview="用户喜欢旧话题",
                created_at=100.0,
                updated_at=100.0,
            )
        )
        result = await store.record_action(
            ReviewAction(
                item_id=item["item_id"],
                action=ReviewStatus.ARCHIVED,
                actor_id="operator",
                payload={"note": "confirmed stale"},
                created_at=110.0,
            )
        )
        closed = await store.get_item(item["item_id"])
        actions = await store.list_actions(item["item_id"])

    assert result["success"] is True
    assert closed is not None
    assert closed["status"] == "archived"
    assert closed["memory_id"] == "mem-1"
    assert actions == [
        {
            "action_id": actions[0]["action_id"],
            "item_id": item["item_id"],
            "action": "archived",
            "actor_id": "operator",
            "payload": {"note": "confirmed stale"},
            "created_at": 110.0,
        }
    ]


@pytest.mark.asyncio
async def test_review_store_filters_and_uses_cursor_limit_newest_order(tmp_path):
    async with _review_store(tmp_path) as store:
        await store.upsert_item(
            ReviewItem(
                memory_id="old-low",
                reasons=[ReviewReason.STALE],
                severity=ReviewSeverity.LOW,
                content_preview="old",
                created_at=100.0,
                updated_at=100.0,
            )
        )
        duplicate = await store.upsert_item(
            ReviewItem(
                memory_id="middle-medium",
                reasons=[ReviewReason.DUPLICATE],
                severity=ReviewSeverity.MEDIUM,
                content_preview="middle",
                created_at=200.0,
                updated_at=200.0,
            )
        )
        sensitive = await store.upsert_item(
            ReviewItem(
                memory_id="new-high",
                reasons=[ReviewReason.SENSITIVE],
                severity=ReviewSeverity.HIGH,
                content_preview="new",
                created_at=300.0,
                updated_at=300.0,
            )
        )
        await store.record_action(
            ReviewAction(
                item_id=duplicate["item_id"],
                action=ReviewStatus.SAFE,
                actor_id="operator",
                created_at=250.0,
            )
        )

        newest_page = await store.list_items(limit=1)
        next_page = await store.list_items(limit=2, cursor=newest_page[-1]["item_id"])
        open_items = await store.list_items(status="open")
        safe_items = await store.list_items(status="safe")
        sensitive_items = await store.list_items(reason=ReviewReason.SENSITIVE)
        high_items = await store.list_items(severity=ReviewSeverity.HIGH)

    assert [item["memory_id"] for item in newest_page] == ["new-high"]
    assert [item["memory_id"] for item in next_page] == [
        "middle-medium",
        "old-low",
    ]
    assert [item["memory_id"] for item in open_items] == ["new-high", "old-low"]
    assert [item["memory_id"] for item in safe_items] == ["middle-medium"]
    assert [item["memory_id"] for item in sensitive_items] == ["new-high"]
    assert [item["memory_id"] for item in high_items] == ["new-high"]
    assert sensitive["item_id"] == newest_page[0]["item_id"]


@pytest.mark.asyncio
async def test_review_store_unknown_cursor_returns_empty_page(tmp_path):
    async with _review_store(tmp_path) as store:
        await store.upsert_item(
            ReviewItem(
                memory_id="mem-1",
                reasons=[ReviewReason.STALE],
                severity=ReviewSeverity.LOW,
                content_preview="old",
            )
        )
        items = await store.list_items(cursor="missing")

    assert items == []


@pytest.mark.asyncio
async def test_review_store_limit_validation_and_clamping(tmp_path):
    async with _review_store(tmp_path) as store:
        for index in range(201):
            await store.upsert_item(
                ReviewItem(
                    memory_id=f"mem-{index}",
                    reasons=[ReviewReason.STALE],
                    severity=ReviewSeverity.LOW,
                    content_preview=str(index),
                    updated_at=float(index),
                )
            )

        assert len(await store.list_items(limit=0)) == 1
        assert len(await store.list_items(limit=999)) == 200
        with pytest.raises(ValueError, match="limit"):
            await store.list_items(limit=None)
        with pytest.raises(ValueError, match="limit"):
            await store.list_items(limit="2")


@pytest.mark.asyncio
async def test_review_store_rejects_invalid_enums_and_empty_reasons(tmp_path):
    async with _review_store(tmp_path) as store:
        with pytest.raises(ValueError):
            await store.upsert_item(
                {
                    "item_id": "bad-status",
                    "memory_id": "mem-1",
                    "reasons": ["stale"],
                    "severity": "medium",
                    "status": "invalid",
                }
            )
        with pytest.raises(ValueError):
            await store.upsert_item(
                ReviewItem(
                    memory_id="mem-2",
                    reasons=[],
                    severity=ReviewSeverity.LOW,
                )
            )
