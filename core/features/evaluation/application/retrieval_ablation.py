"""检索组件能力描述和只读消融快照。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ....shared.adapter_capabilities import AdapterCapability, adapter_contract
from ....shared.mmr import MMRReranker
from .evaluation_snapshot import apply_route_settings, build_evaluation_snapshot

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

GATE_VARIANT_NAMES = (
    "fixed_route_baseline",
    "document_only_baseline",
    "atom_signal_on",
    "graph_route_off",
    "dynamic_route_weighting_on",
    "final_reranker_mmr",
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

    def prepare(self, name: str, *, gate: bool = False) -> PreparedVariant:
        """创建隔离变体；gate=True 时按固定 baseline 语义判定可用性。"""

        if gate:
            return self._prepare_gate_variant(name)
        descriptor = self._descriptor(name)
        if not descriptor["available"]:
            return PreparedVariant(name, False, str(descriptor["reason_code"]))
        try:
            snapshot = build_evaluation_snapshot(self.engine)
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

    def _prepare_gate_variant(self, name: str) -> PreparedVariant:
        """按固定 baseline 语义准备效果门变体。"""

        reason_code = self._gate_reason_code(name)
        if reason_code != "available":
            return PreparedVariant(name, False, reason_code)
        try:
            snapshot = build_evaluation_snapshot(self.engine)
            settings = self._apply_gate_variant(snapshot, name)
            probe = None
            if name == "atom_signal_on":
                probe = getattr(snapshot, "atom_retriever", None)
            elif name == "final_reranker_mmr":
                probe = getattr(
                    getattr(snapshot, "dual_route_retriever", None),
                    "reranker",
                    None,
                )
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

    def _gate_reason_code(self, name: str) -> str:
        """按固定 baseline 判断组件是否存在，不读取 live 等价状态。"""

        if name not in GATE_VARIANT_NAMES:
            return "unknown_variant"
        if self.engine is None:
            return "missing_engine"
        if name == "document_only_baseline":
            return "available"
        if name == "fixed_route_baseline":
            return (
                "available"
                if getattr(self.engine, "graph_retriever", None) is not None
                else "missing_graph_retriever"
            )
        if name == "graph_route_off":
            return (
                "available"
                if getattr(self.engine, "graph_retriever", None) is not None
                else "equivalent_to_baseline"
            )
        if name == "atom_signal_on":
            return (
                "available"
                if getattr(self.engine, "atom_retriever", None) is not None
                else "missing_atom_retriever"
            )
        if name == "dynamic_route_weighting_on":
            return (
                "available"
                if getattr(self.engine, "graph_retriever", None) is not None
                else "missing_graph_retriever"
            )
        if name == "final_reranker_mmr":
            return (
                "available"
                if getattr(self.engine, "dual_route_retriever", None) is not None
                else "missing_dual_route"
            )
        return "unknown_variant"

    def _apply_gate_variant(self, snapshot: Any, name: str) -> dict[str, Any]:
        """只修改快照的路由、Atom、动态权重和重排设置。"""

        self._fix_evolution(snapshot)
        dual = getattr(snapshot, "dual_route_retriever", None)
        if dual is None:
            raise ValueError("missing_dual_route")
        dual.reranker = None

        if name == "fixed_route_baseline":
            settings = apply_route_settings(
                snapshot,
                document_route_weight=0.65,
                graph_route_weight=0.35,
                graph_retrieval_enabled=True,
                dynamic_route_weighting=False,
                atom_route_weight=0.0,
            )
        elif name == "document_only_baseline":
            settings = apply_route_settings(
                snapshot,
                document_route_weight=1.0,
                graph_route_weight=0.0,
                graph_retrieval_enabled=False,
                dynamic_route_weighting=False,
                atom_route_weight=0.0,
            )
        elif name == "atom_signal_on":
            settings = apply_route_settings(
                snapshot,
                document_route_weight=0.65,
                graph_route_weight=0.35,
                graph_retrieval_enabled=True,
                dynamic_route_weighting=False,
                atom_route_weight=0.25,
            )
        elif name == "graph_route_off":
            settings = apply_route_settings(
                snapshot,
                document_route_weight=0.65,
                graph_route_weight=0.35,
                graph_retrieval_enabled=False,
                dynamic_route_weighting=False,
                atom_route_weight=0.0,
            )
        elif name == "dynamic_route_weighting_on":
            settings = apply_route_settings(
                snapshot,
                document_route_weight=0.65,
                graph_route_weight=0.35,
                graph_retrieval_enabled=True,
                dynamic_route_weighting=True,
                atom_route_weight=0.0,
            )
        elif name == "final_reranker_mmr":
            settings = apply_route_settings(
                snapshot,
                document_route_weight=0.65,
                graph_route_weight=0.35,
                graph_retrieval_enabled=True,
                dynamic_route_weighting=False,
                atom_route_weight=0.0,
            )
            weight = _bounded_weight(snapshot.config, "reranker.mmr_lambda", 0.7)
            dual.reranker = _CountingReranker(MMRReranker(weight))
            settings.update(
                {
                    "final_reranker": "mmr",
                    "mmr_lambda": round(weight, 4),
                }
            )
        else:
            raise ValueError("unknown_gate_variant")
        return {"variant": name, **settings}

    @staticmethod
    def _fix_evolution(snapshot: Any) -> None:
        """所有门禁变体使用同一 evolution 状态，避免派生路污染单因子。"""

        config = getattr(snapshot, "config", None)
        if not isinstance(config, dict):
            config = {}
            snapshot.config = config
        evolution = config.get("memory_evolution")
        if not isinstance(evolution, dict):
            evolution = {}
            config["memory_evolution"] = evolution
        evolution["enabled"] = False
        evolution["mode"] = "disabled"


class _CountingReranker:
    """包装同步 MMR，记录真实 rerank 调用。"""

    def __init__(self, target: Any) -> None:
        self._target = target
        self.calls = 0

    @property
    def reason_code(self) -> str:
        """返回重排是否真实执行的稳定状态。"""

        return "available" if self.calls else "variant_not_exercised"

    def rerank(self, *args: Any, **kwargs: Any) -> Any:
        """记录调用后委托 MMR。"""

        self.calls += 1
        return self._target.rerank(*args, **kwargs)


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


__all__ = [
    "GATE_VARIANT_NAMES",
    "RETRIEVAL_VARIANT_NAMES",
    "PreparedVariant",
    "RetrievalAblationController",
]
