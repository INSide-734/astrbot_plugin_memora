"""性格特质演化 — 大量相反记忆积累 → trait_drift 事件。"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from astrbot.api import logger

_MIN_OPPOSING = 14
_CONTRADICTION_RATIO = 0.7
_TRAIT_STATE_FILE = "trait_evolution.json"

_DEFAULT_TRAIT_DIMENSIONS: dict[str, tuple[str, str]] = {
    "openness": ("开放好奇", "保守传统"),
    "conscientiousness": ("认真负责", "随性散漫"),
    "extraversion": ("外向活泼", "内向安静"),
    "agreeableness": ("友善合作", "独立固执"),
    "neuroticism": ("敏感焦虑", "情绪稳定"),
}


class TraitEvolutionTracker:
    """追踪性格特质演化，检测 trait_drift 事件。"""

    def __init__(
        self,
        data_dir: str = "",
        min_opposing: int = _MIN_OPPOSING,
        contradiction_ratio: float = _CONTRADICTION_RATIO,
    ) -> None:
        self._data_dir = data_dir
        self._min_opposing = max(5, min(50, min_opposing))
        self._contradiction_ratio = max(0.5, min(0.95, contradiction_ratio))
        self._trait_scores: dict[str, list[int]] = {
            dim: [0, 0] for dim in _DEFAULT_TRAIT_DIMENSIONS
        }
        self._active_drifts: dict[str, dict[str, Any]] = {}
        self._drift_history: list[dict[str, Any]] = []

    def ingest_memory(
        self, content: str, metadata: dict[str, Any] | None = None
    ) -> None:
        content_lower = content.lower()
        meta = metadata or {}
        sentiment = meta.get("sentiment", "neutral")
        for dim, (pos_label, neg_label) in _DEFAULT_TRAIT_DIMENSIONS.items():
            ph, nh = pos_label in content_lower, neg_label in content_lower
            if ph and not nh:
                self._trait_scores[dim][0] += 1
            elif nh and not ph:
                self._trait_scores[dim][1] += 1
            elif sentiment == "positive":
                self._trait_scores[dim][0] += 1
            elif sentiment == "negative":
                self._trait_scores[dim][1] += 1

    def check_drift(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for dim, scores in self._trait_scores.items():
            pos, neg = scores
            total = pos + neg
            if total < self._min_opposing:
                continue
            pl, nl = _DEFAULT_TRAIT_DIMENSIONS[dim]
            ratio = max(pos, neg) / total if total > 0 else 0
            if ratio < self._contradiction_ratio:
                continue
            direction = pl if pos > neg else nl
            old = nl if pos > neg > 0 else (pl if neg > pos > 0 else "neutral")
            existing = self._active_drifts.get(dim)
            if existing and existing.get("direction") == direction:
                continue
            event = {
                "timestamp": time.time(),
                "dimension": dim,
                "old_dominant": old,
                "new_dominant": direction,
                "positive_count": pos,
                "negative_count": neg,
                "contradiction_ratio": round(ratio, 4),
                "total_evidence": total,
            }
            events.append(event)
            self._active_drifts[dim] = event
            self._drift_history.append(event)
            logger.warning(
                f"[TraitDrift] {dim}: {old} → {direction} "
                f"(ratio={ratio:.2f}, n={total})"
            )
        return events

    def get_trait_profile(self) -> dict[str, Any]:
        profile = {}
        for dim, (pl, nl) in _DEFAULT_TRAIT_DIMENSIONS.items():
            pos, neg = self._trait_scores[dim]
            total = max(1, pos + neg)
            profile[dim] = {
                "positive_label": pl,
                "negative_label": nl,
                "positive_count": pos,
                "negative_count": neg,
                "dominant": pl if pos >= neg else nl,
                "confidence": round(max(pos, neg) / total, 4),
            }
        return profile

    @property
    def drift_history(self) -> list[dict[str, Any]]:
        return list(self._drift_history)

    def get_drift_summary(self) -> dict[str, Any]:
        """返回 drifts 摘要供 auto_learning 消费。

        自主学习系统可据此判断角色是否在经历性格演化期，
        并在演化期间降低参数调整敏感度。
        """
        active_count = len(self._active_drifts)
        recent = self._drift_history[-5:] if self._drift_history else []
        return {
            "active_drifts": active_count,
            "total_drifts": len(self._drift_history),
            "recent_dimensions": [d["dimension"] for d in recent],
            "in_evolution_phase": active_count > 0,
        }

    def _state_path(self) -> str:
        return os.path.join(self._data_dir, _TRAIT_STATE_FILE)

    def save_state(self) -> None:
        if not self._data_dir:
            return
        try:
            os.makedirs(self._data_dir, exist_ok=True)
            state = {
                "trait_scores": self._trait_scores,
                "active_drifts": self._active_drifts,
                "drift_history": self._drift_history[-20:],
            }
            with open(self._state_path(), "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False)
        except OSError:
            logger.debug("[TraitDrift] persist failed", exc_info=True)

    def load_state(self) -> None:
        if not self._data_dir:
            return
        try:
            path = self._state_path()
            if not os.path.exists(path):
                return
            with open(path, encoding="utf-8") as f:
                state = json.load(f)
            for dim in self._trait_scores:
                if dim in state.get("trait_scores", {}):
                    self._trait_scores[dim] = [
                        int(state["trait_scores"][dim][0]),
                        int(state["trait_scores"][dim][1]),
                    ]
            self._active_drifts = state.get("active_drifts", {}) or {}
            self._drift_history = state.get("drift_history", []) or []
            logger.info(
                f"[TraitDrift] restored: {len(self._drift_history)} drifts, "
                f"{len(self._active_drifts)} active"
            )
        except Exception:
            logger.debug("[TraitDrift] restore failed", exc_info=True)


__all__ = ["TraitEvolutionTracker"]
