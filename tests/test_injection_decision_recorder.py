from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.injection.models import InjectionDecisionRecord
from core.injection.recorder import InjectionDecisionRecorder


@pytest.fixture
def store():
    value = MagicMock()
    value.insert_many = AsyncMock(side_effect=lambda rows: len(rows))
    value.cleanup = AsyncMock(return_value=0)
    return value


@pytest.fixture
def make_record():
    def factory(decision_id: str, **changes: object) -> InjectionDecisionRecord:
        base = InjectionDecisionRecord(
            decision_id=decision_id,
            created_at_ms=1,
            routing_mode="auto",
            configured_preset="balanced",
            recommended_preset="quality",
            resolved_preset="quality",
            preferred_delivery="extra_user_content",
            resolved_delivery="extra_user_content",
            fallback_applied=False,
            outcome="injected",
            primary_reason="auto_selection",
            candidate_count=4,
            selected_count=2,
            dropped_count=1,
            truncated_count=1,
            actual_payload_chars=120,
            decision_ms=2.0,
            format_ms=3.0,
            inject_ms=4.0,
        )
        return replace(base, **changes)

    return factory


@pytest.mark.asyncio
async def test_flushes_at_fifty_records(store, make_record) -> None:
    recorder = InjectionDecisionRecorder(store, batch_size=50, flush_interval=60.0)
    await recorder.start()
    for index in range(50):
        recorder.record(make_record(f"decision-{index}"))
    await recorder.wait_until_idle(timeout=1.0)
    await recorder.close(timeout=1.0)
    assert store.insert_many.await_count == 1
    assert len(store.insert_many.await_args.args[0]) == 50


@pytest.mark.asyncio
async def test_flushes_partial_batch_after_interval(store, make_record) -> None:
    recorder = InjectionDecisionRecorder(store, flush_interval=0.005)
    await recorder.start()
    recorder.record(make_record("interval"))
    await recorder.wait_until_idle(timeout=1.0)
    assert store.insert_many.await_count == 1
    await recorder.close(timeout=1.0)


@pytest.mark.asyncio
async def test_failed_batch_retries_before_later_rows_and_record_is_sync(store, make_record) -> None:
    calls: list[list[str]] = []

    async def insert(rows):
        calls.append([row.decision_id for row in rows])
        if len(calls) == 1:
            raise RuntimeError("locked")
        return len(rows)

    store.insert_many = AsyncMock(side_effect=insert)
    recorder = InjectionDecisionRecorder(store, batch_size=1, retry_base_delay=0.001)
    await recorder.start()
    assert recorder.record(make_record("first")) is None
    recorder.record(make_record("later"))
    await recorder.wait_until_idle(timeout=1.0)
    await recorder.close(timeout=1.0)
    assert calls == [["first"], ["first"], ["later"]]
    assert recorder.snapshot()["failures_total"] == 1


def test_queue_overflow_drops_oldest_and_counts_it(store, make_record) -> None:
    recorder = InjectionDecisionRecorder(store, queue_capacity=2)
    recorder.record(make_record("oldest"))
    recorder.record(make_record("middle"))
    recorder.record(make_record("latest"))
    assert recorder.snapshot()["dropped_total"] == 1
    assert recorder.queued_decision_ids() == ["middle", "latest"]


@pytest.mark.asyncio
async def test_schedule_cleanup_validates_replaces_limits_and_retries(store) -> None:
    store.cleanup = AsyncMock(side_effect=[RuntimeError("locked"), 3])
    recorder = InjectionDecisionRecorder(store, retry_base_delay=0.001)
    with pytest.raises(ValueError):
        recorder.schedule_cleanup(retention_days=0)
    with pytest.raises(ValueError):
        recorder.schedule_cleanup(max_rows=0)
    recorder.schedule_cleanup(retention_days=7, max_rows=321)
    await recorder.start()
    await recorder.wait_until_idle(timeout=1.0)
    await recorder.close(timeout=1.0)
    assert store.cleanup.await_count == 2
    assert store.cleanup.await_args.args == (7, 321)
    assert recorder.snapshot()["cleanup_requested"] is False


@pytest.mark.asyncio
async def test_thousand_persisted_rows_schedule_rate_limited_cleanup(store, make_record) -> None:
    recorder = InjectionDecisionRecorder(store, batch_size=1000, queue_capacity=1001)
    await recorder.start()
    for index in range(1000):
        recorder.record(make_record(str(index)))
    await recorder.wait_until_idle(timeout=1.0)
    assert store.cleanup.await_count == 1
    for index in range(1000, 2000):
        recorder.record(make_record(str(index)))
    await recorder.wait_until_idle(timeout=1.0)
    assert store.cleanup.await_count == 1
    await recorder.close(timeout=1.0)


