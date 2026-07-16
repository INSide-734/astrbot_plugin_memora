"""Versioned full-path benchmark support for ``RecallHandler``."""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import platform
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable


TOTAL_RECALL_REGRESSION_LIMIT = 0.05
TOTAL_RECALL_WARMUP_RUNS = 20
TOTAL_RECALL_MEASURED_RUNS = 160
TOTAL_RECALL_RETRIEVAL_DELAY_MS = 10.0
TOTAL_RECALL_BASELINE_PATH = (
    Path(__file__).resolve().parent / "baselines" / "recall_total_path.json"
)
TOTAL_RECALL_BASELINE_DISPLAY_PATH = "scripts/baselines/recall_total_path.json"
BENCHMARK_ENTRYPOINT = Path(__file__).resolve().with_name(
    "benchmark_recall_cost.py"
)
_METRIC = "RecallHandler.handle_memory_recall total-path p95"
_SCENARIO = "balanced_full_path_with_fixed_retrieval"
_SOURCE_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")


class SilentLogger:
    """Drop benchmark logs while retaining production call boundaries."""

    def __getattr__(self, _name: str) -> Callable[..., None]:
        return lambda *_args, **_kwargs: None


class HandlerBenchmarkConfig:
    """Minimal current configuration surface consumed by the handler."""

    filtering_settings = {
        "use_persona_filtering": True,
        "use_session_filtering": True,
    }
    runtime_injection_fallback = False

    _VALUES = {
        "recall_engine.top_k": 5,
        "recall_engine.auto_remove_injected": False,
        "recall_engine.inject_with_recent_context": False,
        "recall_engine.query_rewrite_enabled": False,
        "recall_engine.spontaneous_recall_enabled": False,
        "recall_engine.prospective_recall_enabled": False,
        "recall_engine.injection_routing_mode": "manual",
        "recall_engine.injection_manual_preset": "balanced",
        "recall_engine.injection_auto_fallback_preset": "balanced",
        "recall_engine.injection_hybrid_base_preset": "balanced",
        "recall_engine.injection_hybrid_min_preset": "low_cost",
        "recall_engine.injection_hybrid_max_preset": "quality",
        "recall_engine.injection_delivery_override": "auto",
        "recall_engine.injection_preset_overrides_enabled": False,
        "recall_engine.injection_budget_chars": 1_200,
        "recall_engine.injection_memory_max_chars": 220,
        "recall_engine.injection_metadata_max_chars": 180,
        "recall_engine.injection_compact_header": True,
        "recall_engine.injection_include_key_facts": True,
        "recall_engine.injection_include_topics": True,
        "recall_engine.injection_include_participants": False,
        "recall_engine.cognitive_context_budget_chars": 300,
        "recall_engine.proactive_plan_budget_chars": 240,
    }

    def get(self, key: str, default: Any = None) -> Any:
        return self._VALUES.get(key, default)


class HandlerBenchmarkEngine:
    def __init__(self, memories: list[Any], retrieval_delay_ms: float) -> None:
        self._memories = memories
        self._retrieval_delay_seconds = retrieval_delay_ms / 1_000.0
        self._pending_proactive = None
        self._last_search_timing: dict[str, float] = {}

    async def search_memories(self, **_kwargs: Any) -> list[Any]:
        await asyncio.sleep(self._retrieval_delay_seconds)
        return list(self._memories)


class HandlerBenchmarkConversation:
    async def add_message_from_event(self, **_kwargs: Any) -> None:
        return None


class HandlerBenchmarkProvider:
    provider_config = {
        "type": "openai_chat_completion",
        "max_context_tokens": 16_000,
        "max_completion_tokens": 1_000,
    }

    def get_model(self) -> str:
        return "benchmark-model"


class HandlerBenchmarkContext:
    def __init__(self, provider: HandlerBenchmarkProvider) -> None:
        self._provider = provider

    def get_using_provider(self, _session_id: str) -> HandlerBenchmarkProvider:
        return self._provider


class HandlerBenchmarkRecorder:
    @staticmethod
    def record(_record: Any) -> None:
        return None


class HandlerBenchmarkEvent:
    unified_msg_origin = "benchmark-session"

    def __init__(self, private_message_type: Any) -> None:
        self._private_message_type = private_message_type

    def get_message_type(self) -> Any:
        return self._private_message_type

    @staticmethod
    def get_sender_id() -> str:
        return "benchmark-user"


