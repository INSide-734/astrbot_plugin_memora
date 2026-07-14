"""Jargon 管理服务的严格 CRUD、并发与缓存失效测试。"""

from __future__ import annotations

import asyncio
import importlib
import json
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from core.base.entity_editing import (
    EditConflictError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
)
from core.jargon.jargon_query import JargonQueryService
from core.jargon.jargon_store import JargonStore
from core.jargon.models import JargonMeaning


def _service_class():
    module = importlib.import_module("core.jargon.jargon_admin_service")
    return module.JargonAdminService


async def _store(db_path: str) -> JargonStore:
    store = JargonStore(db_path)
    await store.initialize()
    return store


async def _create(service: Any, **overrides: Any) -> JargonMeaning:
    fields: dict[str, Any] = {
        "term": "灰度",
        "group_id": "g1",
        "meaning": "Gradual rollout",
        "confidence": 0.9,
        "is_jargon": True,
        "is_confirmed": False,
        "is_global": False,
    }
    fields.update(overrides)
    return await service.create(**fields)


@pytest.mark.asyncio
async def test_store_exposes_strict_create_before_service_wiring(
    tmp_db_path: str,
) -> None:
    store = await _store(tmp_db_path)
    try:
        now = 1.0
        created = await store.create_strict(
            JargonMeaning(
                term="strict",
                group_id="g1",
                meaning="strict create",
                confidence=0.5,
                created_at=now,
                updated_at=now,
            )
        )
        assert created.term == "strict"
        assert await store.get_by_term("strict", "g1") == created
    finally:
        await store.close()


def test_jargon_admin_service_exists() -> None:
    assert _service_class().__name__ == "JargonAdminService"


