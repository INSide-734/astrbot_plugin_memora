"""Jargon 管理服务的严格 CRUD、并发与缓存失效测试。"""

from __future__ import annotations

import asyncio
import importlib
import json
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from core.jargon.jargon_query import JargonQueryService
from core.jargon.jargon_store import JargonStore
from core.jargon.models import JargonMeaning
from core.shared.entity_editing import (
    EditConflictError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
)


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


def _install_commit_gate(
    store: JargonStore,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[asyncio.Event, asyncio.Event]:
    original_commit = store._commit
    commit_entered = asyncio.Event()
    release_commit = asyncio.Event()

    async def commit_survives_caller_cancellation() -> None:
        commit_entered.set()
        try:
            await asyncio.shield(release_commit.wait())
        except asyncio.CancelledError:
            await release_commit.wait()
            await original_commit()
            raise
        await original_commit()

    monkeypatch.setattr(store, "_commit", commit_survives_caller_cancellation)
    return commit_entered, release_commit


class _ReleaseOnContentionLock:
    """测试锁：第二个等待者到达时释放 gated query read。"""

    def __init__(self, release_query: asyncio.Event) -> None:
        self._lock = asyncio.Lock()
        self._release_query = release_query

    async def __aenter__(self):
        if self._lock.locked():
            self._release_query.set()
        await self._lock.acquire()
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self._lock.release()


async def _invoke_query_path(query: JargonQueryService, path: str) -> Any:
    if path == "query":
        return await query.query("灰度", "g1")
    if path == "check_and_explain":
        return await query.check_and_explain("采用灰度方案", "g1")
    return await query.get_group_jargon("g1")


def _assert_query_meaning(result: Any, path: str, expected: str) -> None:
    if path == "check_and_explain":
        assert isinstance(result, str)
        assert expected in result
        return
    assert result[0]["meaning"] == expected


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
async def test_store_maps_real_duplicate_key_to_already_exists(
    tmp_db_path: str,
) -> None:
    store = await _store(tmp_db_path)
    meaning = JargonMeaning(
        term="duplicate",
        group_id="g1",
        meaning="first",
        created_at=1.0,
        updated_at=1.0,
    )
    try:
        await store.create_strict(meaning)
        with pytest.raises(EntityAlreadyExistsError):
            await store.create_strict(replace(meaning, meaning="second"))
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_store_does_not_misclassify_non_unique_integrity_failure(
    tmp_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = await _store(tmp_db_path)
    try:
        monkeypatch.setattr(
            store,
            "_rollback_safely",
            AsyncMock(side_effect=RuntimeError("rollback failure")),
        )
        invalid = JargonMeaning(
            term=None,  # type: ignore[arg-type]
            group_id="g1",
            meaning="invalid",
            created_at=1.0,
            updated_at=1.0,
        )
        with pytest.raises(aiosqlite.IntegrityError) as caught:
            await store.create_strict(invalid)
        assert "NOT NULL" in str(caught.value)
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

        assert (
            await service.delete(
                term="灰度",
                group_id="g1",
                expected_revision=service.revision_for(created),
            )
            is True
        )
        assert await store.get_by_term("灰度", "g1") is None
        assert invalidated == ["g1"]
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update", "delete"])
async def test_invalidation_failure_does_not_reverse_committed_mutation(
    tmp_db_path: str,
    caplog: pytest.LogCaptureFixture,
    operation: str,
) -> None:
    store = await _store(tmp_db_path)
    secret = "invalidation-secret-value"
    group_id = "sensitive-group-id"

    def failing_invalidator(_group_id: str) -> None:
        raise RuntimeError(secret)

    try:
        service = _service_class()(store)
        if operation == "create":
            service = _service_class()(store, failing_invalidator)
            result = await _create(service, group_id=group_id)
            assert isinstance(result, JargonMeaning)
            assert await store.get_by_term("灰度", group_id) == result
        else:
            created = await _create(service, group_id=group_id)
            service = _service_class()(store, failing_invalidator)
            if operation == "update":
                result = await service.update(
                    term="灰度",
                    group_id=group_id,
                    changes={"meaning": "committed update"},
                    expected_revision=service.revision_for(created),
                )
                assert isinstance(result, JargonMeaning)
                assert await store.get_by_term("灰度", group_id) == result
            else:
                result = await service.delete(
                    term="灰度",
                    group_id=group_id,
                    expected_revision=service.revision_for(created),
                )
                assert result is True
                assert await store.get_by_term("灰度", group_id) is None

        assert "cache_invalidation_failed" in caplog.text
        assert "RuntimeError" in caplog.text
        assert operation in caplog.text
        assert secret not in caplog.text
        assert group_id not in caplog.text
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
                {"meaning": "local"},
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
async def test_store_rejects_direct_is_complete_update(
    tmp_db_path: str,
) -> None:
    store = await _store(tmp_db_path)
    try:
        service = _service_class()(store)
        created = await _create(service)
        with pytest.raises(EntityValidationError) as caught:
            await store.update_if_revision(
                "灰度",
                "g1",
                {"is_complete": True},
                expected_revision=service.revision_for(created),
            )
        assert caught.value.field_errors == {"is_complete": "字段不可写"}
        assert await store.get_by_term("灰度", "g1") == created
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
@pytest.mark.parametrize("writer_name", ["upsert", "confirm", "delete"])
async def test_legacy_writer_cannot_enter_admin_transaction(
    tmp_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    writer_name: str,
) -> None:
    store = await _store(tmp_db_path)
    try:
        service = _service_class()(store)
        created = await _create(service)
        revision = service.revision_for(created)
        admin_read = asyncio.Event()
        release_admin = asyncio.Event()
        writer_started = asyncio.Event()
        writer_entered_connection = asyncio.Event()
        completed: list[str] = []

        original_fetch = store._fetch_one
        original_execute = store._execute
        fetch_count = 0
        writer_task: asyncio.Task[None] | None = None

        async def gated_fetch(sql: str, params: tuple = ()):
            nonlocal fetch_count
            row = await original_fetch(sql, params)
            fetch_count += 1
            if fetch_count == 1:
                admin_read.set()
                await release_admin.wait()
            return row

        async def observed_execute(sql: str, params: tuple = ()):
            if asyncio.current_task() is writer_task:
                writer_entered_connection.set()
            return await original_execute(sql, params)

        monkeypatch.setattr(store, "_fetch_one", gated_fetch)
        monkeypatch.setattr(store, "_execute", observed_execute)

        async def run_admin() -> None:
            await service.update(
                term="灰度",
                group_id="g1",
                changes={"meaning": "administrator"},
                expected_revision=revision,
            )
            completed.append("admin")

        async def run_writer() -> None:
            writer_started.set()
            if writer_name == "upsert":
                await store.upsert(replace(created, meaning="automatic"))
            elif writer_name == "confirm":
                await store.confirm("灰度", "g1", confirmed=True)
            else:
                await store.delete("灰度", "g1")
            completed.append("writer")

        admin_task = asyncio.create_task(run_admin())
        await admin_read.wait()
        writer_task = asyncio.create_task(run_writer())
        await writer_started.wait()
        try:
            assert writer_entered_connection.is_set() is False
        finally:
            release_admin.set()
            await asyncio.gather(admin_task, writer_task, return_exceptions=True)

        assert writer_entered_connection.is_set() is True
        assert completed == ["admin", "writer"]
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update", "delete"])
async def test_cancelled_mutation_resolves_commit_then_invalidates(
    tmp_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    store = await _store(tmp_db_path)
    invalidated: list[str] = []
    try:
        setup = _service_class()(store)
        created: JargonMeaning | None = None
        if operation != "create":
            created = await _create(setup)

        commit_entered, release_commit = _install_commit_gate(store, monkeypatch)
        service = _service_class()(store, invalidated.append)
        if operation == "create":
            mutation = _create(service)
        elif operation == "update":
            assert created is not None
            mutation = service.update(
                term="灰度",
                group_id="g1",
                changes={"meaning": "committed after cancellation"},
                expected_revision=service.revision_for(created),
            )
        else:
            assert created is not None
            mutation = service.delete(
                term="灰度",
                group_id="g1",
                expected_revision=service.revision_for(created),
            )

        task = asyncio.create_task(mutation)
        await commit_entered.wait()
        task.cancel()
        release_commit.set()
        with pytest.raises(asyncio.CancelledError):
            await task

        current = await store.get_by_term("灰度", "g1")
        if operation == "delete":
            assert current is None
        else:
            assert current is not None
            if operation == "update":
                assert current.meaning == "committed after cancellation"
        assert invalidated == ["g1"]
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
                {"meaning": "must roll back"},
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
async def test_batch_reports_success_when_post_commit_invalidation_fails(
    tmp_db_path: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = await _store(tmp_db_path)
    secret = "batch-invalidation-secret"

    def failing_invalidator(_group_id: str) -> None:
        raise RuntimeError(secret)

    try:
        setup = _service_class()(store)
        created = await _create(setup)
        service = _service_class()(store, failing_invalidator)
        result = await service.batch(
            action="delete",
            items=[
                {
                    "identity": {"term": "灰度", "group_id": "g1"},
                    "expected_revision": service.revision_for(created),
                }
            ],
        )

        assert result["succeeded_count"] == 1
        assert result["failed_count"] == 0
        assert await store.get_by_term("灰度", "g1") is None
        assert "RuntimeError" in caplog.text
        assert secret not in caplog.text
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_batch_propagates_cancellation_after_committed_item_invalidation(
    tmp_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = await _store(tmp_db_path)
    invalidated: list[str] = []
    try:
        setup = _service_class()(store)
        created = await _create(setup)
        commit_entered, release_commit = _install_commit_gate(store, monkeypatch)
        service = _service_class()(store, invalidated.append)
        task = asyncio.create_task(
            service.batch(
                action="delete",
                items=[
                    {
                        "identity": {"term": "灰度", "group_id": "g1"},
                        "expected_revision": service.revision_for(created),
                    }
                ],
            )
        )
        await commit_entered.wait()
        task.cancel()
        release_commit.set()

        with pytest.raises(asyncio.CancelledError):
            await task
        assert await store.get_by_term("灰度", "g1") is None
        assert invalidated == ["g1"]
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ["query", "check_and_explain", "get_group_jargon"],
)
async def test_query_first_cache_population_is_cleared_by_following_write(
    tmp_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    store = await _store(tmp_db_path)
    release_query = asyncio.Event()
    db_read_complete = asyncio.Event()
    try:
        setup = _service_class()(store)
        created = await _create(
            setup,
            meaning="old meaning",
            is_jargon=True,
            is_confirmed=True,
        )
        query = JargonQueryService(store)
        read_method_name = "search" if path == "query" else "list_by_group"
        original_read = getattr(store, read_method_name)

        async def gated_read(*args, **kwargs):
            result = await original_read(*args, **kwargs)
            db_read_complete.set()
            await release_query.wait()
            return result

        monkeypatch.setattr(store, read_method_name, gated_read)
        store._write_lock = _ReleaseOnContentionLock(release_query)  # type: ignore[assignment]

        query_task = asyncio.create_task(_invoke_query_path(query, path))
        await db_read_complete.wait()

        def invalidate_then_release(group_id: str) -> None:
            query.invalidate_group(group_id)
            release_query.set()

        admin = _service_class()(store, invalidate_then_release)
        await admin.update(
            term="灰度",
            group_id="g1",
            changes={"meaning": "new meaning"},
            expected_revision=admin.revision_for(created),
        )
        first_result = await query_task
        _assert_query_meaning(first_result, path, "old meaning")

        second_result = await _invoke_query_path(query, path)
        _assert_query_meaning(second_result, path, "new meaning")
    finally:
        release_query.set()
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "outcome"),
    [
        ("query", "commit"),
        ("query", "rollback"),
        ("check_and_explain", "commit"),
        ("check_and_explain", "rollback"),
        ("get_group_jargon", "commit"),
        ("get_group_jargon", "rollback"),
    ],
)
async def test_query_cannot_read_inside_writer_transaction(
    tmp_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    outcome: str,
) -> None:
    store = await _store(tmp_db_path)
    release_writer = asyncio.Event()
    update_executed = asyncio.Event()
    query_started = asyncio.Event()
    query_entered_read = asyncio.Event()
    admin_task: asyncio.Task[JargonMeaning] | None = None
    query_task: asyncio.Task[Any] | None = None
    try:
        setup = _service_class()(store)
        created = await _create(
            setup,
            meaning="old meaning",
            is_jargon=True,
            is_confirmed=True,
        )
        original_execute = store._execute
        original_fetch = store._fetch_one
        fetch_count = 0

        async def gated_execute(sql: str, params: tuple = ()):
            result = await original_execute(sql, params)
            if sql.startswith("UPDATE jargon_terms SET "):
                update_executed.set()
                await release_writer.wait()
            return result

        async def maybe_fail_readback(sql: str, params: tuple = ()):
            nonlocal fetch_count
            fetch_count += 1
            if outcome == "rollback" and fetch_count == 2:
                raise RuntimeError("force rollback")
            return await original_fetch(sql, params)

        read_method_name = "search" if path == "query" else "list_by_group"
        original_read = getattr(store, read_method_name)

        async def observed_read(*args, **kwargs):
            query_entered_read.set()
            return await original_read(*args, **kwargs)

        monkeypatch.setattr(store, "_execute", gated_execute)
        monkeypatch.setattr(store, "_fetch_one", maybe_fail_readback)
        monkeypatch.setattr(store, read_method_name, observed_read)

        admin = _service_class()(store)
        admin_task = asyncio.create_task(
            admin.update(
                term="灰度",
                group_id="g1",
                changes={"meaning": "new meaning"},
                expected_revision=admin.revision_for(created),
            )
        )
        await update_executed.wait()

        async def run_query() -> Any:
            query_started.set()
            return await _invoke_query_path(JargonQueryService(store), path)

        query_task = asyncio.create_task(run_query())
        await query_started.wait()
        try:
            assert query_entered_read.is_set() is False
        finally:
            release_writer.set()

        if outcome == "commit":
            await admin_task
            expected = "new meaning"
        else:
            with pytest.raises(RuntimeError, match="force rollback"):
                await admin_task
            expected = "old meaning"
        result = await query_task
        _assert_query_meaning(result, path, expected)
    finally:
        release_writer.set()
        pending = [task for task in (admin_task, query_task) if task is not None]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await store.close()
