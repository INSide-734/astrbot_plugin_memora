"""异常检测 — 前序滚动窗口 + 3-sigma 阈值告警。

检测记忆创建速率异常：当某日创建量偏离此前窗口均值超过阈值时触发告警。
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
        """初始化滚动窗口、阈值、已投喂日期与最近状态。"""

        self._data_dir = data_dir
        self._window_days = max(3, min(30, window_days))
        self._sigma_threshold = max(1.5, min(10.0, sigma_threshold))
        # (day_timestamp, count) 的滚动窗口
        self._window: deque[tuple[int, int]] = deque()
        self._fed_days: set[int] = set()
        self._pending_alerts: dict[int, dict[str, Any]] = {}
        self._alert_count = 0
        self._last_alert_at: float = 0.0
        self._last_reason_code = "insufficient_history"

    @property
    def window_days(self) -> int:
        """返回当前滚动窗口天数。"""

        return self._window_days

    # ---- 数据摄入 ----

    def record_daily_count(self, day_ts: int, count: int) -> dict[str, Any] | None:
        """记录某日的记忆创建量，返回告警信息（如有异常）。

        Args:
            day_ts: 当天 00:00:00 的 Unix 时间戳
            count: 当日创建的记忆数

        Returns:
            异常告警 dict 或 None
        """
        normalized_day = int(day_ts)
        if normalized_day in self._pending_alerts:
            return None

        normalized_count = max(0, int(count))
        # 先裁剪并读取此前历史；当前样本不得进入自己的统计基线。
        cutoff = day_ts - self._window_days * 86400
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()

        if len(self._window) < 3:
            self._last_reason_code = "insufficient_history"
            alert = None
        else:
            alert = self._check_anomaly(normalized_day, normalized_count)
            self._last_reason_code = "memory_rate_anomaly" if alert else "ok"

        self._window.append((normalized_day, normalized_count))
        while len(self._window) > self._window_days:
            self._window.popleft()
        if alert is not None:
            self._pending_alerts[normalized_day] = dict(alert)
        return alert

    def record_batch(self, day_counts: list[tuple[int, int]]) -> list[dict[str, Any]]:
        """批量记录多日数据，返回所有告警。"""
        alerts: list[dict[str, Any]] = []
        for day_ts, count in day_counts:
            alert = self.record_daily_count(day_ts, count)
            if alert:
                alerts.append(alert)
        return alerts

    def has_fed(self, day_ts: int) -> bool:
        """判断某日是否已投喂，用于调度去重。"""

        return day_ts in self._fed_days

    def pending_alert(self, day_ts: int) -> dict[str, Any] | None:
        """返回指定日期尚未成功投递的告警副本。"""

        alert = self._pending_alerts.get(day_ts)
        return dict(alert) if alert is not None else None

    def mark_fed(self, day_ts: int) -> None:
        """记录某日已投喂，并清理窗口外的旧日期。"""

        self._fed_days.add(day_ts)
        self._pending_alerts.pop(day_ts, None)
        cutoff = day_ts - self._window_days * 86400
        self._fed_days = {fed for fed in self._fed_days if fed >= cutoff}

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
        # 使用 0.5 的离散计数噪声下限；0 → 1 的变化仍达不到默认 3σ，
        # 但稳定高基线上的真实大幅跳变不再被“零方差”整体跳过。
        stdev = max(math.sqrt(max(variance, 0.0)), 0.5)

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
            "reason_code": "memory_rate_anomaly",
        }

        logger.warning(
            "[异常检测] 记忆创建速率%s: count=%s, mean=%.1f, stdev=%.1f, "
            "z=%.1f > %.1fσ",
            direction,
            count,
            mean,
            stdev,
            z_score,
            self._sigma_threshold,
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
                "reason_code": self._last_reason_code,
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
            "reason_code": self._last_reason_code,
        }

    # ---- 持久化 ----

    def _state_path(self) -> str:
        """返回异常检测状态文件的固定路径。"""

        return os.path.join(self._data_dir, _ANOMALY_STATE_FILE)

    def save_state(self) -> None:
        """保存滚动窗口状态到磁盘。"""
        if not self._data_dir:
            return
        try:
            state = {
                "window": list(self._window),
                "fed_days": sorted(self._fed_days),
                "pending_alerts": list(self._pending_alerts.values()),
                "alert_count": self._alert_count,
                "last_alert_at": self._last_alert_at,
                "last_reason_code": self._last_reason_code,
            }
            os.makedirs(self._data_dir, exist_ok=True)
            with open(self._state_path(), "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False)
        except OSError:
            logger.warning("[异常检测] 状态保存失败")

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
            restored_window = [
                (int(ts), max(0, int(cnt)))
                for ts, cnt in raw_window
                if int(ts) >= cutoff
            ]
            self._window = deque(restored_window[-self._window_days :])
            self._fed_days = {
                int(fed) for fed in state.get("fed_days", []) if int(fed) >= cutoff
            }
            self._pending_alerts = {}
            for raw_alert in state.get("pending_alerts", []) or []:
                if not isinstance(raw_alert, dict):
                    continue
                pending_day = int(raw_alert.get("day_ts", 0) or 0)
                if (
                    pending_day < cutoff
                    or raw_alert.get("reason_code") != "memory_rate_anomaly"
                ):
                    continue
                self._pending_alerts[pending_day] = dict(raw_alert)
            self._alert_count = int(state.get("alert_count", 0))
            self._last_alert_at = float(state.get("last_alert_at", 0.0))
            self._last_reason_code = str(
                state.get("last_reason_code", "insufficient_history")
            )
            logger.info(
                "[异常检测] 状态恢复完成: window=%s, fed_days=%s, alerts=%s",
                len(self._window),
                len(self._fed_days),
                self._alert_count,
            )
        except Exception:
            logger.warning("[异常检测] 状态恢复失败，使用空状态")


__all__ = ["AnomalyDetector"]
