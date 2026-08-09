"""用户画像 proposal 管线的兼容导出。"""

from ..features.profiles.application.profile_proposal_pipeline import (
    ProfileProposalPipeline,
    trusted_profile_subject_id,
)

__all__ = ["ProfileProposalPipeline", "trusted_profile_subject_id"]
