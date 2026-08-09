"""profiles feature 的应用端口。

端口只描述画像应用层需要的能力，不绑定 SQLite、Provider 或旧技术层实现。
组合根可以把迁移期间的旧对象注入这些协议，而不会在 application 层复制实现。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from core.models.domain_provenance import DomainProvenance
from core.shared.contracts import MemorySourceRef

from .domain.models import UserPreferences, UserProfile, UserTag


@runtime_checkable
class ProfileStorePort(Protocol):
    """画像应用服务所需的持久化最小接口。"""

    async def get_or_create_profile(self, user_id: str) -> UserProfile:
        """读取画像，不存在时创建空画像。"""

    async def get_profile(self, user_id: str) -> UserProfile | None:
        """按稳定用户 ID 读取画像。"""

    async def touch(self, user_id: str) -> None:
        """更新画像最近活动时间。"""

    async def create_profile_strict(
        self,
        user_id: str,
        display_name: str = "",
        preferences: UserPreferences | None = None,
        tags: list[UserTag] | None = None,
    ) -> UserProfile:
        """在单个事务中创建人工画像。"""

    async def replace_editable_fields(
        self,
        user_id: str,
        *,
        display_name: str,
        preferences: UserPreferences,
        tags: list[UserTag],
        expected_revision: str,
    ) -> UserProfile:
        """按 revision/CAS 替换人工可写字段。"""

    async def delete_profile_if_revision(
        self,
        user_id: str,
        *,
        expected_revision: str,
    ) -> bool:
        """按 revision/CAS 删除画像。"""

    async def update_profile_fields_atomic(
        self,
        user_id: str,
        *,
        display_name: str | None = None,
        preferences: UserPreferences | None = None,
    ) -> UserProfile | None:
        """原子更新显式人工字段。"""

    async def delete_profile(self, user_id: str) -> bool:
        """删除画像及其标签。"""

    async def add_tag(self, user_id: str, tag: UserTag) -> None:
        """添加单个标签。"""

    async def remove_tag(self, user_id: str, category: str, value: str) -> None:
        """删除单个标签。"""

    async def upsert_tags_atomic(
        self,
        user_id: str,
        tags: list[UserTag],
    ) -> tuple[UserProfile | None, int]:
        """在一个事务中合并自动标签。"""

    async def decay_and_clean_tags_atomic(self, user_id: str) -> int:
        """衰减并清理低置信度标签。"""

    async def record_message_atomic(
        self,
        user_id: str,
        *,
        message_length: int = 0,
    ) -> UserProfile | None:
        """原子递增消息统计。"""

    async def merge_preferences_atomic(
        self,
        user_id: str,
        preferences_update: dict[str, Any],
        *,
        provenance: DomainProvenance | None = None,
    ) -> UserProfile | None:
        """合并自动偏好并保留来源证据。"""

    async def list_profiles(
        self,
        limit: int = 50,
        offset: int = 0,
        sort: Any = None,
    ) -> tuple[list[UserProfile], int]:
        """按稳定排序分页列出画像。"""


@runtime_checkable
class ProfileSourceReaderPort(Protocol):
    """画像 proposal 使用的 canonical source 读取接口。"""

    async def load_sources(
        self,
        memory_ids: Sequence[int],
        *,
        max_content_chars: int = 4_000,
    ) -> list[MemorySourceRef]:
        """按 canonical ID 返回带 revision 和隐私证据的 source。"""


@runtime_checkable
class ProfileExtractorPort(Protocol):
    """画像 proposal 使用的结构化抽取接口。"""

    async def extract(
        self,
        user_message: str,
        bot_response: str = "",
        context: str = "",
    ) -> tuple[list[UserTag], dict[str, Any]]:
        """从文本证据抽取标签和偏好。"""

    def extract_keywords_fallback(self, user_message: str) -> list[UserTag]:
        """在 Provider 不可用时提取有限的显式关键词标签。"""


__all__ = [
    "ProfileExtractorPort",
    "ProfileSourceReaderPort",
    "ProfileStorePort",
]
