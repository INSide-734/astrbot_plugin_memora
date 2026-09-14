"""跨窗口近重复合并的持久化小时桶指标。

只保存 ``(bucket_ms, mode, outcome, count)``：没有 scope_key、session/persona、
正文、记忆 ID 或 reason 明细，因此**不需要** topic metrics 那样的 HMAC 摘要键
——本 Store 不存在可直接还原主体的维度，泄露面为零。

写入是候选级单条 UPSERT 增量（频次与反思窗口候选数同阶）；读取按窗口聚合出
totals、by_mode、trend 与命中率/护栏率/重叠率/失败率。保留期清理在初始化后
执行一次，之后按写入次数或时间节流（默认 64 次写入或 1 小时至多一次）；清理
失败只降级日志，不影响已经落库的计数。
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from typing import Any, Final, TypeGuard

from astrbot.api import logger

from .base_store import BaseStore

HOUR_MS: Final = 3_600_000
DAY_MS: Final = 86_400_000

DEDUP_METRIC_MODES: Final = ("observe", "enforce")
DEDUP_METRIC_OUTCOMES: Final = (
    "checked",
    "hit",
    "merged",
    "fact_mismatch",
    "fact_overlap",
    "conflict",
    "failed",
)
DEDUP_METRIC_WINDOWS: Final = ("1h", "24h", "7d", "30d")

DEFAULT_RETENTION_DAYS: Final = 30
MAX_RETENTION_DAYS: Final = 3650
CLEANUP_INTERVAL_MS: Final = HOUR_MS
CLEANUP_WRITE_INTERVAL: Final = 64

_WINDOW_MS: Final[dict[str, int]] = {
    "1h": HOUR_MS,
    "24h": DAY_MS,
    "7d": 7 * DAY_MS,
    "30d": 30 * DAY_MS,
}


def _valid_retention_days(retention_days: Any) -> bool:
    """判断保留期是否为 1..3650 的整数。"""

    return (
        not isinstance(retention_days, bool)
        and isinstance(retention_days, int)
        and 1 <= retention_days <= MAX_RETENTION_DAYS
    )


class DedupMetricsStore(BaseStore):
    """近重复合并终态的独立 SQLite 小时桶聚合。"""

    def __init__(
        self,
        db_path: str,
        *,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """绑定数据库路径、保留期与墙钟。

        Args:
            db_path: 独立 SQLite 文件路径（与 canonical DB 分离）。
            retention_days: 小时桶保留天数，取值 1..3650。
            clock: 返回 Unix 秒的墙钟；测试注入确定性时间源。

        Raises:
            ValueError: retention_days 非整数或不在 1..3650 内。
        """

        super().__init__(db_path)
        if not _valid_retention_days(retention_days):
            raise ValueError("retention_days must be an integer within 1..3650")
        self._retention_days = retention_days
        self._clock = clock
        self._writes_since_cleanup = 0
        self._last_cleanup_ms = 0

    async def initialize(self) -> None:
        """建表后按保留期清理一次；清理失败只降级日志。"""

        await super().initialize()
        self._last_cleanup_ms = self._now_ms()
        try:
            await self.cleanup()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "近重复指标启动清理失败，异常类型=%s", error.__class__.__name__
            )

    async def _create_tables(self) -> None:
        """创建小时桶聚合表。"""

        await self._execute(
            """
            CREATE TABLE IF NOT EXISTS dedup_metrics (
                bucket_ms INTEGER NOT NULL,
                mode      TEXT    NOT NULL,
                outcome   TEXT    NOT NULL,
                count     INTEGER NOT NULL,
                PRIMARY KEY (bucket_ms, mode, outcome)
            )
            """
        )
        await self._commit()

    async def record(
        self,
        mode: str,
        outcome: str,
        now_ms: int | None = None,
    ) -> bool:
        """把一次终态计入所在小时桶；枚举非法或时间非法时返回 ``False``。

        使用参数绑定的 ``ON CONFLICT ... DO UPDATE`` 增量写：并发写入只会
        累加计数，不会互相覆盖。
        """

        if mode not in DEDUP_METRIC_MODES or outcome not in DEDUP_METRIC_OUTCOMES:
            return False
        current_ms = self._now_ms() if now_ms is None else now_ms
        if not self._valid_ms(current_ms):
            return False
        bucket_ms = (current_ms // HOUR_MS) * HOUR_MS
        await self._execute(
            "INSERT INTO dedup_metrics (bucket_ms, mode, outcome, count) "
            "VALUES (?, ?, ?, 1) "
            "ON CONFLICT(bucket_ms, mode, outcome) "
            "DO UPDATE SET count = count + excluded.count",
            (bucket_ms, mode, outcome),
        )
        await self._commit()
        self._writes_since_cleanup += 1
        await self._maybe_cleanup(current_ms)
        return True

    async def cleanup(
        self,
        retention_days: int | None = None,
        *,
        now_ms: int | None = None,
    ) -> int:
        """按保留期删除过期小时桶并返回删除行数。"""

        days = self._retention_days if retention_days is None else retention_days
        if not _valid_retention_days(days):
            raise ValueError("retention_days must be an integer within 1..3650")
        current_ms = self._now_ms() if now_ms is None else now_ms
        if not self._valid_ms(current_ms):
            raise ValueError("now_ms must be a non-negative integer")
        cutoff_ms = current_ms - days * DAY_MS
        # 桶起点严格早于 cutoff 才过期：边界桶（含保留期内的部分）保留。
        cursor = await self._execute(
            "DELETE FROM dedup_metrics WHERE bucket_ms < ?",
            (cutoff_ms,),
        )
        deleted = cursor.rowcount
        await self._commit()
        return int(deleted or 0)

    async def row_count(self) -> int:
        """返回表内总行数；用于零写入断言与诊断。"""

        return int(await self._fetch_scalar("SELECT COUNT(*) FROM dedup_metrics") or 0)

    async def summary(
        self,
        window: str = "24h",
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        """聚合窗口内的合计、分模式计数、小时趋势与四个比率。

        Args:
            window: ``1h``/``24h``/``7d``/``30d`` 之一。
            now_ms: 聚合基准毫秒；省略时取当前墙钟。

        Returns:
            字段白名单固定的摘要字典；``by_mode`` 始终包含两个模式的全零
            outcome 计数，``trend`` 只列出窗口内有数据的小时桶。

        Raises:
            ValueError: window 不在闭集内。
        """

        if window not in _WINDOW_MS:
            raise ValueError("window must be one of 1h, 24h, 7d, 30d")
        current_ms = self._now_ms() if now_ms is None else now_ms
        if not self._valid_ms(current_ms):
            current_ms = 0
        # 窗口按桶起点过滤：边界桶可能覆盖窗口外不足一小时的部分。
        cutoff_ms = (current_ms - _WINDOW_MS[window]) // HOUR_MS * HOUR_MS
        rows = await self._fetch_all(
            "SELECT bucket_ms, mode, outcome, count FROM dedup_metrics "
            "WHERE bucket_ms >= ? ORDER BY bucket_ms",
            (cutoff_ms,),
        )
        totals = self._zero_outcomes()
        by_mode = {mode: self._zero_outcomes() for mode in DEDUP_METRIC_MODES}
        trend: dict[int, dict[str, int]] = {}
        for row in rows:
            mode = str(row.get("mode", ""))
            outcome = str(row.get("outcome", ""))
            count = row.get("count")
            bucket_ms = row.get("bucket_ms")
            if mode not in by_mode or outcome not in totals:
                continue
            if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
                continue
            if not self._valid_ms(bucket_ms):
                continue
            totals[outcome] += count
            by_mode[mode][outcome] += count
            bucket = trend.get(bucket_ms)
            if bucket is None:
                bucket = self._zero_outcomes()
                trend[bucket_ms] = bucket
            bucket[outcome] += count
        return self._payload(
            window,
            totals,
            by_mode,
            [
                {"bucket_ms": bucket_ms, **counts}
                for bucket_ms, counts in sorted(trend.items())
            ],
        )

    @classmethod
    def empty_summary(cls, window: str) -> dict[str, Any]:
        """构造零值契约摘要；Store 不可用时 API 的稳定后备。"""

        return cls._payload(
            window,
            cls._zero_outcomes(),
            {mode: cls._zero_outcomes() for mode in DEDUP_METRIC_MODES},
            [],
        )

    @classmethod
    def _payload(
        cls,
        window: str,
        totals: dict[str, int],
        by_mode: dict[str, dict[str, int]],
        trend: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """组装固定字段顺序的摘要（白名单即此处键集合）。"""

        return {
            "window": window,
            **totals,
            "hit_rate": cls._rate(totals["hit"], totals["checked"]),
            "guard_rate": cls._rate(totals["fact_mismatch"], totals["checked"]),
            "overlap_rate": cls._rate(totals["fact_overlap"], totals["checked"]),
            "failure_rate": cls._rate(
                totals["conflict"] + totals["failed"], totals["checked"]
            ),
            "by_mode": by_mode,
            "trend": trend,
        }

    @staticmethod
    def _zero_outcomes() -> dict[str, int]:
        """构造全零 outcome 计数。"""

        return {outcome: 0 for outcome in DEDUP_METRIC_OUTCOMES}

    @staticmethod
    def _rate(numerator: int, denominator: int) -> float:
        """计算比率；分母非正时返回 0.0。"""

        return numerator / denominator if denominator > 0 else 0.0

    async def _maybe_cleanup(self, now_ms: int) -> None:
        """按写入次数或时间节流清理过期桶；失败只降级日志。"""

        due_by_writes = self._writes_since_cleanup >= CLEANUP_WRITE_INTERVAL
        due_by_time = now_ms - self._last_cleanup_ms >= CLEANUP_INTERVAL_MS
        if not due_by_writes and not due_by_time:
            return
        self._writes_since_cleanup = 0
        self._last_cleanup_ms = now_ms
        try:
            await self.cleanup(now_ms=now_ms)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "近重复指标节流清理失败，异常类型=%s", error.__class__.__name__
            )

    def _now_ms(self) -> int:
        """当前毫秒墙钟；非法时钟返回 0。"""

        try:
            seconds = float(self._clock())
        except (TypeError, ValueError):
            return 0
        if not math.isfinite(seconds) or seconds < 0:
            return 0
        return int(seconds * 1000)

    @staticmethod
    def _valid_ms(value: Any) -> TypeGuard[int]:
        """判断毫秒时间戳是否非负整数。"""

        return isinstance(value, int) and not isinstance(value, bool) and value >= 0


__all__ = [
    "CLEANUP_INTERVAL_MS",
    "CLEANUP_WRITE_INTERVAL",
    "DAY_MS",
    "DEFAULT_RETENTION_DAYS",
    "DEDUP_METRIC_MODES",
    "DEDUP_METRIC_OUTCOMES",
    "DEDUP_METRIC_WINDOWS",
    "HOUR_MS",
    "MAX_RETENTION_DAYS",
    "DedupMetricsStore",
]
