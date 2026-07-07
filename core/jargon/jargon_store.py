"""Jargon 持久化存储 — 基于 BaseStore 的 SQLite 后端。

与 Memora 现有存储模式一致：使用 aiosqlite 进行持久化。
搜索使用 LIKE 查询（足够用于黑话字段的模糊匹配）。
"""

from __future__ import annotations

import json
import time
from typing import Any

from astrbot.api import logger

from ..storage.base_store import BaseStore
from .models import JargonMeaning


# ---------------------------------------------------------------------------
# 表结构
# ---------------------------------------------------------------------------

_JARGON_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS jargon_terms (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    term        TEXT    NOT NULL,
    group_id    TEXT    NOT NULL,
    meaning     TEXT    DEFAULT '',
    confidence  REAL    DEFAULT 0.0,
    is_jargon   INTEGER DEFAULT 0,
    is_confirmed INTEGER DEFAULT 0,
    is_global   INTEGER DEFAULT 0,
    is_complete INTEGER DEFAULT 0,
    count       INTEGER DEFAULT 0,
    last_inference_count INTEGER DEFAULT 0,
    context_examples TEXT DEFAULT '[]',
    created_at  REAL    DEFAULT 0.0,
    updated_at  REAL    DEFAULT 0.0,
    UNIQUE(term, group_id)
);
"""

_JARGON_TERM_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_jargon_terms_group "
    "ON jargon_terms(group_id, term);"
)


# ---------------------------------------------------------------------------
# JargonStore
# ---------------------------------------------------------------------------


class JargonStore(BaseStore):
    """黑话持久化存储。

    管理 ``jargon_terms`` 表的 CRUD 操作。
    继承自 :class:`BaseStore` 的 per-instance connection 模式。
    """

    def __init__(self, db_path: str) -> None:
        super().__init__(db_path)
        self._initialized = False

    # ---- 生命周期 ---------------------------------------------------------------

    async def _create_tables(self) -> None:
        """创建表结构。"""
        await self._execute(_JARGON_TABLE_SQL)
        await self._execute(_JARGON_TERM_INDEX)
        await self._commit()

    async def initialize(self) -> None:
        """打开连接并创建表（幂等）。"""
        if self._initialized:
            return
        await super().initialize()
        await self._create_tables()
        self._initialized = True
        logger.info("[JargonStore] 数据库初始化完成")

    # ---- 上下文序列化 -----------------------------------------------------------

    @staticmethod
    def _serialize_context(examples: list[str]) -> str:
        """将上下文示例序列化为 JSON 字符串。"""
        return json.dumps(examples, ensure_ascii=False)

    @staticmethod
    def _deserialize_context(raw: str) -> list[str]:
        """从 JSON 字符串反序列化上下文示例。"""
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"[JargonStore] 无法解析 context_examples: {raw[:100]}")
        return []

    # ---- CRUD ------------------------------------------------------------------

    def _row_to_meaning(self, row: dict[str, Any]) -> JargonMeaning:
        """将数据库行映射为 :class:`JargonMeaning` 实例。"""
        return JargonMeaning(
            term=str(row["term"]),
            group_id=str(row["group_id"]),
            meaning=str(row["meaning"] or ""),
            confidence=float(row["confidence"] or 0.0),
            is_jargon=bool(row["is_jargon"]),
            is_confirmed=bool(row["is_confirmed"]),
            is_global=bool(row["is_global"]),
            is_complete=bool(row["is_complete"]),
            count=int(row["count"] or 0),
            last_inference_count=int(row["last_inference_count"] or 0),
            context_examples=self._deserialize_context(
                row["context_examples"] or "[]"
            ),
            created_at=float(row["created_at"] or 0.0),
            updated_at=float(row["updated_at"] or 0.0),
        )

    async def upsert(self, meaning: JargonMeaning) -> None:
        """插入或更新黑话含义（INSERT OR REPLACE）。

        使用 term + group_id 作为唯一键。
        """
        ctx_json = self._serialize_context(meaning.context_examples)
        row = {
            "term": meaning.term,
            "group_id": meaning.group_id,
            "meaning": meaning.meaning,
            "confidence": meaning.confidence,
            "is_jargon": int(meaning.is_jargon),
            "is_confirmed": int(meaning.is_confirmed),
            "is_global": int(meaning.is_global),
            "is_complete": int(meaning.is_complete),
            "count": meaning.count,
            "last_inference_count": meaning.last_inference_count,
            "context_examples": ctx_json,
            "created_at": meaning.created_at or time.time(),
            "updated_at": time.time(),
        }
        await self._execute(
            """INSERT OR REPLACE INTO jargon_terms
            (term, group_id, meaning, confidence, is_jargon, is_confirmed,
             is_global, is_complete, count, last_inference_count,
             context_examples, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            tuple(row.values()),
        )
        await self._commit()

    async def get_by_term(
        self, term: str, group_id: str
    ) -> JargonMeaning | None:
        """按词和群组查询黑话含义。

        Args:
            term: 黑话词条文本。
            group_id: 群组 ID。

        Returns:
            匹配的 JargonMeaning，无匹配时返回 None。
        """
        row = await self._fetch_one(
            "SELECT * FROM jargon_terms WHERE term = ? AND group_id = ?",
            (term, group_id),
        )
        if not row:
            return None
        return self._row_to_meaning(row)

    async def list_by_group(
        self, group_id: str, confirmed_only: bool = True
    ) -> list[JargonMeaning]:
        """列出群组所有黑话。

        Args:
            group_id: 群组 ID。
            confirmed_only: 若 True 则仅返回已确认的条目。

        Returns:
            该群组所有匹配的 JargonMeaning 列表。
        """
        rows = await self._fetch_all(
            "SELECT * FROM jargon_terms WHERE group_id = ?"
            + (" AND is_confirmed = 1" if confirmed_only else ""),
            (group_id,),
        )
        return [self._row_to_meaning(r) for r in rows]

    async def search(
        self, keyword: str, group_id: str
    ) -> list[JargonMeaning]:
        """模糊搜索黑话。

        使用 LIKE 在 term 和 meaning 列中搜索 keyword。

        Args:
            keyword: 搜索关键词。
            group_id: 群组 ID。

        Returns:
            匹配的 JargonMeaning 列表（可能为空）。
        """
        rows = await self._fetch_all(
            "SELECT * FROM jargon_terms WHERE group_id = ?"
            " AND (term LIKE ? OR meaning LIKE ?)",
            (group_id, f"%{keyword}%", f"%{keyword}%"),
        )
        return [self._row_to_meaning(r) for r in rows]

    # ---- 确认 -------------------------------------------------------------------

    async def confirm(
        self, term: str, group_id: str, confirmed: bool = True
    ) -> None:
        """手动确认/取消确认黑话条目。

        Args:
            term: 黑话词条。
            group_id: 群组 ID。
            confirmed: True 确认，False 取消确认。
        """
        await self._execute(
            "UPDATE jargon_terms SET is_confirmed = ? WHERE term = ? AND group_id = ?",
            (int(confirmed), term, group_id),
        )
        await self._commit()
        action = "确认" if confirmed else "取消确认"
        logger.info(
            f"[JargonStore] {action} jargon: term={term}, group={group_id}"
        )

    async def delete(self, term: str, group_id: str) -> None:
        """删除黑话条目。

        Args:
            term: 黑话词条。
            group_id: 群组 ID。
        """
        await self._execute(
            "DELETE FROM jargon_terms WHERE term = ? AND group_id = ?",
            (term, group_id),
        )
        await self._commit()
        logger.info(f"[JargonStore] 已删除 jargon: term={term}, group={group_id}")

    # ---- 统计 -------------------------------------------------------------------

    async def count_by_group(self, group_id: str) -> int:
        """统计群组内黑话条目数量。"""
        return await self._count_where("jargon_terms", group_id=group_id)

    async def count_confirmed(self, group_id: str) -> int:
        """统计群组内已确认黑话条目数量。"""
        return await self._count_where(
            "jargon_terms", group_id=group_id, is_confirmed=1
        )

    async def list_group_ids(self) -> list[str]:
        """返回黑话存储中所有非空且去重后的群组 ID。"""
        rows = await self._fetch_all(
            "SELECT DISTINCT group_id FROM jargon_terms WHERE group_id <> '' ORDER BY group_id"
        )
        return [str(row["group_id"]) for row in rows if row.get("group_id")]
