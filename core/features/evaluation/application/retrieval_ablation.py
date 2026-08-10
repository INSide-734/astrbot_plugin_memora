"""检索组件能力描述和只读消融快照。"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any

from ....shared.adapter_capabilities import AdapterCapability, adapter_contract
from ....shared.mmr import MMRReranker

RETRIEVAL_VARIANT_NAMES = (
    "baseline",
    "A",
    "B",
    "C",
    "graph_expansion_off",
    "topic_expansion_off",
    "final_reranker_off",
    "final_reranker_mmr",
    "final_reranker_embedding_similarity",
    "graph_neighbors_off",
    "graph_neighbors_1_hop",
    "graph_neighbors_2_hops",
)

_DEFAULT_SELECTED = frozenset(
    {"baseline", "graph_expansion_off", "topic_expansion_off"}
)
_GRAPH_HOPS = {
    "graph_neighbors_off": 0,
    "graph_neighbors_1_hop": 1,
    "graph_neighbors_2_hops": 2,
}
_EVOLUTION_MODES = {"A": "disabled", "B": "readonly", "C": "active"}


@dataclass(frozen=True, slots=True)
class PreparedVariant:
    """单个消融变体的隔离引擎和安全设置。"""

    name: str
    available: bool
    reason_code: str
    engine: Any | None = None
    effective_settings: dict[str, Any] = field(default_factory=dict)
    execution_probe: Any | None = field(default=None, repr=False, compare=False)

    @property
    def capability_status(self) -> str:
        """返回报告使用的稳定能力状态。"""

        return "available" if self.available else "unavailable"

    def execution_reason_code(self) -> str:
        """返回运行后能力状态；无探针的变体默认保持 available。"""

        if self.execution_probe is None:
            return "available"
        reason_code = getattr(self.execution_probe, "reason_code", None)
        return str(reason_code or "variant_not_exercised")


class RetrievalAblationController:
    """从 live engine 创建不修改生产组件的单因子评测快照。"""

    def __init__(self, engine: Any | None) -> None:
        """保存仅用于能力探测和快照复制的 live engine 引用。"""

        self.engine = engine

    def descriptors(self) -> list[dict[str, Any]]:
        """返回 Dashboard 可安全展示的变体能力描述。"""

        return [self._descriptor(name) for name in RETRIEVAL_VARIANT_NAMES]

    def prepare(self, name: str) -> PreparedVariant:
        """创建隔离变体；不可用或构造失败时返回稳定 reason code。"""

        descriptor = self._descriptor(name)
        if not descriptor["available"]:
            return PreparedVariant(name, False, str(descriptor["reason_code"]))
        try:
            snapshot = _snapshot_engine(self.engine)
            settings = self._apply_variant(snapshot, name)
            probe = None
            if name == "final_reranker_embedding_similarity":
                probe = snapshot.dual_route_retriever.reranker
            return PreparedVariant(
                name,
                True,
                "available",
                snapshot,
                settings,
                probe,
            )
        except Exception:
            return PreparedVariant(name, False, "variant_prepare_failed")

    def _descriptor(self, name: str) -> dict[str, Any]:
        """返回单个变体的稳定、可公开能力描述。"""

        reason_code = self._reason_code(name)
        return {
            "name": name,
            "available": reason_code == "available",
            "reason_code": reason_code,
            "default_selected": name in _DEFAULT_SELECTED,
        }

    def _reason_code(self, name: str) -> str:
        """根据实际组件能力判断变体是否可执行。"""

        if name not in RETRIEVAL_VARIANT_NAMES:
            return "unknown_variant"
        if self.engine is None:
            return "missing_engine"
        if name == "baseline":
            return "available"
        config = getattr(self.engine, "config", None)
        if name in {"graph_expansion_off", "topic_expansion_off"}:
            if not isinstance(config, dict):
                return "missing_engine_config"
            config_key = (
                "recall_engine.chain_graph_expansion_enabled"
                if name == "graph_expansion_off"
                else "recall_engine.chain_topic_expansion_enabled"
            )
            return (
                "equivalent_to_baseline"
                if config.get(config_key) is False
                else "available"
            )
        if name in _EVOLUTION_MODES:
            evolution = (
                config.get("memory_evolution") if isinstance(config, dict) else None
            )
            if not isinstance(evolution, dict):
                return "missing_engine_config"
            current_mode = str(evolution.get("mode", "disabled"))
            current_reads_derived = current_mode in {"readonly", "active"}
            target_mode = _EVOLUTION_MODES[name]
            target_reads_derived = target_mode in {"readonly", "active"}
            if current_reads_derived == target_reads_derived:
                return "equivalent_to_baseline"
            if target_mode == "active":
                return "readonly_snapshot_cannot_activate_worker"
            if target_reads_derived:
                dual = getattr(self.engine, "dual_route_retriever", None)
                if dual is None:
                    return "missing_dual_route"
                if (
                    getattr(dual, "derived_expander", None) is None
                    and getattr(dual, "projection_reader", None) is None
                ):
                    return "missing_derived_reader"
            return "available"

        dual = getattr(self.engine, "dual_route_retriever", None)
        if name.startswith("final_reranker_") and dual is None:
            return "missing_dual_route"
        if name == "final_reranker_off":
            return (
                "available"
                if getattr(dual, "reranker", None) is not None
                else "equivalent_to_baseline"
            )
        if name == "final_reranker_mmr":
            current = getattr(dual, "reranker", None)
            target_weight = _bounded_weight(config, "reranker.mmr_lambda", 0.7)
            return (
                "equivalent_to_baseline"
                if current is not None
                and current.__class__.__name__ == "MMRReranker"
                and _same_number(getattr(current, "_lambda", None), target_weight)
                else "available"
            )
        if name == "final_reranker_embedding_similarity":
            faiss_db = getattr(self.engine, "faiss_db", None)
            target_weight = _bounded_weight(
                config,
                "reranker.embedding_similarity_lambda",
                0.7,
            )
            current = getattr(dual, "reranker", None)
            if (
                current is not None
                and current.__class__.__name__ == "EmbeddingSimilarityReranker"
                and _same_number(getattr(current, "_lambda", None), target_weight)
            ):
                return "equivalent_to_baseline"
            if not adapter_contract(faiss_db).supports(AdapterCapability.VECTOR_ACCESS):
                return "missing_document_vector_access"
            return (
                "available"
                if callable(getattr(faiss_db, "encode_query", None))
                and callable(getattr(faiss_db, "get_vector", None))
                else "missing_document_vector_access"
            )

        keyword = getattr(self.engine, "graph_keyword_retriever", None)
        if name in _GRAPH_HOPS:
            if keyword is None:
                return "missing_graph_retriever"
            return (
                "equivalent_to_baseline"
                if _same_integer(
                    getattr(keyword, "expansion_hops", None),
                    _GRAPH_HOPS[name],
                )
                else "available"
            )
        return "unknown_variant"

    def _apply_variant(self, snapshot: Any, name: str) -> dict[str, Any]:
        """只修改 snapshot 中与单个变体对应的实际组件。"""

        if name == "baseline":
            return {"variant": "baseline"}
        if name == "graph_expansion_off":
            snapshot.config["recall_engine.chain_graph_expansion_enabled"] = False
            return {"chain_graph_expansion_enabled": False}
        if name == "topic_expansion_off":
            snapshot.config["recall_engine.chain_topic_expansion_enabled"] = False
            return {"chain_topic_expansion_enabled": False}
        if name in _EVOLUTION_MODES:
            snapshot.config["memory_evolution"]["mode"] = _EVOLUTION_MODES[name]
            return {"memory_evolution_mode": _EVOLUTION_MODES[name]}
        if name == "final_reranker_off":
            snapshot.dual_route_retriever.reranker = None
            return {"final_reranker": "off"}
        if name == "final_reranker_mmr":
            weight = _bounded_weight(snapshot.config, "reranker.mmr_lambda", 0.7)
            snapshot.dual_route_retriever.reranker = MMRReranker(weight)
            return {"final_reranker": "mmr", "mmr_lambda": round(weight, 4)}
        if name == "final_reranker_embedding_similarity":
            weight = _bounded_weight(
                snapshot.config,
                "reranker.embedding_similarity_lambda",
                0.7,
            )
            snapshot.dual_route_retriever.reranker = (
                _EmbeddingSimilarityAblationReranker(
                    snapshot.faiss_db,
                    weight,
                )
            )
            return {
                "final_reranker": "embedding_similarity",
                "embedding_similarity_lambda": round(weight, 4),
            }
        if name in _GRAPH_HOPS:
            hops = _GRAPH_HOPS[name]
            snapshot.graph_keyword_retriever.expansion_hops = hops
            snapshot.config["graph_expansion_hops"] = hops
            return {"graph_neighbor_hops": hops}
        raise ValueError("unknown_variant")


class _ReadOnlyMaintenance:
    """阻止评测快照更新访问时间和其他 canonical metadata。"""

    @staticmethod
    def update_access_times_batch(*_args: Any, **_kwargs: Any) -> None:
        """忽略评测召回的访问强化。"""

        return None

    @staticmethod
    def migrate_session_if_needed(*_args: Any, **_kwargs: Any) -> None:
        """评测期间不触发 session metadata 迁移。"""

        return None


class _EmbeddingSimilarityAblationReranker:
    """严格记录 embedding-similarity 是否真实执行，禁止静默伪装完成。"""

    def __init__(self, faiss_db: Any, weight: float) -> None:
        """保存向量后端和实验权重，并初始化执行探针。"""

        self._faiss_db = faiss_db
        self._weight = weight
        self._failure_reason: str | None = None
        self._success_count = 0

    @property
    def reason_code(self) -> str:
        """返回跨用例聚合后的稳定执行状态。"""

        if self._failure_reason is not None:
            return self._failure_reason
        return "available" if self._success_count else "variant_not_exercised"

    @property
    def success_count(self) -> int:
        """返回真实完成 embedding 重排的次数。"""

        return self._success_count

    def _record_failure(self, reason_code: str) -> None:
        """保留首个失败原因，避免后续 fixture 覆盖可信度结论。"""

        if self._failure_reason is None:
            self._failure_reason = reason_code

    def rerank(
        self,
        results: list[Any],
        k: int,
        *,
        query: str = "",
        **_kwargs: Any,
    ) -> list[Any]:
        """执行严格余弦重排，并以稳定 reason code 记录运行能力。"""

        fallback = sorted(results, key=lambda item: item.final_score, reverse=True)
        if len(results) <= k or not query:
            return fallback[:k]
        try:
            query_vector = self._faiss_db.encode_query(query)
        except Exception:
            self._record_failure("embedding_query_failed")
            return fallback[:k]

        similarities: list[tuple[Any, float]] = []
        for result in results:
            try:
                document_vector = self._faiss_db.get_vector(result.doc_id)
                similarity = _finite_cosine(query_vector, document_vector)
            except Exception:
                self._record_failure("missing_document_vector_access")
                return fallback[:k]
            similarities.append((result, similarity))

        if not similarities:
            self._record_failure("missing_document_vector_access")
            return fallback[:k]
        for result, similarity in similarities:
            result.final_score = self._weight * similarity + (
                1.0 - self._weight
            ) * float(result.final_score)
        self._success_count += 1
        results.sort(key=lambda item: item.final_score, reverse=True)
        return results[:k]


def _finite_cosine(left: Any, right: Any) -> float:
    """计算有限向量的有界余弦相似度，非法向量由调用方降级。"""

    left_values = [float(value) for value in left]
    right_values = [float(value) for value in right]
    if not left_values or len(left_values) != len(right_values):
        raise ValueError("embedding_dimension_mismatch")
    if not all(math.isfinite(value) for value in left_values + right_values):
        raise ValueError("embedding_non_finite")
    left_norm = math.sqrt(sum(value * value for value in left_values))
    right_norm = math.sqrt(sum(value * value for value in right_values))
    if left_norm <= 0.0 or right_norm <= 0.0:
        raise ValueError("embedding_zero_norm")
    dot = sum(a * b for a, b in zip(left_values, right_values, strict=True))
    return max(0.0, min(1.0, dot / (left_norm * right_norm)))


def _bounded_weight(config: Any, key: str, default: float) -> float:
    """读取 0..1 的有限实验权重，非法配置回退安全默认值。"""

    try:
        value = float(config.get(key, default)) if isinstance(config, dict) else default
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    return max(0.0, min(1.0, value))


def _same_number(value: Any, expected: float) -> bool:
    """安全比较有限浮点状态，畸形 live 属性按不等价处理。"""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and math.isclose(number, expected)


def _same_integer(value: Any, expected: int) -> bool:
    """安全比较整数状态，畸形 live 属性按不等价处理。"""

    try:
        return int(value) == expected
    except (TypeError, ValueError):
        return False


def _snapshot_engine(engine: Any) -> Any:
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
        "dual_route_retriever",
        "_retrieval",
    ):
        value = getattr(engine, name, None)
        if value is not None:
            setattr(snapshot, name, copy.copy(value))

    hybrid = getattr(snapshot, "hybrid_retriever", None)
    keyword = getattr(snapshot, "graph_keyword_retriever", None)
    graph = getattr(snapshot, "graph_retriever", None)
    dual = getattr(snapshot, "dual_route_retriever", None)
    retrieval = getattr(snapshot, "_retrieval", None)
    if hybrid is not None and hasattr(hybrid, "config"):
        hybrid.config = snapshot.config
    if graph is not None:
        graph.config = snapshot.config
        if keyword is not None:
            graph.keyword_retriever = keyword
    if dual is not None:
        dual.config = snapshot.config
        if hybrid is not None:
            dual.document_retriever = hybrid
        if graph is not None:
            dual.graph_retriever = graph
    if retrieval is not None:
        retrieval._config = snapshot.config
        retrieval._cache = _empty_cache_like(getattr(retrieval, "_cache", {}))
        retrieval._session_cache = {}
        retrieval._dual_route_retriever = dual
        retrieval._search_memories = snapshot.search_memories
        retrieval._update_memory = None
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
    if dual is not None and callable(getattr(snapshot, "get_memory", None)):
        dual.memory_loader = snapshot.get_memory
    return snapshot


def _empty_cache_like(cache: Any) -> dict[Any, Any]:
    """尽量保留缓存容器类型；无法无参构造时安全退化为空字典。"""

    try:
        empty = type(cache)()
    except Exception:
        return {}
    return empty if isinstance(empty, dict) else {}


def _discard_background_work(awaitable: Any) -> None:
    """关闭意外生成的 coroutine，避免评测产生后台写任务。"""

    close = getattr(awaitable, "close", None)
    if callable(close):
        close()


__all__ = [
    "RETRIEVAL_VARIANT_NAMES",
    "PreparedVariant",
    "RetrievalAblationController",
]
