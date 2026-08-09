"""用户画像领域模型的兼容导出。

唯一实现位于 ``core.features.profiles.domain``；本模块保留旧导入路径，
避免迁移期间生成第二套模型类型。
"""

if __package__:
    from ..features.profiles.domain.models import (
        TagCategory,
        UserPreferences,
        UserProfile,
        UserTag,
    )
else:
    from core.features.profiles.domain.models import (
        TagCategory,
        UserPreferences,
        UserProfile,
        UserTag,
    )

__all__ = ["UserProfile", "UserTag", "UserPreferences", "TagCategory"]
