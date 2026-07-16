"""Memora 插件的 Prometheus 指标收集模块。

所有指标注册在独立的 CollectorRegistry 上，确保插件不会污染
进程级别的默认注册表。当未安装 prometheus_client 时每个类均降级
为 no-op stub — 调用方无需检查可用性。
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# 优雅降级：真实 prometheus_client 或轻量级 stub
# ---------------------------------------------------------------------------
try:
    from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

    _HAS_PROMETHEUS = True
except Exception:
    _HAS_PROMETHEUS = False

    class _StubLabeled:
        """No-op 标签子类 — 每个方法均为静默空操作。"""

        def inc(self, amount: float = 1) -> None:  # noqa: D401
            pass

        def dec(self, amount: float = 1) -> None:  # noqa: D401
            pass

        def set(self, value: float) -> None:  # noqa: D401
            pass

        def observe(self, amount: float) -> None:  # noqa: D401
            pass

    class _StubMetric:
        """No-op 指标类 — 对任意标签集均返回 _StubLabeled。"""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._labeled = _StubLabeled()

        def labels(self, *args: Any, **kwargs: Any) -> _StubLabeled:
            return self._labeled

        def inc(self, amount: float = 1) -> None:
            pass

        def observe(self, amount: float) -> None:
            pass

    class CollectorRegistry:  # type: ignore[no-redef]
        """prometheus_client 缺失时的 No-op 注册表。"""

        def get_all(self) -> list:
            return []

        def collect(self) -> list:
            return []

    Counter = _StubMetric  # type: ignore[misc]
    Gauge = _StubMetric  # type: ignore[misc]
    Histogram = _StubMetric  # type: ignore[misc]

# ---------------------------------------------------------------------------
# 独立注册表
# ---------------------------------------------------------------------------
REGISTRY: CollectorRegistry = CollectorRegistry()

# ---------------------------------------------------------------------------
# 预定义指标
# ---------------------------------------------------------------------------

# --- 召回管线 ---

RECALL_DURATION = Histogram(
    "memora_recall_duration_seconds",
    "Recall pipeline stage latency in seconds.",
    labelnames=["stage"],  # bm25 / vector / graph / hybrid / rerank
    registry=REGISTRY,
)

RECALL_REQUESTS = Counter(
    "memora_recall_requests_total",
    "Total number of recall operations.",
    registry=REGISTRY,
)

CACHE_HITS = Counter(
    "memora_recall_cache_hits_total",
    "Recall cache hit counter.",
    registry=REGISTRY,
)

CACHE_MISSES = Counter(
    "memora_recall_cache_misses_total",
    "Recall cache miss counter.",
    registry=REGISTRY,
)

# --- 记忆写入 ---

MEMORY_WRITE_DURATION = Histogram(
    "memora_memory_write_duration_seconds",
    "Memory write latency in seconds.",
    registry=REGISTRY,
)

MEMORY_ATOMS_TOTAL = Counter(
    "memora_memory_atoms_stored_total",
    "Total number of memory atoms persisted.",
    registry=REGISTRY,
)

MEMORY_WRITE_FAILURES_TOTAL = Counter(
    "memora_memory_write_failures_total",
    "Total number of memory write failures by persistence stage.",
    labelnames=["stage"],
    registry=REGISTRY,
)

# --- 写协调器 ---

WRITE_OPERATIONS_TOTAL = Counter(
    "memora_write_operations_total",
    "Total number of coordinated write operations.",
    registry=REGISTRY,
)

WRITE_LOCK_RETRIES_TOTAL = Counter(
    "memora_write_lock_retries_total",
    "Total number of SQLite write retries caused by lock contention.",
    registry=REGISTRY,
)

WRITE_FAILURES_TOTAL = Counter(
    "memora_write_failures_total",
    "Total number of coordinated write failures by reason.",
    labelnames=["reason"],
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# 注入预算与 token 消耗指标
# ---------------------------------------------------------------------------

INJECTION_CHARS = Histogram(
    "memora_injection_chars",
    "Number of characters injected per recall request.",
    buckets=(200, 400, 600, 800, 1000, 1500, 2000, 3000, 5000),
    registry=REGISTRY,
)

INJECTION_DROPPED_BY_BUDGET = Counter(
    "memora_injection_dropped_by_budget_total",
    "Total number of memories dropped due to injection budget constraints.",
    registry=REGISTRY,
)

INJECTION_TRUNCATED = Counter(
    "memora_injection_truncated_total",
    "Total number of memory contents truncated due to per-memory char limits.",
    registry=REGISTRY,
)

RERANKER_LLM_CALLS = Counter(
    "memora_reranker_llm_calls_total",
    "Total number of LLM reranker invocations.",
    registry=REGISTRY,
)

REFLECTION_LLM_CALLS = Counter(
    "memora_reflection_llm_calls_total",
    "Total number of LLM calls triggered by memory reflection/summarization.",
    registry=REGISTRY,
)

SUMMARY_BATCH_COUNT = Histogram(
    "memora_summary_batch_count",
    "Number of summary batches per reflection trigger.",
    buckets=(1, 2, 3, 5, 8, 12),
    registry=REGISTRY,
)

# --- 注入决策与异步记录器 ---

INJECTION_DECISIONS_TOTAL = Counter(
    "memora_injection_decisions_total",
    "Injection decisions by routing mode, resolved preset, and outcome.",
    labelnames=["routing_mode", "resolved_preset", "outcome"],
    registry=REGISTRY,
)

INJECTION_PROVIDER_FALLBACK_TOTAL = Counter(
    "memora_injection_provider_fallback_total",
    "Provider delivery fallback count by reason.",
    labelnames=["reason"],
    registry=REGISTRY,
)

INJECTION_DECISION_QUEUE_SECONDS = Histogram(
    "memora_injection_decision_queue_seconds",
    "Time spent enqueueing a sanitized injection decision.",
    registry=REGISTRY,
)

INJECTION_DECISION_RECORD_FAILURES_TOTAL = Counter(
    "memora_injection_decision_record_failures_total",
    "Decision persistence failures by stable error code.",
    labelnames=["error_code"],
    registry=REGISTRY,
)

INJECTION_DECISION_RECORD_DROPPED_TOTAL = Counter(
    "memora_injection_decision_record_dropped_total",
    "Oldest queued decisions dropped after bounded queue overflow.",
    registry=REGISTRY,
)

INJECTION_PRESET_TRANSITIONS_TOTAL = Counter(
    "memora_injection_preset_transitions_total",
    "Configured, recommended, and resolved preset transitions.",
    labelnames=["configured", "recommended", "resolved"],
    registry=REGISTRY,
)

INJECTION_HYBRID_CLAMP_TOTAL = Counter(
    "memora_injection_hybrid_clamp_total",
    "Hybrid preset clamps by boundary.",
    labelnames=["boundary"],
    registry=REGISTRY,
)

INJECTION_SKIP_TOTAL = Counter(
    "memora_injection_skip_total",
    "Skipped or empty injections by stable reason code.",
    labelnames=["reason"],
    registry=REGISTRY,
)

INJECTION_PAYLOAD_CHARS = Histogram(
    "memora_injection_payload_chars",
    "Actual protected injection payload characters.",
    buckets=(0, 200, 500, 800, 1200, 2400, 5000, 10000, 12000),
    registry=REGISTRY,
)

INJECTION_CANDIDATE_RETENTION_RATIO = Histogram(
    "memora_injection_candidate_retention_ratio",
    "Selected candidates divided by available candidates.",
    registry=REGISTRY,
)

INJECTION_BUDGET_DROP_RATIO = Histogram(
    "memora_injection_budget_drop_ratio",
    "Budget-dropped candidates divided by available candidates.",
    registry=REGISTRY,
)

INJECTION_TRUNCATION_RATIO = Histogram(
    "memora_injection_truncation_ratio",
    "Truncated candidates divided by selected candidates.",
    registry=REGISTRY,
)

INJECTION_STAGE_SECONDS = Histogram(
    "memora_injection_stage_seconds",
    "Injection decision, format, and request-mutation duration.",
    labelnames=["stage"],
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# 公共辅助函数
# ---------------------------------------------------------------------------


def is_prometheus_available() -> bool:
    """返回是否已安装真实的 prometheus_client 库。"""
    return _HAS_PROMETHEUS
