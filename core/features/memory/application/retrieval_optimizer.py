"""
检索优化器
搜索缓存 + 检索后增强 + 干扰衰减 + 链式扩展 + 梦境整合 + 触发词注册
+ 记忆驱动情绪回路 + 测试效应
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from ...retrieval.rrf_fusion import HybridResult
from .retrieval_boosts import RetrievalBoostsMixin
from .retrieval_cache import RetrievalCacheMixin
from .retrieval_expansion import (
    RetrievalExpansionMixin,
)
from .retrieval_expansion import (
    _safe_json as _expansion_safe_json,
)
from .retrieval_narrative import RetrievalNarrativeMixin


class RetrievalOptimizer(
    RetrievalCacheMixin,
    RetrievalBoostsMixin,
    RetrievalExpansionMixin,
    RetrievalNarrativeMixin,
):
    """检索优化 — 缓存、增强、衰减、扩展、整合、触发词"""

    def __init__(
        self,
        config: dict[str, Any],
        db_connection: Any | None = None,
        dual_route_retriever: Any | None = None,
        search_memories_cb: Callable | None = None,
        get_memory_cb: Callable | None = None,
        update_memory_cb: Callable | None = None,
        create_tracked_task_cb: Callable | None = None,
    ) -> None:
        """保存召回协作对象，并冻结本次引擎生命周期内的增强配置。"""

        self._config = config
        self._db = db_connection
        self._dual_route_retriever = dual_route_retriever
        self._search_memories = search_memories_cb
        self._get_memory = get_memory_cb
        self._update_memory = update_memory_cb
        self._create_tracked_task = create_tracked_task_cb

        self._cache_enabled = bool(config.get("search_cache_enabled", True))
        self._cache_ttl = float(config.get("search_cache_ttl_seconds", 45.0))
        self._cache_max_size = int(config.get("search_cache_max_size", 256))
        self._cache_generation = 0
        self._cache: OrderedDict[tuple[Any, ...], tuple[float, list[HybridResult]]] = (
            OrderedDict()
        )

        # 请求级会话缓存：消除同一请求内 Bridge→RecallHandler 的重复搜索
        # 键=(session_id, persona_id)，TTL 极短（10s），仅在 MemoryEngine 层使用
        self._session_cache_enabled = bool(config.get("session_cache_enabled", True))
        self._session_cache_ttl = float(config.get("session_cache_ttl_seconds", 10.0))
        self._session_cache: dict[
            tuple[str, str], tuple[float, list[HybridResult]]
        ] = {}

        self._trigger_registry: dict[str, int] = {}

        # 测试效应配置：后台异步 + top-K 限制，避免阻塞检索热点路径
        self._testing_effect_async = bool(config.get("testing_effect_async", True))
        self._testing_effect_top_k = int(config.get("testing_effect_top_k", 5))
        self._emotion_scoring_mode = str(
            config.get("human_like_memory.emotion_scoring_mode", "enhanced")
        ).casefold()
        self._seasonal_recall_enabled = bool(
            config.get("human_like_memory.seasonal_recall_enabled", True)
        )

        # 由 apply_boosts 填充、供调用方读取的情绪反馈回路状态
        self._last_mood_delta: float = 0.0
        self._last_mood_tags: list[str] = []
        # 情感传染：带权重的情绪标签计数与主导情绪
        self._last_weighted_tags: dict[str, float] = {}
        self._last_dominant_emotion: str = "neutral"

    # ---- 搜索缓存 ----

    # ---- 检索后增强 ----

    # ---- 链式扩展 ----

    # ---- R5: 叙事连贯性 ----


def _safe_json(value: Any) -> dict[str, Any]:
    """保留旧模块导入路径并委托扩展模块规范化 metadata。"""
    return _expansion_safe_json(value)
