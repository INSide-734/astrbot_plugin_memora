"""profiles feature 的领域模型所有权与旧路径兼容契约。"""

from core.features.profiles.application import (
    ProfileManager,
    ProfileProposalPipeline,
    trusted_profile_subject_id,
)
from core.features.profiles.contracts import (
    ProfileExtractorPort,
    ProfileSourceReaderPort,
    ProfileStorePort,
)
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
from core.managers.profile_manager import ProfileManager as LegacyProfileManager
from core.managers.profile_proposal_pipeline import (
    ProfileProposalPipeline as LegacyProfileProposalPipeline,
)
from core.managers.profile_proposal_pipeline import (
    trusted_profile_subject_id as legacy_trusted_profile_subject_id,
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
from core.processors.profile_extractor import ProfileExtractor
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


def test_legacy_profile_manager_import_reuses_feature_implementation() -> None:
    """旧 Manager 路径只能导出 profiles application 的唯一实现。"""

    assert LegacyProfileManager is ProfileManager


def test_legacy_profile_pipeline_import_reuses_feature_implementation() -> None:
    """旧 proposal 路径只能导出 profiles application 的唯一实现。"""

    assert LegacyProfileProposalPipeline is ProfileProposalPipeline
    assert legacy_trusted_profile_subject_id is trusted_profile_subject_id


def test_profile_ports_accept_existing_implementations_structurally() -> None:
    """迁移端口应能接收现有 Store、source reader 和 extractor 实现。"""

    class SourceReader:
        async def load_sources(self, memory_ids, *, max_content_chars=4_000):
            return []

    assert isinstance(ProfileStore(":memory:"), ProfileStorePort)
    assert isinstance(SourceReader(), ProfileSourceReaderPort)
    assert isinstance(ProfileExtractor(), ProfileExtractorPort)
