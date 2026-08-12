"""记忆演化任务的确定性门控。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import timezone
from typing import Any

from ..domain import EvolutionSignal, GateDecision


class MemoryEvolutionGate:
    """在不调用网络和数据库的情况下判断是否创建演化任务。"""

    _VALID_MODES = frozenset({"disabled", "shadow", "readonly", "active"})

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        """读取演化门控配置并归一化模式、阈值与去抖参数。

        参数：
            config: Memory Evolution 配置映射；缺失或非法值使用安全默认值。
        """

        config = config or {}
        self.enabled = bool(config.get("enabled", False))
        raw_mode = str(config.get("mode", "disabled"))
        self.mode = raw_mode if raw_mode in self._VALID_MODES else "disabled"
        if not self.enabled:
            self.mode = "disabled"
        self.trigger_threshold = _bounded_float(
            config.get("trigger_threshold", 0.7), default=0.7
        )
        self.max_pending_jobs = max(
            0, _bounded_int(config.get("max_pending_jobs", 100), default=100)
        )
        self.debounce_seconds = max(
            1,
            _bounded_int(config.get("consolidation_debounce_seconds", 60), default=60),
        )
        self.policy_version = str(config.get("policy_version", "v1")) or "v1"

    def consider(
        self, signal: EvolutionSignal, *, replay: bool = False
    ) -> GateDecision:
        """根据单条 canonical source 信号返回稳定的门控决策。

        ``replay`` 仅供从 canonical 全量重建派生索引使用；它保留模式、
        信号和阈值校验，但绕过正常写入的待处理任务上限。重建完成前
        worker 不会消费队列，若复用普通 cap 会静默丢失 source 的派生任务。
        """

        if self.mode == "disabled":
            return GateDecision(False, None, "mode_disabled")
        if not _signal_is_eligible(signal):
            return GateDecision(False, None, "invalid_signal")
        if signal.importance < self.trigger_threshold:
            return GateDecision(False, None, "below_threshold")
        if not replay and signal.pending_jobs >= self.max_pending_jobs:
            return GateDecision(False, None, "pending_cap")

        bucket_key, idempotency_key = self._stable_keys(signal)
        return GateDecision(
            should_enqueue=True,
            bucket_key=bucket_key,
            reason_code="eligible",
            idempotency_key=idempotency_key,
        )

    def _stable_keys(self, signal: EvolutionSignal) -> tuple[str, str]:
        """从内部信号生成不暴露原文和身份的稳定哈希键。"""

        occurred_at = signal.occurred_at
        if occurred_at is None:
            raise ValueError("eligible signal must contain occurred_at")
        occurred_at = occurred_at.astimezone(timezone.utc)
        bucket = int(occurred_at.timestamp()) // self.debounce_seconds
        payload = {
            "scope": signal.scope_key,
            "topics": sorted(set(signal.topic_keys)),
            "entities": sorted(set(signal.entity_keys)),
            "memory_id": signal.memory_id,
            "revision_token": signal.revision_token,
            "bucket": bucket,
            "policy_version": self.policy_version,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        return f"bucket:{digest[:24]}", f"evolution:{digest}"


def _signal_is_eligible(signal: EvolutionSignal) -> bool:
    """检查信号是否包含稳定来源、作用域、时间与主题或实体证据。"""

    return bool(
        signal.revision_token.strip()
        and signal.scope_key.strip()
        and signal.occurred_at is not None
        and (signal.topic_keys or signal.entity_keys)
    )


def _bounded_float(value: Any, *, default: float) -> float:
    """把配置值转为零到一之间的浮点数，非法值回退默认值。"""

    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return min(1.0, max(0.0, parsed))


def _bounded_int(value: Any, *, default: int) -> int:
    """把配置值转为整数，非法值回退默认值。"""

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


__all__ = ["MemoryEvolutionGate"]