@pytest.mark.asyncio
async def test_manual_create_controls_derived_fields_and_invalidates_after_commit(
    tmp_db_path: str,
) -> None:
    store = await _store(tmp_db_path)
    invalidated: list[str] = []
    try:
        service = _service_class()(store, invalidated.append)
        created = await _create(service, is_confirmed=True)

        assert created.count == 0
        assert created.last_inference_count == 0
        assert created.context_examples == []
        assert created.created_at > 0
        assert created.updated_at >= created.created_at
        assert created.is_complete is True
        assert invalidated == ["g1"]
        assert await store.get_by_term("灰度", "g1") == created
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_manual_create_uses_confirmed_jargon_defaults(
    tmp_db_path: str,
) -> None:
    store = await _store(tmp_db_path)
    try:
        service = _service_class()(store)
        created = await service.create(
            term="灰度",
            group_id="g1",
            meaning="Gradual rollout",
            confidence=0.9,
        )
        assert created.is_jargon is True
        assert created.is_confirmed is True
        assert created.is_global is False
        assert created.is_complete is True
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_duplicate_create_is_strict_and_does_not_invalidate(
    tmp_db_path: str,
) -> None:
    store = await _store(tmp_db_path)
    invalidated: list[str] = []
    try:
        service = _service_class()(store, invalidated.append)
        await _create(service)
        invalidated.clear()

        with pytest.raises(EntityAlreadyExistsError):
            await _create(service, meaning="replacement")

        current = await store.get_by_term("灰度", "g1")
        assert current is not None and current.meaning == "Gradual rollout"
        assert invalidated == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_partial_update_derives_complete_and_refreshes_updated_at(
    tmp_db_path: str,
) -> None:
    store = await _store(tmp_db_path)
    invalidated: list[str] = []
    try:
        service = _service_class()(store, invalidated.append)
        created = await _create(service)
        invalidated.clear()

        updated = await service.update(
            term="灰度",
            group_id="g1",
            changes={"is_confirmed": True},
            expected_revision=service.revision_for(created),
        )

        assert updated.meaning == created.meaning
        assert updated.confidence == created.confidence
        assert updated.is_confirmed is True
        assert updated.is_complete is True
        assert updated.updated_at > created.updated_at
        assert invalidated == ["g1"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_update_keeps_count_threshold_complete_when_unconfirmed(
    tmp_db_path: str,
) -> None:
    store = await _store(tmp_db_path)
    try:
        await store.upsert(
            JargonMeaning(
                term="灰度",
                group_id="g1",
                meaning="automatic",
                confidence=0.8,
                is_jargon=True,
                is_confirmed=False,
                is_complete=False,
                count=100,
            )
        )
        current = await store.get_by_term("灰度", "g1")
        assert current is not None
        service = _service_class()(store)
        updated = await service.update(
            term="灰度",
            group_id="g1",
            changes={"meaning": "edited"},
            expected_revision=service.revision_for(current),
        )
        assert updated.is_confirmed is False
        assert updated.count == 100
        assert updated.is_complete is True
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_delete_is_revisioned_and_invalidates_once(tmp_db_path: str) -> None:
    store = await _store(tmp_db_path)
    invalidated: list[str] = []
    try:
        service = _service_class()(store, invalidated.append)
        created = await _create(service)
        invalidated.clear()

        assert await service.delete(
            term="灰度",
            group_id="g1",
            expected_revision=service.revision_for(created),
        ) is True
        assert await store.get_by_term("灰度", "g1") is None
        assert invalidated == ["g1"]
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_not_found_is_distinct_and_never_invalidates(
    tmp_db_path: str,
    operation: str,
) -> None:
    store = await _store(tmp_db_path)
    invalidated: list[str] = []
    try:
        service = _service_class()(store, invalidated.append)
        kwargs = {
            "term": "missing",
            "group_id": "g1",
            "expected_revision": "revision",
        }
        if operation == "update":
            kwargs["changes"] = {"meaning": "new"}
        with pytest.raises(EntityNotFoundError):
            await getattr(service, operation)(**kwargs)
        assert invalidated == []
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_stale_revision_preserves_entity_and_never_invalidates(
    tmp_db_path: str,
    operation: str,
) -> None:
    store = await _store(tmp_db_path)
    invalidated: list[str] = []
    try:
        service = _service_class()(store, invalidated.append)
        created = await _create(service)
        invalidated.clear()
        kwargs = {
            "term": "灰度",
            "group_id": "g1",
            "expected_revision": "stale",
        }
        if operation == "update":
            kwargs["changes"] = {"meaning": "local"}

        with pytest.raises(EditConflictError) as caught:
            await getattr(service, operation)(**kwargs)

        assert caught.value.current_revision == service.revision_for(created)
        assert caught.value.current_entity["meaning"] == "Gradual rollout"
        assert await store.get_by_term("灰度", "g1") == created
        assert invalidated == []
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"term": "   "}, "term"),
        ({"group_id": True}, "group_id"),
        ({"meaning": ""}, "meaning"),
        ({"confidence": float("nan")}, "confidence"),
        ({"confidence": 1.1}, "confidence"),
        ({"is_jargon": 1}, "is_jargon"),
        ({"count": 7}, "count"),
        ({"unknown": "value"}, "unknown"),
    ],
)
async def test_create_rejects_invalid_unknown_and_server_controlled_fields(
    tmp_db_path: str,
    overrides: dict[str, Any],
    field: str,
) -> None:
    store = await _store(tmp_db_path)
    invalidated: list[str] = []
    try:
        service = _service_class()(store, invalidated.append)
        with pytest.raises(EntityValidationError) as caught:
            await _create(service, **overrides)
        assert field in caught.value.field_errors
        assert await store.count_by_group("g1") == 0
        assert invalidated == []
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"term": "renamed"},
        {"group_id": "g2"},
        {"count": 9},
        {"last_inference_count": 9},
        {"context_examples": ["secret"]},
        {"created_at": 1.0},
        {"updated_at": 2.0},
        {"is_complete": True},
        {"unknown": True},
        {"confidence": float("inf")},
        {"is_confirmed": 1},
        {},
    ],
)
async def test_update_rejects_read_only_unknown_and_invalid_changes(
    tmp_db_path: str,
    changes: dict[str, Any],
) -> None:
    store = await _store(tmp_db_path)
    invalidated: list[str] = []
    try:
        service = _service_class()(store, invalidated.append)
        created = await _create(service)
        invalidated.clear()
        with pytest.raises(EntityValidationError):
            await service.update(
                term="灰度",
                group_id="g1",
                changes=changes,
                expected_revision=service.revision_for(created),
            )
        assert await store.get_by_term("灰度", "g1") == created
        assert invalidated == []
    finally:
        await store.close()


