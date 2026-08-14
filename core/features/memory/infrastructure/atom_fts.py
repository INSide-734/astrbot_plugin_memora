"""记忆原子的全文检索混入，基于 BM25 打分。"""

from __future__ import annotations

import json
import time
from typing import Any

import aiosqlite
from astrbot.api import logger

from ..domain.memory_atom import MemoryAtom


class AtomFTSMixin:
    """为记忆原子提供基于 BM25 的全文检索能力。"""

    async def search_fts(
        self,
        query: str,
        limit: int = 20,
        session_id: str | None = None,
        persona_id: str | None = None,
        include_expired: bool = False,
    ) -> list[MemoryAtom]:
        """检索原子内容，并返回结合时间分数排序的结果。"""
        if not query or not query.strip():
            return []

        # 为兼容中日韩语言，默认使用裸词；仅多词短语使用引号
        tokens = [token for token in query.strip().split() if token]
        if not tokens:
            return []
        escaped = [token.replace('"', '""') for token in tokens]
        # 较长 token 或包含空格的 token 使用引号包裹
        fts_tokens = [
            f'"{token}"' if (" " in token or len(token) > 3) else token
            for token in escaped
        ]
        fts_query = " OR ".join(fts_tokens)

        params = {
            "fts_query": fts_query,
            "include_expired": int(include_expired),
            "session_id": session_id,
            "persona_id": persona_id,
            "limit": limit,
        }

        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            # 优先尝试 FTS 检索
            try:
                cursor = await db.execute(
                    """
                    SELECT ma.*, bm25(memory_atoms_fts) AS bm25_score
                    FROM memory_atoms_fts
                    JOIN memory_atoms ma ON ma.id = memory_atoms_fts.atom_id
                    WHERE memory_atoms_fts MATCH :fts_query
                      AND (:include_expired = 1 OR ma.status = 'active')
                      AND (:session_id IS NULL OR ma.session_id = :session_id)
                      AND (:persona_id IS NULL OR ma.persona_id = :persona_id)
                    ORDER BY bm25_score ASC
                    LIMIT :limit
                    """,
                    params,
                )
                rows = await cursor.fetchall()
            except Exception as e:
                logger.warning(f"BM25 FTS 全文搜索失败: {e}")
                rows = []

            # 当 FTS 无结果时，回退到 LIKE 检索
            if not rows:
                like_params = {
                    **params,
                    "like_patterns_json": json.dumps(
                        [f"%{token}%" for token in tokens]
                    ),
                }
                cursor = await db.execute(
                    """
                    SELECT ma.*, 0.5 AS bm25_score
                    FROM memory_atoms ma
                    WHERE EXISTS (
                        SELECT 1 FROM json_each(:like_patterns_json) AS pattern
                        WHERE ma.content LIKE pattern.value
                    )
                      AND (:include_expired = 1 OR ma.status = 'active')
                      AND (:session_id IS NULL OR ma.session_id = :session_id)
                      AND (:persona_id IS NULL OR ma.persona_id = :persona_id)
                    ORDER BY ma.id DESC
                    LIMIT :limit
                    """,
                    like_params,
                )
                rows = await cursor.fetchall()

        if not rows:
            return []

        scores = [float(row["bm25_score"]) for row in rows]
        max_score = max(scores)
        min_score = min(scores)
        score_range = max_score - min_score

        atoms: list[MemoryAtom] = []
        now = time.time()
        for row in rows:
            atom = self._row_to_atom(row)
            normalized = (
                1.0
                if score_range == 0
                else (max_score - float(row["bm25_score"])) / score_range
            )
            atom.metadata["bm25_score"] = normalized
            atom.metadata["temporal_score"] = atom.compute_temporal_score(now)
            atoms.append(atom)

        atoms.sort(
            key=lambda a: (
                float(a.metadata.get("bm25_score", 0))
                * float(a.metadata.get("temporal_score", 1))
            ),
            reverse=True,
        )
        return atoms

    async def search_fts_by_type(
        self,
        query: str,
        limit: int = 10,
        session_id: str | None = None,
        persona_id: str | None = None,
        atom_types: list[str] | None = None,
        include_expired: bool = False,
    ) -> list[MemoryAtom]:
        has_query = bool(query and query.strip())
        tokens: list[str] = []
        fts_query = ""
        if has_query:
            tokens = [token for token in query.strip().split() if token]
            if not tokens:
                has_query = False
            else:
                escaped = [token.replace('"', '""') for token in tokens]
                fts_tokens = [
                    f'"{token}"' if (" " in token or len(token) > 3) else token
                    for token in escaped
                ]
                fts_query = " OR ".join(fts_tokens)

        atom_type_values = list(atom_types or [])
        params = {
            "fts_query": fts_query,
            "has_query": int(has_query),
            "include_expired": int(include_expired),
            "session_id": session_id,
            "persona_id": persona_id,
            "has_atom_types": int(bool(atom_type_values)),
            "atom_types_json": json.dumps(atom_type_values),
            "like_patterns_json": json.dumps([f"%{token}%" for token in tokens]),
            "limit": limit,
        }

        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            rows: list[Any] = []
            if has_query:
                try:
                    cursor = await db.execute(
                        """
                        SELECT ma.*, bm25(memory_atoms_fts) AS bm25_score
                        FROM memory_atoms_fts
                        JOIN memory_atoms ma ON ma.id = memory_atoms_fts.atom_id
                        WHERE memory_atoms_fts MATCH :fts_query
                          AND (:include_expired = 1 OR ma.status = 'active')
                          AND (:session_id IS NULL OR ma.session_id = :session_id)
                          AND (:persona_id IS NULL OR ma.persona_id = :persona_id)
                          AND (
                            :has_atom_types = 0
                            OR ma.atom_type IN (
                                SELECT value FROM json_each(:atom_types_json)
                            )
                          )
                        ORDER BY bm25_score ASC
                        LIMIT :limit
                        """,
                        params,
                    )
                    rows = await cursor.fetchall()
                except Exception as e:
                    logger.warning(f"BM25 FTS 全文搜索失败: {e}")
                    rows = []

            if not rows:
                cursor = await db.execute(
                    """
                    SELECT ma.*,
                           CASE WHEN :has_query = 1 THEN 0.5 ELSE 0.0 END AS bm25_score
                    FROM memory_atoms ma
                    WHERE (
                        :has_query = 0
                        OR EXISTS (
                            SELECT 1 FROM json_each(:like_patterns_json) AS pattern
                            WHERE ma.content LIKE pattern.value
                        )
                    )
                      AND (:include_expired = 1 OR ma.status = 'active')
                      AND (:session_id IS NULL OR ma.session_id = :session_id)
                      AND (:persona_id IS NULL OR ma.persona_id = :persona_id)
                      AND (
                        :has_atom_types = 0
                        OR ma.atom_type IN (
                            SELECT value FROM json_each(:atom_types_json)
                        )
                      )
                    ORDER BY ma.id DESC
                    LIMIT :limit
                    """,
                    params,
                )
                rows = await cursor.fetchall()

        if not rows:
            return []

        scores = [float(row["bm25_score"]) for row in rows]
        max_score = max(scores)
        min_score = min(scores)
        score_range = max_score - min_score

        atoms: list[MemoryAtom] = []
        now = time.time()
        for row in rows:
            atom = self._row_to_atom(row)
            normalized = (
                1.0
                if score_range == 0
                else (max_score - float(row["bm25_score"])) / score_range
            )
            atom.metadata["bm25_score"] = normalized
            atom.metadata["temporal_score"] = atom.compute_temporal_score(now)
            atoms.append(atom)

        atoms.sort(
            key=lambda a: (
                float(a.metadata.get("bm25_score", 0))
                * float(a.metadata.get("temporal_score", 1))
            ),
            reverse=True,
        )
        return atoms


AtomFTS = AtomFTSMixin
