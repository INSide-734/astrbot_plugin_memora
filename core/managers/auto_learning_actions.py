"""自主学习领域动作的兼容导出。"""

from ..features.learning.domain.auto_learning_actions import (
    CandidateBinding,
    GlobalLearningCandidate,
    aggregation_revision_for,
    is_opaque_id,
    new_opaque_id,
    normalize_weights,
    reduce_global_candidate,
    stable_revision,
    weight_snapshot_hash,
)

__all__ = [
    "aggregation_revision_for",
    "CandidateBinding",
    "GlobalLearningCandidate",
    "is_opaque_id",
    "new_opaque_id",
    "normalize_weights",
    "reduce_global_candidate",
    "stable_revision",
    "weight_snapshot_hash",
]
