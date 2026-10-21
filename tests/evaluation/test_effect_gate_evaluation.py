"""效果门 service 的固定端口、manifest 与隐私边界测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from core.features.evaluation.application.retrieval_quality import EvaluationCase
from core.features.evaluation.infrastructure.effect_gate_evaluation import (
    EffectGateEvaluationError,
    EffectGateEvaluationService,
    build_effect_gate_injection_port,
)
from core.features.evaluation.infrastructure.evaluation_service import EvaluationService
from core.features.injection.domain.models import PresetName, RoutingMode
from tests.effect_gate_test_support import evaluation_report, gate_payload

_FACTORS = {
    "atom": "atom_signal_on",
    "graph_route": "graph_route_off",
    "dynamic_weights": "dynamic_route_weighting_on",
    "rerank": "final_reranker_mmr",
}


class FakeStore:
    def __init__(self) -> None:
        self.saved: dict[str, Any] | None = None

    async def save_report(self, payload: dict[str, Any]) -> str:
        self.saved = payload
        return "report-1"


class FakeEvaluation:
    def __init__(self, *, db_hash: str | None = "b" * 64) -> None:
        self.engine = SimpleNamespace(config={})
        self.fixture_dir = "unused"
        self.include_experimental_datasets = True
        self.store = FakeStore()
        self._db_hash = db_hash
        self._secret = b"x" * 32

    async def _database_snapshot_hash(self, engine: Any) -> str | None:
        return self._db_hash


def _runner(*, leak: str | None = None):
    async def runner(engine, cases, **kwargs):
        gate = gate_payload()
        gate["manifest"] = kwargs["manifest"]
        gate["privacy_canary"] = {
            "checked_values": len(cases) * 2,
            "violations": [],
        }
        if leak is not None:
            gate["recommended_config"] = {"leak": leak}
        variants = {
            factor: SimpleNamespace(
                variant=variant,
                status="completed",
                reason_code="available",
                report=(None if factor == "rerank" else evaluation_report()),
                target_calls=1,
                target_applied=True,
            )
            for factor, variant in _FACTORS.items()
        }
        return SimpleNamespace(
            baseline=SimpleNamespace(report=evaluation_report()),
            variants=variants,
            gate_report=gate,
        )

    return runner


def _case() -> EvaluationCase:
    return EvaluationCase(
        case_id="case-1",
        query="secret query",
        relevant_doc_ids={"1"},
        metadata={"categories": ["positive"]},
    )


async def _run(
    evaluation: FakeEvaluation,
    *,
    runner=None,
    secret: bytes = b"x" * 32,
    cases: list[EvaluationCase] | None = None,
):
    service = EffectGateEvaluationService(
        cast(EvaluationService, evaluation),
        runner=runner or _runner(),
    )
    return await service.run(
        datasets=["runtime"],
        k=7,
        save_report=True,
        injection_port=build_effect_gate_injection_port(),
        fixture_secret=secret,
        runtime_datasets={"runtime": cases if cases is not None else [_case()]},
    )


@pytest.mark.asyncio
async def test_effect_gate_service_builds_bound_report_and_saves() -> None:
    """固定 manual/balanced、baseline cases 与 manifest 绑定必须进入报告。"""

    evaluation = FakeEvaluation()
    calls: dict[str, Any] = {}

    async def runner(engine, cases, **kwargs):
        calls.update(kwargs)
        calls["cases"] = cases
        return await _runner()(engine, cases, **kwargs)

    payload = await _run(evaluation, runner=runner)

    assert payload["baseline"] == "fixed_route_baseline"
    assert payload["datasets"] == ["runtime"]
    assert payload["saved"] is True
    assert payload["report_id"] == "report-1"
    assert payload["cases"][0]["case_id"] == "case-1"
    assert "query" not in payload["cases"][0]
    assert payload["summary"]["effect_gate_summary"]["factors"]["atom"]["status"] == (
        "pass_enable"
    )
    assert payload["variants"]["final_reranker_mmr"]["summary"] is None
    assert payload["variants"]["atom_signal_on"]["factor"] == "atom"
    assert payload["effect_gate"]["privacy_canary"] == {
        "checked_values": 2,
        "violations": [],
    }
    assert calls["manifest"]["db_snapshot_hash"] == "b" * 64
    assert calls["manifest"]["fixture_hmac"] != "a" * 64
    assert len(calls["manifest"]["fixture_hmac"]) == 64
    assert calls["decision"].routing_mode == RoutingMode.MANUAL
    assert calls["decision"].configured_preset == PresetName.BALANCED
    assert callable(calls["select_candidates"])
    assert callable(calls["source_eligible"])
    assert evaluation.store.saved is not None


@pytest.mark.asyncio
async def test_effect_gate_service_fails_closed_on_canary() -> None:
    """query 出现在 gate 报告中时必须拒绝且不保存。"""

    evaluation = FakeEvaluation()

    with pytest.raises(EffectGateEvaluationError) as error:
        await _run(
            evaluation,
            runner=_runner(leak="secret query"),
        )

    assert error.value.code == "effect_gate_privacy_failed"
    assert evaluation.store.saved is None


@pytest.mark.asyncio
async def test_effect_gate_service_rejects_empty_cases_and_missing_db_hash() -> None:
    """空数据集与不可用 DB snapshot 都返回稳定错误码。"""

    with pytest.raises(EffectGateEvaluationError) as empty_error:
        await _run(FakeEvaluation(), cases=[])
    assert empty_error.value.code == "effect_gate_empty_dataset"

    with pytest.raises(EffectGateEvaluationError) as db_error:
        await _run(FakeEvaluation(db_hash=None))
    assert db_error.value.code == "effect_gate_data_unstable"


@pytest.mark.asyncio
async def test_effect_gate_service_requires_fixture_secret() -> None:
    """缺少 HMAC 密钥时 fail-closed。"""

    with pytest.raises(EffectGateEvaluationError) as error:
        await _run(FakeEvaluation(), secret=b"")

    assert error.value.code == "effect_gate_secret_unavailable"
