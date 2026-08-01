"""Evaluation helpers for Memora quality baselines."""

from .report_store import EvaluationReportStore
from .retrieval_quality import (
    AblationReport,
    EvaluationCase,
    EvaluationReport,
    EvaluationResult,
    RetrievalObservation,
    RetrievedDocument,
    VariantComparison,
    compare_reports,
    evaluate_cases,
    evaluate_variants,
    load_fixture_dir,
    load_jsonl_cases,
    make_memory_engine_retriever,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
)

__all__ = [
    "EvaluationCase",
    "EvaluationReport",
    "EvaluationResult",
    "RetrievedDocument",
    "RetrievalObservation",
    "AblationReport",
    "VariantComparison",
    "compare_reports",
    "evaluate_cases",
    "evaluate_variants",
    "load_fixture_dir",
    "load_jsonl_cases",
    "make_memory_engine_retriever",
    "ndcg_at_k",
    "recall_at_k",
    "reciprocal_rank",
    "EvaluationReportStore",
]
