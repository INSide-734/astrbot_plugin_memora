#!/usr/bin/env python3
"""Deterministic benchmark for adaptive injection routing and execution."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from astrbot.api.provider import ProviderRequest

from core.injection.executor import InjectionExecutionContext, InjectionExecutor
from core.injection.models import (
    DeliveryMode,
    InjectionOutcome,
    PresetName,
    RequestSignals,
    RoutingMode,
)
from core.injection.presets import PRESETS
from core.injection.router import InjectionRoutingConfig, InjectionStrategyRouter
from core.utils.injection_adapter import InjectionAdapter


@dataclass(frozen=True, slots=True)
class RoutingCase:
    expected: PresetName
    signals: RequestSignals


class CountingProvider:
    """Provider spy whose inference boundaries count actual LLM calls."""

    def __init__(self) -> None:
        self.inference_calls = 0
        self.provider_config = {"type": "openai_chat_completion"}

    def get_model(self) -> str:
        return "benchmark-model"

    async def text_chat(self, *_args: Any, **_kwargs: Any) -> str:
        self.inference_calls += 1
        return ""

    async def completion(self, *_args: Any, **_kwargs: Any) -> str:
        self.inference_calls += 1
        return ""


PROFILES = {
    name.value: {
        "memory_budget_chars": preset.memory_budget_chars,
        "memory_max_chars": preset.memory_max_chars,
        "metadata_max_chars": preset.metadata_max_chars,
        "max_memories": preset.max_memories,
    }
    for name, preset in PRESETS.items()
}

_CANDIDATES = [
    {
        "id": f"benchmark-{index}",
        "content": content,
        "score": 0.9,
        "metadata": {
            "importance": 1.0,
            "intent_match": 1.0,
            "temporal_value": 1.0,
            "source_value": 1.0,
        },
        "useful": True,
    }
    for index, content in enumerate(
        (
            "用户早餐固定选择燕麦拿铁，并明确要求不要加入额外糖浆；这个偏好在最近三次周末对话中保持一致。",
            "用户安排长途旅行时优先选择靠窗座位，同时会提前确认安静车厢和可用电源，以便途中继续工作。",
            "用户在项目复盘中偏好先列事实再讨论判断，并希望每个行动项都注明负责人、截止时间和验证方式。",
            "用户赠送礼物时重视实用性和长期使用价值，通常避开一次性装饰品，并会保留购买凭证方便更换。",
        ),
        start=1,
    )
]
_PAYLOAD_RUNS = 20

_ROUTING_CASES = (
    RoutingCase(PresetName.QUALITY, RequestSignals(query_intent="temporal", explicit_history_request=True, context_headroom_chars=2400, candidate_count=2, top_confidence=.9)),
    RoutingCase(PresetName.LOW_COST, RequestSignals(context_headroom_chars=1199, candidate_count=2, top_confidence=.9)),
    RoutingCase(PresetName.BALANCED, RequestSignals(candidate_count=2, top_confidence=.9)),
)


def percentile_95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def _request() -> ProviderRequest:
    return ProviderRequest(
        prompt="benchmark",
        extra_user_content_parts=[],
        contexts=[],
        system_prompt="",
    )


async def _profile_metrics(
    profile_name: PresetName,
) -> tuple[dict[str, float | int], dict[str, float | int | str | bool | None]]:
    router = InjectionStrategyRouter()
    provider = CountingProvider()
    adapter = InjectionAdapter()
    signals = RequestSignals(
        tools_supported=True,
        memory_tool_available=True,
        candidate_count=len(_CANDIDATES),
        top_confidence=.94,
        score_gap=.11,
        estimated_payload_chars=sum(len(item["content"]) for item in _CANDIDATES),
    )
    config = InjectionRoutingConfig(
        mode=RoutingMode.MANUAL,
        manual_preset=profile_name,
        delivery_override=DeliveryMode.USER_MESSAGE_BEFORE,
    )
    started = time.perf_counter()
    decision = router.route_final(config, signals)
    latencies = [(time.perf_counter() - started) * 1000]
    manual_correct = int(
        decision.configured_preset is profile_name
        and decision.recommended_preset is profile_name
        and decision.resolved_preset is profile_name
    )
    auto_correct = 0
    hybrid_correct = 0
    hybrid_config = InjectionRoutingConfig(
        mode=RoutingMode.HYBRID,
        hybrid_base=PresetName.BALANCED,
        hybrid_min=PresetName.BALANCED,
        hybrid_max=PresetName.BALANCED,
    )

    for case in _ROUTING_CASES:
        started = time.perf_counter()
        routed = router.route_final(InjectionRoutingConfig(mode=RoutingMode.AUTO), case.signals)
        latencies.append((time.perf_counter() - started) * 1000)
        auto_correct += int(routed.resolved_preset is case.expected)
        started = time.perf_counter()
        hybrid = router.route_final(hybrid_config, case.signals)
        latencies.append((time.perf_counter() - started) * 1000)
        hybrid_correct += int(hybrid.resolved_preset is PresetName.BALANCED)

    preflight = router.route_preflight(
        InjectionRoutingConfig(mode=RoutingMode.MANUAL, manual_preset=PresetName.TOOL_FIRST),
        signals,
    )
    passive_rate = float(not preflight.skip_passive_recall)

    context = InjectionExecutionContext(
        query="benchmark",
        memories=list(_CANDIDATES),
        cognitive_context="",
        prospective_context="",
        cognitive_budget_chars=0,
        prospective_budget_chars=0,
        provider=provider,
    )
    executor = InjectionExecutor(adapter)
    results = [
        await executor.execute(_request(), decision, context)
        for _ in range(_PAYLOAD_RUNS)
    ]
    result = results[-1]
    payload_chars_p95 = percentile_95(
        [float(item.actual_payload_chars) for item in results]
    )
    effective_budget = result.effective_budget_chars
    overflow = max(0, result.actual_payload_chars - effective_budget)
    content_chars = sum(len(item["content"]) for item in _CANDIDATES[: result.selected_count])
    useful_chars = sum(
        len(item["content"])
        for item in _CANDIDATES[: result.selected_count]
        if item["useful"]
    )
    selected_content = [item["content"] for item in _CANDIDATES[: result.selected_count]]
    redundant = len(selected_content) - len(set(selected_content))
    expected_hit = profile_name is not PresetName.TOOL_FIRST
    routing_case_count = len(_ROUTING_CASES)
    total_correct = manual_correct + auto_correct + hybrid_correct
    total_cases = 1 + routing_case_count * 2

    metrics: dict[str, float | int] = {
        "PresetSelectionAccuracy": round(total_correct / total_cases, 6),
        "ManualRoutingAccuracy": float(manual_correct),
        "AutoRoutingAccuracy": round(auto_correct / routing_case_count, 6),
        "HybridRoutingAccuracy": round(hybrid_correct / routing_case_count, 6),
        "InjectionHit@Budget": float(
            (result.actual_payload_chars > 0) == expected_hit
        ),
        "UsefulCharsRatio": (
            round(useful_chars / content_chars, 6) if content_chars else 0.0
        ),
        "RedundancyRate": (
            round(redundant / result.selected_count, 6)
            if result.selected_count
            else 0.0
        ),
        "BudgetOverflowRate": (
            round(overflow / effective_budget, 6) if effective_budget else 0.0
        ),
        "ToolFirstPassiveRecallRate": passive_rate,
        "StrategyDecisionLatency": round(percentile_95(latencies), 6),
        "OrdinaryMemoryCharsP95": round(payload_chars_p95, 6),
        "ExtraLLMCalls": provider.inference_calls,
    }
    diagnostics: dict[str, float | int | str | bool | None] = {
        "execution_outcome": result.outcome.value,
        "error_code": result.error_code,
        "actual_payload_chars": result.actual_payload_chars,
        "effective_budget_chars": effective_budget,
        "manual_tool_first_skip_passive_recall": preflight.skip_passive_recall,
        "inference_spy_count": provider.inference_calls,
    }
    return metrics, diagnostics


def run_benchmark(profile_name: str) -> dict[str, Any]:
    preset = PresetName(profile_name)
    metrics, diagnostics = asyncio.run(_profile_metrics(preset))
    expected_outcome = (
        InjectionOutcome.EMPTY.value
        if preset is PresetName.TOOL_FIRST
        else InjectionOutcome.INJECTED.value
    )
    passed = (
        metrics["PresetSelectionAccuracy"] == 1.0
        and metrics["ManualRoutingAccuracy"] == 1.0
        and metrics["AutoRoutingAccuracy"] == 1.0
        and metrics["HybridRoutingAccuracy"] == 1.0
        and metrics["InjectionHit@Budget"] == 1.0
        and metrics["BudgetOverflowRate"] == 0.0
        and metrics["ToolFirstPassiveRecallRate"] == 0.0
        and metrics["ExtraLLMCalls"] == 0
        and metrics["StrategyDecisionLatency"] < 10.0
        and diagnostics["execution_outcome"] == expected_outcome
        and diagnostics["error_code"] is None
        and diagnostics["actual_payload_chars"]
        <= diagnostics["effective_budget_chars"]
    )
    report = {
        "profile": profile_name,
        "config": PROFILES[profile_name],
        "metrics": metrics,
        "diagnostics": diagnostics,
        "summary": {"all_checks_passed": passed},
    }
    print(f"\nMemora Recall Cost Benchmark: {profile_name}")
    for name, value in metrics.items():
        print(f"  {name}: {value}")
    for name, value in diagnostics.items():
        print(f"  {name}: {value}")
    print("  Result: " + ("ALL PASSED" if passed else "FAILED"))
    return report


def validate_cross_profile_metrics(reports: dict[str, dict[str, Any]]) -> bool:
    low_cost = float(reports[PresetName.LOW_COST.value]["metrics"]["OrdinaryMemoryCharsP95"])
    balanced = float(reports[PresetName.BALANCED.value]["metrics"]["OrdinaryMemoryCharsP95"])
    reduction = 1.0 - (low_cost / balanced) if balanced > 0 else 0.0
    passed = balanced > 0 and reduction >= 0.30
    print(f"\n  LowCostPayloadReduction: {reduction:.6f} (required >= 0.300000)")
    print("  Cross-profile result: " + ("ALL PASSED" if passed else "FAILED"))
    return passed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=list(PROFILES), default="balanced")
    parser.add_argument("--output")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    reports: Any
    if args.all:
        reports = {name: run_benchmark(name) for name in PROFILES}
        profile_checks_passed = all(
            item["summary"]["all_checks_passed"] for item in reports.values()
        )
        cross_profile_checks_passed = validate_cross_profile_metrics(reports)
        passed = profile_checks_passed and cross_profile_checks_passed
    else:
        reports = run_benchmark(args.profile)
        passed = reports["summary"]["all_checks_passed"]
    if args.output:
        Path(args.output).write_text(json.dumps(reports, indent=2, ensure_ascii=False), encoding="utf-8")
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
