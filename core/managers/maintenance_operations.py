"""
维护操作
衰减、清理、统计、迁移、存储维护、图索引重建、会话记忆查询
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..features.decay.application import DecayOperationsMixin
from .lifecycle_operations import LifecycleOperationsMixin
from .stats_operations import StatsOperationsMixin

if TYPE_CHECKING:
    pass


class MaintenanceOperations(
    DecayOperationsMixin,
    LifecycleOperationsMixin,
    StatsOperationsMixin,
):
    """维护操作 -- 衰减、清理、统计、迁移、存储维护"""

    def __init__(
        self,
        config: dict[str, Any],
        db_connection: Any | None = None,
        db_path: str = "",
        faiss_db: Any = None,
        hybrid_retriever: Any | None = None,
        graph_memory_manager: Any | None = None,
        graph_store: Any | None = None,
        batch_delete_memories_cb: Callable | None = None,
        invalidate_cache_cb: Callable | None = None,
        update_memory_cb: Callable | None = None,
    ) -> None:
        self._config = config
        self._db = db_connection
        self._db_path = db_path
        self._faiss_db = faiss_db
        self._hybrid_retriever = hybrid_retriever
        self._graph_memory_manager = graph_memory_manager
        self._graph_store = graph_store
        self._batch_delete_memories = batch_delete_memories_cb
        self._invalidate_cache = invalidate_cache_cb
        self._update_memory = update_memory_cb
