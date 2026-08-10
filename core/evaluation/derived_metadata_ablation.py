"""派生元数据离线消融应用实现的旧路径兼容导出。"""

from ..features.evaluation.application import (
    derived_metadata_ablation as _feature_derived_metadata,
)

DERIVED_INDEX_REASON_CODES = _feature_derived_metadata.DERIVED_INDEX_REASON_CODES
DerivedMetadataBranchMetrics = _feature_derived_metadata.DerivedMetadataBranchMetrics
DerivedMetadataIndexSummary = _feature_derived_metadata.DerivedMetadataIndexSummary
DerivedMetadataMatch = _feature_derived_metadata.DerivedMetadataMatch
DerivedMetadataReport = _feature_derived_metadata.DerivedMetadataReport
RunLocalDerivedMetadataIndex = _feature_derived_metadata.RunLocalDerivedMetadataIndex
run_derived_metadata_ablation = _feature_derived_metadata.run_derived_metadata_ablation

__all__ = [
    "DERIVED_INDEX_REASON_CODES",
    "DerivedMetadataBranchMetrics",
    "DerivedMetadataIndexSummary",
    "DerivedMetadataMatch",
    "DerivedMetadataReport",
    "RunLocalDerivedMetadataIndex",
    "run_derived_metadata_ablation",
]
