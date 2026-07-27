"""Bounded, asynchronous persistence for sanitized injection decisions."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..monitoring.metrics import (
    INJECTION_BUDGET_DROP_RATIO,
    INJECTION_CANDIDATE_RETENTION_RATIO,
    INJECTION_DECISION_QUEUE_SECONDS,
    INJECTION_DECISION_RECORD_DROPPED_TOTAL,
    INJECTION_DECISION_RECORD_FAILURES_TOTAL,
    INJECTION_DECISIONS_TOTAL,
    INJECTION_HYBRID_CLAMP_TOTAL,
    INJECTION_PAYLOAD_CHARS,
    INJECTION_PRESET_TRANSITIONS_TOTAL,
    INJECTION_PROVIDER_FALLBACK_TOTAL,
    INJECTION_SKIP_TOTAL,
    INJECTION_STAGE_SECONDS,
    INJECTION_TRUNCATION_RATIO,
)
from .models import InjectionDecisionRecord

if TYPE_CHECKING:
    from ..storage.injection_decision_store import InjectionDecisionStore

__all__ = ["InjectionDecisionRecorder"]

_PRESET_RANKS = {"tool_first": 0, "low_cost": 1, "balanced": 2, "quality": 3}


@dataclass(slots=True)
class RecorderWorkerState:
    retained: list[InjectionDecisionRecord]
    flush_at: float | None
    batch_retry_at: float
    batch_attempt: int
    cleanup_retry_at: float
    cleanup_attempt: int
    next_periodic_cleanup_at: float
    last_lightweight_cleanup_at: float
    rows_since_cleanup: int = 0


class InjectionDecisionRecorder:
    """Persist sanitized decisions off the request path with bounded memory use."""

    def __init__(
        self,
        store: InjectionDecisionStore,
        *,
        retention_days: int = 30,
        max_rows: int = 100_000,
        queue_capacity: int = 10_000,
        batch_size: int = 50,
        flush_interval: float = 0.250,
        retry_base_delay: float = 0.050,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if retention_days < 0:
            raise ValueError("retention_days must be non-negative")
        if max_rows < 1:
            raise ValueError("max_rows must be at least 1")
        if queue_capacity < 1 or batch_size < 1:
            raise ValueError("queue_capacity and batch_size must be at least 1")
        if flush_interval <= 0 or retry_base_delay <= 0:
            raise ValueError("intervals must be positive")
        self.store = store
        self._queue: asyncio.Queue[InjectionDecisionRecord] = asyncio.Queue(
            queue_capacity
        )
        self._queue_capacity = queue_capacity
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._retry_base_delay = retry_base_delay
        self._retention_days = retention_days
        self._max_rows = max_rows
        self._monotonic = monotonic
        self._sleep = sleep
        self._worker: asyncio.Task[None] | None = None
        self._closing = False
        self._wake = asyncio.Event()
        self._wake_generation = 0
        self._idle = asyncio.Event()
        self._idle.set()
        self._retained_batch: list[InjectionDecisionRecord] = []
        self._cleanup_generation = 0
        self._cleanup_completed_generation = 0
        self._dropped_total = 0
        self._persisted_total = 0
        self._failures_total = 0

    async def start(self) -> None:
        """Start the sole persistence worker once."""
        if self._closing:
            return
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(
                self._run(), name="memora-injection-decision-recorder"
            )

    def record(self, record: InjectionDecisionRecord) -> None:
        """Enqueue one already-sanitized record without awaiting or doing I/O."""
        started = self._monotonic()
        try:
            if self._closing:
                self._failures_total += 1
                self._safe_failure("closed")
                return
            if len(self._retained_batch) + self._queue.qsize() >= self._queue_capacity:
                if self._retained_batch:
                    self._retained_batch.pop(0)
                else:
                    self._queue.get_nowait()
                self._queue.task_done()
                self._count_dropped()
            self._queue.put_nowait(record)
            self._idle.clear()
            self._signal_wake()
            self._observe_record(record)
        except Exception:
            # Observability and bounded-queue races must never reach chat callers.
            self._failures_total += 1
            self._safe_failure("enqueue")
        finally:
            self._safe_observe(
                INJECTION_DECISION_QUEUE_SECONDS,
                max(0.0, self._monotonic() - started),
            )

    def schedule_cleanup(
        self,
        retention_days: int | None = None,
        max_rows: int | None = None,
    ) -> None:
        """Replace pending cleanup limits and wake the worker without doing I/O."""
        if self._closing:
            self._failures_total += 1
            self._safe_failure("closed")
            return
        new_retention = (
            self._retention_days if retention_days is None else retention_days
        )
        new_max_rows = self._max_rows if max_rows is None else max_rows
        if new_retention < 0:
            raise ValueError("retention_days must be non-negative")
        if new_max_rows < 1:
            raise ValueError("max_rows must be at least 1")
        self._retention_days = new_retention
        self._max_rows = new_max_rows
        self._cleanup_generation += 1
        self._idle.clear()
        self._signal_wake()

    def snapshot(self) -> dict[str, int | bool]:
        """Return a stable, sanitized state snapshot."""
        return {
            "queue_size": self._queue.qsize(),
            "retained_size": len(self._retained_batch),
            "dropped_total": self._dropped_total,
            "persisted_total": self._persisted_total,
            "failures_total": self._failures_total,
            "cleanup_requested": self._cleanup_pending(),
            "running": self._worker is not None and not self._worker.done(),
            "closing": self._closing,
        }

    def queued_decision_ids(self) -> list[str]:
        """Expose ordered sanitized identifiers as a deterministic test seam."""
        queued = list(self._queue._queue)  # noqa: SLF001 - asyncio.Queue has no snapshot API
        return [row.decision_id for row in self._retained_batch] + [
            row.decision_id for row in queued
        ]

    async def wait_until_idle(self, timeout: float = 5.0) -> None:
        """Wait until persistence and requested cleanup have completed."""
        await asyncio.wait_for(self._idle.wait(), timeout=timeout)

    async def close(self, timeout: float = 5.0) -> None:
        """Flush queued rows, then cancel a worker that exceeds the deadline."""
        if self._closing and self._worker is None:
            return
        self._closing = True
        self._signal_wake()
        worker = self._worker
        if worker is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(worker), timeout=timeout)
        except asyncio.TimeoutError:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
        finally:
            if worker.done() and self._worker is worker:
                self._worker = None

    async def _run(self) -> None:
        state = self._new_worker_state()
        try:
            while True:
                observed_wake_generation = self._wake_generation
                now = self._monotonic()
                self._schedule_periodic_cleanup(state, now)
                cleanup_due = self._cleanup_pending() and now >= state.cleanup_retry_at
                self._fill_retained_batch(state, now, cleanup_due)
                if await self._flush_retained_if_due(state, now):
                    continue
                if await self._run_cleanup_if_due(state):
                    continue
                if self._closing and not state.retained and self._queue.empty():
                    self._idle.set()
                    return
                self._update_idle_state(state)
                delay = self._next_worker_delay(state)
                await self._wait_for_wake(delay, observed_wake_generation)
        finally:
            if (
                not state.retained
                and self._queue.empty()
                and not self._cleanup_pending()
            ):
                self._idle.set()
            else:
                self._idle.clear()

    def _new_worker_state(self) -> RecorderWorkerState:
        now = self._monotonic()
        return RecorderWorkerState(
            retained=self._retained_batch,
            flush_at=now + self._flush_interval if self._retained_batch else None,
            batch_retry_at=0.0,
            batch_attempt=0,
            cleanup_retry_at=0.0,
            cleanup_attempt=0,
            next_periodic_cleanup_at=now + 86_400.0,
            last_lightweight_cleanup_at=now - 3_600.0,
        )

    def _schedule_periodic_cleanup(
        self, state: RecorderWorkerState, now: float
    ) -> None:
        if now < state.next_periodic_cleanup_at:
            return
        self._cleanup_generation += 1
        self._idle.clear()
        state.next_periodic_cleanup_at += 86_400.0

    def _fill_retained_batch(
        self,
        state: RecorderWorkerState,
        now: float,
        cleanup_due: bool,
    ) -> None:
        if cleanup_due or (state.retained and state.batch_attempt > 0):
            return
        was_empty = not state.retained
        while len(state.retained) < self._batch_size:
            try:
                state.retained.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if was_empty and state.retained:
            state.flush_at = now + self._flush_interval

    async def _flush_retained_if_due(
        self, state: RecorderWorkerState, now: float
    ) -> bool:
        if not self._flush_is_due(state, now) or now < state.batch_retry_at:
            return False
        attempted = list(state.retained)
        del state.retained[: len(attempted)]
        try:
            await self.store.insert_many(attempted)
        except asyncio.CancelledError:
            self._restore_failed_batch(state, attempted)
            raise
        except Exception:
            self._restore_failed_batch(state, attempted)
            self._failures_total += 1
            self._safe_failure("persist")
            state.batch_retry_at = self._monotonic() + self._retry_delay(
                state.batch_attempt
            )
            state.batch_attempt += 1
            return False
        self._complete_persist(state, attempted)
        return True

    def _flush_is_due(self, state: RecorderWorkerState, now: float) -> bool:
        return bool(state.retained) and (
            state.batch_attempt > 0
            or len(state.retained) >= self._batch_size
            or self._closing
            or (state.flush_at is not None and now >= state.flush_at)
        )

    def _restore_failed_batch(
        self,
        state: RecorderWorkerState,
        attempted: list[InjectionDecisionRecord],
    ) -> None:
        state.retained[:0] = attempted
        self._trim_pending_after_failed_attempt()

    def _complete_persist(
        self,
        state: RecorderWorkerState,
        attempted: list[InjectionDecisionRecord],
    ) -> None:
        for _ in attempted:
            self._queue.task_done()
        row_count = len(attempted)
        self._persisted_total += row_count
        state.rows_since_cleanup += row_count
        state.flush_at = None
        state.batch_attempt = 0
        state.batch_retry_at = 0.0
        self._schedule_lightweight_cleanup(state)

    def _schedule_lightweight_cleanup(self, state: RecorderWorkerState) -> None:
        if state.rows_since_cleanup < 1_000:
            return
        completed_at = self._monotonic()
        if completed_at - state.last_lightweight_cleanup_at >= 3_600.0:
            self._cleanup_generation += 1
            self._idle.clear()
            state.last_lightweight_cleanup_at = completed_at
        state.rows_since_cleanup = 0

    async def _run_cleanup_if_due(self, state: RecorderWorkerState) -> bool:
        now = self._monotonic()
        if not self._cleanup_pending() or now < state.cleanup_retry_at:
            return False
        serviced_generation = self._cleanup_generation
        try:
            await self.store.cleanup(self._retention_days, self._max_rows)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._failures_total += 1
            self._safe_failure("cleanup")
            state.cleanup_retry_at = self._monotonic() + self._retry_delay(
                state.cleanup_attempt
            )
            state.cleanup_attempt += 1
            return False
        self._cleanup_completed_generation = serviced_generation
        state.cleanup_attempt = 0
        state.cleanup_retry_at = 0.0
        return True

    def _update_idle_state(self, state: RecorderWorkerState) -> None:
        if not state.retained and self._queue.empty() and not self._cleanup_pending():
            self._idle.set()
        else:
            self._idle.clear()

    def _next_worker_delay(self, state: RecorderWorkerState) -> float:
        deadlines = [state.next_periodic_cleanup_at]
        if state.retained:
            if (
                state.batch_attempt > 0
                or self._closing
                or len(state.retained) >= self._batch_size
            ):
                deadlines.append(state.batch_retry_at)
            elif state.flush_at is not None:
                deadlines.append(max(state.flush_at, state.batch_retry_at))
        if self._cleanup_pending():
            deadlines.append(state.cleanup_retry_at)
        return max(0.0, min(deadlines) - self._monotonic())

    async def _wait_for_wake(self, delay: float, observed_generation: int) -> None:
        if self._wake_generation != observed_generation:
            return
        self._wake.clear()
        if self._wake_generation != observed_generation:
            return
        wake_task = asyncio.create_task(self._wake.wait())
        sleep_task = asyncio.create_task(self._sleep(delay))
        try:
            done, pending = await asyncio.wait(
                {wake_task, sleep_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
        except asyncio.CancelledError:
            wake_task.cancel()
            sleep_task.cancel()
            await asyncio.gather(wake_task, sleep_task, return_exceptions=True)
            raise

    def _trim_pending_after_failed_attempt(self) -> None:
        while len(self._retained_batch) + self._queue.qsize() > self._queue_capacity:
            if self._retained_batch:
                self._retained_batch.pop(0)
            else:
                self._queue.get_nowait()
            self._queue.task_done()
            self._count_dropped()

    def _count_dropped(self) -> None:
        self._dropped_total += 1
        self._safe_inc(INJECTION_DECISION_RECORD_DROPPED_TOTAL)

    def _signal_wake(self) -> None:
        self._wake_generation += 1
        self._wake.set()

    def _cleanup_pending(self) -> bool:
        return self._cleanup_generation > self._cleanup_completed_generation

    def _retry_delay(self, attempt: int) -> float:
        if self._retry_base_delay >= 5.0:
            return 5.0
        exponent_to_cap = max(
            0,
            math.ceil(math.log2(5.0) - math.log2(self._retry_base_delay)),
        )
        exponent = min(attempt, exponent_to_cap)
        return min(5.0, math.ldexp(self._retry_base_delay, exponent))

    def _observe_record(self, record: InjectionDecisionRecord) -> None:
        self._observe_decision_counters(record)
        self._observe_decision_ratios(record)
        self._observe_stage_timings(record)

    def _observe_decision_counters(self, record: InjectionDecisionRecord) -> None:
        self._safe_labeled_inc(
            INJECTION_DECISIONS_TOTAL,
            routing_mode=record.routing_mode,
            resolved_preset=record.resolved_preset,
            outcome=record.outcome,
        )
        if record.fallback_applied:
            self._safe_labeled_inc(
                INJECTION_PROVIDER_FALLBACK_TOTAL, reason=record.primary_reason
            )
        if record.routing_mode == "auto" and (
            record.recommended_preset != record.configured_preset
        ):
            self._safe_labeled_inc(
                INJECTION_PRESET_TRANSITIONS_TOTAL,
                configured=record.configured_preset,
                recommended=record.recommended_preset,
                resolved=record.resolved_preset,
            )
        if record.routing_mode == "hybrid" and (
            record.resolved_preset != record.recommended_preset
        ):
            resolved_rank = _PRESET_RANKS.get(record.resolved_preset, 0)
            recommended_rank = _PRESET_RANKS.get(record.recommended_preset, 0)
            boundary = "min" if resolved_rank > recommended_rank else "max"
            self._safe_labeled_inc(INJECTION_HYBRID_CLAMP_TOTAL, boundary=boundary)
        if record.outcome in {"skipped", "empty"}:
            self._safe_labeled_inc(INJECTION_SKIP_TOTAL, reason=record.primary_reason)

    def _observe_decision_ratios(self, record: InjectionDecisionRecord) -> None:
        self._safe_observe(INJECTION_PAYLOAD_CHARS, max(0, record.actual_payload_chars))
        candidates = max(0, record.candidate_count)
        selected = max(0, record.selected_count)
        self._safe_observe(
            INJECTION_CANDIDATE_RETENTION_RATIO,
            selected / candidates if candidates else 0.0,
        )
        self._safe_observe(
            INJECTION_BUDGET_DROP_RATIO,
            max(0, record.dropped_count) / candidates if candidates else 0.0,
        )
        self._safe_observe(
            INJECTION_TRUNCATION_RATIO,
            max(0, record.truncated_count) / selected if selected else 0.0,
        )

    def _observe_stage_timings(self, record: InjectionDecisionRecord) -> None:
        for stage, milliseconds in (
            ("decision", record.decision_ms),
            ("format", record.format_ms),
            ("inject", record.inject_ms),
        ):
            self._safe_labeled_observe(
                INJECTION_STAGE_SECONDS, max(0.0, milliseconds) / 1000.0, stage=stage
            )

    def _safe_failure(self, error_code: str) -> None:
        self._safe_labeled_inc(
            INJECTION_DECISION_RECORD_FAILURES_TOTAL, error_code=error_code
        )

    @staticmethod
    def _safe_inc(metric: Any) -> None:
        try:
            metric.inc()
        except Exception:
            pass

    @staticmethod
    def _safe_labeled_inc(metric: Any, **labels: str) -> None:
        try:
            metric.labels(**labels).inc()
        except Exception:
            pass

    @staticmethod
    def _safe_observe(metric: Any, value: float) -> None:
        try:
            metric.observe(value)
        except Exception:
            pass

    @staticmethod
    def _safe_labeled_observe(metric: Any, value: float, **labels: str) -> None:
        try:
            metric.labels(**labels).observe(value)
        except Exception:
            pass
