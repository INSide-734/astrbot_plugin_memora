"""notes feature 的应用端口。

端口只描述笔记应用层需要的能力，不绑定 SQLite、Provider 或旧技术层实现。
组合根可以把迁移期间的旧对象注入这些协议，而不会在 application 层复制实现。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from ...shared.contracts import MemorySourceRef
from .domain.models import Note, NoteVersion


@runtime_checkable
class NoteStorePort(Protocol):
    """笔记应用服务所需的持久化最小接口。"""

    async def create(self, note: Note) -> int:
        """创建笔记及首个版本并返回内部 ID。"""
        ...

    async def get(self, note_id: int) -> Note | None:
        """按内部 ID 读取可见笔记。"""
        ...

    async def update(self, note: Note) -> bool:
        """按 revision/CAS 更新笔记并追加版本。"""
        ...

    async def delete(self, note_id: int) -> bool:
        """按内部 ID 删除笔记及版本历史。"""
        ...

    async def search(
        self,
        query: str,
        limit: int = 20,
    ) -> tuple[list[Note], int]:
        """按关键词搜索可见笔记。"""
        ...

    async def filter_current_sources(self, notes: list[Note]) -> list[Note]:
        """过滤来源已失效的派生笔记。"""
        ...

    async def list_notes(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str = "",
    ) -> tuple[list[Note], int]:
        """按状态分页列出可见笔记。"""
        ...

    async def get_versions(self, note_id: int) -> list[NoteVersion]:
        """按版本倒序返回笔记历史。"""
        ...

    async def count(self) -> int:
        """返回未删除笔记总数。"""
        ...

    async def prune_versions(self, max_versions: int = 20) -> int:
        """为每条笔记裁剪超过上限的旧版本。"""
        ...


@runtime_checkable
class NoteSourceReaderPort(Protocol):
    """自动笔记 proposal 使用的 canonical source 读取接口。"""

    async def load_sources(
        self,
        memory_ids: Sequence[int],
        *,
        max_content_chars: int = 4_000,
    ) -> list[MemorySourceRef]:
        """按 canonical ID 返回带 revision、作用域和隐私证据的 source。"""
        ...

    async def load_all_sources(
        self,
        *,
        max_content_chars: int = 4_000,
    ) -> list[MemorySourceRef]:
        """按 canonical ID 顺序返回全部可用 source。"""
        ...


@runtime_checkable
class NoteGeneratorPort(Protocol):
    """自动笔记 proposal 使用的结构化生成接口。"""

    async def generate(
        self,
        conversation_text: str,
        context: str = "",
    ) -> dict[str, Any] | None:
        """从有限 canonical evidence 生成一个笔记候选。"""
        ...


__all__ = [
    "NoteGeneratorPort",
    "NoteSourceReaderPort",
    "NoteStorePort",
]
