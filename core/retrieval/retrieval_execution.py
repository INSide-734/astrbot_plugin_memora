"""受管理文档、图和 Atom 三路并发检索协调器。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger


@dataclass(frozen=True, slots=True)
class RouteOutcome:
    """单条查询的三路结果、Atom 证据和请求局部计时。"""

    document_results: list[Any]
    graph_results: list[Any]
    atom_results: list[Any]
    atom_scores: dict[str, float]
    timing: dict[str, float | bool]
    degraded_routes: tuple[str, ...]


class RouteExecutionCoordinator:
    """并发执行文档、图和 Atom 证据路，并对失败做有限降级。"""

    MAX_FAILED_ROUTES_BEFORE_ABORT: int = 2

    def __init__(
        self,
        document_retriever: Any,
        graph_retriever: Any,
        atom_retriever: Any | None = None,
    ) -> None:
        self._document_retriever = document_retriever
        self._graph_retriever = graph_retriever
        self._atom_retriever = atom_retriever

    async def execute(
        self,
        query: str,
        k: int,
        session_id: str | None = None,
        persona_id: str | None = None,
        memory_types: list[str] | None = None,
        reference_time: Any = None,
        deadline_monotonic: float | None = None,
        use_graph_route: bool = True,
    ) -> RouteOutcome:
        """并发启动三路检索，并在调用方取消后收敛所有子任务。"""
        timing: dict[str, float | bool] = {}
        degraded: list[str] = []
        _t_total_start = time.perf_counter()

        async def _run_document() -> tuple[list[Any], dict[str, float]]:
            route_timing: dict[str, float] = {}
            started = time.perf_counter()
            try:
                results = await self._document_retriever.search(
                    query,
                    k,
                    session_id,
                    persona_id,
                    memory_types=memory_types,
                    timing_sink=route_timing,
                    deadline_monotonic=deadline_monotonic,
                )
                route_timing.setdefault(
                    "document_total_ms", (time.perf_counter() - started) * 1000.0
                )
                return results, route_timing
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "[RouteExecutionCoordinator] 文档路降级，异常类型=%s",
                    exc.__class__.__name__,
                )
                degraded.append("document")
                route_timing["document_total_ms"] = (
                    time.perf_counter() - started
                ) * 1000.0
                return [], route_timing

        async def _run_graph() -> tuple[list[Any], dict[str, float]]:
            route_timing: dict[str, float] = {}
            started = time.perf_counter()
            try:
                results = await self._graph_retriever.search(
                    query,
                    k,
                    session_id,
                    persona_id,
                    memory_types=memory_types,
                    reference_time=reference_time,
                    timing_sink=route_timing,
                    deadline_monotonic=deadline_monotonic,
                )
                route_timing.setdefault(
                    "graph_total_ms", (time.perf_counter() - started) * 1000.0
                )
                return results, route_timing
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "[RouteExecutionCoordinator] 图路降级，异常类型=%s",
                    exc.__class__.__name__,
                )
                degraded.append("graph")
                route_timing["graph_total_ms"] = (
                    time.perf_counter() - started
                ) * 1000.0
                return [], route_timing

        async def _run_atom() -> tuple[list[Any], dict[str, float]]:
            route_timing: dict[str, float] = {}
            if self._atom_retriever is None:
                return [], route_timing
            try:
                started = time.perf_counter()
                results = await self._atom_retriever.search(
                    query,
                    k=max(k * 2, k),
                    session_id=session_id,
                    persona_id=persona_id,
                )
                route_timing["atom_ms"] = (time.perf_counter() - started) * 1000.0
                return results, route_timing
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "[RouteExecutionCoordinator] Atom 路降级，异常类型=%s",
                    exc.__class__.__name__,
                )
                degraded.append("atom")
                route_timing["atom_ms"] = (time.perf_counter() - started) * 1000.0
                return [], route_timing

        tasks: dict[str, asyncio.Task[tuple[list[Any], dict[str, float]]]] = {
            "document": asyncio.create_task(_run_document()),
            "atom": asyncio.create_task(_run_atom()),
        }
        if use_graph_route:
            tasks["graph"] = asyncio.create_task(_run_graph())
        else:
            timing["graph_route_skipped"] = True
        try:
            route_results = dict(
                zip(tasks, await asyncio.gather(*tasks.values()), strict=True)
            )
        except asyncio.CancelledError:
            for task in tasks.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
            raise

        doc_results, doc_timing = route_results["document"]
        atom_results, atom_timing = route_results["atom"]
        graph_results, graph_timing = route_results.get("graph", ([], {}))

        # 合并各路子阶段计时
        for source_timing in (doc_timing, graph_timing, atom_timing):
            for key, value in source_timing.items():
                if isinstance(value, (int, float)):
                    timing[key] = float(value)

        timing["route_wall_ms"] = (time.perf_counter() - _t_total_start) * 1000.0
        timing["document_total_ms"] = timing.get("document_total_ms", 0.0)
        timing["graph_total_ms"] = timing.get("graph_total_ms", 0.0)
        timing["atom_ms"] = timing.get("atom_ms", 0.0)

        # 解析 Atom 评分
        atom_scores: dict[str, float] = {}
        for result in atom_results:
            doc_id = getattr(result, "parent_doc_id", None)
            if doc_id is not None:
                atom_scores[str(doc_id)] = getattr(result, "score", 0.0)

        return RouteOutcome(
            document_results=list(doc_results),
            graph_results=list(graph_results),
            atom_results=list(atom_results),
            atom_scores=atom_scores,
            timing=timing,
            degraded_routes=tuple(degraded),
        )
