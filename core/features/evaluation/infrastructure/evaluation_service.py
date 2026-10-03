"""检索评测运行与报告的服务层。"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ...backup.infrastructure.snapshot import snapshot_sqlite
from ..application.quality_loop import (
    QualityLoopPairError,
    build_quality_loop_pairs,
    check_quality_loop_payload_privacy,
    run_quality_loop,
)
from ..application.retrieval_ablation import (
    RETRIEVAL_VARIANT_NAMES,
    PreparedVariant,
    RetrievalAblationController,
)
from ..application.retrieval_quality import (
    EvaluationCase,
    EvaluationReport,
    evaluate_cases,
    load_fixture_dir,
    make_memory_engine_retriever,
)
from ..domain.quality_loop_stages import (
    REASON_CANARY,
    REASON_ENGINE_MISSING,
    REASON_HMAC_SECRET_UNAVAILABLE,
    REASON_INJECTION_PORT_MISSING,
    REASON_LIFECYCLE_PORT_MISSING,
    REASON_NO_ANNOTATION,
    REASON_RETRIEVAL_FAILED,
    REASON_SNAPSHOT_UNAVAILABLE,
    STAGE_DEGRADED,
    STAGE_UNAVAILABLE,
    make_stage_read,
    stage_read_to_payload,
)
from .report_store import EvaluationReportStore

_SUPPORTED_VARIANTS = frozenset(RETRIEVAL_VARIANT_NAMES)
_METRICS = ("recall_at_k", "mrr", "ndcg_at_k", "observed_p95_latency_ms")
_CACHE_ATTRS = (
    "_retrieval",
    "retrieval_optimizer",
    "search_cache",
    "session_cache",
    "cache",
)
_CACHE_CLEAR_METHODS = ("invalidate_cache", "clear", "invalidate")


class EvaluationService:
    """针对 MemoryEngine 类对象运行检索夹具。"""

    def __init__(
        self,
        *,
        engine: Any,
        fixture_dir: str | Path = "tests/fixtures/retrieval",
        db_path: str | Path | None = None,
        include_experimental_datasets: bool = False,
        lifecycle_port: Any | None = None,
        quality_loop_secret: bytes | None = None,
    ) -> None:
        """装配评测引擎、夹具目录和可选报告存储。"""

        self.engine = engine
        self.fixture_dir = Path(fixture_dir)
        self.store = EvaluationReportStore(db_path) if db_path else None
        self.include_experimental_datasets = include_experimental_datasets
        self.lifecycle_port = lifecycle_port
        self.quality_loop_secret = quality_loop_secret

    async def initialize(self) -> None:
        """配置报告存储时初始化持久化。"""
        if self.store is not None:
            await self.store.initialize()

    def list_datasets(self) -> dict[str, list[dict[str, Any]]]:
        """返回可用 JSONL 评测夹具的元数据。"""
        datasets = []
        for name, cases in self._load_datasets().items():
            path = self.fixture_dir / f"{name}.jsonl"
            datasets.append(
                {
                    "name": name,
                    "case_count": len(cases),
                    "path": path.name,
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
        variants = RetrievalAblationController(self.engine).descriptors()
        return {"datasets": datasets, "variants": variants}

    async def run_evaluation(
        self,
        *,
        datasets: Sequence[str] | None,
        k: int,
        variants: Sequence[str] | None,
        baseline: str | None,
        save_report: bool,
        runtime_datasets: Mapping[str, Sequence[EvaluationCase]] | None = None,
        quality_loop: Mapping[str, Any] | None = None,
        quality_loop_seed: int = 0,
    ) -> dict[str, Any]:
        """评测文件或运行时数据集，并按需持久化安全报告。"""
        safe_k = self._clamp_k(k)
        selected_cases_by_dataset = self._select_datasets(
            datasets,
            runtime_datasets=runtime_datasets,
        )
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
        controller = RetrievalAblationController(self.engine)
        baseline_engine: Any | None = None

        for variant_name in requested_variants:
            prepared = controller.prepare(variant_name)
            if not prepared.available or prepared.engine is None:
                variants_payload[variant_name] = self._variant_skipped_payload(prepared)
                continue
            if variant_name == baseline_name:
                baseline_engine = prepared.engine
            try:
                await self._clear_evaluation_caches(prepared.engine)
                report = await evaluate_cases(
                    cases,
                    make_memory_engine_retriever(prepared.engine),
                    k=safe_k,
                )
                execution_reason = prepared.execution_reason_code()
                if execution_reason != "available":
                    variants_payload[variant_name] = self._variant_skipped_payload(
                        prepared,
                        reason_code=execution_reason,
                    )
                    continue
                completed_reports[variant_name] = report
                variants_payload[variant_name] = self._variant_completed_payload(
                    prepared,
                    report,
                )
                variants_payload[variant_name]["summary"]["configuration_hash"] = (
                    self._configuration_hash(prepared.engine)
                )
                variants_payload[variant_name]["summary"]["variant"] = variant_name
            except asyncio.CancelledError:
                raise
            except Exception:
                variants_payload[variant_name] = self._variant_skipped_payload(
                    prepared,
                    reason_code="variant_execution_failed",
                )
            finally:
                await self._clear_evaluation_caches(prepared.engine)

        if baseline_name not in completed_reports:
            unavailable_result = {
                "status": "error",
                "message": "Baseline variant unavailable",
                "baseline": baseline_name,
                "datasets": list(selected_cases_by_dataset),
                "variants": variants_payload,
                "deltas": {},
                "report_id": None,
                "saved": False,
            }
            if quality_loop is not None:
                unavailable_result[
                    "quality_loop"
                ] = await self._run_quality_loop_safely(
                    cases,
                    k=safe_k,
                    seed=quality_loop_seed,
                    engine=None,
                )
            return unavailable_result

        baseline_report = completed_reports[baseline_name]
        summary = self._report_summary(baseline_report)
        baseline_payload = variants_payload[baseline_name]
        summary["configuration_hash"] = baseline_payload["summary"][
            "configuration_hash"
        ]
        summary["variant"] = baseline_name
        report_payload = {
            "baseline": baseline_name,
            "summary": summary,
            "datasets": list(selected_cases_by_dataset),
            "variants": variants_payload,
            "deltas": self._variant_deltas(completed_reports, baseline_name),
            "cases": self._report_cases(baseline_report),
        }
        if quality_loop is not None:
            report_payload["quality_loop"] = await self._run_quality_loop_safely(
                cases,
                k=safe_k,
                seed=quality_loop_seed,
                engine=baseline_engine,
            )

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
        """报告存储可用时返回已持久化的报告元数据。"""
        if self.store is None:
            return []
        return await self.store.list_reports(limit=limit)

    async def get_report(self, report_id: str) -> dict[str, Any] | None:
        """按标识读取已持久化报告。"""
        if self.store is None:
            return None
        return await self.store.get_report(report_id)

    async def compare_reports(
        self,
        report_id_a: str,
        report_id_b: str,
    ) -> dict[str, Any] | None:
        """返回报告 A 到报告 B 的指标差异。"""
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
        """从夹具目录加载全部 JSONL 数据集。"""

        return load_fixture_dir(
            self.fixture_dir,
            include_experimental=self.include_experimental_datasets,
        )

    def _select_datasets(
        self,
        requested: Sequence[str] | None,
        *,
        runtime_datasets: Mapping[str, Sequence[EvaluationCase]] | None = None,
    ) -> dict[str, list[EvaluationCase]]:
        """合并受信运行时用例并按请求名称筛选；空请求表示全部。"""

        available = self._load_datasets()
        for name, cases in (runtime_datasets or {}).items():
            available[str(name)] = list(cases)
        if not requested:
            return available
        selected = {name: available[name] for name in requested if name in available}
        return selected

    @staticmethod
    def _select_variants(variants: Sequence[str] | None) -> list[str]:
        """过滤未知变体、去重并确保 baseline 存在。"""

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
        """将检索 K 规范化到 1..20。"""

        try:
            parsed = int(k)
        except (TypeError, ValueError):
            parsed = 5
        return max(1, min(20, parsed))

    async def _clear_evaluation_caches(self, engine: Any | None = None) -> None:
        """尽力隔离消融变体之间的缓存。"""
        seen: set[int] = set()
        visited: set[int] = set()
        targets: list[Any] = [engine if engine is not None else self.engine]
        index = 0
        while index < len(targets):
            root = targets[index]
            index += 1
            # 引擎对象之间可能互相引用（例如 cache 持有 retriever），
            # 已访问对象必须跳过，否则遍历永不收敛。
            if id(root) in visited:
                continue
            visited.add(id(root))
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
        prepared: PreparedVariant,
        report: EvaluationReport,
    ) -> dict[str, Any]:
        """将成功变体报告转换为稳定响应。"""

        return {
            "name": prepared.name,
            "status": "completed",
            "capability_status": prepared.capability_status,
            "reason_code": prepared.reason_code,
            "effective_settings": dict(prepared.effective_settings),
            "summary": cls._report_summary(report),
        }

    @staticmethod
    def _variant_skipped_payload(
        prepared: PreparedVariant,
        *,
        reason_code: str | None = None,
    ) -> dict[str, Any]:
        """返回不泄露组件细节的稳定 skipped 结果。"""

        effective_reason = reason_code or prepared.reason_code
        return {
            "name": prepared.name,
            "status": "skipped",
            "capability_status": "unavailable",
            "reason_code": effective_reason,
            "effective_settings": (
                dict(prepared.effective_settings) if reason_code else {}
            ),
        }

    @staticmethod
    def _report_summary(report: EvaluationReport) -> dict[str, Any]:
        """从完整报告提取安全汇总指标。"""

        summary = {
            "total_cases": report.total_cases,
            "k": report.k,
            "recall_at_k": report.recall_at_k,
            "precision_at_k": report.precision_at_k,
            "mrr": report.mrr,
            "ndcg_at_k": report.ndcg_at_k,
            "multi_hop_recall": report.multi_hop_recall,
            "single_hop_recall": report.single_hop_recall,
            "noise_negative_false_hit": report.noise_negative_false_hit,
            "temporal_consistency": report.temporal_consistency,
            "conflict_accuracy": report.conflict_accuracy,
            "source_supported_projection_rate": report.source_supported_projection_rate,
            "observed_p50_latency_ms": report.observed_p50_latency_ms,
            "observed_p95_latency_ms": report.observed_p95_latency_ms,
            "annotated_p50_latency_ms": report.annotated_p50_latency_ms,
            "annotated_p95_latency_ms": report.annotated_p95_latency_ms,
            "reported_p50_latency_ms": report.reported_p50_latency_ms,
            "reported_p95_latency_ms": report.reported_p95_latency_ms,
            "annotated_answer_faithfulness": report.annotated_answer_faithfulness,
            "annotated_answer_relevancy": report.annotated_answer_relevancy,
            "judged_answer_faithfulness": report.judged_answer_faithfulness,
            "judged_answer_relevancy": report.judged_answer_relevancy,
            "reported_answer_faithfulness": report.reported_answer_faithfulness,
            "reported_answer_relevancy": report.reported_answer_relevancy,
            "observed_provider_calls": report.observed_provider_calls,
            "observed_token_cost": report.observed_token_cost,
            "annotated_provider_calls": report.annotated_provider_calls,
            "annotated_token_cost": report.annotated_token_cost,
            "reported_provider_calls": report.reported_provider_calls,
            "reported_token_cost": report.reported_token_cost,
            "reason_code_aggregates": dict(report.reason_code_aggregates),
            "dataset_breakdown": EvaluationReportStore._normalize_json_value(
                report.dataset_breakdown
            ),
        }
        return summary

    def _configuration_hash(self, engine: Any | None = None) -> str:
        """计算匿名配置摘要，不将配置原文写入评测报告。"""
        config = getattr(engine if engine is not None else self.engine, "config", {})
        if not isinstance(config, Mapping):
            config = {"config_type": type(config).__name__}
        encoded = json.dumps(
            self._redact_config(dict(config)),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _schema_hash() -> str:
        """读取插件 schema 的哈希，不把 schema 正文写入报告。"""
        schema_path = Path(__file__).resolve().parents[4] / "_conf_schema.json"
        try:
            return hashlib.sha256(schema_path.read_bytes()).hexdigest()
        except (OSError, ValueError):
            return hashlib.sha256(b"quality-loop-schema-unavailable").hexdigest()

    async def _database_snapshot_hash(self, engine: Any) -> str | None:
        """在线程池中对 canonical SQLite 做临时 backup snapshot 并返回哈希。"""
        return await asyncio.to_thread(self._database_snapshot_hash_sync, engine)

    def _database_snapshot_hash_sync(self, engine: Any) -> str | None:
        """同步执行 SQLite snapshot；调用方不得在事件循环直接调用。"""
        try:
            database_path = getattr(engine, "db_path", None)
            if not isinstance(database_path, (str, Path)):
                return None
            with tempfile.TemporaryDirectory(prefix="memora-eval-") as directory:
                result = snapshot_sqlite(
                    Path(database_path), Path(directory) / "canonical.db"
                )
                return result.sha256
        except Exception:
            return None

    @staticmethod
    def _evaluator_code_revision() -> str:
        """按质量闭环实现源码计算稳定的低敏代码绑定。"""
        root = Path(__file__).resolve().parents[4]
        paths = (
            root / "core/features/evaluation/application/quality_loop.py",
            root / "core/features/evaluation/domain/quality_loop_manifest.py",
            root / "core/features/evaluation/domain/quality_loop_stages.py",
            root / "core/features/evaluation/infrastructure/evaluation_service.py",
        )
        digest = hashlib.sha256()
        for path in paths:
            digest.update(path.name.encode("ascii"))
            try:
                digest.update(path.read_bytes())
            except OSError:
                digest.update(b"quality-loop-source-unavailable")
        return digest.hexdigest()

    def _provider_identifiers(
        self, engine: Any
    ) -> tuple[str | None, str | None, str | None]:
        """从只读服务配置取得 LLM、Embedding 与 tokenizer 标识。"""
        config = getattr(engine, "config", {})
        provider_settings = (
            config.get("provider_settings", {}) if isinstance(config, Mapping) else {}
        )
        if not isinstance(provider_settings, Mapping):
            provider_settings = {}
        llm_id = provider_settings.get("llm_provider_id")
        embedding_id = provider_settings.get("embedding_provider_id")
        tokenizer_id = provider_settings.get("tokenizer_id")
        if tokenizer_id is None and isinstance(config, Mapping):
            tokenizer_id = config.get("tokenizer_id")
        return (
            self._manifest_identifier(llm_id),
            self._manifest_identifier(embedding_id),
            self._manifest_identifier(tokenizer_id),
        )

    @staticmethod
    def _manifest_identifier(value: Any) -> str | None:
        """将自由格式 provider 标识收敛为短标识或不可逆哈希。"""
        text = str(value).strip() if value is not None else ""
        if not text:
            return None
        if (
            len(text) <= 128
            and text.isascii()
            and all(char.isalnum() or char in "-_.:" for char in text)
        ):
            return text
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _sensitive_config_values(engine: Any) -> tuple[str, ...]:
        """Collect configured secret values for the final public-payload canary."""
        found: set[str] = set()

        def visit(value: Any, sensitive_parent: bool = False) -> None:
            if isinstance(value, Mapping):
                for key, item in value.items():
                    key_text = str(key).lower()
                    marker = any(
                        part in key_text
                        for part in ("secret", "password", "key", "credential")
                    ) or ("token" in key_text and "tokenizer" not in key_text)
                    visit(item, marker)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    visit(item, sensitive_parent)
            elif sensitive_parent and isinstance(value, str) and len(value) >= 4:
                found.add(value)

        config = getattr(engine, "config", {})
        visit(config)
        return tuple(sorted(found))

    def _quality_loop_unavailable_payload(
        self,
        cases: Sequence[EvaluationCase],
        *,
        reason: str,
    ) -> dict[str, Any]:
        """Return closed-set skipped evidence when quality-loop execution cannot start."""
        pairs, rejects = build_quality_loop_pairs(cases)
        pair_reasons: dict[str, int] = {}
        for reject in rejects:
            pair_reasons[reject] = pair_reasons.get(reject, 0) + 1
        owning_stage = {
            REASON_ENGINE_MISSING: "recall",
            REASON_RETRIEVAL_FAILED: "recall",
            REASON_CANARY: "source",
            REASON_HMAC_SECRET_UNAVAILABLE: "source",
        }.get(reason, "write")
        metrics = {
            "write": {"write_fact_correctness": None},
            "source": {
                "source_faithfulness": None,
                "source_evidence_completeness": None,
            },
            "recall": {"candidate_hit_rate": None},
            "injection": {
                "final_injected_hit_rate": None,
                "negative_injection_rate": None,
                "configured_budget_chars": None,
                "effective_budget_chars": None,
            },
            "lifecycle": {
                "retrieved_count": None,
                "injected_count": None,
                "observed_p50_latency_ms": None,
                "observed_p95_latency_ms": None,
            },
            "expression": {
                "annotated_answer_faithfulness": None,
                "annotated_answer_relevancy": None,
            },
        }
        stages = []
        for stage, stage_metrics in metrics.items():
            if stage == owning_stage:
                stage_reason, state = reason, STAGE_UNAVAILABLE
            elif stage == "injection":
                stage_reason, state = REASON_INJECTION_PORT_MISSING, STAGE_UNAVAILABLE
            elif stage == "lifecycle":
                stage_reason, state = REASON_LIFECYCLE_PORT_MISSING, STAGE_UNAVAILABLE
            else:
                stage_reason, state = REASON_NO_ANNOTATION, STAGE_DEGRADED
            stages.append(
                stage_read_to_payload(
                    make_stage_read(
                        stage,
                        state=state,
                        reason=stage_reason,
                        metrics=stage_metrics,
                    )
                )
            )
        return {
            "manifest": None,
            "stages": stages,
            "pairs": {
                "total_pairs": len(pairs),
                "should_use_hit_rate": None,
                "should_silence_correct_rate": None,
                "reject_reason_counts": dict(sorted(pair_reasons.items())),
            },
            "pair_outcomes": [],
        }

    async def _run_quality_loop_safely(
        self,
        cases: Sequence[EvaluationCase],
        *,
        k: int,
        seed: int,
        engine: Any | None = None,
    ) -> dict[str, Any]:
        """Run only against the isolated baseline and server-owned bindings."""
        run_engine = engine
        if (
            not isinstance(self.quality_loop_secret, bytes)
            or len(self.quality_loop_secret) < 32
        ):
            return self._quality_loop_unavailable_payload(
                cases, reason=REASON_HMAC_SECRET_UNAVAILABLE
            )
        pairs, _ = build_quality_loop_pairs(cases)
        if pairs and run_engine is None:
            return self._quality_loop_unavailable_payload(
                cases, reason=REASON_ENGINE_MISSING
            )
        if not pairs:
            snapshot_hash = None
        else:
            snapshot_hash = await self._database_snapshot_hash(run_engine)
        if run_engine is None:
            retriever = None
            model_id = embedding_id = tokenizer_id = None
            config_hash = self._configuration_hash(self.engine)
            extra_forbidden_values: tuple[str, ...] = ()
        else:
            retriever = make_memory_engine_retriever(run_engine)
            model_id, embedding_id, tokenizer_id = self._provider_identifiers(
                run_engine
            )
            config_hash = self._configuration_hash(run_engine)
            extra_forbidden_values = self._sensitive_config_values(run_engine)
        try:
            payload = await run_quality_loop(
                cases,
                retriever=retriever,
                k=k,
                seed=seed,
                code_revision=self._evaluator_code_revision(),
                config_hash=config_hash,
                schema_hash=self._schema_hash(),
                context_key_secret=self.quality_loop_secret,
                model_id=model_id,
                embedding_id=embedding_id,
                tokenizer_id=tokenizer_id,
                db_snapshot_hash=snapshot_hash,
                lifecycle_port=self.lifecycle_port,
                additional_forbidden_values=extra_forbidden_values,
            )
            if pairs and snapshot_hash is None:
                for stage in payload["stages"]:
                    if stage.get("stage") == "source":
                        stage["state"] = STAGE_DEGRADED
                        stage["reason"] = REASON_SNAPSHOT_UNAVAILABLE
                        stage["owning_stage"] = "source"
                        stage["metrics"] = {
                            key: None for key in stage.get("metrics", {})
                        }
            safe_payload = EvaluationReportStore.safe_quality_loop_payload(payload)
            if safe_payload is None or not check_quality_loop_payload_privacy(
                safe_payload,
                cases,
                additional_forbidden_values=extra_forbidden_values,
            ):
                return self._quality_loop_unavailable_payload(
                    cases, reason=REASON_CANARY
                )
            return safe_payload
        except asyncio.CancelledError:
            raise
        except QualityLoopPairError as error:
            return self._quality_loop_unavailable_payload(cases, reason=error.code)
        except Exception:
            return self._quality_loop_unavailable_payload(
                cases, reason=REASON_RETRIEVAL_FAILED
            )
        finally:
            if run_engine is not None:
                await self._clear_evaluation_caches(run_engine)

    @classmethod
    def _redact_config(cls, config: Mapping[str, Any]) -> dict[str, Any]:
        """递归隐藏配置中的密钥类字段。"""

        redacted: dict[str, Any] = {}
        for key, value in config.items():
            key_text = str(key)
            if any(
                token in key_text.lower()
                for token in ("key", "token", "secret", "password")
            ):
                redacted[key_text] = "<已隐藏>"
            elif isinstance(value, Mapping):
                redacted[key_text] = cls._redact_config(value)
            else:
                redacted[key_text] = value
        return redacted

    @staticmethod
    def _report_cases(report: EvaluationReport) -> list[dict[str, Any]]:
        """把逐用例 dataclass 规范化为可持久化 JSON 值。"""

        return [EvaluationReportStore.safe_case_payload(case) for case in report.cases]

    @classmethod
    def _variant_deltas(
        cls,
        reports: Mapping[str, EvaluationReport],
        baseline_name: str,
    ) -> dict[str, dict[str, float | None]]:
        """计算各已完成变体相对 baseline 的指标差值。"""

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
    def _metric_value(
        report_or_summary: Mapping[str, Any], metric: str
    ) -> float | None:
        """将可选指标安全转换为浮点数。"""

        value = report_or_summary.get(metric)
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _metric_delta(a: float | None, b: float | None) -> float | None:
        """返回 B-A 差值；任一值缺失时保持未知。"""

        if a is None or b is None:
            return None
        return round(b - a, 4)

    @staticmethod
    def _report_compare_summary(report: Mapping[str, Any]) -> dict[str, Any]:
        """提取报告对比端点需要的最小摘要。"""

        return {
            "report_id": report.get("report_id"),
            "created_at": report.get("created_at"),
            "baseline": report.get("baseline"),
            "summary": dict(report.get("summary", {}) or {}),
        }


__all__ = ["EvaluationService"]
