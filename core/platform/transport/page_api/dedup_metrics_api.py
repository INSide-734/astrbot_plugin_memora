"""近重复合并观测面的只读 Page API。

字段全部来自显式键映射白名单：响应只含窗口合计、四个比率、分模式计数与
小时趋势，不含 scope/session/正文/记忆 ID。未知窗口返回稳定错误码
``invalid_window``；Store 缺失或读取失败时返回零值契约而不是 500。

窗口合计/分模式/趋势包含可选语义模式的闭集 outcome（``semantic_*``）；
四个比率仍是词法模式口径（由 Store 计算），语义计数只追加不改变既有比率。
Dashboard 面板只渲染词法 outcome 标签，新增字段不改变面板字段形状。
"""

from __future__ import annotations

import math
from typing import Any

from astrbot.api import logger
from quart import request

from ....features.memory.infrastructure.dedup_metrics_store import (
    DEDUP_METRIC_MODES,
    DEDUP_METRIC_OUTCOMES,
    DEDUP_METRIC_WINDOWS,
    DedupMetricsStore,
)
from .response_utils import error_response, ok_response

_OUTCOME_FIELDS = DEDUP_METRIC_OUTCOMES
_SUMMARY_FIELDS = (
    *_OUTCOME_FIELDS,
    "hit_rate",
    "guard_rate",
    "overlap_rate",
    "failure_rate",
)
_TREND_FIELDS = ("bucket_ms", *_OUTCOME_FIELDS)
_RATE_FIELDS = frozenset({"hit_rate", "guard_rate", "overlap_rate", "failure_rate"})


class DedupMetricsApiMixin:
    """暴露跨窗口去重指标的窗口摘要。"""

    async def get_memory_dedup_metrics(self):
        return await self.get_memory_dedup_metrics_payload(dict(request.args))

    async def get_memory_dedup_metrics_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """按 ``window`` 返回近重复指标摘要。"""

        window = str(payload.get("window", "24h"))
        if window not in DEDUP_METRIC_WINDOWS:
            return error_response(
                "window must be one of 1h, 24h, 7d, 30d",
                code="invalid_window",
            )
        store = self._dedup_metrics_store()
        if store is None:
            return ok_response(_project_summary(window, None))
        try:
            summary = await store.summary(window)
        except Exception:
            logger.error("跨窗口去重指标读取失败，已降级为零值契约", exc_info=True)
            return ok_response(_project_summary(window, None))
        return ok_response(_project_summary(window, summary))

    def _dedup_metrics_store(self) -> DedupMetricsStore | None:
        """取组合根注入的指标 Store；未初始化时返回 None。"""

        plugin = getattr(self, "plugin", None)
        initializer = getattr(plugin, "initializer", None)
        return getattr(initializer, "dedup_metrics_store", None)


def _project_summary(window: str, summary: Any) -> dict[str, Any]:
    """按白名单投影摘要；缺失或非法输入降级为零值契约。

    必须是模块级函数：``PluginPageApi`` 由 20+ mixin 组合而成，同名私有方法
    会被前面的 mixin（例如 ``InjectionStrategyApiMixin._safe_summary``）按 MRO
    遮蔽，使端点在生产组合下调用错方法。
    """

    source = (
        summary
        if isinstance(summary, dict)
        else DedupMetricsStore.empty_summary(window)
    )
    by_mode_source = source.get("by_mode")
    trend_source = source.get("trend")
    return {
        "window": window,
        **{
            field: (
                _safe_rate(source.get(field))
                if field in _RATE_FIELDS
                else _safe_count(source.get(field))
            )
            for field in _SUMMARY_FIELDS
        },
        "by_mode": {
            mode: {
                outcome: _safe_count(
                    by_mode_source.get(mode, {}).get(outcome)
                    if isinstance(by_mode_source, dict)
                    and isinstance(by_mode_source.get(mode), dict)
                    else None
                )
                for outcome in _OUTCOME_FIELDS
            }
            for mode in DEDUP_METRIC_MODES
        },
        "trend": [
            {field: _safe_count(item.get(field)) for field in _TREND_FIELDS}
            for item in trend_source
            if isinstance(item, dict)
        ]
        if isinstance(trend_source, list)
        else [],
    }


def _safe_count(value: Any) -> int:
    """规范化计数字段；非法值按 0 处理。"""

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _safe_rate(value: Any) -> float:
    """规范化比率字段；非法值按 0.0 处理。"""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    rate = float(value)
    if not math.isfinite(rate) or rate < 0.0:
        return 0.0
    return rate


__all__ = ["DedupMetricsApiMixin"]
