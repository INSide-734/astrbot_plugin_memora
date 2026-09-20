"""质量 funnel 的只读 Page API。

``GET /astrbot_plugin_memora/page/metrics/quality-funnel?window=1h|24h|7d|30d``

响应字段来自显式白名单：``window``/``bucket``/``advisory``/四个 stage 的
``state``/``reason``/``counts``/``values``/``rates`` 与 UTC 日趋势。stage 状态
为 ``available|degraded|unavailable`` 闭集；不可用 stage 的计数为 ``null``
（topic HMAC key 缺失、非法或轮换断层时绝不以零值伪装）。响应不含 query、
正文、记忆 ID、scope、revision、source mapping、身份、逐请求关联键或
threshold pass/fail 结论。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from quart import request

from ....features.memory.infrastructure.topic_metrics import (
    load_topic_metrics_key_state,
)
from ....features.quality.application.quality_funnel import (
    FUNNEL_REASON_CODES,
    FUNNEL_STAGE_COUNT_KEYS,
    FUNNEL_STAGE_RATE_KEYS,
    FUNNEL_STAGE_VALUE_KEYS,
    FUNNEL_STAGES,
    FUNNEL_STATE_VALUES,
    FUNNEL_WINDOWS,
    MAX_TREND_DAYS,
    REASON_READ_FAILED,
    STAGE_UNAVAILABLE,
    TREND_DAY_FIELD,
    TREND_FIELDS,
    QualityFunnelSources,
    build_trend,
    collect_quality_funnel,
    is_funnel_window,
)
from .response_utils import error_response, ok_response


class QualityFunnelApiMixin:
    """暴露四阶段质量 funnel 的 UTC 日聚合。"""

    plugin: Any

    async def get_quality_funnel(self):
        return await self.get_quality_funnel_payload(dict(request.args))

    async def get_quality_funnel_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """按 ``window`` 返回质量 funnel 摘要。"""

        window = str(payload.get("window", "24h"))
        if not is_funnel_window(window):
            return error_response(
                "window must be one of 1h, 24h, 7d, 30d",
                code="invalid_window",
            )
        ensure_ready: Any = getattr(self, "_ensure_plugin_ready")
        engines, err = await ensure_ready()
        if err:
            return err
        sources = self._quality_funnel_sources(engines or {})
        stages = await collect_quality_funnel(sources, window=window)
        return ok_response(_project_quality_funnel(window, stages, build_trend(stages)))

    def _quality_funnel_sources(
        self, engines: Mapping[str, Any]
    ) -> QualityFunnelSources:
        """只从组合根已装配的组件构造窄读取端口。"""

        initializer = getattr(getattr(self, "plugin", None), "initializer", None)
        memory_engine = engines.get("memory_engine")
        conversation_manager = engines.get("conversation_manager")
        conversation_store = getattr(conversation_manager, "store", None)
        key_state, key_error = _load_topic_key_state(
            _funnel_data_dir(initializer, memory_engine)
        )
        return QualityFunnelSources(
            topic_store=getattr(memory_engine, "topic_catalog_store", None),
            topic_key_state=key_state,
            topic_key_error=key_error,
            summary_connection=getattr(conversation_store, "connection", None),
            dedup_store=getattr(initializer, "dedup_metrics_store", None),
            injection_store=getattr(initializer, "injection_decision_store", None),
        )


def _funnel_data_dir(initializer: Any, memory_engine: Any) -> Any:
    """定位指标 HMAC sidecar 所在目录；缺失时返回 None（按不可用处理）。"""

    data_dir = getattr(initializer, "data_dir", None)
    if data_dir:
        return data_dir
    db_path = getattr(memory_engine, "db_path", None)
    return Path(db_path).parent if db_path else None


def _load_topic_key_state(data_dir: Any) -> tuple[Any | None, BaseException | None]:
    """装载安装级 HMAC key 状态；不创建 key，也不回显路径或异常原文。"""

    if not data_dir:
        return None, None
    try:
        return load_topic_metrics_key_state(data_dir, create=False), None
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return None, exc


def _project_quality_funnel(
    window: str,
    stages: Any,
    trend: Any,
) -> dict[str, Any]:
    """按白名单投影 funnel 载荷；缺失或非法输入降级为不可用。

    必须是模块级函数：``PluginPageApi`` 由 30+ mixin 组合而成，同名私有方法会
    被前面的 mixin 按 MRO 遮蔽，使端点在生产组合下调用错方法。
    """

    stage_map = stages if isinstance(stages, Mapping) else {}
    return {
        "window": window,
        "bucket": "utc_day",
        "advisory": True,
        "stages": [
            _project_stage(stage_id, stage_map.get(stage_id))
            for stage_id in FUNNEL_STAGES
        ],
        "trend": _project_trend(trend),
    }


def _project_stage(stage_id: str, read: Any) -> dict[str, Any]:
    """投影单个 stage；状态/原因不在闭集内时收敛为不可用。"""

    state = getattr(read, "state", None)
    if state not in FUNNEL_STATE_VALUES:
        state = STAGE_UNAVAILABLE
    reason = getattr(read, "reason", None)
    if not isinstance(reason, str) or reason not in FUNNEL_REASON_CODES:
        reason = REASON_READ_FAILED
    if state == STAGE_UNAVAILABLE:
        return {
            "id": stage_id,
            "state": state,
            "reason": reason,
            "counts": None,
            "values": None,
            "rates": None,
        }
    counts_source = getattr(read, "counts", None)
    values_source = getattr(read, "values", None)
    counts = {
        key: _safe_count(
            counts_source.get(key) if isinstance(counts_source, Mapping) else None
        )
        for key in FUNNEL_STAGE_COUNT_KEYS[stage_id]
    }
    values = {
        key: _safe_value(
            values_source.get(key) if isinstance(values_source, Mapping) else None
        )
        for key in FUNNEL_STAGE_VALUE_KEYS[stage_id]
    }
    return {
        "id": stage_id,
        "state": state,
        "reason": reason,
        "counts": counts,
        "values": values,
        "rates": _project_rates(stage_id, counts),
    }


def _project_rates(
    stage_id: str,
    counts: Mapping[str, int],
) -> dict[str, float]:
    """由计数派生比率；分母为 0 时返回 0.0。"""

    if stage_id == "candidates":
        derived = {
            "reuse_rate": _ratio(counts["exact_reuse"], counts["candidates"]),
            "degraded_rate": _ratio(counts["catalog_degraded"], counts["windows"]),
        }
    elif stage_id == "facts":
        dispositions = sum(
            counts[key]
            for key in (
                "canonical",
                "merged",
                "quarantined",
                "discarded",
                "mark_write",
                "failed",
                "skipped",
            )
        )
        derived = {
            "merge_rate": _ratio(counts["merged"], dispositions),
            "discard_rate": _ratio(counts["discarded"], dispositions),
        }
    elif stage_id == "dedup":
        checked = counts["checked"]
        derived = {
            "hit_rate": _ratio(counts["hit"], checked),
            "guard_rate": _ratio(counts["fact_mismatch"], checked),
            "overlap_rate": _ratio(counts["fact_overlap"], checked),
            "failure_rate": _ratio(counts["conflict"] + counts["failed"], checked),
        }
    else:
        derived = {
            "memory_present_rate": _ratio(
                counts["memory_present"], counts["decisions"]
            ),
            "payload_injected_rate": _ratio(
                counts["payload_injected"], counts["decisions"]
            ),
        }
    return {key: derived.get(key, 0.0) for key in FUNNEL_STAGE_RATE_KEYS[stage_id]}


def _project_trend(trend: Any) -> list[dict[str, Any]]:
    """投影 UTC 日趋势；坏行被丢弃而不是让整页失败。"""

    if not isinstance(trend, (list, tuple)):
        return []
    rows: list[dict[str, Any]] = []
    for row in trend:
        if not isinstance(row, Mapping):
            continue
        day = row.get(TREND_DAY_FIELD)
        if not isinstance(day, str) or len(day) != 10:
            continue
        rows.append(
            {
                TREND_DAY_FIELD: day,
                **{field: _safe_count(row.get(field)) for field in TREND_FIELDS},
            }
        )
    return rows[-MAX_TREND_DAYS:]


def _safe_count(value: Any) -> int:
    """规范化计数字段；非法值按 0 处理。"""

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _safe_value(value: Any) -> float:
    """规范化非计数标量；非法值按 0.0 处理。"""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")) or number < 0:
        return 0.0
    return number


def _ratio(numerator: int, denominator: int) -> float:
    """计算比率；分母非正时返回 0.0。"""

    return numerator / denominator if denominator > 0 else 0.0


__all__ = ["FUNNEL_WINDOWS", "QualityFunnelApiMixin"]
