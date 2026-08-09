"""profiles feature 的领域模型所有权与旧路径兼容契约。"""

from core.features.profiles.domain.models import (
    TagCategory,
    UserPreferences,
    UserProfile,
    UserTag,
)
from core.features.profiles.infrastructure.profile_store import (
    PROFILE_SORT_COLUMNS,
    ProfileStore,
)
from core.models.user_profile import (
    TagCategory as LegacyTagCategory,
)
from core.models.user_profile import (
    UserPreferences as LegacyUserPreferences,
)
from core.models.user_profile import (
    UserProfile as LegacyUserProfile,
)
from core.models.user_profile import (
    UserTag as LegacyUserTag,
)
from core.storage.profile_queries import PROFILE_LIST_SQL as LEGACY_PROFILE_LIST_SQL
from core.storage.profile_store import (
    PROFILE_SORT_COLUMNS as LEGACY_PROFILE_SORT_COLUMNS,
)
from core.storage.profile_store import (
    ProfileStore as LegacyProfileStore,
)


def test_legacy_profile_model_imports_reuse_feature_types() -> None:
    """旧画像模型路径只能导出 profiles feature 的唯一实现。"""

    assert LegacyTagCategory is TagCategory
    assert LegacyUserPreferences is UserPreferences
    assert LegacyUserProfile is UserProfile
    assert LegacyUserTag is UserTag


def test_legacy_profile_store_import_reuses_feature_implementation() -> None:
    """旧 Store 路径只能导出 profiles feature 的唯一实现。"""

    assert LegacyProfileStore is ProfileStore
    assert LEGACY_PROFILE_SORT_COLUMNS is PROFILE_SORT_COLUMNS
    assert LEGACY_PROFILE_LIST_SQL
