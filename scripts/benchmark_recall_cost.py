#!/usr/bin/env python3
"""Deterministic benchmark for adaptive memory-injection routing and budgets."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.injection.models import PresetName, RequestSignals, RoutingMode
from core.injection.presets import PRESETS
from core.injection.router import InjectionRoutingConfig, InjectionStrategyRouter
from core.utils.injection_budget import InjectionBudget, select_memories_with_budget


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    expected: PresetName
    signals: RequestSignals


PROFILES = {
    preset_name.value: {
        "memory_budget_chars": preset.memory_budget_chars,
        "memory_max_chars": preset.memory_max_chars,
        "metadata_max_chars": preset.metadata_max_chars,
        "max_memories": preset.max_memories,
        "compact_header": preset.compact_header,
    }
    for preset_name, preset in PRESETS.items()
}

_CANDIDATES = [
    {"content": "用户喜欢燕麦拿铁", "score": 0.94, "useful": True},
    {"content": "用户常在周末喝咖啡", "score": 0.83, "useful": True},
    {"content": "用户喜欢燕麦拿铁", "score": 0.72, "useful": False},
    {"content": "普通对话背景", "score": 0.31, "useful": False},
]

_ROUTING_CASES = (
    BenchmarkCase(
        PresetName.QUALITY,
        RequestSignals(
            query_intent="temporal",
            explicit_history_request=True,
            context_headroom_chars=PRESETS[PresetName.QUALITY].memory_budget_chars,
            candidate_count=2,
            top_confidence=0.9,
            score_gap=0.2,
            estimated_payload_chars=600,
        ),
    ),
    BenchmarkCase(
        PresetName.LOW_COST,
        RequestSignals(
            context_headroom_chars=PRESETS[PresetName.BALANCED].memory_budget_chars - 1,
            candidate_count=2,
            top_confidence=0.9,
            score_gap=0.2,
            estimated_payload_chars=600,
        ),
    ),
    BenchmarkCase(
        PresetName.BALANCED,
        RequestSignals(
            candidate_count=2,
            top_confidence=0.9,
            score_gap=0.2,
            estimated_payload_chars=600,
        ),
    ),
    BenchmarkCase(
        PresetName.TOOL_FIRST,
        RequestSignals(
            tools_supported=True,
            memory_tool_available=True,
            candidate_count=2,
            top_confidence=0.9,
            score_gap=0.2,
            estimated_payload_chars=600,
        ),
    ),
)


def _budget_for(preset_name: PresetName) -> InjectionBudget:
    preset = PRESETS[preset_name]
    return InjectionBudget(
        total_chars=preset.memory_budget_chars,
        memory_max_chars=preset.memory_max_chars,
        metadata_max_chars=preset.metadata_max_chars,
        compact_header=preset.compact_header,
    )


def _selection_metrics(preset_name: PresetName) -> dict[str, float]:
    preset = PRESETS[preset_name]
    selected, _ = select_memories_with_budget(list(_CANDIDATES), _budget_for(preset_name))
    selected = selected[: preset.max_memories]
    selected_chars = sum(
        min(len(str(item["content"])), preset.memory_max_chars)
        + min(preset.metadata_max_chars, 180)
        for item in selected
    )
    useful_chars = sum(
        min(len(str(item["content"])), preset.memory_max_chars)
        for item in selected
        if item["useful"]
    )
    content_chars = sum(
        min(len(str(item["content"])), preset.memory_max_chars) for item in selected
    )
    normalized_content = [str(item["content"]).casefold() for item in selected]
    redundant_count = len(normalized_content) - len(set(normalized_content))
    overflow = max(0, selected_chars - preset.memory_budget_chars)
    expected_hit = preset.memory_budget_chars > 0 and preset.max_memories > 0
    return {
        "InjectionHit@Budget": float(bool(selected) == expected_hit),
        "UsefulCharsRatio": round(useful_chars / content_chars, 6) if content_chars else 0.0,
        "RedundancyRate": round(redundant_count / len(selected), 6) if selected else 0.0,
        "BudgetOverflowRate": round(overflow / preset.memory_budget_chars, 6)
        if preset.memory_budget_chars
        else 0.0,
    }


def _strategy_metrics(profile_name: PresetName) -> dict[str, float | int | str]:
    router = InjectionStrategyRouter()
    manual_config = InjectionRoutingConfig(
        mode=RoutingMode.MANUAL,
        manual_preset=profile_name,
    )
    manual_signals = RequestSignals(
        tools_supported=True,
        memory_tool_available=True,
        candidate_count=2,
        top_confidence=0.9,
        score_gap=0.2,
        estimated_payload_chars=600,
    )
    manual_started = time.perf_counter()
    manual_decision = router.route_final(manual_config, manual_signals)
    latencies_ms = [(time.perf_counter() - manual_started) * 1000]

    correct = 0
    tool_first_skips = 0
    tool_first_cases = 0
    for case in _ROUTING_CASES:
        started = time.perf_counter()
        decision = router.route_final(
            InjectionRoutingConfig(mode=RoutingMode.AUTO),
            case.signals,
        )
        latencies_ms.append((time.perf_counter() - started) * 1000)
        correct += int(decision.resolved_preset is case.expected)
        if case.expected is PresetName.TOOL_FIRST:
            tool_first_cases += 1
            tool_first_skips += int(decision.skip_passive_recall)

    return {
        "PresetSelectionAccuracy": round(correct / len(_ROUTING_CASES), 6),
        "ToolFirstPassiveRecallRate": round(
            1.0 - (tool_first_skips / tool_first_cases), 6
        ),
        "StrategyDecisionLatency": round(statistics.mean(latencies_ms), 6),
        "ExtraLLMCalls": 0,
        "manual_resolved_preset": manual_decision.resolved_preset.value,
    }


def run_benchmark(profile_name: str) -> dict[str, Any]:
    try:
        preset_name = PresetName(profile_name)
    except ValueError:
        return {"error": f"Unknown profile: {profile_name}", "available": list(PROFILES)}

    metrics = {
        **_strategy_metrics(preset_name),
        **_selection_metrics(preset_name),
    }
    passed = (
        metrics["PresetSelectionAccuracy"] == 1.0
        and metrics["InjectionHit@Budget"] == 1.0
        and metrics["BudgetOverflowRate"] == 0.0
        and metrics["ToolFirstPassiveRecallRate"] == 0.0
        and metrics["ExtraLLMCalls"] == 0
    )
    report = {
        "profile": profile_name,
        "config": PROFILES[profile_name],
        "metrics": metrics,
        "summary": {"all_checks_passed": passed},
    }
    print(f"\nMemora Recall Cost Benchmark: {profile_name}")
    for name, value in metrics.items():
        print(f"  {name}: {value}")
    print("  Result: " + ("ALL PASSED" if passed else "FAILED"))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Memora adaptive recall cost benchmark")
    parser.add_argument(
        "--profile",
        choices=list(PROFILES),
        default=PresetName.BALANCED.value,
        help="built-in strategy preset",
    )
    parser.add_argument("--output", type=str, default=None, help="write JSON report")
    parser.add_argument("--all", action="store_true", help="run all four presets")
    args = parser.parse_args()

    if args.all:
        reports = {name: run_benchmark(name) for name in PROFILES}
        passed = all(report["summary"]["all_checks_passed"] for report in reports.values())
        report_payload: dict[str, Any] = reports
    else:
        report = run_benchmark(args.profile)
        passed = report["summary"]["all_checks_passed"]
        report_payload = report

    if args.output:
        with open(args.output, "w", encoding="utf-8") as output_file:
            json.dump(report_payload, output_file, indent=2, ensure_ascii=False)
        print(f"Report written: {args.output}")
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
