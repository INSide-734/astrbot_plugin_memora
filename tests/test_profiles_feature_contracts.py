"""profiles feature 的分层所有权与端口契约。"""

from collections.abc import Sequence

import core.features.profiles as profile_feature
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
from core.features.profiles.infrastructure import ProfileExtractor
from core.features.profiles.infrastructure.profile_queries import PROFILE_LIST_SQL
from core.features.profiles.infrastructure.profile_store import (
    PROFILE_SORT_COLUMNS,
    ProfileStore,
)
from core.shared.contracts import MemorySourceRef


def test_profile_feature_exports_domain_owner() -> None:
    """feature 包级入口应恒等导出领域模型的唯一实现。"""

    assert profile_feature.TagCategory is TagCategory
    assert profile_feature.UserPreferences is UserPreferences
    assert profile_feature.UserProfile is UserProfile
    assert profile_feature.UserTag is UserTag


def test_profile_feature_exports_store_owner() -> None:
    """feature 包级入口应恒等导出 Store，并保留查询契约。"""

    assert profile_feature.ProfileStore is ProfileStore
    assert profile_feature.PROFILE_SORT_COLUMNS is PROFILE_SORT_COLUMNS
    assert "user_profiles" in PROFILE_LIST_SQL


def test_profile_feature_exports_manager_owner() -> None:
    """feature 包级入口应恒等导出应用 Manager。"""

    assert profile_feature.ProfileManager is ProfileManager


def test_profile_feature_exports_pipeline_owner() -> None:
    """feature 包级入口应恒等导出 proposal 管线及稳定身份助手。"""

    assert profile_feature.ProfileProposalPipeline is ProfileProposalPipeline
    assert profile_feature.trusted_profile_subject_id is trusted_profile_subject_id


def test_profile_feature_exports_extractor_owner() -> None:
    """feature 包级入口应恒等导出画像抽取器。"""

    assert profile_feature.ProfileExtractor is ProfileExtractor


def test_profile_ports_accept_existing_implementations_structurally() -> None:
    """迁移端口应能接收现有 Store、source reader 和 extractor 实现。"""

    class SourceReader:
        """提供只返回空集合的来源读取测试替身。"""

        async def load_sources(
            self,
            memory_ids: Sequence[int],
            *,
            max_content_chars: int = 4_000,
        ) -> list[MemorySourceRef]:
            """返回空来源集合以验证端口结构兼容性。

            Args:
                memory_ids: 待读取的 canonical memory ID。
                max_content_chars: 单次读取允许的最大正文字符数。

            Returns:
                空来源集合。
            """

            return []

    store_port: ProfileStorePort = ProfileStore(":memory:")
    source_reader_port: ProfileSourceReaderPort = SourceReader()
    extractor_port: ProfileExtractorPort = ProfileExtractor()

    assert isinstance(store_port, ProfileStorePort)
    assert isinstance(source_reader_port, ProfileSourceReaderPort)
    assert isinstance(extractor_port, ProfileExtractorPort)
