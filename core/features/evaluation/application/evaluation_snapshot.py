"""离线评测的只读引擎快照、路由重建与 Provider 调用计数。

模块导入必须保持无 AstrBot 依赖；``RouteExecutionCoordinator`` 在真正构建
快照时延迟导入，避免评测包导入契约失败。
"""

from __future__ import annotations

import copy
from typing import Any

__all__ = [
    "ProviderCallCounter",
    "apply_route_settings",
    "build_evaluation_snapshot",
]


class ProviderCallCounter:
    """按评测 case 重置的 Provider 调用计数。"""

    def __init__(self) -> None:
        self._calls = 0

    def reset(self) -> None:
        """在单次检索开始前清零。"""

        self._calls = 0

    def record(self) -> None:
        """记录一次实际向量检索调用。"""

        self._calls += 1

    def value(self) -> float:
        """返回当前 case 的真实调用次数。"""

        return float(self._calls)


class _CountingVectorStore:
    """只读代理：计数 retrieve 调用，其余属性原样委托。"""

    def __init__(self, target: Any, counter: ProviderCallCounter) -> None:
        self._target = target
        self._counter = counter

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)

    async def retrieve(self, *args: Any, **kwargs: Any) -> Any:
        """记录 Provider 调用后委托原始向量库。"""

        self._counter.record()
        return await self._target.retrieve(*args, **kwargs)


class _CountingRetriever:
    """只读检索器代理：计数 search 调用并原样委托。"""

    def __init__(self, target: Any) -> None:
        self._target = target
        self.call_count = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)

    @property
    def reason_code(self) -> str:
        """返回检索路是否真实执行的稳定状态。"""

        return "available" if self.call_count else "variant_not_exercised"

    async def search(self, *args: Any, **kwargs: Any) -> Any:
        """记录一次检索调用后委托原始检索器。"""

        self.call_count += 1
        return await self._target.search(*args, **kwargs)


class _ReadOnlyAtomRetriever:
    """Atom 检索只读代理：允许 search，禁止写访问反馈。"""

    def __init__(self, target: Any) -> None:
        self._target = target
        self.search_count = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)

    @property
    def reason_code(self) -> str:
        """返回 Atom 路是否真实执行的稳定状态。"""

        return "available" if self.search_count else "variant_not_exercised"

    async def search(self, *args: Any, **kwargs: Any) -> Any:
        """执行只读检索并记录真实调用。"""

        self.search_count += 1
        return await self._target.search(*args, **kwargs)

    async def touch_many(self, *_args: Any, **_kwargs: Any) -> None:
        """评测不得修改 Atom 访问状态。"""

        return None


class _ReadOnlyMaintenance:
    """阻止评测快照更新访问时间和其他 canonical metadata。"""

    @staticmethod
    def update_access_times_batch(*_args: Any, **_kwargs: Any) -> None:
        return None

    @staticmethod
    def migrate_session_if_needed(*_args: Any, **_kwargs: Any) -> None:
        return None


def _discard_background_work(awaitable: Any) -> None:
    """关闭意外生成的 coroutine，避免评测产生后台写任务。"""

    close = getattr(awaitable, "close", None)
    if callable(close):
        close()