@pytest.mark.asyncio
async def test_periodic_cleanup_uses_injected_monotonic(store) -> None:
    clock = [0.0]
    recorder = InjectionDecisionRecorder(store, monotonic=lambda: clock[0])
    await recorder.start()
    clock[0] = 86_400.0
    recorder.schedule_cleanup()
    await recorder.wait_until_idle(timeout=1.0)
    assert store.cleanup.await_count == 1
    await recorder.close(timeout=1.0)


@pytest.mark.asyncio
async def test_snapshot_queued_ids_and_start_are_stable(store, make_record) -> None:
    recorder = InjectionDecisionRecorder(store, flush_interval=60.0)
    recorder.record(make_record("queued"))
    before = recorder.snapshot()
    assert before["queue_size"] == 1
    assert recorder.queued_decision_ids() == ["queued"]
    await recorder.start()
    worker = recorder._worker
    await recorder.start()
    assert recorder._worker is worker
    await recorder.close(timeout=1.0)
    assert recorder.snapshot()["persisted_total"] == 1


@pytest.mark.asyncio
async def test_cancelled_worker_propagates_cancellation(store) -> None:
    recorder = InjectionDecisionRecorder(store, flush_interval=60.0)
    await recorder.start()
    worker = recorder._worker
    assert worker is not None
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    await recorder.close(timeout=1.0)


@pytest.mark.asyncio
async def test_close_wakes_long_interval_flush_and_is_idempotent(store, make_record) -> None:
    recorder = InjectionDecisionRecorder(store, flush_interval=60.0)
    await recorder.start()
    recorder.record(make_record("shutdown"))
    await recorder.close(timeout=1.0)
    await recorder.close(timeout=1.0)
    assert store.insert_many.await_count == 1


@pytest.mark.asyncio
async def test_close_timeout_cancels_stuck_worker(store, make_record) -> None:
    blocker = asyncio.Event()

    async def stuck(rows):
        await blocker.wait()

    store.insert_many = AsyncMock(side_effect=stuck)
    recorder = InjectionDecisionRecorder(store, batch_size=1)
    await recorder.start()
    recorder.record(make_record("stuck"))
    await asyncio.sleep(0)
    await recorder.close(timeout=0.01)
    assert recorder._worker is None


@pytest.mark.asyncio
async def test_record_during_blocked_cleanup_is_flushed_without_lost_wake(
    store, make_record
) -> None:
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def cleanup(retention_days, max_rows):
        cleanup_started.set()
        await release_cleanup.wait()
        return 0

    store.cleanup = AsyncMock(side_effect=cleanup)
    recorder = InjectionDecisionRecorder(store, batch_size=1, flush_interval=86_400.0)
    recorder.schedule_cleanup()
    await recorder.start()
    await asyncio.wait_for(cleanup_started.wait(), timeout=1.0)
    recorder.record(make_record("during-cleanup"))
    release_cleanup.set()
    await recorder.wait_until_idle(timeout=1.0)
    assert [row.decision_id for row in store.insert_many.await_args.args[0]] == [
        "during-cleanup"
    ]
    await recorder.close(timeout=1.0)


@pytest.mark.asyncio
async def test_cleanup_scheduled_during_active_cleanup_preserves_new_limits(store) -> None:
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    calls: list[tuple[int, int]] = []

    async def cleanup(retention_days, max_rows):
        calls.append((retention_days, max_rows))
        if len(calls) == 1:
            cleanup_started.set()
            await release_cleanup.wait()
        return 0

    store.cleanup = AsyncMock(side_effect=cleanup)
    recorder = InjectionDecisionRecorder(store)
    recorder.schedule_cleanup(retention_days=30, max_rows=100)
    await recorder.start()
    await asyncio.wait_for(cleanup_started.wait(), timeout=1.0)
    recorder.schedule_cleanup(retention_days=7, max_rows=25)
    release_cleanup.set()
    await recorder.wait_until_idle(timeout=1.0)
    assert calls == [(30, 100), (7, 25)]
    await recorder.close(timeout=1.0)


