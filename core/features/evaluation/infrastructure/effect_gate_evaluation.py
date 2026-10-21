"""Page API 侧的效果门执行、绑定与安全报告组装。"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ...injection.application.router import (
    InjectionRoutingConfig,
    InjectionStrategyRouter,
)
from ...injection.application.selection import (
    metadata_has_user_evidence,
    select_candidates,
)
from ...injection.domain.models import (
    PresetName,
    RequestSignals,
    RoutingMode,
)
from ..application.effect_gate_runner import run_effect_gate
from ..application.retrieval_quality import EvaluationCase
from ..domain.effect_gate import EffectGatePolicy
from .dataset_selection import select_datasets
from .effect_gate_payloads import effect_gate_summary, safe_effect_gate_payload
from .evaluation_manifest import build_effect_gate_manifest
from .evaluation_service import EvaluationService
from .report_summary import report_cases, report_summary


@dataclass(frozen=True, slots=True)
class EffectGateInjectionPort:
    """注入观察端口；由服务端固定构造。"""

    decision: Any
    select_candidates: Callable[..., Any]
    source_eligible: Callable[[Mapping[str, Any]], bool]


class EffectGateEvaluationError(Exception):
    """稳定的效果门失败；错误码可安全返回给客户端。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def build_effect_gate_injection_port() -> EffectGateInjectionPort:
    """构造固定的 manual/balanced 注入观察端口。"""

    decision = InjectionStrategyRouter().route_final(
        InjectionRoutingConfig(
            mode=RoutingMode.MANUAL,
            manual_preset=PresetName.BALANCED,
        ),
        RequestSignals(),
    )
    return EffectGateInjectionPort(
        decision=decision,
        select_candidates=select_candidates,
        source_eligible=metadata_has_user_evidence,
    )


def _privacy_canary(
    cases: Sequence[EvaluationCase],
    gate_report: Mapping[str, Any],
) -> dict[str, Any]:
    """检查 query/长 case_id 是否出现在报告中；短 ID 只做精确 JSON 字符串匹配。"""

    report_text = json.dumps(
        gate_report,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    checked = [
        str(value)
        for case in cases
        for value in (
            getattr(case, "case_id", None),
            getattr(case, "query", None),
        )
        if value is not None
    ]
    violations: list[str] = []
    for value in checked:
        if not value:
            continue
        if len(value) >= 8:
            if value in report_text:
                violations.append(value)
        elif json.dumps(value, ensure_ascii=False) in report_text:
            violations.append(value)
    return {"checked_values": len(checked), "violations": violations}


class EffectGateEvaluationService:
    """在评测服务之上执行固定效果门并生成安全报告。"""

    def __init__(
        self,
        evaluation: EvaluationService,
        *,
        runner: Callable[..., Any] = run_effect_gate,
    ) -> None:
        self.evaluation = evaluation
        self._runner = runner

    async def run(
        self,
        *,
        datasets: Sequence[str] | None,
        k: int,
        save_report: bool,
        injection_port: EffectGateInjectionPort,
        fixture_secret: bytes,
        runtime_datasets: Mapping[str, Sequence[EvaluationCase]] | None = None,
        policy: EffectGatePolicy | None = None,
    ) -> dict[str, Any]:
        """执行固定 baseline/变体并返回脱敏报告。"""

        engine = self.evaluation.engine
        if engine is None:
            raise EffectGateEvaluationError(
                "effect_gate_engine_unavailable",
                "MemoryEngine unavailable",
            )
        if not fixture_secret:
            raise EffectGateEvaluationError(
                "effect_gate_secret_unavailable",
                "Effect-gate fixture secret unavailable",
            )
        selected = select_datasets(
            datasets,
            fixture_dir=self.evaluation.fixture_dir,
            include_experimental=self.evaluation.include_experimental_datasets,
            runtime_datasets=runtime_datasets,
        )
        cases = [case for name in selected for case in selected[name]]
        if not cases:
            raise EffectGateEvaluationError(
                "effect_gate_empty_dataset",
                "No evaluation cases selected",
            )
        db_snapshot_hash = await self.evaluation._database_snapshot_hash(engine)
        if db_snapshot_hash is None:
            raise EffectGateEvaluationError(
                "effect_gate_data_unstable",
                "Canonical database snapshot unavailable",
            )
        active_policy = policy or EffectGatePolicy()
        manifest = build_effect_gate_manifest(
            engine,
            cases,
            k=k,
            policy=active_policy,
            fixture_secret=fixture_secret,
            db_snapshot_hash=db_snapshot_hash,
        )
        try:
            result = await self._runner(
                engine,
                cases,
                decision=injection_port.decision,
                select_candidates=injection_port.select_candidates,
                source_eligible=injection_port.source_eligible,
                k=k,
                policy=active_policy,
                manifest=manifest,
                privacy_canary={
                    "checked_values": len(cases) * 2,
                    "violations": [],
                },
            )
        except Exception as error:
            raise EffectGateEvaluationError(
                "effect_gate_execution_failed",
                "Effect gate execution failed",
            ) from error

        baseline_report = result.baseline.report
        if baseline_report is None:
            raise EffectGateEvaluationError(
                "effect_gate_baseline_failed",
                "Effect-gate baseline report unavailable",
            )
        gate_report = dict(result.gate_report)
        canary = _privacy_canary(cases, gate_report)
        if canary["violations"]:
            raise EffectGateEvaluationError(
                "effect_gate_privacy_failed",
                "Effect-gate privacy validation failed",
            )
        gate_report["privacy_canary"] = canary
        safe_gate = safe_effect_gate_payload(gate_report)
        if safe_gate is None:
            raise EffectGateEvaluationError(
                "effect_gate_invalid_payload",
                "Effect-gate report validation failed",
            )

        summary = report_summary(baseline_report)
        summary["effect_gate_summary"] = effect_gate_summary(safe_gate)
        variants: dict[str, Any] = {}
        for factor, evidence in result.variants.items():
            variants[evidence.variant] = {
                "name": evidence.variant,
                "factor": factor,
                "status": evidence.status,
                "reason_code": evidence.reason_code,
                "execution": {
                    "target_calls": evidence.target_calls,
                    "target_applied": evidence.target_applied,
                },
                "summary": (
                    report_summary(evidence.report)
                    if evidence.report is not None
                    else None
                ),
            }
        payload: dict[str, Any] = {
            "status": "ok",
            "schema_version": safe_gate["schema_version"],
            "baseline": safe_gate["baseline"],
            "datasets": sorted(selected),
            "summary": summary,
            "variants": variants,
            "cases": report_cases(baseline_report),
            "effect_gate": safe_gate,
            "manifest": safe_gate["manifest"],
            "report_id": None,
            "saved": False,
        }
        if save_report and self.evaluation.store is not None:
            payload["report_id"] = await self.evaluation.store.save_report(payload)
            payload["saved"] = True
        return payload


__all__ = [
    "EffectGateEvaluationError",
    "EffectGateEvaluationService",
    "EffectGateInjectionPort",
    "build_effect_gate_injection_port",
]