def test_revision_uses_every_persisted_jargon_field() -> None:
    service = _service_class()
    base = JargonMeaning(
        term="灰度",
        group_id="g1",
        meaning="rollout",
        confidence=0.5,
        is_jargon=True,
        is_confirmed=False,
        is_global=False,
        is_complete=False,
        count=3,
        last_inference_count=2,
        context_examples=["example"],
        created_at=1.0,
        updated_at=2.0,
    )
    changes = {
        "term": "other",
        "group_id": "g2",
        "meaning": "other",
        "confidence": 0.6,
        "is_jargon": False,
        "is_confirmed": True,
        "is_global": True,
        "is_complete": True,
        "count": 4,
        "last_inference_count": 3,
        "context_examples": ["other"],
        "created_at": 2.0,
        "updated_at": 3.0,
    }
    revision = service.revision_for(base)
    for field, value in changes.items():
        assert service.revision_for(replace(base, **{field: value})) != revision


@pytest.mark.asyncio
async def test_store_revision_compare_includes_read_only_state(
    tmp_db_path: str,
) -> None:
    store = await _store(tmp_db_path)
    try:
        service = _service_class()(store)
        created = await _create(service)
        revision = service.revision_for(created)
        await store._execute(
            "UPDATE jargon_terms SET count = ? WHERE term = ? AND group_id = ?",
            (1, "灰度", "g1"),
        )
        await store._commit()

        with pytest.raises(EditConflictError):
            await store.update_if_revision(
                "灰度",
                "g1",
                {"meaning": "local", "is_complete": False},
                expected_revision=revision,
            )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_store_rejects_read_only_update_even_when_called_directly(
    tmp_db_path: str,
) -> None:
    store = await _store(tmp_db_path)
    try:
        service = _service_class()(store)
        created = await _create(service)
        with pytest.raises(EntityValidationError):
            await store.update_if_revision(
                "灰度",
                "g1",
                {"count": 99},
                expected_revision=service.revision_for(created),
            )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_same_revision_concurrency_allows_exactly_one_writer(
    tmp_db_path: str,
) -> None:
    store = await _store(tmp_db_path)
    invalidated: list[str] = []
    try:
        service = _service_class()(store, invalidated.append)
        created = await _create(service)
        revision = service.revision_for(created)
        invalidated.clear()

        results = await asyncio.gather(
            service.update(
                term="灰度",
                group_id="g1",
                changes={"meaning": "writer-a"},
                expected_revision=revision,
            ),
            service.update(
                term="灰度",
                group_id="g1",
                changes={"meaning": "writer-b"},
                expected_revision=revision,
            ),
            return_exceptions=True,
        )

        winners = [item for item in results if isinstance(item, JargonMeaning)]
        conflicts = [item for item in results if isinstance(item, EditConflictError)]
        assert len(winners) == 1
        assert len(conflicts) == 1
        assert invalidated == ["g1"]
        current = await store.get_by_term("灰度", "g1")
        assert current is not None and current.meaning == winners[0].meaning
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_rolled_back_update_does_not_mutate_or_invalidate(
    tmp_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = await _store(tmp_db_path)
    invalidated: list[str] = []
    try:
        service = _service_class()(store, invalidated.append)
        created = await _create(service)
        invalidated.clear()
        original_fetch = store._fetch_one
        call_count = 0

        async def fail_after_write(sql: str, params: tuple = ()):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("read-back failed")
            return await original_fetch(sql, params)

        monkeypatch.setattr(store, "_fetch_one", fail_after_write)
        with pytest.raises(RuntimeError, match="read-back failed"):
            await service.update(
                term="灰度",
                group_id="g1",
                changes={"meaning": "must roll back"},
                expected_revision=service.revision_for(created),
            )
        monkeypatch.setattr(store, "_fetch_one", original_fetch)

        assert await store.get_by_term("灰度", "g1") == created
        assert invalidated == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_rollback_failure_never_replaces_original_error(
    tmp_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = await _store(tmp_db_path)
    try:
        service = _service_class()(store)
        created = await _create(service)
        original_fetch = store._fetch_one
        call_count = 0

        async def fail_after_write(sql: str, params: tuple = ()):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise ValueError("original write failure")
            return await original_fetch(sql, params)

        monkeypatch.setattr(store, "_fetch_one", fail_after_write)
        monkeypatch.setattr(
            store,
            "_rollback_safely",
            AsyncMock(side_effect=RuntimeError("rollback failure")),
        )
        with pytest.raises(ValueError, match="original write failure"):
            await store.update_if_revision(
                "灰度",
                "g1",
                {"meaning": "must roll back", "is_complete": False},
                expected_revision=service.revision_for(created),
            )
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "initial", "field", "expected"),
    [
        ("confirm", {"is_confirmed": False}, "is_confirmed", True),
        ("unconfirm", {"is_confirmed": True}, "is_confirmed", False),
        ("set_global", {"is_global": False}, "is_global", True),
        ("unset_global", {"is_global": True}, "is_global", False),
    ],
)
async def test_batch_dispatches_only_safe_jargon_state_actions(
    tmp_db_path: str,
    action: str,
    initial: dict[str, bool],
    field: str,
    expected: bool,
) -> None:
    store = await _store(tmp_db_path)
    try:
        service = _service_class()(store)
        created = await _create(service, **initial)
        result = await service.batch(
            action=action,
            items=[
                {
                    "identity": {"term": "灰度", "group_id": "g1"},
                    "expected_revision": service.revision_for(created),
                }
            ],
        )

        assert result == {
            "total": 1,
            "succeeded_count": 1,
            "failed_count": 0,
            "succeeded_ids": [{"term": "灰度", "group_id": "g1"}],
            "failures": [],
        }
        current = await store.get_by_term("灰度", "g1")
        assert current is not None and getattr(current, field) is expected
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_batch_keeps_item_failures_independent_ordered_and_json_safe(
    tmp_db_path: str,
) -> None:
    store = await _store(tmp_db_path)
    try:
        service = _service_class()(store)
        first = await _create(service, term="first")
        second = await _create(service, term="second")
        result = await service.batch(
            action="delete",
            items=[
                {
                    "identity": {"term": "first", "group_id": "g1"},
                    "expected_revision": "stale",
                },
                {
                    "identity": {"term": True, "group_id": "g1"},
                    "expected_revision": "invalid-item",
                },
                {
                    "identity": {"term": "second", "group_id": "g1"},
                    "expected_revision": service.revision_for(second),
                },
            ],
        )

        assert result["total"] == 3
        assert result["succeeded_ids"] == [{"term": "second", "group_id": "g1"}]
        assert [failure["code"] for failure in result["failures"]] == [
            "edit_conflict",
            "validation_error",
        ]
        assert result["failures"][0]["identity"] == {
            "term": "first",
            "group_id": "g1",
        }
        assert result["failures"][0]["current_revision"] == service.revision_for(first)
        json.dumps(result, allow_nan=False)
        assert await store.get_by_term("first", "g1") is not None
        assert await store.get_by_term("second", "g1") is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_batch_rejects_arbitrary_updates_before_any_mutation(
    tmp_db_path: str,
) -> None:
    store = await _store(tmp_db_path)
    try:
        service = _service_class()(store)
        created = await _create(service)
        with pytest.raises(EntityValidationError):
            await service.batch(
                action="update",
                items=[
                    {
                        "identity": {"term": "灰度", "group_id": "g1"},
                        "expected_revision": service.revision_for(created),
                        "changes": {"meaning": "bulk replacement"},
                    }
                ],
            )
        assert await store.get_by_term("灰度", "g1") == created
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_invalidate_group_is_synchronous_and_exactly_scoped(
    tmp_db_path: str,
) -> None:
    store = await _store(tmp_db_path)
    try:
        query = JargonQueryService(store)
        query._cache.set("query:g1:term", ["g1"])
        query._cache.set("explain:g1:1", "g1")
        query._cache.set("group:g1", ["g1"])
        query._cache.set("query:g10:term", ["g10"])
        query._cache.set("group:other", ["other"])

        assert query.invalidate_group("g1") is None

        assert query._cache.get("query:g1:term") is None
        assert query._cache.get("explain:g1:1") is None
        assert query._cache.get("group:g1") is None
        assert query._cache.get("query:g10:term") == ["g10"]
        assert query._cache.get("group:other") == ["other"]
    finally:
        await store.close()
