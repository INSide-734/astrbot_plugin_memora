"""用户画像 feature 的公开领域边界。"""

from .contracts import (
    ProfileExtractorPort,
    ProfileSourceReaderPort,
    ProfileStorePort,
)
from .domain import TagCategory, UserPreferences, UserProfile, UserTag
from .infrastructure import PROFILE_SORT_COLUMNS, ProfileStore

__all__ = [
    "PROFILE_SORT_COLUMNS",
    "ProfileExtractorPort",
    "ProfileSourceReaderPort",
    "ProfileStorePort",
    "ProfileStore",
    "TagCategory",
    "UserPreferences",
    "UserProfile",
    "UserTag",
]
