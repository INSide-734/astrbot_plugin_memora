"""质量 funnel 的 UTC 日只读聚合与 stage 可用性判定。

四个 stage 各自只通过一个窄读取端口取数，互不 join、不做逐请求关联：

``candidates``
    topic 候选窗口的 UTC 日聚合（``topic_candidate_scope_metrics``）。依赖安装级
    HMAC 摘要键：键缺失/非法或当前版本在窗口内无样本而历史非空（轮换断层）时
    必须报告 ``unavailable``，绝不回落零值。
``facts``
    总结窗口的业务计数（``summary_jobs`` 的 canonical/merged/quarantine/discard
    与新增 ``facts_rejected_count``），按终态时间所属 UTC 日聚合。
``dedup``
    跨窗口去重指标 Store 的零值契约摘要。
``injection``
    注入决策 Store 的窗口摘要（选择/丢弃/预算利用率）。

可用性语义：
- ``available``：端口可用且返回了本窗口的完整样本。
- ``degraded``：容器可用但数据缺省（Store 未装配/读取失败），沿既有零值契约
  返回计数，同时用闭集 reason 让 UI 与真实零区分。
- ``unavailable``：无法证明数据完整（连接/键/轮换断层），计数不返回。

粒度说明：topic 候选源数据只有 UTC 日粒度，因此候选 stage 按「窗口触及的 UTC
日」求和（短于一天的窗口跨日边界时包含前一日样本，而不是伪造小时级精度）；
facts 按窗口内的毫秒时间戳精确过滤。

本模块只读取计数字段与时间戳，不接触正文、query、记忆 ID、scope、revision、
source mapping、身份或 reason 明细；也不输出 threshold pass/fail 结论。
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

from astrbot.api import logger

HOUR_MS: Final = 3_600_000
DAY_MS: Final = 86_400_000

FUNNEL_WINDOWS: Final = ("1h", "24h", "7d", "30d")
_WINDOW_MS: Final[dict[str, int]] = {
    "1h": HOUR_MS,
    "24h": DAY_MS,
    "7d": 7 * DAY_MS,
    "30d": 30 * DAY_MS,
}

STAGE_AVAILABLE: Final = "available"
STAGE_DEGRADED: Final = "degraded"
STAGE_UNAVAILABLE: Final = "unavailable"
FUNNEL_STATE_VALUES: Final = (STAGE_AVAILABLE, STAGE_DEGRADED, STAGE_UNAVAILABLE)

REASON_OK: Final = "ok"
REASON_SOURCE_MISSING: Final = "source_missing"
REASON_READ_FAILED: Final = "read_failed"
REASON_TOPIC_KEY_MISSING: Final = "topic_metrics_key_missing"
REASON_TOPIC_KEY_INVALID: Final = "topic_metrics_key_invalid"
REASON_TOPIC_STORE_UNAVAILABLE: Final = "topic_metrics_store_unavailable"
REASON_TOPIC_READ_FAILED: Final = "topic_metrics_read_failed"
REASON_TOPIC_KEY_ROTATION_GAP: Final = "topic_metrics_key_rotation_gap"
REASON_SUMMARY_STORE_UNAVAILABLE: Final = "summary_store_unavailable"
REASON_SUMMARY_READ_FAILED: Final = "summary_read_failed"
REASON_DEDUP_STORE_MISSING: Final = "dedup_store_missing"
REASON_DEDUP_READ_FAILED: Final = "dedup_read_failed"
REASON_INJECTION_STORE_MISSING: Final = "injection_store_missing"
REASON_INJECTION_READ_FAILED: Final = "injection_read_failed"

FUNNEL_REASON_CODES: Final = frozenset(
    {
        REASON_OK,
        REASON_SOURCE_MISSING,
        REASON_READ_FAILED,
        REASON_TOPIC_KEY_MISSING,
        REASON_TOPIC_KEY_INVALID,
        REASON_TOPIC_STORE_UNAVAILABLE,
        REASON_TOPIC_READ_FAILED,
        REASON_TOPIC_KEY_ROTATION_GAP,
        REASON_SUMMARY_STORE_UNAVAILABLE,
        REASON_SUMMARY_READ_FAILED,
        REASON_DEDUP_STORE_MISSING,
        REASON_DEDUP_READ_FAILED,
        REASON_INJECTION_STORE_MISSING,
        REASON_INJECTION_READ_FAILED,
    }
)

FUNNEL_STAGES: Final = ("candidates", "facts", "dedup", "injection")

CANDIDATE_COUNT_KEYS: Final = (
    "windows",
    "candidates",
    "exact_reuse",
    "duplicate_topics",
    "identity_drops",
    "budget_exceeded",
    "catalog_degraded",
)
FACT_COUNT_KEYS: Final = (
    "windows",
    "canonical",
    "merged",
    "quarantined",
    "discarded",
    "mark_write",
    "failed",
    "skipped",
    "facts_rejected",
)
DEDUP_COUNT_KEYS: Final = (
    "checked",
    "hit",
    "merged",
    "fact_mismatch",
    "fact_overlap",
    "conflict",
    "failed",
)
INJECTION_COUNT_KEYS: Final = (
    "decisions",
    "selected",
    "dropped",
    "truncated",
    "memory_present",
    "payload_injected",
)
FUNNEL_STAGE_COUNT_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "candidates": CANDIDATE_COUNT_KEYS,
    "facts": FACT_COUNT_KEYS,
    "dedup": DEDUP_COUNT_KEYS,
    "injection": INJECTION_COUNT_KEYS,
}
FUNNEL_STAGE_VALUE_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "candidates": (),
    "facts": (),
    "dedup": (),
    "injection": ("budget_utilization",),
}
_INJECTION_VALUE_KEYS: Final = FUNNEL_STAGE_VALUE_KEYS["injection"]

#: 派生比率（由投影层从计数计算，分母为 0 时为 0.0）。
FUNNEL_STAGE_RATE_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "candidates": ("reuse_rate", "degraded_rate"),
    "facts": ("merge_rate", "discard_rate"),
    "dedup": ("hit_rate", "guard_rate", "overlap_rate", "failure_rate"),
    "injection": ("memory_present_rate", "payload_injected_rate"),
}

#: 趋势行字段与各 stage 趋势字段的映射（不同 stage 的同名计数必须改名）。
TREND_DAY_FIELD: Final = "day"
TREND_FIELDS: Final = (
    "candidates",
    "canonical",
    "merged",
    "facts_rejected",
    "dedup_checked",
    "dedup_hit",
    "decisions",
    "selected",
)
_TREND_FIELD_MAP: Final[dict[str, dict[str, str]]] = {
    "candidates": {"candidates": "candidates"},
    "facts": {
        "canonical": "canonical",
        "merged": "merged",
        "facts_rejected": "facts_rejected",
    },
    "dedup": {"checked": "dedup_checked", "hit": "dedup_hit"},
    "injection": {"decisions": "decisions", "selected": "selected"},
}
#: 趋势最多保留的 UTC 天数（30d 窗口的上界）。
MAX_TREND_DAYS: Final = 30


@dataclass(frozen=True, slots=True)
class StageRead:
    """单个 stage 的闭集读取结果。"""

    state: str
    reason: str
    counts: Mapping[str, int]
    values: Mapping[str, float]
    trend: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class QualityFunnelSources:
    """组合根注入的只读组件；缺省值一律按可用性问题处理。"""

    topic_store: Any = None
    topic_key_state: Any = None
    topic_key_error: BaseException | None = None
    summary_connection: Any = None
    dedup_store: Any = None
    injection_store: Any = None


def is_funnel_window(window: Any) -> bool:
    """判断窗口是否为闭集内取值。"""

    return isinstance(window, str) and window in _WINDOW_MS


def _safe_count(value: Any) -> int:
    """把计数字段规范化为非负整数；布尔、小数、NaN 与负数按 0 处理。"""

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _safe_value(value: Any) -> float:
    """把非计数标量规范化为有限非负浮点；非法值按 0.0 处理。"""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")) or number < 0:
        return 0.0
    return number


def _row_cells(row: Any, minimum: int) -> list[Any] | None:
    """把 ``sqlite3.Row``/元组行规范化为值列表；无法解析时返回 None。"""

    try:
        cells = list(row)
    except TypeError:
        return None
    return cells if len(cells) >= minimum else None


def _utc_day(day_index: Any) -> str | None:
    """把 UTC 日序号（Unix 秒 // 86400）转换为 ``YYYY-MM-DD``。"""

    if isinstance(day_index, bool) or not isinstance(day_index, int):
        return None
    if day_index < 0 or day_index > 3_000_000:
        return None
    try:
        return (
            datetime.fromtimestamp(day_index * 86400, tz=timezone.utc)
            .date()
            .isoformat()
        )
    except (OverflowError, OSError, ValueError):
        return None


