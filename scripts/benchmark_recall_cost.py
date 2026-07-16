#!/usr/bin/env python3
"""Deterministic benchmark for adaptive injection routing and execution."""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
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
    {"content": "用户喜欢燕麦拿铁", "score": 0.94, "metadata": {}, "useful": True},
    {"content": "用户常在周末喝咖啡", "score": 0.83, "metadata": {}, "useful": True},
    {"content": "用户喜欢燕麦拿铁", "score": 0.72, "metadata": {}, "useful": False},
    {"content": "普通对话背景", "score": 0.31, "metadata": {}, "useful": False},
]

_ROUTING_CASES = (
    RoutingCase(PresetName.QUALITY, RequestSignals(query_intent="temporal", explicit_history_request=True, context_headroom_chars=2400, candidate_count=2, top_confidence=.9)),
    RoutingCase(PresetName.LOW_COST, RequestSignals(context_headroom_chars=1199, candidate_count=2, top_confidence=.9)),
    RoutingCase(PresetName.BALANCED, RequestSignals(candidate_count=2, top_confidence=.9)),
)


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
    correct = int(
        decision.configured_preset is profile_name
        and decision.recommended_preset is profile_name
        and decision.resolved_preset is profile_name
    )
    total = 1

    for case in _ROUTING_CASES:
        started = time.perf_counter()
        routed = router.route_final(InjectionRoutingConfig(mode=RoutingMode.AUTO), case.signals)
        latencies.append((time.perf_counter() - started) * 1000)
        correct += int(routed.resolved_preset is case.expected)
        total += 1

    preflight = router.route_preflight(
        InjectionRoutingConfig(mode=RoutingMode.MANUAL, manual_preset=PresetName.TOOL_FIRST),
        signals,
    )
    passive_rate = float(not preflight.skip_passive_recall)

    req = _request()
    context = InjectionExecutionContext(
        query="benchmark",
        memories=list(_CANDIDATES),
        cognitive_context="",
        prospective_context="",
        cognitive_budget_chars=0,
        prospective_budget_chars=0,
        provider=provider,
    )
    result = await InjectionExecutor(adapter).execute(req, decision, context)
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

    metrics: dict[str, float | int] = {
        "PresetSelectionAccuracy": round(correct / total, 6),
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
        "StrategyDecisionLatency": round(statistics.mean(latencies), 6),
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
        and metrics["InjectionHit@Budget"] == 1.0
        and metrics["BudgetOverflowRate"] == 0.0
        and metrics["ToolFirstPassiveRecallRate"] == 0.0
        and metrics["ExtraLLMCalls"] == 0
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=list(PROFILES), default="balanced")
    parser.add_argument("--output")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    reports: Any
    if args.all:
        reports = {name: run_benchmark(name) for name in PROFILES}
        passed = all(item["summary"]["all_checks_passed"] for item in reports.values())
    else:
        reports = run_benchmark(args.profile)
        passed = reports["summary"]["all_checks_passed"]
    if args.output:
        Path(args.output).write_text(json.dumps(reports, indent=2, ensure_ascii=False), encoding="utf-8")
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
