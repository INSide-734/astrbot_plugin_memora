"""图记忆实体规范化工具。

G3: 实体层级 — IS-A 树，搜索"宠物"时自动沿层级扩展找到"旺财"。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any


class EntityResolver:
    """Provide lightweight canonicalization + G3 IS-A hierarchy for graph entities."""

    _whitespace_re = re.compile(r"\s+")
    _edge_punctuation_re = re.compile(
        r"^[\s,.;:!?'\"，。；：！？、（）()\[\]{}<>《》]+|[\s,.;:!?'\"，。；：！？、（）()\[\]{}<>《》]+$"
    )

    # G3: IS-A 层级 — parent_canonical → set of child_canonical
    _isa_children: dict[str, set[str]] = {}
    _isa_parents: dict[str, str] = {}

    @classmethod
    def canonicalize(cls, value: str) -> str:
        """Normalize an entity string for deduplication."""
        if not value:
            return ""
        normalized = cls._edge_punctuation_re.sub("", value.strip())
        normalized = cls._whitespace_re.sub(" ", normalized)
        if normalized.isascii():
            normalized = normalized.lower()
        return normalized

    @classmethod
    def dedupe_preserve_order(cls, values: list[str]) -> list[str]:
        """Return unique values while preserving the original order."""
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            canonical = cls.canonicalize(value)
            if not canonical or canonical in seen:
                continue
            seen.add(canonical)
            result.append(value.strip())
        return result

    # ---- G3: IS-A 实体层级 ----

    @classmethod
    def add_isa(cls, child: str, parent: str) -> None:
        """注册 IS-A 关系：child IS-A parent（如 旺财 IS-A 宠物）。"""
        child_c = cls.canonicalize(child)
        parent_c = cls.canonicalize(parent)
        if not child_c or not parent_c or child_c == parent_c:
            return
        cls._isa_children.setdefault(parent_c, set()).add(child_c)
        cls._isa_parents[child_c] = parent_c

    @classmethod
    def expand_with_children(cls, entity: str) -> list[str]:
        """沿 IS-A 树向下扩展：输入"宠物"，返回["宠物", "旺财"]。"""
        canonical = cls.canonicalize(entity)
        if not canonical:
            return []
        result = [entity]
        visited: set[str] = {canonical}
        stack = [canonical]
        while stack:
            current = stack.pop()
            for child in cls._isa_children.get(current, set()):
                if child not in visited:
                    visited.add(child)
                    result.append(child)
                    stack.append(child)
        return result

    @classmethod
    def expand_with_parents(cls, entity: str) -> list[str]:
        """沿 IS-A 树向上扩展：输入"旺财"，返回["旺财", "宠物", "动物"]。"""
        canonical = cls.canonicalize(entity)
        if not canonical:
            return []
        result = [entity]
        current = canonical
        visited: set[str] = {canonical}
        while current in cls._isa_parents:
            parent = cls._isa_parents[current]
            if parent in visited:
                break
            visited.add(parent)
            result.append(parent)
            current = parent
        return result

    @classmethod
    def get_hierarchy_stats(cls) -> dict[str, Any]:
        return {
            "total_relations": sum(len(v) for v in cls._isa_children.values()),
            "parent_count": len(cls._isa_children),
            "child_count": len(cls._isa_parents),
        }

    @classmethod
    async def load_hierarchy(cls, data_dir: str = "") -> None:
        """异步加载 IS-A 层级关系 JSON 文件。"""
        if not data_dir:
            return
        try:
            path = os.path.join(data_dir, "entity_hierarchy.json")
            if not os.path.exists(path):
                return

            def _read():
                with open(path, encoding="utf-8") as f:
                    return json.load(f)

            data = await asyncio.to_thread(_read)
            cls._isa_children.clear()
            cls._isa_parents.clear()
            for parent, children in (data.get("relations", {}) or {}).items():
                for child in children:
                    cls.add_isa(child, parent)
        except Exception:
            pass

    @classmethod
    async def save_hierarchy(cls, data_dir: str = "") -> None:
        """异步保存 IS-A 层级关系 JSON 文件。"""
        if not data_dir:
            return
        try:
            await asyncio.to_thread(os.makedirs, data_dir, exist_ok=True)
            path = os.path.join(data_dir, "entity_hierarchy.json")
            data = {
                "relations": {p: sorted(c) for p, c in cls._isa_children.items()},
            }

            def _write():
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

            await asyncio.to_thread(_write)
        except OSError:
            pass


__all__ = ["EntityResolver"]
