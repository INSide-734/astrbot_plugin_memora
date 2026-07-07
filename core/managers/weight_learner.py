"""个性化检索权重 — MAB 在线学习，基于隐式反馈更新融合权重。"""

from __future__ import annotations

import json
import os
import random
from typing import Any

from astrbot.api import logger

_DEFAULT_LR = 0.05
_DEFAULT_EPSILON = 0.1
_STATE_FILE = "mab_weights.json"


class MABWeightLearner:
    """Epsilon-Greedy 多臂老虎机，用于检索融合权重学习。"""

    def __init__(
        self,
        data_dir: str = "",
        learning_rate: float = _DEFAULT_LR,
        epsilon: float = _DEFAULT_EPSILON,
    ) -> None:
        self._data_dir = data_dir
        self._lr = max(0.01, min(0.5, learning_rate))
        self._epsilon = max(0.01, min(0.5, epsilon))
        self._global_doc_weight = 0.65
        self._global_graph_weight = 0.35
        self._doc_rewards: list[float] = []
        self._graph_rewards: list[float] = []
        self._total_trials = 0
        self._persona_weights: dict[str, tuple[float, float]] = {}

    def get_weights(self, persona_id: str | None = None) -> tuple[float, float]:
        if persona_id and persona_id in self._persona_weights:
            return self._persona_weights[persona_id]
        return self._global_doc_weight, self._global_graph_weight

    def get_explore_weights(self, persona_id: str | None = None) -> tuple[float, float]:
        if random.random() < self._epsilon:
            d = random.uniform(0.3, 0.8)
            return round(d, 4), round(1.0 - d, 4)
        return self.get_weights(persona_id)

    def record_feedback(
        self,
        doc_weight: float,
        graph_weight: float,
        reward: float,
        persona_id: str | None = None,
    ) -> None:
        reward = max(0.0, min(1.0, reward))
        self._total_trials += 1
        if doc_weight > graph_weight:
            self._doc_rewards.append(reward)
        else:
            self._graph_rewards.append(reward)

        self._global_doc_weight += (
            self._lr * (reward - 0.5) * (doc_weight - self._global_doc_weight)
        )
        self._global_graph_weight = max(0.1, min(0.9, 1.0 - self._global_doc_weight))
        self._global_doc_weight = max(0.1, min(0.9, 1.0 - self._global_graph_weight))
        self._global_doc_weight = round(self._global_doc_weight, 4)
        self._global_graph_weight = round(self._global_graph_weight, 4)

        if persona_id:
            pw = self._persona_weights.get(
                persona_id, (self._global_doc_weight, self._global_graph_weight)
            )
            nd = pw[0] + self._lr * (reward - 0.5) * (doc_weight - pw[0])
            ng = max(0.1, min(0.9, 1.0 - nd))
            nd = max(0.1, min(0.9, 1.0 - ng))
            self._persona_weights[persona_id] = (round(nd, 4), round(ng, 4))

    @property
    def stats(self) -> dict[str, Any]:
        avg_d = sum(self._doc_rewards) / max(1, len(self._doc_rewards))
        avg_g = sum(self._graph_rewards) / max(1, len(self._graph_rewards))
        return {
            "global_doc_weight": self._global_doc_weight,
            "global_graph_weight": self._global_graph_weight,
            "avg_doc_reward": round(avg_d, 4),
            "avg_graph_reward": round(avg_g, 4),
            "total_trials": self._total_trials,
            "epsilon": self._epsilon,
            "persona_count": len(self._persona_weights),
        }

    def _state_path(self) -> str:
        return os.path.join(self._data_dir, _STATE_FILE)

    def save_state(self) -> None:
        if not self._data_dir:
            return
        try:
            os.makedirs(self._data_dir, exist_ok=True)
            state = {
                "global_doc_weight": self._global_doc_weight,
                "global_graph_weight": self._global_graph_weight,
                "doc_rewards": self._doc_rewards[-100:],
                "graph_rewards": self._graph_rewards[-100:],
                "total_trials": self._total_trials,
                "persona_weights": {
                    k: list(v) for k, v in self._persona_weights.items()
                },
            }
            with open(self._state_path(), "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False)
        except OSError:
            logger.debug("[MAB] persist failed", exc_info=True)

    def load_state(self) -> None:
        if not self._data_dir:
            return
        try:
            path = self._state_path()
            if not os.path.exists(path):
                return
            with open(path, encoding="utf-8") as f:
                state = json.load(f)
            self._global_doc_weight = float(state.get("global_doc_weight", 0.65))
            self._global_graph_weight = float(state.get("global_graph_weight", 0.35))
            self._doc_rewards = state.get("doc_rewards", []) or []
            self._graph_rewards = state.get("graph_rewards", []) or []
            self._total_trials = int(state.get("total_trials", 0))
            self._persona_weights = {
                k: (float(v[0]), float(v[1]))
                for k, v in (state.get("persona_weights", {}) or {}).items()
            }
            logger.info(
                f"[MAB] restored: doc={self._global_doc_weight}, "
                f"graph={self._global_graph_weight}, trials={self._total_trials}"
            )
        except Exception:
            logger.debug("[MAB] restore failed", exc_info=True)


__all__ = ["MABWeightLearner"]