def handler_request() -> Any:
    from astrbot.api.provider import ProviderRequest

    return ProviderRequest(
        prompt="我之前喝咖啡有什么固定偏好？",
        system_prompt="benchmark-system-prompt",
        contexts=[],
        extra_user_content_parts=[],
    )


def percentile_95(values: list[float]) -> float:
    if not values:
        raise ValueError("p95 requires at least one measured value")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


async def noop_async(*_args: Any, **_kwargs: Any) -> None:
    return None


def build_memories() -> list[Any]:
    from core.retrieval.rrf_fusion import HybridResult

    rows = (
        (0.95, "用户固定选择燕麦拿铁，并明确要求不要额外添加糖浆。"),
        (0.82, "用户通常在上午十点前喝咖啡，周末会改为低咖啡因。"),
        (0.61, "用户偏好安静座位，并会提前确认附近是否有可用电源。"),
    )
    return [
        HybridResult(
            doc_id=index,
            final_score=score,
            rrf_score=score,
            bm25_score=None,
            vector_score=None,
            content=content,
            metadata={
                "importance": 0.9,
                "create_time": 1_783_150_200,
                "intent_match": 1.0,
                "temporal_value": 1.0,
                "source_value": 1.0,
            },
        )
        for index, (score, content) in enumerate(rows, start=1)
    ]


def build_handler(retrieval_delay_ms: float) -> tuple[Any, HandlerBenchmarkProvider]:
    from core.handlers import recall_handler as recall_module
    from core.utils.injection_adapter import InjectionAdapter

    provider = HandlerBenchmarkProvider()
    constructor_values: dict[str, Any] = {
        "context": HandlerBenchmarkContext(provider),
        "config_manager": HandlerBenchmarkConfig(),
        "memory_engine": HandlerBenchmarkEngine(
            build_memories(), retrieval_delay_ms
        ),
        "conversation_manager": HandlerBenchmarkConversation(),
        "injection_adapter": InjectionAdapter(),
        "enforce_limit_cb": noop_async,
    }
    parameters = inspect.signature(recall_module.RecallHandler.__init__).parameters
    if "injection_recorder" in parameters:
        constructor_values["injection_recorder"] = HandlerBenchmarkRecorder()
    if "memory_tool_available" in parameters:
        constructor_values["memory_tool_available"] = False
    return recall_module.RecallHandler(**constructor_values), provider


def make_rewrite_result() -> SimpleNamespace:
    return SimpleNamespace(
        intent="default",
        rewritten_queries=[],
        memory_types=[],
        extracted_entities=[],
    )


def configure_deterministic_handler(handler: Any) -> None:
    async def fixed_message(_event: Any) -> str:
        return "我之前喝咖啡有什么固定偏好？"

    async def fixed_rewrite(**_kwargs: Any) -> SimpleNamespace:
        return make_rewrite_result()

    async def no_candidates(**_kwargs: Any) -> list[Any]:
        return []

    async def no_context(**_kwargs: Any) -> str:
        return ""

    handler._extractor.get_event_message_str = fixed_message
    handler._query_rewriter.rewrite = fixed_rewrite
    handler._maybe_spontaneous_recall = no_candidates
    handler._maybe_prospective_recall = no_candidates
    handler._build_cognitive_context = no_context


def install_worker_overrides() -> Any:
    from core.handlers import recall_handler as recall_module

    recall_module.logger = SilentLogger()

    async def fixed_persona_id(_context: Any, _event: Any) -> str:
        return "benchmark-persona"

    recall_module.get_persona_id = fixed_persona_id
    return recall_module


def build_private_event() -> HandlerBenchmarkEvent:
    from astrbot.api.platform import MessageType

    message_type = getattr(
        MessageType,
        "FRIEND_MESSAGE",
        getattr(MessageType, "PRIVATE_MESSAGE", object()),
    )
    return HandlerBenchmarkEvent(message_type)


def is_temporary_extra_user_content(request: Any) -> bool:
    parts = list(getattr(request, "extra_user_content_parts", []) or [])
    return bool(parts) and all(
        bool(getattr(part, "_no_save", False)) for part in parts
    )


