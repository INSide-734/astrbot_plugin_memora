"""反馈排序离线消融应用实现的旧路径兼容导出。"""

from ..features.evaluation.application import (
    feedback_ranking_ablation as _feature_feedback_ranking,
)

build_feedback_ranking_replay_manifest = (
    _feature_feedback_ranking.build_feedback_ranking_replay_manifest
)
feedback_ranking_case_hash = _feature_feedback_ranking.feedback_ranking_case_hash
FeedbackRankingConfigSnapshot = _feature_feedback_ranking.FeedbackRankingConfigSnapshot
FeedbackRankingEvidenceRequest = (
    _feature_feedback_ranking.FeedbackRankingEvidenceRequest
)
FeedbackRankingMetrics = _feature_feedback_ranking.FeedbackRankingMetrics
FeedbackRankingPairedSample = _feature_feedback_ranking.FeedbackRankingPairedSample
FeedbackRankingReplayManifest = _feature_feedback_ranking.FeedbackRankingReplayManifest
FeedbackRankingReport = _feature_feedback_ranking.FeedbackRankingReport
run_feedback_ranking_ablation = _feature_feedback_ranking.run_feedback_ranking_ablation

__all__ = [
    "build_feedback_ranking_replay_manifest",
    "feedback_ranking_case_hash",
    "FeedbackRankingConfigSnapshot",
    "FeedbackRankingEvidenceRequest",
    "FeedbackRankingMetrics",
    "FeedbackRankingPairedSample",
    "FeedbackRankingReplayManifest",
    "FeedbackRankingReport",
    "run_feedback_ranking_ablation",
]
