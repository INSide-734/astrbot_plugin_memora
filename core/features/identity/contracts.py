"""身份 feature 的应用端口。

应用服务只依赖本模块定义的目录能力；SQLite 连接、事务和表结构由基础设施
实现负责，组合根可以在不改变名称业务规则的情况下替换实现。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from .domain.models import (
    IdentityMerger,
    IdentityObservation,
    StoredIdentity,
)


@runtime_checkable
class IdentityDirectoryPort(Protocol):
    """身份目录应用层需要的读写能力。"""

    async def get_identity(
        self,
        identity_namespace: str,
        stable_user_id: str,
        scope_type: str | None = None,
        scope_id: str | None = None,
    ) -> StoredIdentity | None:
        """按稳定身份和可选作用域读取当前目录记录。"""

        ...

    async def find_aliases(
        self,
        identity_namespace: str,
        stable_user_id: str,
        scope_type: str,
        scope_id: str,
        limit: int = 128,
    ) -> list[str]:
        """读取指定身份作用域内的历史别名。"""

        ...

    async def find_alias_owner_ids(
        self,
        identity_namespace: str,
        alias: str,
        scope_type: str,
        scope_id: str,
        *,
        member_scope_type: str | None = None,
        member_scope_id: str | None = None,
        limit: int = 2,
    ) -> list[str]:
        """按精确别名反查稳定身份，并可限定当前成员作用域。"""

        ...

    async def record_aliases(
        self,
        identity_namespace: str,
        stable_user_id: str,
        aliases: Iterable[tuple[str, str, str]],
        created_at: float | None = None,
    ) -> int:
        """幂等记录已验证的历史别名，返回新增行数。"""

        ...

    async def merge_observation(
        self,
        identity: IdentityObservation,
        merger: IdentityMerger,
    ) -> StoredIdentity:
        """在一个持久化事务中应用身份观察合并计划。"""

        ...


__all__ = ["IdentityDirectoryPort"]
