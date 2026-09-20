"""记忆原子的全文检索混入，基于 BM25 打分。"""

from __future__ import annotations

import json
import time
from typing import Any

import aiosqlite
from astrbot.api import logger

from ....shared.sql import build_fts5_or_query
from ..domain.memory_atom import MemoryAtom


def _build_match_expression(query: str) -> str | None:
    """把用户查询转换为仅由双引号短语组成的 FTS5 MATCH 表达式。

    空白切分后的每个非空片段都作为短语处理（内部 ``"`` 按 FTS5 规则双写），
    使 ``OR``/``AND``/``NEAR`` 等关键字、标点与纯数字一律按词面匹配，
    不会再进入 FTS5 语法位置；加引号不改变 unicode61 下的分词口径，
    因此既有命中语义不变。查询为空或只含空白时返回 ``None``：
    ``search_fts`` 据此直接返回空结果，``search_fts_by_type`` 退化为
    「无查询词」分支并按类型返回。
    """
    fragments = [fragment for fragment in (query or "").split() if fragment]
    if not fragments:
        return None
    return build_fts5_or_query(fragments)


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
        """检索原子内容，并返回结合时间分数排序的结果。

        维护用途：本接口不校验父 canonical 来源，生产读取路径（AtomRetriever、
        前瞻查询）必须走 Store 的父来源校验入口；直接消费本接口的调用方需自行
        完成 ``filter_current_sources`` 等来源门。``include_expired=True`` 取消
        全部 Atom 状态条件（不只是放行 ``expired``），仅供维护与强化使用。
        """
        # 用户文本一律作为双引号短语进入 FTS5，避免污染 MATCH 语法位置
        fts_query = _build_match_expression(query)
        if fts_query is None:
            return []

        # LIKE 回退仍沿用原始空白切分口径
        tokens = [token for token in query.split() if token]

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
            except Exception as exc:
                # sqlite 的异常 message 会回显查询片段（fts5: syntax error near "…"），
                # 因此只记异常类型，不记原始 message。
                logger.warning(
                    "BM25 FTS 全文搜索失败，异常类型=%s",
                    exc.__class__.__name__,
                )
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
        """按 Atom 类型检索原子，供维护与诊断使用。

        与 ``search_fts`` 同一维护边界：不校验父 canonical 来源，当前没有生产
        召回消费者；新增生产消费者前必须补来源门并通过评审。``include_expired``
        为真时取消全部 Atom 状态条件。
        """
        # 用户文本一律作为双引号短语进入 FTS5，避免污染 MATCH 语法位置；
        # 无可用片段时退化为「无查询」分支，按类型返回。
        fts_query = _build_match_expression(query) or ""
        has_query = bool(fts_query)
        tokens = [token for token in query.split() if token] if has_query else []

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
                except Exception as exc:
                    # sqlite 的异常 message 会回显查询片段（fts5: syntax error near "…"），
                    # 因此只记异常类型，不记原始 message。
                    logger.warning(
                        "BM25 FTS 全文搜索失败，异常类型=%s",
                        exc.__class__.__name__,
                    )
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
