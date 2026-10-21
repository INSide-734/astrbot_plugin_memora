"""Page API 效果门参数、固定绑定与错误映射测试。"""

from __future__ import annotations

from typing import Any

import pytest

import core.platform.transport.page_api.evaluation_api as evaluation_api
from core.features.evaluation.infrastructure.effect_gate_evaluation import (
    EffectGateEvaluationError,
)
from core.platform.transport.page_api.page_api import PluginPageApi
from tests.effect_gate_test_support import gate_payload
from tests.test_api_evaluation import (
    FakeEngine,
    _plugin_with_engine,
    _write_production_dataset,
)


class FakeGateService:
    """记录 API 传给 gate service 的参数，不执行真实变体。"""

    calls: list[dict[str, Any]] = []

    def __init__(self, evaluation) -> None:
        self.evaluation = evaluation

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        gate = gate_payload()
        return {
            "status": "ok",
            "baseline": "fixed_route_baseline",
            "datasets": list(kwargs["datasets"]),
            "summary": {"total_cases": 1, "recall_at_k": 1.0},
            "variants": {},
            "cases": [],
            "effect_gate": gate,
            "manifest": gate["manifest"],
            "report_id": None,
            "saved": False,
        }


def _api(tmp_path, monkeypatch) -> PluginPageApi:
    _write_production_dataset(tmp_path)
    monkeypatch.setattr(
        evaluation_api,
        "EffectGateEvaluationService",
        FakeGateService,
    )
    FakeGateService.calls = []
    return PluginPageApi(_plugin_with_engine(FakeEngine(), data_dir=tmp_path))


@pytest.mark.asyncio
async def test_gate_api_runs_with_server_bound_selection(tmp_path, monkeypatch) -> None:
    """gate 请求接受空对象，k 被钳制，响应含脱敏 gate 与 manifest。"""

    api = _api(tmp_path, monkeypatch)
    result = await api.run_evaluation_payload(
        {
            "datasets": ["private_basic"],
            "k": 99,
            "save_report": True,
            "effect_gate": {},
        }
    )

    assert result["status"] == "ok"
    data = result["data"]
    assert data["baseline"] == "fixed_route_baseline"
    assert data["datasets"] == ["private_basic"]
    assert data["effect_gate"]["schema_version"] == "effect-gate-v1"
    assert data["manifest"]["fixture_hmac"] == "a" * 64
    call = FakeGateService.calls[-1]
    assert call["datasets"] == ["private_basic"]
    assert call["k"] == 20
    assert call["save_report"] is True
    assert call["runtime_datasets"] == {}
    assert len(call["fixture_secret"]) == 32
    assert callable(call["injection_port"].select_candidates)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "baseline",
        "variants",
        "manifest",
        "injection",
        "gate_policy",
        "policy",
        "fixture_hmac",
        "db_snapshot_hash",
        "seed",
        "context_key",
    ],
)
async def test_gate_api_rejects_server_binding_overrides(
    tmp_path, monkeypatch, field: str
) -> None:
    """客户端覆盖字段必须返回 invalid_request，且不执行 gate。"""

    api = _api(tmp_path, monkeypatch)
    payload = {
        "datasets": ["private_basic"],
        "effect_gate": {},
        field: "client-value" if field not in {"variants", "seed"} else [1],
    }

    result = await api.run_evaluation_payload(payload)

    assert result["status"] == "error"
    assert result["code"] == "invalid_request"
    assert FakeGateService.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("effect_gate", ["yes", {"policy": "x"}, 1, False])
async def test_gate_api_rejects_invalid_effect_gate_shape(
    tmp_path, monkeypatch, effect_gate
) -> None:
    """effect_gate 只接受 true 或空对象。"""

    api = _api(tmp_path, monkeypatch)
    result = await api.run_evaluation_payload(
        {"datasets": ["private_basic"], "effect_gate": effect_gate}
    )

    assert result["status"] == "error"
    assert result["code"] == "invalid_request"
    assert FakeGateService.calls == []


@pytest.mark.asyncio
async def test_gate_api_rejects_quality_loop_combination(tmp_path, monkeypatch) -> None:
    """gate 与 quality_loop 不能同时请求。"""

    api = _api(tmp_path, monkeypatch)
    result = await api.run_evaluation_payload(
        {
            "datasets": ["private_basic"],
            "effect_gate": {},
            "quality_loop": {},
        }
    )

    assert result["status"] == "error"
    assert result["code"] == "invalid_request"
    assert FakeGateService.calls == []


@pytest.mark.asyncio
async def test_gate_api_maps_secret_and_service_errors(tmp_path, monkeypatch) -> None:
    """密钥缺失与 gate service 错误必须映射为稳定错误码。"""

    api = _api(tmp_path, monkeypatch)

    def unavailable(_path):
        raise RuntimeError("missing")

    monkeypatch.setattr(
        evaluation_api,
        "load_or_create_quality_loop_secret",
        unavailable,
    )
    missing_secret = await api.run_evaluation_payload(
        {"datasets": ["private_basic"], "effect_gate": {}}
    )
    assert missing_secret["code"] == "effect_gate_secret_unavailable"

    monkeypatch.setattr(
        evaluation_api,
        "load_or_create_quality_loop_secret",
        lambda _path: b"x" * 32,
    )

    async def fail(self, **kwargs):
        raise EffectGateEvaluationError(
            "effect_gate_data_unstable",
            "Canonical database snapshot unavailable",
        )

    monkeypatch.setattr(FakeGateService, "run", fail)
    failed = await api.run_evaluation_payload(
        {"datasets": ["private_basic"], "effect_gate": {}}
    )
    assert failed["status"] == "error"
    assert failed["code"] == "effect_gate_data_unstable"


@pytest.mark.asyncio
async def test_normal_api_request_still_accepts_variants(tmp_path, monkeypatch) -> None:
    """普通评测的 variants 不应被 gate 字段校验误伤。"""

    api = _api(tmp_path, monkeypatch)
    result = await api.run_evaluation_payload(
        {
            "datasets": ["private_basic"],
            "k": 5,
            "variants": ["baseline"],
            "baseline": "baseline",
        }
    )

    assert result["status"] == "ok"
    assert FakeGateService.calls == []
