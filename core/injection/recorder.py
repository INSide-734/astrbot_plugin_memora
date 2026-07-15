"""Bounded, asynchronous persistence for sanitized injection decisions."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from core.monitoring.metrics import (
    INJECTION_BUDGET_DROP_RATIO,
    INJECTION_CANDIDATE_RETENTION_RATIO,
    INJECTION_DECISIONS_TOTAL,
    INJECTION_DECISION_QUEUE_SECONDS,
    INJECTION_DECISION_RECORD_DROPPED_TOTAL,
    INJECTION_DECISION_RECORD_FAILURES_TOTAL,
    INJECTION_HYBRID_CLAMP_TOTAL,
    INJECTION_PAYLOAD_CHARS,
    INJECTION_PRESET_TRANSITIONS_TOTAL,
    INJECTION_PROVIDER_FALLBACK_TOTAL,
    INJECTION_SKIP_TOTAL,
    INJECTION_STAGE_SECONDS,
    INJECTION_TRUNCATION_RATIO,
)
from core.storage.injection_decision_store import InjectionDecisionStore

from .models import InjectionDecisionRecord

__all__ = ["InjectionDecisionRecorder"]

_PRESET_RANKS = {"tool_first": 0, "low_cost": 1, "balanced": 2, "quality": 3}


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
        if retention_days < 1:
            raise ValueError("retention_days must be at least 1")
        if max_rows < 1:
            raise ValueError("max_rows must be at least 1")
        if queue_capacity < 1 or batch_size < 1:
            raise ValueError("queue_capacity and batch_size must be at least 1")
        if flush_interval <= 0 or retry_base_delay <= 0:
            raise ValueError("intervals must be positive")
        self.store = store
        self._queue: asyncio.Queue[InjectionDecisionRecord] = asyncio.Queue(queue_capacity)
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
        self._idle = asyncio.Event()
        self._idle.set()
        self._retained_batch: list[InjectionDecisionRecord] = []
        self._cleanup_requested = False
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
            try:
                self._queue.put_nowait(record)
            except asyncio.QueueFull:
                self._queue.get_nowait()
                self._queue.task_done()
                self._dropped_total += 1
                self._safe_inc(INJECTION_DECISION_RECORD_DROPPED_TOTAL)
                self._queue.put_nowait(record)
            self._idle.clear()
            self._wake.set()
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
        new_retention = self._retention_days if retention_days is None else retention_days
        new_max_rows = self._max_rows if max_rows is None else max_rows
        if new_retention < 1:
            raise ValueError("retention_days must be at least 1")
        if new_max_rows < 1:
            raise ValueError("max_rows must be at least 1")
        self._retention_days = new_retention
        self._max_rows = new_max_rows
        self._cleanup_requested = True
        self._idle.clear()
        self._wake.set()

    def snapshot(self) -> dict[str, int | bool]:
        """Return a stable, sanitized state snapshot."""
        return {
            "queue_size": self._queue.qsize(),
            "retained_size": len(self._retained_batch),
            "dropped_total": self._dropped_total,
            "persisted_total": self._persisted_total,
            "failures_total": self._failures_total,
            "cleanup_requested": self._cleanup_requested,
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
        self._wake.set()
        worker = self._worker
        if worker is None:
            return
        try:
            await asyncio.wait_for(worker, timeout=timeout)
        except asyncio.TimeoutError:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        except asyncio.CancelledError:
            if not worker.cancelled():
                raise
        finally:
            self._worker = None

    async def _run(self) -> None:
        retained: list[InjectionDecisionRecord] = []
        self._retained_batch = retained
        flush_at: float | None = None
        batch_retry_at = 0.0
        batch_attempt = 0
        cleanup_retry_at = 0.0
        cleanup_attempt = 0
        now = self._monotonic()
        next_periodic_cleanup_at = now + 86_400.0
        last_lightweight_cleanup_at = now - 3_600.0
        rows_since_cleanup = 0
        try:
            while True:
                now = self._monotonic()
                periodic_due = now >= next_periodic_cleanup_at
                if periodic_due:
                    self._cleanup_requested = True

                if not retained:
                    while len(retained) < self._batch_size:
                        try:
                            retained.append(self._queue.get_nowait())
                        except asyncio.QueueEmpty:
                            break
                    if retained:
                        flush_at = now + self._flush_interval

                flush_due = bool(retained) and (
                    len(retained) >= self._batch_size
                    or self._closing
                    or (flush_at is not None and now >= flush_at)
                )
                if flush_due and now >= batch_retry_at:
                    try:
                        await self.store.insert_many(list(retained))
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        self._failures_total += 1
                        self._safe_failure("persist")
                        batch_retry_at = now + min(
                            5.0, self._retry_base_delay * (2**batch_attempt)
                        )
                        batch_attempt += 1
                    else:
                        row_count = len(retained)
                        for _ in retained:
                            self._queue.task_done()
                        retained.clear()
                        self._persisted_total += row_count
                        rows_since_cleanup += row_count
                        flush_at = None
                        batch_attempt = 0
                        batch_retry_at = 0.0
                        if rows_since_cleanup >= 1_000:
                            if now - last_lightweight_cleanup_at >= 3_600.0:
                                self._cleanup_requested = True
                                last_lightweight_cleanup_at = now
                            rows_since_cleanup = 0
                        continue

                if (
                    self._cleanup_requested
                    and now >= cleanup_retry_at
                    and not (retained and flush_due)
                ):
                    attempted_periodic = periodic_due
                    try:
                        await self.store.cleanup(self._retention_days, self._max_rows)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        self._failures_total += 1
                        self._safe_failure("cleanup")
                        cleanup_retry_at = now + min(
                            5.0, self._retry_base_delay * (2**cleanup_attempt)
                        )
                        cleanup_attempt += 1
                    else:
                        self._cleanup_requested = False
                        cleanup_attempt = 0
                        cleanup_retry_at = 0.0
                    if attempted_periodic:
                        next_periodic_cleanup_at += 86_400.0
                    if self._cleanup_requested:
                        continue

                if self._closing and not retained and self._queue.empty():
                    self._idle.set()
                    return

                if (
                    not retained
                    and self._queue.empty()
                    and not self._cleanup_requested
                ):
                    self._idle.set()
                else:
                    self._idle.clear()

                deadlines = [next_periodic_cleanup_at]
                if retained and flush_at is not None:
                    deadlines.append(max(flush_at, batch_retry_at))
                if self._cleanup_requested:
                    deadlines.append(cleanup_retry_at)
                delay = max(0.0, min(deadlines) - self._monotonic())
                await self._wait_for_wake(delay)
        finally:
            self._retained_batch = retained
            self._idle.set()

    async def _wait_for_wake(self, delay: float) -> None:
        self._wake.clear()
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

    def _observe_record(self, record: InjectionDecisionRecord) -> None:
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