async def run_handler_samples(
    handler: Any,
    event: Any,
    *,
    warmup_runs: int,
    measured_runs: int,
) -> tuple[list[float], dict[str, int]]:
    latencies: list[float] = []
    contract = {
        "injected_runs": 0,
        "system_prompt_mutations": 0,
        "temporary_extra_user_content_runs": 0,
    }
    for index in range(warmup_runs + measured_runs):
        request = handler_request()
        original_system_prompt = request.system_prompt
        started = time.perf_counter()
        await handler.handle_memory_recall(event, request)
        elapsed_ms = (time.perf_counter() - started) * 1_000.0
        if index < warmup_runs:
            continue
        latencies.append(elapsed_ms)
        if getattr(request, "extra_user_content_parts", None):
            contract["injected_runs"] += 1
        if request.system_prompt != original_system_prompt:
            contract["system_prompt_mutations"] += 1
        if is_temporary_extra_user_content(request):
            contract["temporary_extra_user_content_runs"] += 1
    return latencies, contract


async def measure_handler_total_path(
    *,
    warmup_runs: int,
    measured_runs: int,
    retrieval_delay_ms: float,
) -> dict[str, Any]:
    """Measure ``RecallHandler.handle_memory_recall`` via its public entrypoint."""

    if warmup_runs < 0 or measured_runs <= 0 or retrieval_delay_ms < 0:
        raise ValueError("benchmark run counts and retrieval delay are invalid")
    install_worker_overrides()
    handler, _provider = build_handler(retrieval_delay_ms)
    configure_deterministic_handler(handler)
    latencies, contract = await run_handler_samples(
        handler,
        build_private_event(),
        warmup_runs=warmup_runs,
        measured_runs=measured_runs,
    )
    return {
        "metric": _METRIC,
        "scenario": _SCENARIO,
        "warmup_runs": warmup_runs,
        "measured_runs": measured_runs,
        "retrieval_delay_ms": retrieval_delay_ms,
        "p50_ms": statistics.median(latencies),
        "p95_ms": percentile_95(latencies),
        "min_ms": min(latencies),
        "max_ms": max(latencies),
        **contract,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
    }


def handler_worker_main(argument_value: Callable[[str, str], str]) -> int:
    result = asyncio.run(
        measure_handler_total_path(
            warmup_runs=int(
                argument_value(
                    "--warmup-runs", str(TOTAL_RECALL_WARMUP_RUNS)
                )
            ),
            measured_runs=int(
                argument_value(
                    "--measured-runs", str(TOTAL_RECALL_MEASURED_RUNS)
                )
            ),
            retrieval_delay_ms=float(
                argument_value(
                    "--retrieval-delay-ms",
                    str(TOTAL_RECALL_RETRIEVAL_DELAY_MS),
                )
            ),
        )
    )
    print("MEMORA_HANDLER_RESULT=" + json.dumps(result, ensure_ascii=False))
    return 0


def run_handler_worker(
    source_root: Path,
    *,
    warmup_runs: int,
    measured_runs: int,
    retrieval_delay_ms: float,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(BENCHMARK_ENTRYPOINT),
        "--handler-worker",
        "--source-root",
        str(source_root.resolve()),
        "--warmup-runs",
        str(warmup_runs),
        "--measured-runs",
        str(measured_runs),
        "--retrieval-delay-ms",
        str(retrieval_delay_ms),
    ]
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    prefix = "MEMORA_HANDLER_RESULT="
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith(prefix):
            return json.loads(line[len(prefix) :])
    raise RuntimeError("RecallHandler benchmark worker returned no result")


def load_total_path_baseline(
    path: Path = TOTAL_RECALL_BASELINE_PATH,
) -> dict[str, Any]:
    try:
        baseline = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid total recall baseline: {exc}") from exc
    valid = (
        baseline.get("schema_version") == 1
        and baseline.get("metric") == _METRIC
        and baseline.get("scenario") == _SCENARIO
        and _positive_number(baseline.get("p95_ms"))
        and _integer_at_least(baseline.get("measured_runs"), 100)
        and _integer_at_least(baseline.get("warmup_runs"), 10)
        and _positive_number(baseline.get("retrieval_delay_ms"))
        and _SOURCE_COMMIT_PATTERN.fullmatch(
            str(baseline.get("source_commit", ""))
        )
        is not None
        and baseline.get("injected_runs") == baseline.get("measured_runs")
        and baseline.get("system_prompt_mutations") == 0
        and baseline.get("temporary_extra_user_content_runs")
        == baseline.get("measured_runs")
    )
    if not valid:
        raise ValueError("invalid total recall baseline schema or values")
    return baseline


def _positive_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) > 0
    )


