"""Topic candidate 窗口终态去重与安全聚合。"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any

_INTEGER_FIELDS = frozenset(
    {
        "candidate_count_sum",
        "bm25_hit_count",
        "recent_fill_count",
        "identity_drop_count",
        "budget_exceeded_count",
        "catalog_degraded_count",
        "exact_reuse_count",
        "exact_topic_count",
        "duplicate_topic_count",
        "window_topic_count",
        "prompt_chars",
        "prompt_tokens",
    }
)
_FLOAT_FIELDS = frozenset({"selector_duration_ms"})
_AGGREGATE_FIELDS = (
    "candidate_count_sum",
    "bm25_hit_count",
    "recent_fill_count",
    "identity_drop_count",
    "budget_exceeded_count",
    "catalog_degraded_count",
    "exact_reuse_count",
    "exact_topic_count",
    "duplicate_topic_count",
    "window_topic_count",
    "prompt_chars",
    "selector_duration_ms",
)


class TopicCatalogMetricsMixin:
    """以 HMAC 摘要键和窗口终态 CAS 更新本地指标。"""

    _db: Any | None = None

    def _catalog_transaction(self) -> Any:
        """由具体 Store 提供统一 catalog 写事务。"""

        raise NotImplementedError

    async def has_metric_history(self) -> bool:
        """报告任一指标表是否已有历史，防止 key 丢失后以版本一重新计数。"""
        if self._db is None:
            raise RuntimeError("topic_metrics_store_unavailable")
        cursor = await self._db.execute(
            "SELECT EXISTS(SELECT 1 FROM topic_candidate_metric_windows) "
            "OR EXISTS(SELECT 1 FROM topic_candidate_scope_metrics)"
        )
        row = await cursor.fetchone()
        return bool(row and row[0])

    async def record_metric_window(
        self,
        *,
        window_key_hash: str,
        hash_key_version: int,
        terminal_state: str,
        token_source_available: bool,
        scope_key_hash: str,
        bucket_date: str,
        mode: str,
        topic_count_bucket: str,
        values: Mapping[str, int | float | None] | None = None,
        now: float | None = None,
    ) -> bool:
        """占有唯一窗口终态后恰好一次更新 scope 聚合。"""

        if self._db is None or not self._valid_metric_key(window_key_hash):
            return False
        if (
            not self._valid_metric_key(scope_key_hash)
            or not isinstance(hash_key_version, int)
            or isinstance(hash_key_version, bool)
            or hash_key_version <= 0
            or not isinstance(token_source_available, bool)
        ):
            return False
        if terminal_state not in {"success", "failed", "cancelled"}:
            return False
        if mode not in {"off", "observe", "full", "top_k"}:
            return False
        if (
            not isinstance(bucket_date, str)
            or len(bucket_date) != 10
            or not isinstance(topic_count_bucket, str)
            or not 1 <= len(topic_count_bucket) <= 32
            or (values is not None and not isinstance(values, Mapping))
        ):
            return False
        try:
            if datetime.fromisoformat(bucket_date).date().isoformat() != bucket_date:
                return False
        except ValueError:
            return False
        safe_values = dict(values or {})
        if not self._valid_metric_values(safe_values):
            return False
        prompt_tokens = safe_values.get("prompt_tokens")
        if token_source_available != (prompt_tokens is not None):
            return False
        current_time = time.time() if now is None else now
        if not self._valid_metric_timestamp(current_time):
            return False
        async with self._catalog_transaction() as db:
            inserted = await db.execute(
                """
                INSERT OR IGNORE INTO topic_candidate_metric_windows(
                    window_key_hash, hash_key_version, terminal_state,
                    token_source_available, mode, topic_count_bucket,
                    candidate_count, selector_duration_ms, prompt_chars,
                    prompt_tokens, metrics_revision, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    window_key_hash,
                    hash_key_version,
                    terminal_state,
                    int(token_source_available),
                    mode,
                    topic_count_bucket,
                    safe_values.get("candidate_count_sum"),
                    safe_values.get("selector_duration_ms"),
                    safe_values.get("prompt_chars"),
                    prompt_tokens,
                    current_time,
                ),
            )
            if inserted.rowcount != 1:
                return False
            increments = {
                name: safe_values.get(name, 0)
                if safe_values.get(name) is not None
                else 0
                for name in _AGGREGATE_FIELDS
            }
            await db.execute(
                """
                INSERT INTO topic_candidate_scope_metrics(
                    scope_key_hash, hash_key_version, bucket_date, mode,
                    topic_count_bucket, window_count, quality_sample_count,
                    token_sample_count, latency_sample_count,
                    candidate_count_sum, bm25_hit_count, recent_fill_count,
                    identity_drop_count, budget_exceeded_count, catalog_degraded_count,
                    exact_reuse_count, exact_topic_count, duplicate_topic_count,
                    window_topic_count, selector_duration_ms, prompt_chars,
                    prompt_tokens, terminal_state, token_source_available,
                    metrics_revision
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(scope_key_hash, hash_key_version, bucket_date, mode, topic_count_bucket)
                DO UPDATE SET
                    window_count = topic_candidate_scope_metrics.window_count + 1,
                    quality_sample_count = topic_candidate_scope_metrics.quality_sample_count + \
                        excluded.quality_sample_count,
                    token_sample_count = topic_candidate_scope_metrics.token_sample_count + \
                        excluded.token_sample_count,
                    latency_sample_count = topic_candidate_scope_metrics.latency_sample_count + \
                        excluded.latency_sample_count,
                    candidate_count_sum = topic_candidate_scope_metrics.candidate_count_sum + \
                        excluded.candidate_count_sum,
                    bm25_hit_count = topic_candidate_scope_metrics.bm25_hit_count + excluded.bm25_hit_count,
                    recent_fill_count = topic_candidate_scope_metrics.recent_fill_count + \
                        excluded.recent_fill_count,
                    identity_drop_count = topic_candidate_scope_metrics.identity_drop_count + \
                        excluded.identity_drop_count,
                    budget_exceeded_count = topic_candidate_scope_metrics.budget_exceeded_count + \
                        excluded.budget_exceeded_count,
                    catalog_degraded_count = topic_candidate_scope_metrics.catalog_degraded_count + \
                        excluded.catalog_degraded_count,
                    exact_reuse_count = topic_candidate_scope_metrics.exact_reuse_count + \
                        excluded.exact_reuse_count,
                    exact_topic_count = topic_candidate_scope_metrics.exact_topic_count + \
                        excluded.exact_topic_count,
                    duplicate_topic_count = topic_candidate_scope_metrics.duplicate_topic_count + \
                        excluded.duplicate_topic_count,
                    window_topic_count = topic_candidate_scope_metrics.window_topic_count + \
                        excluded.window_topic_count,
                    selector_duration_ms = topic_candidate_scope_metrics.selector_duration_ms + \
                        excluded.selector_duration_ms,
                    prompt_chars = topic_candidate_scope_metrics.prompt_chars + excluded.prompt_chars,
                    prompt_tokens = CASE WHEN excluded.prompt_tokens IS NULL
                        THEN topic_candidate_scope_metrics.prompt_tokens
                        ELSE COALESCE(topic_candidate_scope_metrics.prompt_tokens, 0)
                             + excluded.prompt_tokens END,
                    -- terminal_state/token_source_available 是窗口级字段，
                    -- 聚合行不再覆盖（last-write-wins 无聚合语义）；
                    -- 窗口样本保留在 metric_windows 表中按需查询。
                    metrics_revision = topic_candidate_scope_metrics.metrics_revision + 1
                """,
                (
                    scope_key_hash,
                    hash_key_version,
                    bucket_date,
                    mode,
                    topic_count_bucket,
                    int(terminal_state == "success"),
                    int(terminal_state == "success" and token_source_available),
                    int(terminal_state == "success"),
                    *(increments[name] for name in _AGGREGATE_FIELDS),
                    prompt_tokens,
                    terminal_state,
                    int(token_source_available),
                ),
            )
        return True

    async def cleanup_metric_windows(
        self,
        *,
        retention_days: int,
        now: float | None = None,
    ) -> int:
        """按保留期删除过期窗口指标样本与无样本的空聚合行。

        返回删除的窗口行数；retention_days 非正或数据库不可用时返回 0。
        聚合行在窗口全部过期后一并删除（聚合无独立时间戳，跟随窗口生命周期）。
        """

        current_time = time.time() if now is None else now
        if (
            isinstance(retention_days, bool)
            or not isinstance(retention_days, int)
            or retention_days <= 0
            or self._db is None
            or not self._valid_metric_timestamp(current_time)
        ):
            return 0
        cutoff = current_time - retention_days * 86400.0
        async with self._catalog_transaction() as db:
            cursor = await db.execute(
                "DELETE FROM topic_candidate_metric_windows WHERE updated_at < ?",
                (cutoff,),
            )
            deleted = int(cursor.rowcount or 0)
            if deleted:
                # 空聚合不再有样本，随窗口清理删除，防止表无限增长
                await db.execute(
                    """
                    DELETE FROM topic_candidate_scope_metrics
                    WHERE NOT EXISTS (
                        SELECT 1 FROM topic_candidate_metric_windows windows
                        WHERE windows.scope_key_hash =
                              topic_candidate_scope_metrics.scope_key_hash
                          AND windows.hash_key_version =
                              topic_candidate_scope_metrics.hash_key_version
                          AND windows.mode =
                              topic_candidate_scope_metrics.mode
                          AND windows.topic_count_bucket =
                              topic_candidate_scope_metrics.topic_count_bucket
                    )
                    """,
                )
            return deleted

    async def read_metric_summary(
        self,
        *,
        hash_key_version: int,
        since: float,
        until: float | None = None,
    ) -> dict[str, float | None] | None:
        """读取当前密钥版本的 UTC 窗口样本并计算真实 nearest-rank P95。

        新增列之前的历史窗口保持缺失；不得把 scope 聚合均值当作百分位。
        时间区间为闭区间，调用方只接收固定字段，不接触摘要键或原始行。
        """

        end = time.time() if until is None else until
        if (
            self._db is None
            or isinstance(hash_key_version, bool)
            or not isinstance(hash_key_version, int)
            or hash_key_version <= 0
            or not self._valid_metric_timestamp(since)
            or not self._valid_metric_timestamp(end)
            or since > end
        ):
            return None
        cursor = await self._db.execute(
            """
            SELECT selector_duration_ms, candidate_count,
                   CASE WHEN terminal_state = 'success' AND token_source_available = 1
                        THEN prompt_tokens END
            FROM topic_candidate_metric_windows
            WHERE hash_key_version = ? AND updated_at >= ? AND updated_at <= ?
            """,
            (hash_key_version, since, end),
        )
        samples: tuple[list[float], list[float], list[float]] = ([], [], [])
        for row in await cursor.fetchall():
            for index, value in enumerate(row):
                if value is not None and self._valid_metric_timestamp(value):
                    samples[index].append(float(value))
        if not any(samples):
            return None
        result: dict[str, float | None] = {}
        for field, values in zip(
            ("p95_latency_ms", "p95_candidates", "p95_tokens"), samples, strict=True
        ):
            values.sort()
            result[field] = (
                values[math.ceil(len(values) * 0.95) - 1] if values else None
            )
        return result

    @staticmethod
    def _valid_metric_timestamp(value: object) -> bool:
        """只接受有限非负 Unix 秒或安全数值样本，拒绝布尔伪装。"""

        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= 0
        )

    @staticmethod
    def _valid_metric_key(value: object) -> bool:
        """只接受 SHA-256/HMAC-SHA-256 的小写十六进制摘要。"""

        return (
            isinstance(value, str)
            and len(value) == 64
            and all(char in "0123456789abcdef" for char in value)
        )

    @staticmethod
    def _valid_metric_values(values: Mapping[str, object]) -> bool:
        """拒绝未知、负值、布尔、小数计数及 NaN/Inf 指标。"""

        if not set(values) <= (_INTEGER_FIELDS | _FLOAT_FIELDS):
            return False
        for name, value in values.items():
            if value is None:
                continue
            if name in _INTEGER_FIELDS:
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    return False
            elif (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value < 0
            ):
                return False
        return True


__all__ = ["TopicCatalogMetricsMixin"]
