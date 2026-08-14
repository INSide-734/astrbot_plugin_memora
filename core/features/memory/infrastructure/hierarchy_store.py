"""G3: 实体层级存储 — 带环检测的 IS-A 树。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from astrbot.api import logger

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
        if await self._would_create_cycle(child, parent):
            logger.warning(f"[Hierarchy] Cycle detected: {child} -> {parent}")
            return False
        await self._db.execute(
            "INSERT OR IGNORE INTO entity_hierarchy (child, parent) VALUES (?, ?)",
            (child, parent),
        )
        await self._db.commit()
        return True

    async def get_parents(self, child: str) -> list[str]:
        cursor = await self._db.execute(
            "SELECT parent FROM entity_hierarchy WHERE child = ?",
            (child,),
        )
        return [row[0] for row in await cursor.fetchall()]

    async def get_ancestors(self, child: str, max_depth: int = 5) -> set[str]:
        seen: set[str] = set()
        current = {child}
        for _ in range(max_depth):
            if not current:
                break
            next_level: set[str] = set()
            for c in current:
                for p in await self.get_parents(c):
                    if p not in seen:
                        seen.add(p)
                        next_level.add(p)
            current = next_level
        return seen

    async def _would_create_cycle(self, child: str, parent: str) -> bool:
        ancestors = await self.get_ancestors(parent, max_depth=50)
        return child in ancestors

    async def detect_cycle(self) -> list[tuple[str, str]]:
        cycles: list[tuple[str, str]] = []
        cursor = await self._db.execute("SELECT child, parent FROM entity_hierarchy")
        for child, parent in await cursor.fetchall():
            if child in await self.get_ancestors(parent, max_depth=50):
                cycles.append((child, parent))
        return cycles


__all__ = ["EntityHierarchyStore"]
