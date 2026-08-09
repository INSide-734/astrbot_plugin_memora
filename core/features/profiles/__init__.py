"""用户画像 feature 的公开边界。"""

from .application import (
    ProfileManager,
    ProfileProposalPipeline,
    trusted_profile_subject_id,
)
from .contracts import (
    ProfileExtractorPort,
    ProfileSourceReaderPort,
    ProfileStorePort,
)
from .domain import TagCategory, UserPreferences, UserProfile, UserTag
from .infrastructure import PROFILE_SORT_COLUMNS, ProfileExtractor, ProfileStore

__all__ = [
    "PROFILE_SORT_COLUMNS",
    "ProfileExtractorPort",
    "ProfileExtractor",
    "ProfileManager",
    "ProfileProposalPipeline",
    "ProfileSourceReaderPort",
    "ProfileStorePort",
    "ProfileStore",
    "TagCategory",
    "UserPreferences",
    "UserProfile",
    "UserTag",
    "trusted_profile_subject_id",
]
