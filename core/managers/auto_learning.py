"""Autonomous learning — feedback collection + parameter optimization loop."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from astrbot.api import logger

_STATE_FILE = "auto_learning.json"


class FeedbackCollector:
    """Collect implicit and explicit feedback for learning."""

    def __init__(self, max_samples: int = 200) -> None:
        self._max_samples = max_samples
        self._feedback: list[dict[str, Any]] = []
        self._stats = {
            "total_recalls": 0,
            "total_hits": 0,
            "total_misses": 0,
            "total_corrections": 0,
            "avg_quality": 0.5,
        }

    def record_recall(self, memory_id: int, was_relevant: bool) -> None:
        self._stats["total_recalls"] += 1
        if was_relevant:
            self._stats["total_hits"] += 1
        else:
            self._stats["total_misses"] += 1
        self._feedback.append(
            {
                "type": "recall",
                "memory_id": memory_id,
                "relevant": was_relevant,
                "timestamp": time.time(),
            }
        )
        self._trim()

    def record_quality(self, score: float) -> None:
        score = max(0.0, min(1.0, score))
        alpha = 0.1
        self._stats["avg_quality"] = (1 - alpha) * self._stats[
            "avg_quality"
        ] + alpha * score
        self._feedback.append(
            {"type": "quality", "score": score, "timestamp": time.time()}
        )
        self._trim()

    def record_correction(self, detail: str = "") -> None:
        self._stats["total_corrections"] += 1
        self._feedback.append(
            {"type": "correction", "detail": detail, "timestamp": time.time()}
        )
        self._trim()

    @property
    def hit_rate(self) -> float:
        return self._stats["total_hits"] / max(1, self._stats["total_recalls"])

    @property
    def stats(self) -> dict[str, Any]:
        return dict(self._stats)

    def _trim(self) -> None:
        if len(self._feedback) > self._max_samples:
            self._feedback = self._feedback[-self._max_samples :]


class ParamOptimizer:
    """Online parameter optimizer based on feedback signals."""

    def __init__(self, learning_rate: float = 0.01) -> None:
        self._lr = max(0.001, min(0.1, learning_rate))
        self._params: dict[str, float] = {
            "importance_threshold": 0.3,
            "recall_top_k": 5,
            "ttl_modifier": 1.0,
            "document_route_weight": 0.65,
            "graph_route_weight": 0.35,
        }
        self._history: list[dict[str, Any]] = []

    def get_all_params(self) -> dict[str, float]:
        return dict(self._params)

    def update(self, feedback: FeedbackCollector) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        hit_rate = feedback.hit_rate
        quality = feedback.stats["avg_quality"]

        if hit_rate < 0.3:
            new_t = max(0.1, self._params["importance_threshold"] - 0.05)
            changes["importance_threshold"] = round(new_t, 4)
        elif hit_rate > 0.7:
            new_t = min(0.7, self._params["importance_threshold"] + 0.02)
            changes["importance_threshold"] = round(new_t, 4)

        if quality < 0.3:
            new_ttl = max(0.3, self._params["ttl_modifier"] - 0.05)
            changes["ttl_modifier"] = round(new_ttl, 4)
        elif quality > 0.7:
            new_ttl = min(2.0, self._params["ttl_modifier"] + 0.02)
            changes["ttl_modifier"] = round(new_ttl, 4)

        for name, value in changes.items():
            old = self._params[name]
            self._params[name] = value
            self._history.append(
                {
                    "param": name,
                    "old": old,
                    "new": value,
                    "reason": f"hit_rate={hit_rate:.2f} quality={quality:.2f}",
                    "timestamp": time.time(),
                }
            )
            logger.debug(f"[AutoLearn] {name}: {old:.4f} -> {value:.4f}")

        if self._history:
            self._history = self._history[-100:]
        return changes

    def get_history(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._history[-limit:]


class AutoLearningManager:
    """Coordinate feedback collection and parameter optimization."""

    def __init__(
        self,
        data_dir: str = "",
        learning_rate: float = 0.01,
        enabled: bool = True,
    ) -> None:
        self._data_dir = data_dir
        self._enabled = enabled
        self._feedback = FeedbackCollector()
        self._optimizer = ParamOptimizer(learning_rate=learning_rate)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def record_recall(self, memory_id: int, was_relevant: bool) -> None:
        if self._enabled:
            self._feedback.record_recall(memory_id, was_relevant)

    def record_quality(self, score: float) -> None:
        if self._enabled:
            self._feedback.record_quality(score)

    def record_correction(self, detail: str = "") -> None:
        if self._enabled:
            self._feedback.record_correction(detail)

    async def optimize(self) -> dict[str, Any]:
        if not self._enabled:
            return {}
        changes = self._optimizer.update(self._feedback)
        if changes:
            await self._save_state()
        return changes

    def get_params(self) -> dict[str, float]:
        return self._optimizer.get_all_params()

    def get_stats(self) -> dict[str, Any]:
        return {
            "feedback": self._feedback.stats,
            "params": self._optimizer.get_all_params(),
            "history": self._optimizer.get_history(10),
            "enabled": self._enabled,
        }

    def _state_path(self) -> str:
        return os.path.join(self._data_dir, _STATE_FILE)

    async def _save_state(self) -> None:
        if not self._data_dir:
            return
        try:
            os.makedirs(self._data_dir, exist_ok=True)
            state = {
                "params": self._optimizer.get_all_params(),
                "history": self._optimizer.get_history(20),
                "feedback_stats": self._feedback.stats,
            }
            with open(self._state_path(), "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False)
        except OSError:
            logger.debug("[AutoLearn] persist failed", exc_info=True)

    async def load_state(self) -> None:
        if not self._data_dir:
            return
        try:
            path = self._state_path()
            if not os.path.exists(path):
                return
            with open(path, encoding="utf-8") as f:
                state = json.load(f)
            for k, v in (state.get("params", {}) or {}).items():
                self._optimizer._params[k] = float(v)
            logger.info(f"[AutoLearn] restored: {self._optimizer.get_all_params()}")
        except Exception:
            logger.debug("[AutoLearn] restore failed", exc_info=True)

    async def reset(self) -> None:
        self._feedback = FeedbackCollector()
        self._optimizer = ParamOptimizer()
        await self._save_state()


__all__ = ["AutoLearningManager", "FeedbackCollector", "ParamOptimizer"]
