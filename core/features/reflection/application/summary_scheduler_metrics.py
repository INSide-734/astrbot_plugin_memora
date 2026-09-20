"""SummaryScheduler 的候选指标记录与保留期清理 Mixin。"""

from __future__ import annotations

import asyncio
import inspect

from ..domain.summary_models import ClaimedJob, WindowOutcome


class SummarySchedulerMetricsMixin:
    """提供窗口终态指标写入与指标保留期清理。

    两者都 fail-safe：未装配 recorder 或失败时静默跳过，
    不阻塞总结主链；``asyncio.CancelledError`` 必须传播。
    """

    _metrics_recorder: object | None

    def _now(self):  # noqa: ANN202 - 由宿主类提供注入时钟
        """由宿主 Scheduler 提供的墙钟读取。"""

        raise NotImplementedError

    def _retention_days(self) -> int:
        """读取配置的指标保留天数；缺失或非法时回落 30 天。"""

        reader = getattr(self, "_config_reader", None)
        value = getattr(reader, "get", lambda *_: None)(
            "topic_segmentation.candidate_reuse.metrics_retention_days", 30
        )
        # bool 是 int 子类，True 会被下游 store 的保留期校验按非法值拒绝，
        # 表现为「清理静默不执行」，因此必须一并排除。
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return 30
        return value

    async def _cleanup_metric_retention(self) -> None:
        """按配置的保留期清理过期候选指标窗口样本。

        聚合行随窗口样本清理（无独立时间戳，跟随窗口生命周期）；
        清理失败只记固定 reason code，不阻塞总结主链。
        """
        recorder = self._metrics_recorder
        if recorder is None:
            return
        cleanup = getattr(recorder, "cleanup_metric_windows", None)
        if not callable(cleanup):
            # 生产 recorder（TopicCandidateMetricsRecorder）只暴露 record_success
            # 写入端口，窗口样本清理由它绑定的 TopicCatalogStore 提供；不解析
            # 这一层会让 metrics_retention_days 永远空转、指标表无限增长。
            cleanup = getattr(
                getattr(recorder, "_store", None),
                "cleanup_metric_windows",
                None,
            )
        if not callable(cleanup):
            return
        try:
            result = cleanup(
                retention_days=self._retention_days(),
                now=self._now().timestamp(),
            )
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception:
            # 函数内导入与既有 _persist 风格一致，避免模块顶部 AstrBot 依赖
            from astrbot.api import logger

            logger.warning(
                "候选指标保留期清理失败",
                extra={"reason_code": "metrics_retention_cleanup_failed"},
            )
            return

    async def _record_candidate_metrics(
        self, claim: ClaimedJob, outcome: WindowOutcome
    ) -> None:
        """经 HMAC 摘要把窗口终态候选指标写入 catalog metrics 表。"""
        recorder = self._metrics_recorder
        if recorder is None:
            return
        try:
            await recorder.record_success(claim, outcome.candidate_metrics)
        except asyncio.CancelledError:
            raise
        except Exception:
            return


__all__ = ["SummarySchedulerMetricsMixin"]