def _integer_at_least(value: Any, minimum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def evaluate_total_path_regression(
    *, current_p95_ms: float, baseline_p95_ms: float
) -> dict[str, float | bool]:
    if baseline_p95_ms <= 0 or not math.isfinite(baseline_p95_ms):
        raise ValueError("baseline p95 must be a positive finite value")
    if current_p95_ms < 0 or not math.isfinite(current_p95_ms):
        raise ValueError("current p95 must be a nonnegative finite value")
    regression_ratio = current_p95_ms / baseline_p95_ms - 1.0
    return {
        "regression_ratio": regression_ratio,
        "passed": current_p95_ms
        <= baseline_p95_ms * (1.0 + TOTAL_RECALL_REGRESSION_LIMIT),
    }


def source_checkout_commit(source_root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(source_root.resolve()), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout.strip()


def ensure_clean_source_checkout(source_root: Path, source_commit: str) -> None:
    actual_commit = source_checkout_commit(source_root)
    if actual_commit != source_commit:
        raise ValueError(
            "source commit does not match the benchmark source checkout"
        )
    completed = subprocess.run(
        ["git", "-C", str(source_root.resolve()), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.stdout.strip():
        raise ValueError("source commit checkout must be clean")


def record_total_path_baseline(
    *, source_root: Path, source_commit: str, output_path: Path
) -> dict[str, Any]:
    if _SOURCE_COMMIT_PATTERN.fullmatch(source_commit) is None:
        raise ValueError("source commit must be a full 40-character Git hash")
    ensure_clean_source_checkout(source_root, source_commit)
    measured = run_handler_worker(
        source_root,
        warmup_runs=TOTAL_RECALL_WARMUP_RUNS,
        measured_runs=TOTAL_RECALL_MEASURED_RUNS,
        retrieval_delay_ms=TOTAL_RECALL_RETRIEVAL_DELAY_MS,
    )
    baseline = {
        "schema_version": 1,
        "metric": measured["metric"],
        "scenario": measured["scenario"],
        "source_commit": source_commit,
        "warmup_runs": measured["warmup_runs"],
        "measured_runs": measured["measured_runs"],
        "retrieval_delay_ms": measured["retrieval_delay_ms"],
        "p95_ms": measured["p95_ms"],
        "injected_runs": measured["injected_runs"],
        "system_prompt_mutations": measured["system_prompt_mutations"],
        "temporary_extra_user_content_runs": measured[
            "temporary_extra_user_content_runs"
        ],
        "recorded_python_version": measured["python_version"],
        "recorded_platform": measured["platform"],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(baseline, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return load_total_path_baseline(output_path)


def measurement_contract_passed(result: dict[str, Any]) -> bool:
    measured_runs = result.get("measured_runs")
    return (
        result.get("injected_runs") == measured_runs
        and result.get("system_prompt_mutations") == 0
        and result.get("temporary_extra_user_content_runs") == measured_runs
    )


def run_total_path_benchmark(project_root: Path) -> dict[str, Any]:
    baseline = load_total_path_baseline()
    current = run_handler_worker(
        project_root,
        warmup_runs=int(baseline["warmup_runs"]),
        measured_runs=int(baseline["measured_runs"]),
        retrieval_delay_ms=float(baseline["retrieval_delay_ms"]),
    )
    evaluation = evaluate_total_path_regression(
        current_p95_ms=float(current["p95_ms"]),
        baseline_p95_ms=float(baseline["p95_ms"]),
    )
    regression = float(evaluation["regression_ratio"])
    contract_passed = measurement_contract_passed(current)
    passed = bool(evaluation["passed"]) and contract_passed
    print("\n  RecallHandler total-path benchmark")
    print(f"  TotalRecallPathP95: {current['p95_ms']:.6f} ms")
    print(f"  RecordedBaselineP95: {baseline['p95_ms']:.6f} ms")
    print(f"  RecordedBaselinePath: {TOTAL_RECALL_BASELINE_DISPLAY_PATH}")
    print(
        "  TotalRecallPathRegression: "
        f"{regression:.6f} (required <= {TOTAL_RECALL_REGRESSION_LIMIT:.6f})"
    )
    print(
        "  TotalRecallPathContract: "
        + ("ALL PASSED" if contract_passed else "FAILED")
    )
    print("  Total-path result: " + ("ALL PASSED" if passed else "FAILED"))
    return {
        "baseline": baseline,
        "current": current,
        "regression_ratio": regression,
        "summary": {"all_checks_passed": passed},
    }
