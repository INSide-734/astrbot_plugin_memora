"""异常检测 — 7 日滚动窗口 + 3-sigma 阈值告警。

检测记忆创建速率异常：当某日创建量偏离 7 日均值超过 3 个标准差时触发告警。
可用于发现 LLM 幻觉爆发、用户滥用、系统故障等异常状况。
"""

from __future__ import annotations

import json
import math
import os
import time
from collections import deque
from typing import Any

from astrbot.api import logger

# 滚动窗口大小（天）
_DEFAULT_WINDOW_DAYS = 7
# 告警阈值（sigma 倍数）
_DEFAULT_SIGMA_THRESHOLD = 3.0
# 持久化文件名
_ANOMALY_STATE_FILE = "anomaly_state.json"


class AnomalyDetector:
    """基于滚动统计的记忆创建速率异常检测器。

    每个 tick（通常每天一次）记录当日创建量，
    与过去 7 日均值 + 3-sigma 比较，超阈值输出告警。
    """

    def __init__(
        self,
        data_dir: str = "",
        window_days: int = _DEFAULT_WINDOW_DAYS,
        sigma_threshold: float = _DEFAULT_SIGMA_THRESHOLD,
    ) -> None:
        self._data_dir = data_dir
        self._window_days = max(3, min(30, window_days))
        self._sigma_threshold = max(1.5, min(10.0, sigma_threshold))
        # (day_timestamp, count) 的滚动窗口
        self._window: deque[tuple[int, int]] = deque()
        self._alert_count = 0
        self._last_alert_at: float = 0.0

    # ---- 数据摄入 ----

    def record_daily_count(self, day_ts: int, count: int) -> dict[str, Any] | None:
        """记录某日的记忆创建量，返回告警信息（如有异常）。

        Args:
            day_ts: 当天 00:00:00 的 Unix 时间戳
            count: 当日创建的记忆数

        Returns:
            异常告警 dict 或 None
        """
        self._window.append((day_ts, max(0, count)))
        # 裁剪窗口
        cutoff = day_ts - self._window_days * 86400
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()

        if len(self._window) < 3:
            return None  # 不足 3 天，无法计算标准差

        return self._check_anomaly(day_ts, count)

    def record_batch(self, day_counts: list[tuple[int, int]]) -> list[dict[str, Any]]:
        """批量记录多日数据，返回所有告警。"""
        alerts: list[dict[str, Any]] = []
        for day_ts, count in day_counts:
            alert = self.record_daily_count(day_ts, count)
            if alert:
                alerts.append(alert)
        return alerts

    # ---- 异常检测核心 ----

    def _check_anomaly(
        self,
        day_ts: int,
        count: int,
    ) -> dict[str, Any] | None:
        """计算滚动统计并判断是否异常。"""
        values = [c for _ts, c in self._window]
        n = len(values)
        if n < 3:
            return None

        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / n
        stdev = math.sqrt(max(variance, 1e-6))

        # 低方差时跳过（如连续数天 0 → 突然 1 不算异常）
        if stdev < 0.5:
            return None

        z_score = (count - mean) / stdev if stdev > 0 else 0.0
        if abs(z_score) < self._sigma_threshold:
            return None

        now = time.time()
        # 防抖：5 分钟内不重复告警
        if now - self._last_alert_at < 300:
            return None

        self._alert_count += 1
        self._last_alert_at = now

        direction = "spike" if count > mean else "drop"
        alert = {
            "day_ts": day_ts,
            "count": count,
            "mean_7d": round(mean, 2),
            "stdev_7d": round(stdev, 2),
            "z_score": round(z_score, 2),
            "direction": direction,
            "sigma_threshold": self._sigma_threshold,
            "window_size": n,
            "alert_number": self._alert_count,
        }

        logger.warning(
            f"[Anomaly] 记忆创建速率{direction}: "
            f"count={count}, mean={mean:.1f}, stdev={stdev:.1f}, "
            f"z={z_score:.1f} > {self._sigma_threshold}σ"
        )
        return alert

    # ---- 统计查询 ----

    @property
    def stats(self) -> dict[str, Any]:
        """当前滚动统计摘要。"""
        if not self._window:
            return {
                "window_size": 0,
                "mean": 0.0,
                "stdev": 0.0,
                "alerts": self._alert_count,
            }

        values = [c for _ts, c in self._window]
        n = len(values)
        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / n if n > 1 else 0.0
        return {
            "window_size": n,
            "mean": round(mean, 2),
            "stdev": round(math.sqrt(max(variance, 1e-6)), 2),
            "alerts": self._alert_count,
            "latest_count": values[-1],
            "sigma_threshold": self._sigma_threshold,
        }

    # ---- 持久化 ----

    def _state_path(self) -> str:
        return os.path.join(self._data_dir, _ANOMALY_STATE_FILE)

    def save_state(self) -> None:
        """保存滚动窗口状态到磁盘。"""
        if not self._data_dir:
            return
        try:
            state = {
                "window": list(self._window),
                "alert_count": self._alert_count,
                "last_alert_at": self._last_alert_at,
            }
            os.makedirs(self._data_dir, exist_ok=True)
            with open(self._state_path(), "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False)
        except OSError:
            logger.debug("[Anomaly] 持久化状态失败", exc_info=True)

    def load_state(self) -> None:
        """从磁盘恢复滚动窗口状态。"""
        if not self._data_dir:
            return
        try:
            path = self._state_path()
            if not os.path.exists(path):
                return
            with open(path, encoding="utf-8") as f:
                state = json.load(f)
            raw_window = state.get("window", [])
            # 过滤过期数据
            now_ts = int(time.time())
            cutoff = now_ts - self._window_days * 86400
            self._window = deque((ts, cnt) for ts, cnt in raw_window if ts >= cutoff)
            self._alert_count = int(state.get("alert_count", 0))
            self._last_alert_at = float(state.get("last_alert_at", 0.0))
            logger.info(
                f"[Anomaly] 已恢复状态: window={len(self._window)} days, "
                f"alerts={self._alert_count}"
            )
        except Exception:
            logger.debug("[Anomaly] 恢复状态失败", exc_info=True)


__all__ = ["AnomalyDetector"]
