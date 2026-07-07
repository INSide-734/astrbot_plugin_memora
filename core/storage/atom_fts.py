"""记忆原子的全文检索混入，基于 BM25 打分。"""

from __future__ import annotations

import time
from typing import Any

import aiosqlite

from astrbot.api import logger

from ..models.memory_atom import MemoryAtom


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

        filters = ["ma.status = 'active'"] if not include_expired else []
        params: list[Any] = [fts_query]
        if session_id is not None:
            filters.append("ma.session_id = ?")
            params.append(session_id)
        if persona_id is not None:
            filters.append("ma.persona_id = ?")
            params.append(persona_id)

        where_clause = f"AND {' AND '.join(filters)}" if filters else ""

        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            # 优先尝试 FTS 检索
            try:
                cursor = await db.execute(
                    f"""
                    SELECT ma.*, bm25(memory_atoms_fts) AS bm25_score
                    FROM memory_atoms_fts
                    JOIN memory_atoms ma ON ma.id = memory_atoms_fts.atom_id
                    WHERE memory_atoms_fts MATCH ? {where_clause}
                    ORDER BY bm25_score ASC
                    LIMIT ?
                    """,
                    (*params, limit),
                )
                rows = await cursor.fetchall()
            except Exception as e:
                logger.warning(f"BM25 FTS 全文搜索失败: {e}")
                rows = []

            # 当 FTS 无结果时，回退到 LIKE 检索
            if not rows:
                like_clauses = " OR ".join(["ma.content LIKE ?" for _ in tokens])
                like_params_full: list[Any] = [f"%{t}%" for t in tokens]
                status_filter = (
                    "AND ma.status = 'active'" if not include_expired else ""
                )
                session_filter = (
                    "AND ma.session_id = ?" if session_id is not None else ""
                )
                persona_filter = (
                    "AND ma.persona_id = ?" if persona_id is not None else ""
                )
                if session_id is not None:
                    like_params_full.append(session_id)
                if persona_id is not None:
                    like_params_full.append(persona_id)
                cursor = await db.execute(
                    f"""
                    SELECT ma.*, 0.5 AS bm25_score
                    FROM memory_atoms ma
                    WHERE ({like_clauses}) {status_filter} {session_filter} {persona_filter}
                    ORDER BY ma.id DESC
                    LIMIT ?
                    """,
                    (*like_params_full, limit),
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

        filters: list[str] = ["ma.status = 'active'"] if not include_expired else []
        params: list[Any] = []
        if has_query:
            params.append(fts_query)
        if session_id is not None:
            filters.append("ma.session_id = ?")
            params.append(session_id)
        if persona_id is not None:
            filters.append("ma.persona_id = ?")
            params.append(persona_id)
        if atom_types is not None and len(atom_types) > 0:
            placeholders = ", ".join(["?"] * len(atom_types))
            filters.append(f"ma.atom_type IN ({placeholders})")
            params.extend(atom_types)

        where_clause = f"AND {' AND '.join(filters)}" if filters else ""

        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            rows: list[Any] = []
            if has_query:
                try:
                    cursor = await db.execute(
                        f"""
                        SELECT ma.*, bm25(memory_atoms_fts) AS bm25_score
                        FROM memory_atoms_fts
                        JOIN memory_atoms ma ON ma.id = memory_atoms_fts.atom_id
                        WHERE memory_atoms_fts MATCH ? {where_clause}
                        ORDER BY bm25_score ASC
                        LIMIT ?
                        """,
                        (*params, limit),
                    )
                    rows = await cursor.fetchall()
                except Exception as e:
                    logger.warning(f"BM25 FTS 全文搜索失败: {e}")
                    rows = []

            if not rows:
                if has_query:
                    like_clauses = " OR ".join(["ma.content LIKE ?" for _ in tokens])
                    like_params_full: list[Any] = [f"%{t}%" for t in tokens]
                else:
                    like_clauses = "1=1"
                    like_params_full = []

                status_filter = (
                    "AND ma.status = 'active'" if not include_expired else ""
                )
                session_filter = (
                    "AND ma.session_id = ?" if session_id is not None else ""
                )
                persona_filter = (
                    "AND ma.persona_id = ?" if persona_id is not None else ""
                )
                type_filter = ""
                if atom_types is not None and len(atom_types) > 0:
                    type_placeholders = ", ".join(["?"] * len(atom_types))
                    type_filter = f"AND ma.atom_type IN ({type_placeholders})"
                    like_params_full.extend(atom_types)

                if session_id is not None:
                    like_params_full.append(session_id)
                if persona_id is not None:
                    like_params_full.append(persona_id)

                cursor = await db.execute(
                    f"""
                    SELECT ma.*, {"0.5" if has_query else "0.0"} AS bm25_score
                    FROM memory_atoms ma
                    WHERE ({like_clauses}) {status_filter} {session_filter} {persona_filter} {type_filter}
                    ORDER BY ma.id DESC
                    LIMIT ?
                    """,
                    (*like_params_full, limit),
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
