"""检索质量评测应用实现的旧路径兼容导出。"""

from ..features.evaluation.application import retrieval_quality as _feature_retrieval

AblationReport = _feature_retrieval.AblationReport
EvaluationCase = _feature_retrieval.EvaluationCase
EvaluationReport = _feature_retrieval.EvaluationReport
EvaluationResult = _feature_retrieval.EvaluationResult
RetrieverFn = _feature_retrieval.RetrieverFn
RetrieverValue = _feature_retrieval.RetrieverValue
RetrievalObservation = _feature_retrieval.RetrievalObservation
RetrievedDocument = _feature_retrieval.RetrievedDocument
VariantComparison = _feature_retrieval.VariantComparison
compare_reports = _feature_retrieval.compare_reports
evaluate_cases = _feature_retrieval.evaluate_cases
evaluate_variants = _feature_retrieval.evaluate_variants
load_fixture_dir = _feature_retrieval.load_fixture_dir
load_jsonl_cases = _feature_retrieval.load_jsonl_cases
make_memory_engine_retriever = _feature_retrieval.make_memory_engine_retriever
ndcg_at_k = _feature_retrieval.ndcg_at_k
recall_at_k = _feature_retrieval.recall_at_k
reciprocal_rank = _feature_retrieval.reciprocal_rank

__all__ = [
    "AblationReport",
    "EvaluationCase",
    "EvaluationReport",
    "EvaluationResult",
    "RetrieverFn",
    "RetrieverValue",
    "RetrievalObservation",
    "RetrievedDocument",
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
]
