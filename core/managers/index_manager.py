"""增量索引 + FAISS IVF — 索引管理与自动切换。"""

from __future__ import annotations

import time
from typing import Any

from astrbot.api import logger

_IVF_SWITCH_THRESHOLD = 10000
_IVF_NLIST = 4096
_INCREMENTAL_REBUILD_THRESHOLD = 500
_REBUILD_COOLDOWN_SEC = 3600.0


class IndexManager:
    """监控向量数，触发增量重建，建议 IVF 切换。"""

    def __init__(
        self,
        get_vector_count_cb: Any | None = None,
        rebuild_index_cb: Any | None = None,
    ) -> None:
        self._get_count = get_vector_count_cb
        self._rebuild = rebuild_index_cb
        self._last_rebuild_at: float = 0.0
        self._incremental_count = 0
        self._total_vectors = 0
        self._index_type = "FlatL2"
        self._ivf_recommended = False

    # ---- 增量索引 ----

    def record_new_vectors(self, count: int) -> None:
        self._incremental_count += max(0, count)
        self._total_vectors += max(0, count)

    async def maybe_rebuild(self, force: bool = False) -> dict[str, Any]:
        if self._incremental_count < _INCREMENTAL_REBUILD_THRESHOLD and not force:
            return {
                "skipped": "below_threshold",
                "incremental": self._incremental_count,
            }
        now = time.time()
        if now - self._last_rebuild_at < _REBUILD_COOLDOWN_SEC and not force:
            return {
                "skipped": "cooldown",
                "cooldown_remaining": round(
                    _REBUILD_COOLDOWN_SEC - (now - self._last_rebuild_at),
                    0,
                ),
            }
        if self._rebuild is None:
            return {"skipped": "no_callback"}
        try:
            start = time.time()
            await self._rebuild()
            elapsed = time.time() - start
            self._last_rebuild_at = now
            self._incremental_count = 0
            logger.info(
                f"[IndexManager] rebuilt in {elapsed:.1f}s, {self._total_vectors} vectors"
            )
            return {
                "rebuilt": True,
                "elapsed_sec": round(elapsed, 1),
                "total_vectors": self._total_vectors,
            }
        except Exception as e:
            logger.error(f"[IndexManager] rebuild failed: {e}", exc_info=True)
            return {"rebuilt": False, "error": str(e)}

    # ---- I6: FAISS IVF ----

    def check_ivf_recommendation(self) -> dict[str, Any]:
        should = self._total_vectors >= _IVF_SWITCH_THRESHOLD
        recommended = f"IVF{_IVF_NLIST},Flat" if should else "FlatL2"
        if should and not self._ivf_recommended:
            self._ivf_recommended = True
            logger.warning(
                f"[IndexManager] IVF recommended: {self._total_vectors} >= {_IVF_SWITCH_THRESHOLD}"
            )
        return {
            "current_type": self._index_type,
            "recommended_type": recommended,
            "should_switch": should,
            "reason": (
                f"{self._total_vectors} >= {_IVF_SWITCH_THRESHOLD}"
                if should
                else f"{self._total_vectors} < {_IVF_SWITCH_THRESHOLD}"
            ),
            "threshold": _IVF_SWITCH_THRESHOLD,
            "vector_count": self._total_vectors,
        }

    def mark_index_type(self, index_type: str) -> None:
        self._index_type = index_type

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "index_type": self._index_type,
            "total_vectors": self._total_vectors,
            "incremental_pending": self._incremental_count,
            "ivf_recommended": self._ivf_recommended,
            "ivf_threshold": _IVF_SWITCH_THRESHOLD,
            "incremental_threshold": _INCREMENTAL_REBUILD_THRESHOLD,
            "last_rebuild_at": self._last_rebuild_at or None,
        }


__all__ = ["IndexManager"]