def _utc_day_from_ms(bucket_ms: Any) -> str | None:
    """把毫秒时间戳转换为 UTC 日 ``YYYY-MM-DD``。"""

    if isinstance(bucket_ms, bool) or not isinstance(bucket_ms, int) or bucket_ms < 0:
        return None
    return _utc_day(bucket_ms // DAY_MS)


def _empty_trend() -> tuple[Mapping[str, Any], ...]:
    """返回空趋势序列。"""

    return ()


def _unavailable(reason: str) -> StageRead:
    """构造无计数的不可用结果。"""

    return StageRead(
        state=STAGE_UNAVAILABLE,
        reason=reason,
        counts={},
        values={},
        trend=_empty_trend(),
    )


def _degraded(
    reason: str,
    count_keys: tuple[str, ...],
    value_keys: tuple[str, ...] = (),
) -> StageRead:
    """构造零值契约的降级结果（计数全零，状态不伪装成可用）。"""

    return StageRead(
        state=STAGE_DEGRADED,
        reason=reason,
        counts={key: 0 for key in count_keys},
        values={key: 0.0 for key in value_keys},
        trend=_empty_trend(),
    )


def _topic_key_reason(error: BaseException | None) -> str:
    """把 key 装载错误映射为闭集 reason，绝不回显异常原文。

    只有 sidecar 明确不存在才是 ``missing``；其余装载/格式/版本错误都归为
    ``invalid``，两者都不允许回落零值。
    """

    message = str(error) if error is not None else ""
    if message == REASON_TOPIC_KEY_MISSING:
        return REASON_TOPIC_KEY_MISSING
    return REASON_TOPIC_KEY_INVALID


async def read_candidates_stage(
    store: Any,
    key_state: Any,
    key_error: BaseException | None,
    *,
    since_date: str,
    until_date: str,
) -> StageRead:
    """读取 topic 候选的 UTC 日聚合；键问题一律 unavailable。"""

    reader = getattr(store, "read_metric_day_totals", None)
    if store is None or not callable(reader):
        return _unavailable(REASON_TOPIC_STORE_UNAVAILABLE)
    if key_state is None:
        return _unavailable(_topic_key_reason(key_error))
    version = getattr(key_state, "version", None)
    try:
        rows = reader(
            hash_key_version=version,
            since_date=since_date,
            until_date=until_date,
        )
        if inspect.isawaitable(rows):
            rows = await rows
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("[质量漏斗] 读取 topic 候选日聚合失败")
        return _unavailable(REASON_TOPIC_READ_FAILED)
    if rows is None or not isinstance(rows, (list, tuple)):
        return _unavailable(REASON_TOPIC_READ_FAILED)
    counts = {key: 0 for key in CANDIDATE_COUNT_KEYS}
    trend: list[Mapping[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        day = row.get("day")
        if not isinstance(day, str) or len(day) != 10:
            continue
        for key in CANDIDATE_COUNT_KEYS:
            counts[key] += _safe_count(row.get(key))
        trend.append({"day": day, "candidates": _safe_count(row.get("candidates"))})
    if not rows and await _has_topic_history(store):
        # 历史非空但当前密钥版本在窗口内没有任何样本：只能来自 key 轮换或
        # 从未在窗口内记录。两者都不能证明本窗口计数完整，不能返回零值。
        return _unavailable(REASON_TOPIC_KEY_ROTATION_GAP)
    return StageRead(
        state=STAGE_AVAILABLE,
        reason=REASON_OK,
        counts=counts,
        values={},
        trend=tuple(trend),
    )


async def _has_topic_history(store: Any) -> bool:
    """只读探测 topic 指标是否已有历史；失败按无历史处理。"""

    probe = getattr(store, "has_metric_history", None)
    if not callable(probe):
        return False
    try:
        result = probe()
        if inspect.isawaitable(result):
            result = await result
    except asyncio.CancelledError:
        raise
    except Exception:
        return False
    return bool(result)


async def read_facts_stage(
    connection: Any,
    *,
    since_ms: int,
    until_ms: int,
) -> StageRead:
    """按终态时间的 UTC 日聚合总结窗口计数。

    只读取计数字段；连接缺失或查询失败时报告 unavailable，不用零值伪装。
    """

    execute = getattr(connection, "execute", None)
    if connection is None or not callable(execute):
        return _unavailable(REASON_SUMMARY_STORE_UNAVAILABLE)
    try:
        runner: Any = execute
        cursor: Any = runner(
            """
            SELECT CAST(updated_at / 86400 AS INTEGER) AS day_index,
                   COUNT(*) AS windows,
                   COALESCE(SUM(canonical_count), 0) AS canonical,
                   COALESCE(SUM(merged_count), 0) AS merged,
                   COALESCE(SUM(quarantine_count), 0) AS quarantined,
                   COALESCE(SUM(discard_count), 0) AS discarded,
                   COALESCE(SUM(mark_write_count), 0) AS mark_write,
                   COALESCE(SUM(failed_count), 0) AS failed,
                   COALESCE(SUM(skipped_count), 0) AS skipped,
                   COALESCE(SUM(facts_rejected_count), 0) AS facts_rejected
            FROM summary_jobs
            WHERE updated_at >= ? AND updated_at <= ?
            GROUP BY day_index
            ORDER BY day_index
            """,
            (since_ms / 1000.0, until_ms / 1000.0),
        )
        if inspect.isawaitable(cursor):
            cursor = await cursor
        rows: Any = cursor.fetchall()
        if inspect.isawaitable(rows):
            rows = await rows
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("[质量漏斗] 读取总结窗口日聚合失败")
        return _unavailable(REASON_SUMMARY_READ_FAILED)
    counts = {key: 0 for key in FACT_COUNT_KEYS}
    trend: list[Mapping[str, Any]] = []
    for row in rows or ():
        cells = _row_cells(row, len(FACT_COUNT_KEYS) + 1)
        if cells is None:
            continue
        day = _utc_day(cells[0])
        if day is None:
            continue
        row_counts = {
            key: _safe_count(cells[index + 1])
            for index, key in enumerate(FACT_COUNT_KEYS)
        }
        for key, value in row_counts.items():
            counts[key] += value
        trend.append(
            {
                "day": day,
                "canonical": row_counts["canonical"],
                "merged": row_counts["merged"],
                "facts_rejected": row_counts["facts_rejected"],
            }
        )
    return StageRead(
        state=STAGE_AVAILABLE,
        reason=REASON_OK,
        counts=counts,
        values={},
        trend=tuple(trend),
    )


async def read_dedup_stage(store: Any, *, window: str) -> StageRead:
    """读取跨窗口去重摘要；Store 缺失/失败沿零值契约并标记 degraded。"""

    summary = getattr(store, "summary", None)
    if store is None or not callable(summary):
        return _degraded(REASON_DEDUP_STORE_MISSING, DEDUP_COUNT_KEYS)
    try:
        payload = summary(window)
        if inspect.isawaitable(payload):
            payload = await payload
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("[质量漏斗] 读取去重指标失败，已降级为零值契约")
        return _degraded(REASON_DEDUP_READ_FAILED, DEDUP_COUNT_KEYS)
    if not isinstance(payload, Mapping):
        return _degraded(REASON_DEDUP_READ_FAILED, DEDUP_COUNT_KEYS)
    counts = {key: _safe_count(payload.get(key)) for key in DEDUP_COUNT_KEYS}
    trend = _dedup_trend(payload.get("trend"))
    return StageRead(
        state=STAGE_AVAILABLE,
        reason=REASON_OK,
        counts=counts,
        values={},
        trend=trend,
    )


def _dedup_trend(source: Any) -> tuple[Mapping[str, Any], ...]:
    """把去重 Store 的小时趋势折算为 UTC 日趋势。"""

    if not isinstance(source, list):
        return ()
    merged: dict[str, dict[str, Any]] = {}
    for point in source:
        if not isinstance(point, Mapping):
            continue
        day = _utc_day_from_ms(point.get("bucket_ms"))
        if day is None:
            continue
        row = merged.setdefault(day, {"day": day, "checked": 0, "hit": 0})
        row["checked"] += _safe_count(point.get("checked"))
        row["hit"] += _safe_count(point.get("hit"))
    return tuple(merged[day] for day in sorted(merged))


async def read_injection_stage(store: Any, *, window: str) -> StageRead:
    """读取注入决策窗口摘要；Store 缺失/失败沿零值契约并标记 degraded。"""

    summary = getattr(store, "summary", None)
    if store is None or not callable(summary):
        return _degraded(
            REASON_INJECTION_STORE_MISSING,
            INJECTION_COUNT_KEYS,
            _INJECTION_VALUE_KEYS,
        )
    try:
        payload = summary(window)
        if inspect.isawaitable(payload):
            payload = await payload
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("[质量漏斗] 读取注入决策摘要失败，已降级为零值契约")
        return _degraded(
            REASON_INJECTION_READ_FAILED,
            INJECTION_COUNT_KEYS,
            _INJECTION_VALUE_KEYS,
        )
    if not isinstance(payload, Mapping):
        return _degraded(
            REASON_INJECTION_READ_FAILED,
            INJECTION_COUNT_KEYS,
            _INJECTION_VALUE_KEYS,
        )
    counts = {
        "decisions": _safe_count(payload.get("decision_count")),
        "selected": _safe_count(payload.get("selected_count_total")),
        "dropped": _safe_count(payload.get("dropped_count_total")),
        "truncated": _safe_count(payload.get("truncated_count_total")),
        "memory_present": _safe_count(payload.get("memory_present_count")),
        "payload_injected": _safe_count(payload.get("payload_injected_count")),
    }
    values = {"budget_utilization": _safe_value(payload.get("budget_utilization_avg"))}
    trend = _injection_trend(payload.get("cost_trend"))
    return StageRead(
        state=STAGE_AVAILABLE,
        reason=REASON_OK,
        counts=counts,
        values=values,
        trend=trend,
    )


def _injection_trend(source: Any) -> tuple[Mapping[str, Any], ...]:
    """把注入成本趋势的小时桶折算为 UTC 日趋势。"""

    if not isinstance(source, list):
        return ()
    merged: dict[str, dict[str, Any]] = {}
    for point in source:
        if not isinstance(point, Mapping):
            continue
        day = _utc_day_from_ms(point.get("bucket_ms"))
        if day is None:
            continue
        row = merged.setdefault(day, {"day": day, "decisions": 0, "selected": 0})
        row["decisions"] += _safe_count(point.get("decision_count"))
        row["selected"] += _safe_count(point.get("selected_count_total"))
    return tuple(merged[day] for day in sorted(merged))


async def _guarded_stage(stage: str, awaitable: Any) -> StageRead:
    """把单个 stage 的意外异常收敛为 unavailable，取消仍然传播。"""

    try:
        result = await awaitable
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.error("[质量漏斗] stage=%s 读取异常，已标记不可用", stage, exc_info=True)
        return _unavailable(REASON_READ_FAILED)
    if not isinstance(result, StageRead):
        return _unavailable(REASON_READ_FAILED)
    return result


async def collect_quality_funnel(
    sources: QualityFunnelSources,
    *,
    window: str,
    now_ms: int | None = None,
) -> dict[str, StageRead]:
    """并行收集四个 stage 的读取结果；窗口非法时返回空映射。"""

    if not is_funnel_window(window):
        return {}
    current_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    since_ms = current_ms - _WINDOW_MS[window]
    since_day = _utc_day(since_ms // DAY_MS)
    until_day = _utc_day(current_ms // DAY_MS)
    if since_day is None or until_day is None:
        return {stage: _unavailable(REASON_READ_FAILED) for stage in FUNNEL_STAGES}
    reads = await asyncio.gather(
        _guarded_stage(
            "candidates",
            read_candidates_stage(
                sources.topic_store,
                sources.topic_key_state,
                sources.topic_key_error,
                since_date=since_day,
                until_date=until_day,
            ),
        ),
        _guarded_stage(
            "facts",
            read_facts_stage(
                sources.summary_connection,
                since_ms=since_ms,
                until_ms=current_ms,
            ),
        ),
        _guarded_stage("dedup", read_dedup_stage(sources.dedup_store, window=window)),
        _guarded_stage(
            "injection", read_injection_stage(sources.injection_store, window=window)
        ),
    )
    return dict(zip(FUNNEL_STAGES, reads, strict=True))


def build_trend(stages: Mapping[str, StageRead]) -> list[dict[str, Any]]:
    """按 UTC 日合并四个 stage 的趋势；任一 stage 不可用时返回空趋势。

    不可用 stage 的计数无法证明完整，因此不允许它静默贡献零值行。
    """

    if set(stages) != set(FUNNEL_STAGES):
        return []
    if any(read.state == STAGE_UNAVAILABLE for read in stages.values()):
        return []
    days: dict[str, dict[str, Any]] = {}
    for stage, read in stages.items():
        field_map = _TREND_FIELD_MAP[stage]
        for point in read.trend:
            if not isinstance(point, Mapping):
                continue
            day = point.get("day")
            if not isinstance(day, str) or len(day) != 10:
                continue
            row = days.setdefault(
                day,
                {TREND_DAY_FIELD: day, **{field: 0 for field in TREND_FIELDS}},
            )
            for source_field, target_field in field_map.items():
                row[target_field] += _safe_count(point.get(source_field))
    ordered = sorted(days)
    return [days[day] for day in ordered[-MAX_TREND_DAYS:]]


__all__ = [
    "CANDIDATE_COUNT_KEYS",
    "DAY_MS",
    "DEDUP_COUNT_KEYS",
    "FACT_COUNT_KEYS",
    "FUNNEL_REASON_CODES",
    "FUNNEL_STAGE_COUNT_KEYS",
    "FUNNEL_STAGE_RATE_KEYS",
    "FUNNEL_STAGE_VALUE_KEYS",
    "FUNNEL_STAGES",
    "FUNNEL_STATE_VALUES",
    "FUNNEL_WINDOWS",
    "HOUR_MS",
    "INJECTION_COUNT_KEYS",
    "MAX_TREND_DAYS",
    "QualityFunnelSources",
    "REASON_OK",
    "STAGE_AVAILABLE",
    "STAGE_DEGRADED",
    "STAGE_UNAVAILABLE",
    "StageRead",
    "TREND_FIELDS",
    "build_trend",
    "collect_quality_funnel",
    "is_funnel_window",
    "read_candidates_stage",
    "read_dedup_stage",
    "read_facts_stage",
    "read_injection_stage",
]