def build_evaluation_snapshot(engine: Any) -> Any:
    """复制评测会修改的组件，并共享只读 Store/索引后端。"""

    snapshot = copy.copy(engine)
    live_config = getattr(engine, "config", None)
    if isinstance(live_config, dict):
        snapshot.config = dict(live_config)
        evolution = live_config.get("memory_evolution")
        if isinstance(evolution, dict):
            snapshot.config["memory_evolution"] = dict(evolution)

    for name in (
        "hybrid_retriever",
        "graph_keyword_retriever",
        "graph_retriever",
        "graph_vector_retriever",
        "dual_route_retriever",
        "_retrieval",
    ):
        value = getattr(engine, name, None)
        if value is not None:
            setattr(snapshot, name, copy.copy(value))

    counter = getattr(engine, "_evaluation_cost_counter", None)
    if not isinstance(counter, ProviderCallCounter):
        counter = ProviderCallCounter()
    hybrid = getattr(snapshot, "hybrid_retriever", None)
    keyword = getattr(snapshot, "graph_keyword_retriever", None)
    graph = getattr(snapshot, "graph_retriever", None)
    graph_vector = getattr(snapshot, "graph_vector_retriever", None)
    dual = getattr(snapshot, "dual_route_retriever", None)
    retrieval = getattr(snapshot, "_retrieval", None)
    atom = getattr(snapshot, "atom_retriever", None)
    readonly_atom = _ReadOnlyAtomRetriever(atom) if atom is not None else None

    if hybrid is not None and hasattr(hybrid, "config"):
        hybrid.config = snapshot.config
    if hybrid is not None:
        vector_retriever = getattr(hybrid, "vector_retriever", None)
        if vector_retriever is not None:
            vector_retriever = copy.copy(vector_retriever)
            hybrid.vector_retriever = vector_retriever
            vector_store = getattr(vector_retriever, "faiss_db", None)
            if vector_store is not None:
                vector_retriever.faiss_db = _CountingVectorStore(vector_store, counter)
    if graph is not None:
        graph.config = snapshot.config
        if keyword is not None:
            graph.keyword_retriever = keyword
    if graph_vector is None and graph is not None:
        graph_vector = getattr(graph, "vector_retriever", None)
    if graph_vector is not None:
        graph_vector = copy.copy(graph_vector)
        graph_vector.faiss_db = _CountingVectorStore(
            getattr(graph_vector, "faiss_db", None),
            counter,
        )
        snapshot.graph_vector_retriever = graph_vector
        if graph is not None:
            graph.vector_retriever = graph_vector

    graph_route = _CountingRetriever(graph) if graph is not None else None
    if graph_route is not None:
        snapshot.graph_retriever = graph_route

    live_faiss = getattr(engine, "faiss_db", None)
    if live_faiss is not None:
        snapshot.faiss_db = _CountingVectorStore(live_faiss, counter)
    live_graph_vector = getattr(engine, "graph_vector_db", None)
    if live_graph_vector is not None:
        snapshot.graph_vector_db = _CountingVectorStore(live_graph_vector, counter)

    if atom is not None:
        snapshot.atom_retriever = readonly_atom
    if dual is not None:
        dual.config = snapshot.config
        if hybrid is not None:
            dual.document_retriever = hybrid
        if graph_route is not None:
            dual.graph_retriever = graph_route
        dual.atom_retriever = readonly_atom
        dual._create_tracked_task = _discard_background_work
        _rebuild_route_coordinator(dual, hybrid, graph_route, readonly_atom)
        if callable(getattr(snapshot, "get_memory", None)):
            dual.memory_loader = snapshot.get_memory

    if retrieval is not None:
        retrieval._config = snapshot.config
        retrieval._cache = _empty_cache_like(getattr(retrieval, "_cache", {}))
        retrieval._session_cache = {}
        retrieval._dual_route_retriever = dual
        retrieval._search_memories = snapshot.search_memories
        retrieval._update_memory = None
        retrieval._reinforce_recall_state = None
        retrieval._apply_interference_decay = None
        retrieval._create_tracked_task = _discard_background_work
        if callable(getattr(snapshot, "get_memory", None)):
            retrieval._get_memory = snapshot.get_memory

    for cache_name in ("cache", "search_cache", "session_cache"):
        value = getattr(snapshot, cache_name, None)
        if isinstance(value, dict):
            setattr(snapshot, cache_name, _empty_cache_like(value))

    if hasattr(snapshot, "_maintenance"):
        snapshot._maintenance = _ReadOnlyMaintenance()
    if hasattr(snapshot, "_create_tracked_task"):
        snapshot._create_tracked_task = _discard_background_work
    snapshot._evaluation_cost_counter = counter
    return snapshot


def apply_route_settings(
    snapshot: Any,
    *,
    document_route_weight: float | None = None,
    graph_route_weight: float | None = None,
    graph_retrieval_enabled: bool | None = None,
    dynamic_route_weighting: bool | None = None,
    atom_route_weight: float | None = None,
) -> dict[str, Any]:
    """把单个变体的路由设置同时写入 config 与 dual 实例。"""

    config = getattr(snapshot, "config", None)
    if not isinstance(config, dict):
        config = {}
        snapshot.config = config
    dual = getattr(snapshot, "dual_route_retriever", None)
    settings: dict[str, Any] = {}

    def _set(name: str, value: Any) -> None:
        config[name] = value
        if dual is not None:
            setattr(dual, name, value)
        settings[name] = value

    if document_route_weight is not None:
        _set("document_route_weight", float(document_route_weight))
    if graph_route_weight is not None:
        _set("graph_route_weight", float(graph_route_weight))
    if graph_retrieval_enabled is not None:
        _set("graph_retrieval_enabled", bool(graph_retrieval_enabled))
    if dynamic_route_weighting is not None:
        _set("dynamic_route_weighting", bool(dynamic_route_weighting))
    if atom_route_weight is not None:
        config["atom_route_weight"] = float(atom_route_weight)
        settings["atom_route_weight"] = float(atom_route_weight)
    return settings


def _rebuild_route_coordinator(
    dual: Any,
    document: Any,
    graph: Any,
    atom: Any,
) -> None:
    """用快照检索器重建协调器，避免仍调用 live 检索器。"""

    try:
        from ...retrieval.retrieval_execution import RouteExecutionCoordinator
    except ImportError:
        return
    if document is None:
        return
    dual._route_coordinator = RouteExecutionCoordinator(
        document_retriever=document,
        graph_retriever=graph,
        atom_retriever=atom,
    )


def _empty_cache_like(cache: Any) -> dict[Any, Any]:
    """尽量保留缓存容器类型；无法无参构造时安全退化为空字典。"""

    try:
        empty = type(cache)()
    except Exception:
        return {}
    return empty if isinstance(empty, dict) else {}
