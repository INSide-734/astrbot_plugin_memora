"""knowledge feature 的应用端口。

端口只描述知识应用层需要的能力，不绑定 SQLite、Provider 或旧技术层实现。
组合根可以把迁移期间的旧对象注入这些协议，而不会在 application 层复制实现。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ...base.list_sorting import SortQuery
from ...shared.contracts import MemorySourceRef
from .domain.models import KnowledgeEntry


@runtime_checkable
class KnowledgeStorePort(Protocol):
    """知识应用服务所需的持久化最小接口。"""

    async def insert(self, entry: KnowledgeEntry) -> int:
        """插入知识条目并返回内部 ID。"""

    async def get(self, entry_id: int) -> KnowledgeEntry | None:
        """按内部 ID 读取可见知识条目。"""

    async def search(
        self,
        query: str,
        limit: int = 20,
        category: str = "",
        sort: SortQuery = SortQuery("updated_at", "desc"),
    ) -> tuple[list[KnowledgeEntry], int]:
        """按关键词分页搜索知识条目。"""

    async def search_merge_candidates(
        self,
        query: str,
        limit: int = 5,
    ) -> list[KnowledgeEntry]:
        """返回去重合并候选。"""

    async def list_entries(
        self,
        limit: int = 50,
        offset: int = 0,
        category: str = "",
        sort: SortQuery = SortQuery("updated_at", "desc"),
    ) -> tuple[list[KnowledgeEntry], int]:
        """按稳定排序分页列出知识条目。"""

    async def filter_current_sources(
        self,
        entries: list[KnowledgeEntry],
    ) -> list[KnowledgeEntry]:
        """过滤来源已失效的派生知识。"""

    async def update(self, entry: KnowledgeEntry) -> None:
        """更新知识条目并重新校验来源。"""

    async def delete(self, entry_id: int) -> bool:
        """按内部 ID 删除知识条目。"""

    async def count(self) -> int:
        """返回知识条目总数。"""


@runtime_checkable
class KnowledgeSourceReaderPort(Protocol):
    """知识 proposal 使用的 canonical source 读取接口。"""

    async def load_sources(
        self,
        memory_ids: Sequence[int],
        *,
        max_content_chars: int = 4_000,
    ) -> list[MemorySourceRef]:
        """按 canonical ID 返回带 revision、作用域和隐私证据的 source。"""


@runtime_checkable
class KnowledgeExtractorPort(Protocol):
    """知识 proposal 使用的结构化抽取接口。"""

    async def extract(
        self,
        memory_content: str,
        memory_type: str = "",
    ) -> KnowledgeEntry | None:
        """从有限 canonical evidence 抽取一个知识条目。"""


__all__ = [
    "KnowledgeExtractorPort",
    "KnowledgeSourceReaderPort",
    "KnowledgeStorePort",
]
