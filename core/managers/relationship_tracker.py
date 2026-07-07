"""关系阶段追踪 — 基于 warmth score 累积量化人际关系阶段。

认知原理：人际关系是分阶段的 — 陌生人 → 熟人 → 朋友 → 密友 → 知己。
每个阶段对应不同的社交行为和自我暴露程度。

warmth score 通过交互重要性 × 情感极性累积，随时间缓慢衰减。
"""

from __future__ import annotations

import asyncio
import json
import time
from enum import Enum
from pathlib import Path
from typing import Any

from astrbot.api import logger


class RelationshipStage(str, Enum):
    """关系阶段 — 由 warmth score 决定。"""

    STRANGER = "stranger"  # 0.00 – 0.15  陌生人
    ACQUAINTANCE = "acquaintance"  # 0.15 – 0.35  熟人
    FRIEND = "friend"  # 0.35 – 0.60  朋友
    CLOSE_FRIEND = "close_friend"  # 0.60 – 0.80  密友
    CONFIDANT = "confidant"  # 0.80 – 1.00  知己

    @classmethod
    def from_warmth(cls, warmth: float) -> RelationshipStage:
        if warmth >= 0.80:
            return cls.CONFIDANT
        if warmth >= 0.60:
            return cls.CLOSE_FRIEND
        if warmth >= 0.35:
            return cls.FRIEND
        if warmth >= 0.15:
            return cls.ACQUAINTANCE
        return cls.STRANGER


# 每日自然衰减率（无交互时 warmth 每天降低的比例）
_DECAY_PER_DAY = 0.005  # 0.5% / day — 极慢衰减


class RelationshipTracker:
    """关系阶段追踪器 — 累积 warmth score，在交互中量化关系演进。"""

    def __init__(
        self,
        data_dir: str | None = None,
        decay_per_day: float = _DECAY_PER_DAY,
    ) -> None:
        self._data_dir = Path(data_dir) if data_dir else None
        self._decay_per_day = decay_per_day
        self._warmth: dict[str, float] = {}
        self._interaction_counts: dict[str, int] = {}
        self._last_decay_at: float = time.time()

        self._state_file = (
            self._data_dir / "relationship_state.json" if self._data_dir else None
        )

    # ---- public API ----

    async def record_interaction(
        self,
        participant_id: str,
        importance: float = 0.5,
        sentiment: str = "neutral",
    ) -> tuple[float, RelationshipStage]:
        """记录一次交互，返回 (new_warmth, new_stage)。

        Args:
            participant_id: 参与者标识（如 user_id）
            importance: 交互重要性 (0.0-1.0)
            sentiment: 情感极性 (positive/neutral/negative)
        """
        # 每日衰减检查
        await self._maybe_decay()

        importance = max(0.0, min(1.0, importance))
        delta = importance * 0.1  # 基础增量

        sentiment = sentiment.lower().strip()
        if sentiment == "positive" or sentiment.startswith("pos"):
            delta *= 1.3
        elif sentiment == "negative" or sentiment.startswith("neg"):
            delta *= 0.7

        old_warmth = self._warmth.get(participant_id, 0.0)
        new_warmth = min(1.0, max(0.0, old_warmth + delta))
        self._warmth[participant_id] = new_warmth

        count = self._interaction_counts.get(participant_id, 0) + 1
        self._interaction_counts[participant_id] = count

        new_stage = RelationshipStage.from_warmth(new_warmth)
        old_stage = RelationshipStage.from_warmth(old_warmth)

        if new_stage != old_stage:
            logger.info(
                f"[RelationshipTracker] {participant_id}: "
                f"{old_stage.value} → {new_stage.value} "
                f"(warmth={new_warmth:.3f}, interactions={count})"
            )

        return new_warmth, new_stage

    def get_warmth(self, participant_id: str) -> float:
        """查询 warmth score（不记录交互）。"""
        return self._warmth.get(participant_id, 0.0)

    def get_stage(self, participant_id: str) -> RelationshipStage:
        """查询关系阶段（不记录交互）。"""
        return RelationshipStage.from_warmth(self.get_warmth(participant_id))

    def get_stats(self, participant_id: str) -> dict[str, Any]:
        """获取完整关系统计。"""
        warmth = self.get_warmth(participant_id)
        return {
            "participant_id": participant_id,
            "warmth": round(warmth, 4),
            "stage": self.get_stage(participant_id).value,
            "interaction_count": self._interaction_counts.get(participant_id, 0),
        }

    def all_stats(self) -> list[dict[str, Any]]:
        """获取所有参与者的关系统计。"""
        return [self.get_stats(pid) for pid in self._warmth]

    # ---- persistence ----

    async def load(self) -> None:
        """从文件加载关系状态。"""
        if not self._state_file or not self._state_file.exists():
            return
        try:
            content = await asyncio.to_thread(
                self._state_file.read_text,
                encoding="utf-8",
            )
            state = json.loads(content)
            self._warmth = state.get("warmth", {})
            self._interaction_counts = state.get("interaction_counts", {})
            self._last_decay_at = state.get("last_decay_at", time.time())
            logger.info(f"[RelationshipTracker] 加载 {len(self._warmth)} 个关系状态")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[RelationshipTracker] 加载状态失败: {e}")

    async def save(self) -> None:
        """持久化关系状态到文件。"""
        if not self._state_file:
            return
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            content = json.dumps(
                {
                    "warmth": self._warmth,
                    "interaction_counts": self._interaction_counts,
                    "last_decay_at": self._last_decay_at,
                },
                ensure_ascii=False,
            )
            await asyncio.to_thread(
                self._state_file.write_text,
                content,
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning(f"[RelationshipTracker] 保存状态失败: {e}")

    # ---- internal ----

    async def _maybe_decay(self) -> None:
        """应用每日自然衰减。"""
        now = time.time()
        days_since = (now - self._last_decay_at) / 86400.0
        if days_since < 1.0:
            return

        decay_factor = max(0.0, 1.0 - self._decay_per_day * days_since)
        for pid in list(self._warmth):
            self._warmth[pid] = max(0.0, self._warmth[pid] * decay_factor)
        self._last_decay_at = now


__all__ = ["RelationshipTracker", "RelationshipStage"]
