"""Service layer for retrieval evaluation runs and reports."""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from .report_store import EvaluationReportStore
from .retrieval_quality import (
    EvaluationCase,
    EvaluationReport,
    evaluate_cases,
    load_fixture_dir,
    make_memory_engine_retriever,
)


_SUPPORTED_VARIANTS = frozenset(
    {"baseline", "graph_expansion_off", "topic_expansion_off"}
)
_METRICS = ("recall_at_k", "mrr", "ndcg_at_k", "p95_latency_ms")
_VARIANT_CONFIG_KEYS = {
    "graph_expansion_off": "recall_engine.chain_graph_expansion_enabled",
    "topic_expansion_off": "recall_engine.chain_topic_expansion_enabled",
}
_CACHE_ATTRS = (
    "_retrieval",
    "retrieval_optimizer",
    "search_cache",
    "session_cache",
    "cache",
)
_CACHE_CLEAR_METHODS = ("invalidate_cache", "clear", "invalidate")
_MISSING = object()


class EvaluationService:
    """Run retrieval fixtures against a MemoryEngine-like object."""

    def __init__(
        self,
        *,
        engine: Any,
        fixture_dir: str | Path = "tests/fixtures/retrieval",
        db_path: str | Path | None = None,
    ) -> None:
        self.engine = engine
        self.fixture_dir = Path(fixture_dir)
        self.store = EvaluationReportStore(db_path) if db_path else None

    async def initialize(self) -> None:
        """Initialize persistence when a report store is configured."""
        if self.store is not None:
            await self.store.initialize()

    def list_datasets(self) -> dict[str, list[dict[str, Any]]]:
        """Return metadata for available JSONL evaluation fixtures."""
        datasets = []
        for name, cases in self._load_datasets().items():
            path = self.fixture_dir / f"{name}.jsonl"
            datasets.append(
                {
                    "name": name,
                    "case_count": len(cases),
                    "path": str(path),
                    "intents": sorted(
                        {
                            str(case.metadata.get("intent"))
                            for case in cases
                            if case.metadata.get("intent")
                        }
                    ),
                    "chat_types": sorted(
                        {
                            str(case.metadata.get("chat_type"))
                            for case in cases
                            if case.metadata.get("chat_type")
                        }
                    ),
                }
            )
        return {"datasets": datasets}

    async def run_evaluation(
        self,
        *,
        datasets: Sequence[str] | None,
        k: int,
        variants: Sequence[str] | None,
        baseline: str | None,
        save_report: bool,
    ) -> dict[str, Any]:
        """Evaluate selected datasets and optionally persist the report."""
        safe_k = self._clamp_k(k)
        selected_cases_by_dataset = self._select_datasets(datasets)
        cases = [
            case
            for dataset_cases in selected_cases_by_dataset.values()
            for case in dataset_cases
        ]
        requested_variants = self._select_variants(variants)
        baseline_name = baseline if baseline in requested_variants else "baseline"
        if baseline_name not in requested_variants:
            requested_variants.insert(0, baseline_name)

        completed_reports: dict[str, EvaluationReport] = {}
        variants_payload: dict[str, dict[str, Any]] = {}

        for variant_name in requested_variants:
            await self._clear_evaluation_caches()
            if variant_name == "baseline":
                report = await evaluate_cases(
                    cases,
                    make_memory_engine_retriever(self.engine),
                    k=safe_k,
                )
                completed_reports[variant_name] = report
                variants_payload[variant_name] = self._variant_completed_payload(
                    variant_name,
                    report,
                )
                continue

            async with self._configured_variant(variant_name) as can_run:
                if not can_run:
                    variants_payload[variant_name] = {
                        "name": variant_name,
                        "status": "skipped",
                        "reason": "engine_config_unavailable",
                    }
                    continue
                report = await evaluate_cases(
                    cases,
                    make_memory_engine_retriever(self.engine),
                    k=safe_k,
                )
                completed_reports[variant_name] = report
                variants_payload[variant_name] = self._variant_completed_payload(
                    variant_name,
                    report,
                )

        if baseline_name not in completed_reports:
            return {
                "status": "error",
                "message": "Baseline variant unavailable",
                "baseline": baseline_name,
                "datasets": list(selected_cases_by_dataset),
                "variants": variants_payload,
                "deltas": {},
                "report_id": None,
                "saved": False,
            }

        baseline_report = completed_reports[baseline_name]
        summary = self._report_summary(baseline_report)
        report_payload = {
            "baseline": baseline_name,
            "summary": summary,
            "datasets": list(selected_cases_by_dataset),
            "variants": variants_payload,
            "deltas": self._variant_deltas(completed_reports, baseline_name),
            "cases": self._report_cases(baseline_report),
        }

        report_id = None
        saved = False
        if save_report and self.store is not None:
            report_id = await self.store.save_report(report_payload)
            saved = True

        return {
            "report_id": report_id,
            "saved": saved,
            **report_payload,
        }

    async def list_reports(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return persisted report metadata when persistence is available."""
        if self.store is None:
            return []
        return await self.store.list_reports(limit=limit)

    async def get_report(self, report_id: str) -> dict[str, Any] | None:
        """Load a persisted report by ID."""
        if self.store is None:
            return None
        return await self.store.get_report(report_id)

    async def compare_reports(
        self,
        report_id_a: str,
        report_id_b: str,
    ) -> dict[str, Any] | None:
        """Return metric deltas from report A to report B."""
        if self.store is None:
            return None
        report_a = await self.store.get_report(report_id_a)
        report_b = await self.store.get_report(report_id_b)
        if report_a is None or report_b is None:
            return None

        summary_a = report_a.get("summary", {})
        summary_b = report_b.get("summary", {})
        return {
            "report_id_a": report_id_a,
            "report_id_b": report_id_b,
            "deltas": {
                metric: self._metric_delta(
                    self._metric_value(summary_a, metric),
                    self._metric_value(summary_b, metric),
                )
                for metric in _METRICS
            },
            "reports": {
                "a": self._report_compare_summary(report_a),
                "b": self._report_compare_summary(report_b),
            },
        }

    def _load_datasets(self) -> dict[str, list[EvaluationCase]]:
        return load_fixture_dir(self.fixture_dir)

    def _select_datasets(
        self,
        requested: Sequence[str] | None,
    ) -> dict[str, list[EvaluationCase]]:
        available = self._load_datasets()
        if not requested:
            return available
        selected = {
            name: available[name]
            for name in requested
            if name in available
        }
        return selected

    @staticmethod
    def _select_variants(variants: Sequence[str] | None) -> list[str]:
        selected = [
            str(name)
            for name in (variants or ["baseline"])
            if str(name) in _SUPPORTED_VARIANTS
        ]
        if "baseline" not in selected:
            selected.insert(0, "baseline")
        return list(dict.fromkeys(selected))

    @staticmethod
    def _clamp_k(k: Any) -> int:
        try:
            parsed = int(k)
        except (TypeError, ValueError):
            parsed = 5
        return max(1, min(20, parsed))

    @asynccontextmanager
    async def _configured_variant(self, name: str):
        setting = _VARIANT_CONFIG_KEYS.get(name)
        if not setting:
            yield False
            return

        config = getattr(self.engine, "config", None)
        if isinstance(config, dict):
            had_key = setting in config
            original = config.get(setting, _MISSING)
            try:
                config[setting] = False
                yield True
            finally:
                if had_key:
                    config[setting] = original
                else:
                    config.pop(setting, None)
                await self._clear_evaluation_caches()
            return

        get_config = getattr(self.engine, "get_config", None)
        set_config = getattr(self.engine, "set_config", None)
        if not callable(get_config) or not callable(set_config):
            yield False
            return

        original = get_config(setting, _MISSING)
        try:
            maybe_result = set_config(setting, False)
            if inspect.isawaitable(maybe_result):
                await maybe_result
            yield True
        finally:
            maybe_result = set_config(setting, original)
            if inspect.isawaitable(maybe_result):
                await maybe_result
            await self._clear_evaluation_caches()

    async def _clear_evaluation_caches(self) -> None:
        """Best-effort cache isolation for ablation variants."""
        seen: set[int] = set()
        targets: list[Any] = [self.engine]
        index = 0
        while index < len(targets):
            root = targets[index]
            index += 1
            for attr in _CACHE_ATTRS:
                try:
                    value = getattr(root, attr)
                except Exception:
                    continue
                if value is not None:
                    targets.append(value)

        for target in targets:
            target_id = id(target)
            if target_id in seen:
                continue
            seen.add(target_id)
            for method_name in _CACHE_CLEAR_METHODS:
                method = getattr(target, method_name, None)
                if not callable(method):
                    continue
                try:
                    result = method()
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    continue

    @classmethod
    def _variant_completed_payload(
        cls,
        name: str,
        report: EvaluationReport,
    ) -> dict[str, Any]:
        return {
            "name": name,
            "status": "completed",
            "summary": cls._report_summary(report),
        }

    @staticmethod
    def _report_summary(report: EvaluationReport) -> dict[str, Any]:
        return {
            "total_cases": report.total_cases,
            "k": report.k,
            "recall_at_k": report.recall_at_k,
            "mrr": report.mrr,
            "ndcg_at_k": report.ndcg_at_k,
            "p95_latency_ms": report.p95_latency_ms,
            "dataset_breakdown": EvaluationReportStore._normalize_json_value(
                report.dataset_breakdown
            ),
        }

    @staticmethod
    def _report_cases(report: EvaluationReport) -> list[dict[str, Any]]:
        return [
            EvaluationReportStore._normalize_json_value(case)
            for case in report.cases
        ]

    @classmethod
    def _variant_deltas(
        cls,
        reports: Mapping[str, EvaluationReport],
        baseline_name: str,
    ) -> dict[str, dict[str, float | None]]:
        baseline = reports.get(baseline_name)
        if baseline is None:
            return {}
        baseline_summary = cls._report_summary(baseline)
        deltas = {}
        for name, report in reports.items():
            if name == baseline_name:
                continue
            summary = cls._report_summary(report)
            deltas[name] = {
                metric: cls._metric_delta(
                    cls._metric_value(baseline_summary, metric),
                    cls._metric_value(summary, metric),
                )
                for metric in _METRICS
            }
        return deltas

    @staticmethod
    def _metric_value(report_or_summary: Mapping[str, Any], metric: str) -> float | None:
        value = report_or_summary.get(metric)
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _metric_delta(a: float | None, b: float | None) -> float | None:
        if a is None or b is None:
            return None
        return round(b - a, 4)

    @staticmethod
    def _report_compare_summary(report: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "report_id": report.get("report_id"),
            "created_at": report.get("created_at"),
            "baseline": report.get("baseline"),
            "summary": dict(report.get("summary", {}) or {}),
        }


__all__ = ["EvaluationService"]
