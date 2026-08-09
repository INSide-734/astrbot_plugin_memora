"""profiles feature 的领域模型所有权与旧路径兼容契约。"""

from core.features.profiles.domain.models import (
    TagCategory,
    UserPreferences,
    UserProfile,
    UserTag,
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


def test_legacy_profile_model_imports_reuse_feature_types() -> None:
    """旧画像模型路径只能导出 profiles feature 的唯一实现。"""

    assert LegacyTagCategory is TagCategory
    assert LegacyUserPreferences is UserPreferences
    assert LegacyUserProfile is UserProfile
    assert LegacyUserTag is UserTag
