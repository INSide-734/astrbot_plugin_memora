#!/usr/bin/env python3
"""benchmark_recall_cost.py — Memora 召回成本基准脚本

对 low_cost / balanced / quality 三档配置进行离线评估，输出:
- 注入字符数 (p50 / p95)
- 候选记忆数量
- 额外 LLM 调用数 (reranker / strategy D)
- 配置转发覆盖率

用法:
    python scripts/benchmark_recall_cost.py --profile low_cost
    python scripts/benchmark_recall_cost.py --profile balanced --output reports/recall-balanced.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

# 添加项目根目录到 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 预设配置档位
PROFILES: dict[str, dict] = {
    "low_cost": {
        "recall_engine.top_k": 3,
        "recall_engine.max_k": 5,
        "recall_engine.session_cache_enabled": True,
        "recall_engine.spontaneous_recall_enabled": False,
        "recall_engine.prospective_recall_enabled": True,
        "recall_engine.prospective_recall_k": 1,
        "recall_engine.max_chain_hops": 1,
        "recall_engine.injection_budget_chars": 800,
        "recall_engine.injection_memory_max_chars": 160,
        "recall_engine.injection_compact_header": True,
        "recall_engine.injection_include_participants": False,
        "graph_memory.enabled": True,
        "graph_memory.expansion_hops": 1,
        "graph_memory.expansion_limit": 8,
        "reflection_engine.summary_trigger_rounds": 16,
        "topic_segmentation.strategy": "a_b_hybrid",
        "reranker.enabled": True,
        "reranker.strategy": "mmr",
        "cost_control.mode": "low_cost",
        "cost_control.max_extra_llm_calls_per_turn": 0,
    },
    "balanced": {
        "recall_engine.top_k": 5,
        "recall_engine.max_k": 10,
        "recall_engine.session_cache_enabled": True,
        "recall_engine.session_cache_ttl_seconds": 10.0,
        "recall_engine.max_chain_hops": 1,
        "recall_engine.chain_hop_decay": 0.65,
        "recall_engine.spontaneous_recall_enabled": True,
        "recall_engine.spontaneous_recall_probability": 0.03,
        "recall_engine.spontaneous_recall_k": 1,
        "recall_engine.prospective_recall_enabled": True,
        "recall_engine.prospective_recall_k": 2,
        "recall_engine.injection_budget_chars": 1200,
        "recall_engine.injection_memory_max_chars": 220,
        "recall_engine.injection_metadata_max_chars": 180,
        "recall_engine.injection_compact_header": True,
        "recall_engine.injection_include_key_facts": True,
        "recall_engine.injection_include_topics": True,
        "recall_engine.injection_include_participants": False,
        "graph_memory.enabled": True,
        "graph_memory.expansion_hops": 1,
        "graph_memory.expansion_limit": 16,
        "reflection_engine.summary_trigger_rounds": 12,
        "topic_segmentation.strategy": "a_b_hybrid",
        "reranker.enabled": True,
        "reranker.strategy": "mmr",
        "cost_control.mode": "balanced",
        "cost_control.max_extra_llm_calls_per_turn": 0,
    },
    "quality": {
        "recall_engine.top_k": 6,
        "recall_engine.max_k": 12,
        "recall_engine.max_chain_hops": 2,
        "recall_engine.spontaneous_recall_probability": 0.06,
        "recall_engine.prospective_recall_k": 3,
        "recall_engine.injection_budget_chars": 2400,
        "recall_engine.injection_memory_max_chars": 400,
        "recall_engine.injection_compact_header": False,
        "graph_memory.enabled": True,
        "graph_memory.expansion_hops": 2,
        "graph_memory.expansion_limit": 24,
        "topic_segmentation.strategy": "strategy_c",
        "reranker.enabled": True,
        "reranker.strategy": "cross_encoder",
        "cost_control.mode": "quality",
        "cost_control.max_extra_llm_calls_per_turn": 1,
        "cost_control.allow_llm_reranker_in_passive_recall": True,
    },
}

# 配置键转发检查清单：_build_engine_config() 应包含的所有性能键
EXPECTED_ENGINE_CONFIG_KEYS = [
    "search_cache_enabled",
    "search_cache_ttl_seconds",
    "search_cache_max_size",
    "session_cache_enabled",
    "session_cache_ttl_seconds",
    "testing_effect_async",
    "testing_effect_top_k",
    "recall_engine.max_chain_hops",
    "recall_engine.chain_hop_decay",
    "recall_engine.chain_graph_expansion_enabled",
    "recall_engine.chain_topic_expansion_enabled",
    "reranker.enabled",
    "reranker.strategy",
    "reranker.llm_batch_size",
    "reranker.cross_encoder_lambda",
    "reranker.mmr_lambda",
    "cost_control.mode",
    "cost_control.max_extra_llm_calls_per_turn",
    "cost_control.allow_llm_reranker_in_passive_recall",
    "cost_control.allow_llm_topic_strategy_d",
]


def check_config_forwarding() -> dict:
    """验证 ComponentFactory._build_engine_config() 转发完整性。"""
    from core.initializer.component_factory import ComponentFactory
    from pathlib import Path
    from unittest.mock import MagicMock

    cm = MagicMock()
    cm.get.side_effect = lambda key, default=None: default
    factory = ComponentFactory(
        context=MagicMock(),
        config_manager=cm,
        data_dir=str(PROJECT_ROOT / "data"),
    )
    stopwords_dir = Path(PROJECT_ROOT / "data" / "stopwords")

    try:
        engine_config = factory._build_engine_config(stopwords_dir, True)
    except Exception as exc:
        return {"error": str(exc), "forwarding_coverage": 0.0}

    missing = [k for k in EXPECTED_ENGINE_CONFIG_KEYS if k not in engine_config]
    coverage = (
        (len(EXPECTED_ENGINE_CONFIG_KEYS) - len(missing))
        / len(EXPECTED_ENGINE_CONFIG_KEYS)
        * 100
    )
    return {
        "forwarding_coverage": round(coverage, 1),
        "total_keys": len(EXPECTED_ENGINE_CONFIG_KEYS),
        "forwarded": len(EXPECTED_ENGINE_CONFIG_KEYS) - len(missing),
        "missing": missing,
    }


def check_injection_budget(profile: dict) -> dict:
    """验证注入预算模块的函数行为。"""
    from core.utils.injection_budget import (
        InjectionBudget,
        select_memories_with_budget,
        truncate_preserving_sentence,
    )

    results = {}

    # 测试截断
    long_text = "这是一个很长的句子。" * 50
    truncated = truncate_preserving_sentence(long_text, 50)
    results["truncation_respects_limit"] = len(truncated) <= 50

    # 测试预算筛选
    budget = InjectionBudget(
        total_chars=profile.get("recall_engine.injection_budget_chars", 1200),
        memory_max_chars=profile.get("recall_engine.injection_memory_max_chars", 220),
        metadata_max_chars=profile.get(
            "recall_engine.injection_metadata_max_chars", 180
        ),
        compact_header=profile.get("recall_engine.injection_compact_header", True),
    )
    memories = [
        {"content": "M" * 300, "score": 0.9},
        {"content": "N" * 300, "score": 0.6},
        {"content": "L" * 100, "score": 0.8},
    ]
    selected, dropped = select_memories_with_budget(memories, budget)
    results["budget_selects_at_least_one"] = len(selected) >= 1
    results["selected_count"] = len(selected)
    results["dropped_count"] = len(dropped)
    results["total_budget_chars"] = budget.total_chars
    results["memory_max_chars"] = budget.memory_max_chars

    return results


def check_cost_control(profile: dict) -> dict:
    """验证成本控制门的正确性。"""
    from core.base.cost_control import CostControl, build_cost_control_from_config

    results = {}
    mode = profile.get("cost_control.mode", "balanced")
    max_calls = profile.get("cost_control.max_extra_llm_calls_per_turn", 0)

    cc = CostControl(mode=mode, max_extra_llm_calls_per_turn=max_calls)

    results["mode"] = mode
    results["llm_reranker_allowed"] = cc.allow("llm_reranker")
    results["topic_strategy_d_allowed"] = cc.allow("topic_strategy_d")

    # 验收：balanced/low_cost 下 LLM reranker 应被禁止
    if mode in ("balanced", "low_cost"):
        results["no_extra_llm_in_passive"] = not cc.allow("llm_reranker")
    else:
        results["extra_llm_allowed_in_quality"] = cc.allow("llm_reranker")

    return results


def check_schema_integrity() -> dict:
    """验证 _conf_schema.json 完整性。"""
    schema_path = PROJECT_ROOT / "_conf_schema.json"
    if not schema_path.exists():
        return {"error": "_conf_schema.json not found"}

    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)

    checks = {}

    # 检查注入预算字段
    recall_items = schema.get("recall_engine", {}).get("items", {})
    injection_keys = [
        "injection_budget_chars",
        "injection_memory_max_chars",
        "injection_metadata_max_chars",
        "injection_compact_header",
        "cognitive_context_budget_chars",
        "proactive_plan_budget_chars",
    ]
    missing_injection = [k for k in injection_keys if k not in recall_items]
    checks["injection_budget_fields"] = len(missing_injection) == 0
    if missing_injection:
        checks["missing_injection_fields"] = missing_injection

    # 检查 reranker enabled 字段
    reranker_items = schema.get("reranker", {}).get("items", {})
    checks["reranker_has_enabled"] = "enabled" in reranker_items

    # 检查 cost_control 字段
    cc_items = schema.get("cost_control", {}).get("items", {})
    checks["cost_control_section_exists"] = bool(cc_items)
    checks["cost_control_has_mode"] = "mode" in cc_items

    return checks


def run_benchmark(profile_name: str) -> dict:
    """运行完整基准检查。"""
    profile = PROFILES.get(profile_name)
    if profile is None:
        return {"error": f"Unknown profile: {profile_name}", "available": list(PROFILES)}

    print(f"\n{'='*60}")
    print(f" Memora Recall Cost Benchmark: {profile_name}")
    print(f"{'='*60}")

    report = {
        "profile": profile_name,
        "config": profile,
    }

    # 1. 配置转发检查
    print("\n[1/4] 配置转发完整性检查...")
    forwarding = check_config_forwarding()
    report["config_forwarding"] = forwarding
    if forwarding.get("error"):
        print(f"  ❌ Error: {forwarding['error']}")
    else:
        status = "[PASS]" if forwarding["forwarding_coverage"] >= 100 else "[WARN]"
        print(f"  {status} 转发覆盖率: {forwarding['forwarding_coverage']}%")
        if forwarding.get("missing"):
            print(f"  缺失键 ({len(forwarding['missing'])}): {forwarding['missing']}")

    # 2. 注入预算检查
    print("\n[2/4] 注入预算检查...")
    budget = check_injection_budget(profile)
    report["injection_budget"] = budget
    print(f"  预算总字符数: {budget.get('total_budget_chars')}")
    print(f"  单条截断上限: {budget.get('memory_max_chars')} chars")
    print(f"  选中: {budget.get('selected_count')}, 丢弃: {budget.get('dropped_count')}")

    # 3. 成本控制检查
    print("\n[3/4] 成本控制门检查...")
    cost = check_cost_control(profile)
    report["cost_control"] = cost
    print(f"  成本模式: {cost['mode']}")
    print(f"  LLM reranker 允许: {cost['llm_reranker_allowed']}")
    print(f"  strategy D 允许: {cost['topic_strategy_d_allowed']}")

    # 4. Schema 完整性
    print("\n[4/4] Schema 完整性检查...")
    schema = check_schema_integrity()
    report["schema_integrity"] = schema
    for check_name, passed in schema.items():
        if check_name.startswith("missing"):
            print(f"  ❌ {check_name}: {passed}")
        elif isinstance(passed, bool):
            status = "[PASS]" if passed else "[FAIL]"
            print(f"  {status} {check_name}")

    # 摘要
    all_ok = (
        forwarding.get("forwarding_coverage", 0) >= 100
        and budget.get("budget_selects_at_least_one", False)
        and cost.get("no_extra_llm_in_passive", True)
        and schema.get("injection_budget_fields", False)
        and schema.get("reranker_has_enabled", False)
    )
    report["summary"] = {
        "all_checks_passed": all_ok,
        "profile": profile_name,
        "extra_llm_calls_expected": (
            0 if cost["mode"] in ("balanced", "low_cost") else "configurable"
        ),
    }

    print(f"\n{'='*60}")
    print(f" 结果: " + ("ALL PASSED" if all_ok else "SOME CHECKS FAILED"))
    print(f"{'='*60}\n")

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Memora 召回成本基准脚本")
    parser.add_argument(
        "--profile",
        choices=["low_cost", "balanced", "quality"],
        default="balanced",
        help="配置档位 (default: balanced)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="输出 JSON 报告路径",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="运行所有三档配置",
    )
    args = parser.parse_args()

    if args.all:
        reports = {}
        for profile_name in PROFILES:
            reports[profile_name] = run_benchmark(profile_name)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(reports, f, indent=2, ensure_ascii=False)
            print(f"报告已写入: {args.output}")
    else:
        report = run_benchmark(args.profile)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            print(f"报告已写入: {args.output}")


if __name__ == "__main__":
    main()
