"""G3: 实体层级存储 — 带环检测的 IS-A 树。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from astrbot.api import logger

from ..application.write_coordinator import coordinated_transaction

if TYPE_CHECKING:
    import aiosqlite


class EntityHierarchyStore:
    """管理 entity_hierarchy 表，支持 IS-A 关系与环检测。"""

    def __init__(self, db_connection: aiosqlite.Connection) -> None:
        self._db = db_connection

    async def init_table(self) -> None:
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS entity_hierarchy (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                child TEXT NOT NULL,
                parent TEXT NOT NULL,
                UNIQUE(child, parent)
            )
        """)
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_hierarchy_child ON entity_hierarchy(child)"
        )
        await self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_hierarchy_parent "
            "ON entity_hierarchy(parent)"
        )
        await self._db.commit()

    async def add_relation(self, child: str, parent: str) -> bool:
        """添加 IS-A 关系（child is-a parent）。若检测到环则返回 False。"""
        if child == parent:
            return False
        async with coordinated_transaction(self._db) as db:
            # 环检测必须与插入在同一写事务内，否则并发的反向关系会同时通过检查。
            if await self._would_create_cycle(child, parent):
                logger.warning(f"[Hierarchy] Cycle detected: {child} -> {parent}")
                return False
            await db.execute(
                "INSERT OR IGNORE INTO entity_hierarchy (child, parent) VALUES (?, ?)",
                (child, parent),
            )
        return True

    async def get_parents(self, child: str) -> list[str]:
        cursor = await self._db.execute(
            "SELECT parent FROM entity_hierarchy WHERE child = ?",
            (child,),
        )
        return [row[0] for row in await cursor.fetchall()]

    async def get_ancestors(self, child: str, max_depth: int | None = 5) -> set[str]:
        """返回祖先集合；``max_depth=None`` 表示遍历到不再出现新祖先为止。"""
        seen: set[str] = set()
        current = {child}
        depth = 0
        while current and (max_depth is None or depth < max_depth):
            next_level: set[str] = set()
            for c in current:
                for p in await self.get_parents(c):
                    if p not in seen:
                        seen.add(p)
                        next_level.add(p)
            current = next_level
            depth += 1
        return seen

    async def _would_create_cycle(self, child: str, parent: str) -> bool:
        # 不设深度上限：超过固定深度的长链同样可能被新边闭合成环；
        # seen 集合保证即使库内已有环也会终止。
        ancestors = await self.get_ancestors(parent, max_depth=None)
        return child in ancestors

    async def detect_cycle(self) -> list[tuple[str, str]]:
        cycles: list[tuple[str, str]] = []
        cursor = await self._db.execute("SELECT child, parent FROM entity_hierarchy")
        for child, parent in await cursor.fetchall():
            if child in await self.get_ancestors(parent, max_depth=None):
                cycles.append((child, parent))
        return cycles


__all__ = ["EntityHierarchyStore"]
