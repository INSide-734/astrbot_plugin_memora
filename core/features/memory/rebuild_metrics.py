"""重建测量的 Prometheus 投影；只使用固定低基数标签。"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from ..observability.infrastructure.metrics import REGISTRY, Counter, Histogram
from .rebuild_observability import normalize_rebuild_trigger

REBUILD_TRIGGERS_TOTAL = Counter(
    "memora_rebuild_triggers_total",
    "Total derived rebuild triggers by stable reason.",
    labelnames=["reason"],
    registry=REGISTRY,
)
REBUILD_STAGE_SECONDS = Histogram(
    "memora_rebuild_stage_seconds",
    "Derived rebuild stage duration in seconds.",
    labelnames=["stage"],
    registry=REGISTRY,
)
REBUILD_ITEMS_TOTAL = Counter(
    "memora_rebuild_items_total",
    "Derived rebuild item counts by stage and outcome.",
    labelnames=["stage", "outcome"],
    registry=REGISTRY,
)
REBUILD_EMBEDDING_REQUESTS_TOTAL = Counter(
    "memora_rebuild_embedding_requests_total",
    "Physical embedding requests issued by derived rebuilds.",
    registry=REGISTRY,
)
REBUILD_EMBEDDING_BATCHES_TOTAL = Counter(
    "memora_rebuild_embedding_batches_total",
    "Embedding batches issued by derived rebuilds.",
    registry=REGISTRY,
)

_STAGE_LABELS = frozenset(
    {
        "canonical",
        "indexes",
        "bm25",
        "vector",
        "catalog",
        "graph",
        "evolution",
        "semantic_compression",
        "notes",
        "rebuild",
        "unknown",
    }
)


def record_rebuild_metrics(snapshot: Mapping[str, Any]) -> None:
    """将已清洗的重建快照投影到独立指标注册表。"""

    try:
        REBUILD_TRIGGERS_TOTAL.labels(
            reason=normalize_rebuild_trigger(snapshot.get("trigger_reason"))
        ).inc()
        stages = snapshot.get("stages")
        if isinstance(stages, Mapping):
            for raw_name, stage in stages.items():
                if not isinstance(stage, Mapping):
                    continue
                stage_name = raw_name if raw_name in _STAGE_LABELS else "unknown"
                duration = _safe_float(stage.get("duration_seconds"))
                if duration is not None:
                    REBUILD_STAGE_SECONDS.labels(stage=stage_name).observe(duration)
                for outcome in ("processed", "failed", "total"):
                    value = _safe_float(stage.get(outcome))
                    if value is not None:
                        REBUILD_ITEMS_TOTAL.labels(
                            stage=stage_name, outcome=outcome
                        ).inc(value)
        requests = _safe_float(snapshot.get("embedding_requests"))
        if requests is not None:
            REBUILD_EMBEDDING_REQUESTS_TOTAL.inc(requests)
        batches = _safe_float(snapshot.get("embedding_batches"))
        if batches is not None:
            REBUILD_EMBEDDING_BATCHES_TOTAL.inc(batches)
    except Exception:
        # 监控依赖不可用时不影响 canonical 派生重建。
        return


def _safe_float(value: Any) -> float | None:
    """仅接受有限非负数值。"""

    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


__all__ = [
    "REBUILD_EMBEDDING_BATCHES_TOTAL",
    "REBUILD_EMBEDDING_REQUESTS_TOTAL",
    "REBUILD_ITEMS_TOTAL",
    "REBUILD_STAGE_SECONDS",
    "REBUILD_TRIGGERS_TOTAL",
    "record_rebuild_metrics",
]