@pytest.mark.asyncio
async def test_requested_cleanup_runs_between_full_batches(store, make_record) -> None:
    first_insert_started = asyncio.Event()
    release_first_insert = asyncio.Event()
    operations: list[str] = []

    async def insert(rows):
        operations.append("insert:" + ",".join(row.decision_id for row in rows))
        if len(operations) == 1:
            first_insert_started.set()
            await release_first_insert.wait()
        return len(rows)

    async def cleanup(retention_days, max_rows):
        operations.append("cleanup")
        return 0

    store.insert_many = AsyncMock(side_effect=insert)
    store.cleanup = AsyncMock(side_effect=cleanup)
    recorder = InjectionDecisionRecorder(store, batch_size=2, queue_capacity=6)
    await recorder.start()
    recorder.record(make_record("0"))
    recorder.record(make_record("1"))
    await asyncio.wait_for(first_insert_started.wait(), timeout=1.0)
    for index in range(2, 6):
        recorder.record(make_record(str(index)))
    recorder.schedule_cleanup()
    release_first_insert.set()
    await recorder.wait_until_idle(timeout=1.0)
    assert operations[:3] == ["insert:0,1", "cleanup", "insert:2,3"]
    await recorder.close(timeout=1.0)


@pytest.mark.asyncio
async def test_open_partial_batch_absorbs_later_rows_and_flushes_at_batch_size(
    store, make_record
) -> None:
    recorder = InjectionDecisionRecorder(store, batch_size=50, flush_interval=86_400.0)
    await recorder.start()
    recorder.record(make_record("0"))
    for _ in range(10):
        await asyncio.sleep(0)
        if recorder.snapshot()["retained_size"] == 1:
            break
    assert recorder.snapshot()["retained_size"] == 1
    for index in range(1, 50):
        recorder.record(make_record(str(index)))
    await recorder.wait_until_idle(timeout=1.0)
    assert len(store.insert_many.await_args.args[0]) == 50
    await recorder.close(timeout=1.0)


@pytest.mark.asyncio
async def test_failed_retained_batch_counts_toward_capacity_and_evicts_global_oldest(
    store, make_record
) -> None:
    failed = asyncio.Event()
    retry_sleep = asyncio.Event()

    async def insert(rows):
        failed.set()
        raise RuntimeError("locked")

    async def sleep(delay):
        await retry_sleep.wait()

    store.insert_many = AsyncMock(side_effect=insert)
    recorder = InjectionDecisionRecorder(
        store,
        batch_size=2,
        queue_capacity=3,
        retry_base_delay=5.0,
        sleep=sleep,
    )
    await recorder.start()
    recorder.record(make_record("oldest"))
    recorder.record(make_record("second"))
    await asyncio.wait_for(failed.wait(), timeout=1.0)
    recorder.record(make_record("third"))
    recorder.record(make_record("latest"))
    await asyncio.sleep(0)
    snapshot = recorder.snapshot()
    assert snapshot["retained_size"] + snapshot["queue_size"] <= 3
    assert recorder.queued_decision_ids() == ["second", "third", "latest"]
    assert snapshot["dropped_total"] == 1
    await recorder.close(timeout=0.01)


@pytest.mark.asyncio
async def test_very_large_retry_attempt_is_capped_and_does_not_kill_worker(
    store, make_record
) -> None:
    recorder = InjectionDecisionRecorder(store, batch_size=1, retry_base_delay=0.001)
    assert recorder._retry_delay(10**100) == 5.0
    store.insert_many = AsyncMock(side_effect=RuntimeError("locked"))
    await recorder.start()
    recorder.record(make_record("retry"))
    await asyncio.sleep(0.01)
    assert recorder.snapshot()["running"] is True
    await recorder.close(timeout=0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["insert", "cleanup"])
async def test_retry_deadline_is_sampled_after_slow_failed_io(
    store, make_record, operation
) -> None:
    clock = [0.0]
    observed_delays: list[float] = []
    retry_waiting = asyncio.Event()
    hold_sleep = asyncio.Event()

    async def fail_after_time(*args):
        clock[0] = 100.0
        raise RuntimeError("slow failure")

    async def sleep(delay):
        observed_delays.append(delay)
        retry_waiting.set()
        await hold_sleep.wait()

    recorder = InjectionDecisionRecorder(
        store,
        batch_size=1,
        flush_interval=0.001,
        retry_base_delay=0.05,
        monotonic=lambda: clock[0],
        sleep=sleep,
    )
    if operation == "insert":
        store.insert_many = AsyncMock(side_effect=fail_after_time)
        recorder.record(make_record("slow-insert"))
    else:
        store.cleanup = AsyncMock(side_effect=fail_after_time)
        recorder.schedule_cleanup()
    await recorder.start()
    await asyncio.wait_for(retry_waiting.wait(), timeout=1.0)
    assert observed_delays[0] == pytest.approx(0.05)
    await recorder.close(timeout=0.01)
