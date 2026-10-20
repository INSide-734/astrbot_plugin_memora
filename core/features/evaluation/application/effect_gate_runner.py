"""CLI/集成测试使用的效果门变体执行器。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..domain.effect_gate import (
    EffectGate,
    EffectGatePolicy,
    VariantEvidence,
)
from .engine_adapter import make_memory_engine_retriever
from .injection_ablation import InjectionAblationObserver
from .retrieval_ablation import RetrievalAblationController
from .retrieval_quality import EvaluationCase, evaluate_cases

__all__ = [
    "FACTOR_VARIANTS",
    "GateRunResult",
    "GateVariantRun",
    "run_effect_gate",
]

FACTOR_VARIANTS: dict[str, str] = {
    "atom": "atom_signal_on",
    "graph_route": "graph_route_off",
    "dynamic_weights": "dynamic_route_weighting_on",
    "rerank": "final_reranker_mmr",
}
_BASELINE_VARIANT = "fixed_route_baseline"
_DYNAMIC_INTENTS = frozenset({"", "fixed", "default", "strategy"})


@dataclass(frozen=True, slots=True)
class GateVariantRun:
    """单个因子的执行证据与报告。"""

    factor: str
    variant: str
    status: str
    reason_code: str
    report: Any | None
    target_calls: int | None
    target_applied: bool | None

    def to_evidence(self) -> VariantEvidence:
        """转换为效果门输入。"""

        return VariantEvidence(
            factor=self.factor,
            variant=self.variant,
            report=self.report,
            status=self.status,
            reason_code=self.reason_code,
            target_calls=self.target_calls,
            target_applied=self.target_applied,
        )


@dataclass(frozen=True, slots=True)
class GateRunResult:
    """baseline、因子证据与门禁报告。"""

    baseline: GateVariantRun
    variants: dict[str, GateVariantRun]
    gate_report: dict[str, Any]


async def run_effect_gate(
    engine: Any,
    cases: Sequence[EvaluationCase],
    *,
    decision: Any,
    select_candidates: Callable[..., Any],
    source_eligible: Callable[[Mapping[str, Any]], bool] | None = None,
    k: int = 5,
    policy: EffectGatePolicy | None = None,
    manifest: Mapping[str, Any] | None = None,
    privacy_canary: Mapping[str, Any] | None = None,
) -> GateRunResult:
    """执行固定 baseline 与四个单因子变体，并返回门禁报告。"""

    controller = RetrievalAblationController(engine)
    baseline_prepared = controller.prepare(_BASELINE_VARIANT, gate=True)
    if not baseline_prepared.available or baseline_prepared.engine is None:
        raise ValueError(f"baseline_unavailable:{baseline_prepared.reason_code}")
    baseline_report = await _run_variant(
        baseline_prepared.engine,
        cases,
        k=k,
        decision=decision,
        select_candidates=select_candidates,
        source_eligible=source_eligible,
    )
    baseline_calls = _graph_calls(baseline_prepared.engine)
    baseline = GateVariantRun(
        factor="baseline",
        variant=_BASELINE_VARIANT,
        status="completed",
        reason_code="available",
        report=baseline_report,
        target_calls=baseline_calls,
        target_applied=True,
    )

    variants: dict[str, GateVariantRun] = {}
    for factor, variant_name in FACTOR_VARIANTS.items():
        prepared = controller.prepare(variant_name, gate=True)
        if not prepared.available or prepared.engine is None:
            variants[factor] = GateVariantRun(
                factor=factor,
                variant=variant_name,
                status="skipped",
                reason_code=prepared.reason_code,
                report=None,
                target_calls=None,
                target_applied=False,
            )
            continue
        report = await _run_variant(
            prepared.engine,
            cases,
            k=k,
            decision=decision,
            select_candidates=select_candidates,
            source_eligible=source_eligible,
        )
        target_calls = _target_calls(factor, prepared, report, baseline_calls)
        reason_code = _variant_reason(factor, prepared, target_calls, baseline_calls)
        variants[factor] = GateVariantRun(
            factor=factor,
            variant=variant_name,
            status="completed" if reason_code == "available" else "skipped",
            reason_code=reason_code,
            report=report,
            target_calls=target_calls,
            target_applied=_target_applied(factor, target_calls, baseline_calls),
        )

    gate_report = EffectGate().evaluate(
        baseline=baseline.to_evidence(),
        variants={factor: run.to_evidence() for factor, run in variants.items()},
        policy=policy,
        manifest=manifest,
        privacy_canary=privacy_canary,
    )
    return GateRunResult(
        baseline=baseline,
        variants=variants,
        gate_report=gate_report,
    )


async def _run_variant(
    engine: Any,
    cases: Sequence[EvaluationCase],
    *,
    k: int,
    decision: Any,
    select_candidates: Callable[..., Any],
    source_eligible: Callable[[Mapping[str, Any]], bool] | None,
) -> Any:
    observer = InjectionAblationObserver(
        decision=decision,
        select_candidates=select_candidates,
        source_eligible=source_eligible,
    )
    return await evaluate_cases(
        cases,
        make_memory_engine_retriever(engine),
        k=k,
        case_observer=observer.observe,
    )


def _graph_calls(engine: Any) -> int:
    graph = getattr(engine, "graph_retriever", None)
    return int(getattr(graph, "call_count", 0) or 0)


def _target_calls(
    factor: str,
    prepared: Any,
    report: Any,
    baseline_calls: int,
) -> int | None:
    if factor == "graph_route":
        return _graph_calls(prepared.engine)
    if factor == "dynamic_weights":
        return sum(1 for case in getattr(report, "cases", ()) if _dynamic_intent(case))
    probe = getattr(prepared, "execution_probe", None)
    if probe is None:
        return None
    return int(getattr(probe, "calls", getattr(probe, "search_count", 0)) or 0)


def _variant_reason(
    factor: str,
    prepared: Any,
    target_calls: int | None,
    baseline_calls: int,
) -> str:
    if target_calls is None:
        return "execution_probe_missing"
    if factor == "graph_route":
        if target_calls != 0:
            return "variant_not_exercised"
        return "available" if baseline_calls > 0 else "baseline_component_not_exercised"
    if target_calls <= 0:
        return "variant_not_exercised"
    return prepared.execution_reason_code()


def _target_applied(
    factor: str,
    target_calls: int | None,
    baseline_calls: int,
) -> bool:
    if target_calls is None:
        return False
    if factor == "graph_route":
        return target_calls == 0 and baseline_calls > 0
    return target_calls > 0


def _dynamic_intent(case: Any) -> bool:
    injection = getattr(case, "injection", None)
    if not isinstance(injection, Mapping):
        return False
    signals = injection.get("route_signals")
    if not isinstance(signals, Mapping):
        return False
    return str(signals.get("query_intent") or "").strip().casefold() not in (
        _DYNAMIC_INTENTS
    )
