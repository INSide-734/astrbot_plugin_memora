#!/usr/bin/env python3
"""对脱敏注入决策的持久化与查询执行基准测试。"""

from __future__ import annotations

import asyncio
import statistics
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# 脚本需先把仓库根目录加入模块路径，以下运行时代码导入必须后置。
from core.injection.models import InjectionDecisionRecord  # noqa: E402
from core.injection.recorder import InjectionDecisionRecorder  # noqa: E402
from core.managers.write_coordinator import write_transaction  # noqa: E402
from core.storage.injection_decision_store import (  # noqa: E402
    DecisionQuery,
    InjectionDecisionStore,
)

ROW_COUNT = 100_000
RUNS = 5
SUMMARY_LIMIT_MS = 200.0
PAGE_LIMIT_MS = 100.0
CLEANUP_LIMIT_MS = 2_000.0
ENQUEUE_LIMIT_MS = 1.0


def make_record(index: int, now_ms: int) -> InjectionDecisionRecord:
    preset = ("low_cost", "balanced", "quality")[index % 3]
    return InjectionDecisionRecord(
        decision_id=str(uuid.UUID(int=(index + 1) | (4 << 76) | (2 << 62))),
        created_at_ms=now_ms - index * 1_000,
        routing_mode=("manual", "auto", "hybrid")[index % 3],
        configured_preset="balanced",
        recommended_preset=preset,
        resolved_preset=preset,
        preferred_delivery="extra_user_content",
        resolved_delivery="extra_user_content",
        fallback_applied=index % 17 == 0,
        outcome="fallback" if index % 17 == 0 else "injected",
        primary_reason=(
            "PROVIDER_DELIVERY_DOWNGRADED" if index % 17 == 0 else "MANUAL_SELECTED"
        ),
        provider_type=("openai", "gemini")[index % 2],
        provider_model="benchmark-model",
        candidate_count=6,
        selected_count=4,
        dropped_count=2,
        configured_budget_chars=1_200,
        effective_budget_chars=1_200,
        actual_payload_chars=600 + index % 500,
        context_headroom_chars=8_000,
        decision_ms=0.5,
        format_ms=1.5,
        inject_ms=0.3,
    )


async def elapsed_ms(call) -> float:
    started = time.perf_counter()
    await call()
    return (time.perf_counter() - started) * 1_000


async def median_ms(call, *, runs: int = RUNS) -> float:
    await call()  # warmup
    return statistics.median([await elapsed_ms(call) for _ in range(runs)])


async def populate_store(store: InjectionDecisionStore, now_ms: int) -> None:
    for start in range(0, ROW_COUNT, 1_000):
        await store.insert_many(
            [
                make_record(index, now_ms)
                for index in range(start, min(ROW_COUNT, start + 1_000))
            ]
        )


async def measure_summary(store: InjectionDecisionStore, now_ms: int) -> float:
    summary = await store.summary("24h", now_ms=now_ms)
    expected_rows = 86_401
    if summary["decision_count"] != expected_rows:
        raise RuntimeError(
            "24h summary did not cover the populated benchmark window: "
            f"expected {expected_rows}, got {summary['decision_count']}"
        )
    return await median_ms(lambda: store.summary("24h", now_ms=now_ms))


async def measure_page(store: InjectionDecisionStore) -> float:
    query = DecisionQuery(
        resolved_preset="balanced",
        provider_type="openai",
        outcome="injected",
        offset=500,
        limit=100,
    )
    return await median_ms(lambda: store.list_decisions(query))


async def measure_enqueue(store: InjectionDecisionStore, now_ms: int) -> float:
    recorder = InjectionDecisionRecorder(store, flush_interval=60.0)
    await recorder.start()
    try:
        samples = []
        for index in range(1_000):
            started = time.perf_counter()
            recorder.record(make_record(ROW_COUNT + index, now_ms))
            samples.append((time.perf_counter() - started) * 1_000)
        return sorted(samples)[949]
    finally:
        await recorder.close(timeout=5.0)


async def measure_cleanup(store: InjectionDecisionStore, now_ms: int) -> float:
    return await elapsed_ms(lambda: store.cleanup(30, 50_000, now_ms))


async def run_benchmark() -> dict[str, float]:
    with tempfile.TemporaryDirectory(prefix="memora-injection-bench-") as directory:
        store = InjectionDecisionStore(
            Path(directory) / "memora.db", lambda: write_transaction
        )
        await store.initialize()
        try:
            now_ms = time.time_ns() // 1_000_000
            await populate_store(store, now_ms)
            return {
                "summary_median_ms": await measure_summary(store, now_ms),
                "page_median_ms": await measure_page(store),
                "enqueue_p95_ms": await measure_enqueue(store, now_ms),
                "cleanup_ms": await measure_cleanup(store, now_ms),
            }
        finally:
            await store.close()


def main() -> int:
    results = asyncio.run(run_benchmark())
    limits = {
        "summary_median_ms": SUMMARY_LIMIT_MS,
        "page_median_ms": PAGE_LIMIT_MS,
        "cleanup_ms": CLEANUP_LIMIT_MS,
        "enqueue_p95_ms": ENQUEUE_LIMIT_MS,
    }
    for name, value in results.items():
        print(f"{name}={value:.3f} limit={limits[name]:.3f}")
    failed = [name for name, value in results.items() if value >= limits[name]]
    if failed:
        print("FAILED thresholds: " + ", ".join(failed))
        return 1
    print("Injection decision benchmark passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
