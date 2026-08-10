"""提供 canonical 写入指标、质量采样与隐私安全阶段计时。"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger

from ..features.observability.application.memory_write_timing import (
    MemoryWriteTiming,
    measure_memory_write_stage,
    memory_write_timing_scope,
    observe_memory_write,
)
from .memory_engine_atom_support import record_quality_samples


class MemoryEngineWriteObservabilityMixin:
    """为 MemoryEngine 提供既有写入指标与质量采样 helper。"""

    @staticmethod
    def _record_add_memory_failure(stage: str) -> None:
        """按固定阶段记录 canonical 写入失败计数。"""

        try:
            from ..features.observability.infrastructure.metrics import (
                MEMORY_WRITE_FAILURES_TOTAL,
            )

            MEMORY_WRITE_FAILURES_TOTAL.labels(stage=stage).inc()
        except Exception:
            logger.debug("[MemoryEngine] 写入失败指标记录失败", exc_info=True)

    def _record_add_memory_observability(
        self,
        *,
        doc_id: int,
        content: str,
        metadata: dict[str, Any],
        atoms: list | None,
        duration_s: float,
    ) -> None:
        """canonical 提交后记录低成本写入指标与质量样本。"""

        try:
            from ..features.observability.infrastructure.metrics import (
                MEMORY_ATOMS_TOTAL,
                MEMORY_WRITE_DURATION,
            )

            MEMORY_WRITE_DURATION.observe(max(0.0, duration_s))
            if atoms:
                MEMORY_ATOMS_TOTAL.inc(len(atoms))
        except Exception:
            logger.debug("[MemoryEngine] 写入指标记录失败", exc_info=True)

        scorer = getattr(self, "_quality_scorer", None) or getattr(
            self, "quality_scorer", None
        )
        if scorer is None:
            return
        try:
            record_quality_samples(
                scorer,
                doc_id=doc_id,
                content=content,
                metadata=metadata,
                atoms=list(atoms or []),
            )
        except Exception:
            logger.warning("[MemoryEngine] 质量评分记录失败", exc_info=True)


__all__ = [
    "MemoryEngineWriteObservabilityMixin",
    "MemoryWriteTiming",
    "measure_memory_write_stage",
    "memory_write_timing_scope",
    "observe_memory_write",
]
