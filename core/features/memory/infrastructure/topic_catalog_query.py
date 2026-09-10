"""Topic catalog 候选查询 mixin：full/BM25/补足三种查询模式。"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger

_ALLOWED_CHAT_TYPES = frozenset({"private", "group"})
_ALLOWED_PRIVACY = frozenset({"public", "shared", "confidential"})


class TopicCatalogQueryMixin:
    """提供候选查询端口：full、BM25、recent/frequent 补足。"""

    _db: Any

    async def select_full_candidates(
        self,
        scope_key: str,
        privacy_level: str,
        chat_type: str,
        generation: int,
        *,
        resolver_revision: str | None = None,
        max_count: int = 100,
    ) -> list[dict[str, Any]]:
        """有界全量读取，并对整个 scope 执行 source/fence 复核。"""
        if not self._valid_query_args(
            scope_key,
            privacy_level,
            chat_type,
            generation,
            resolver_revision,
            max_count,
        ):
            return []
        try:
            async with self._db.execute(
                """
                WITH selected_topics AS (
                    SELECT st.generation, st.scope_key, st.chat_type,
                           st.privacy_level, st.topic_key, st.display_topic,
                           st.active_source_count, st.last_seen_at
                    FROM scope_topics st
                    JOIN topic_catalog_state state
                      ON state.id = 1
                     AND state.status = 'ready'
                     AND state.active_generation = st.generation
                    WHERE st.generation = ?
                      AND st.scope_key = ?
                      AND st.chat_type = ?
                      AND st.privacy_level = ?
                    ORDER BY st.active_source_count DESC,
                             st.last_seen_at DESC,
                             st.topic_key ASC
                    LIMIT ?
                )
                SELECT st.topic_key, st.display_topic,
                       mts.memory_id, mts.source_revision, mts.observed_at,
                       st.active_source_count,
                       (
                           SELECT COUNT(*) FROM memory_topic_sources all_mts
                           WHERE all_mts.generation = st.generation
                             AND all_mts.scope_key = st.scope_key
                             AND all_mts.chat_type = st.chat_type
                             AND all_mts.privacy_level = st.privacy_level
                             AND all_mts.topic_key = st.topic_key
                       ) AS mapping_source_count,
                       d.metadata, d.created_at, d.updated_at
                FROM selected_topics st
                LEFT JOIN memory_topic_sources mts
                  ON mts.generation = st.generation
                 AND mts.scope_key = st.scope_key
                 AND mts.chat_type = st.chat_type
                 AND mts.privacy_level = st.privacy_level
                 AND mts.topic_key = st.topic_key
                LEFT JOIN documents d ON d.id = mts.memory_id
                ORDER BY st.active_source_count DESC,
                         st.last_seen_at DESC,
                         st.topic_key ASC,
                         mts.memory_id ASC
                """,
                (generation, scope_key, chat_type, privacy_level, max_count),
            ) as cursor:
                rows = await cursor.fetchall()
            if not rows:
                return []

            candidates: list[dict[str, Any]] = []
            seen_topics: set[str] = set()
            for row in rows:
                if row[2] is None or int(row[6]) != int(row[5]):
                    return []
                candidate = self._validated_standard_candidate(
                    row,
                    scope_key=scope_key,
                    privacy_level=privacy_level,
                    chat_type=chat_type,
                    resolver_revision=resolver_revision,
                )
                if candidate is None:
                    return []
                topic_key = candidate["topic_key"]
                if topic_key in seen_topics:
                    continue
                seen_topics.add(topic_key)
                candidates.append(candidate)
            return candidates
        except Exception:
            logger.exception("select_full_candidates 查询失败")
            return []

    async def count_scope_topics(
        self,
        scope_key: str,
        privacy_level: str,
        chat_type: str,
        generation: int,
    ) -> int:
        """只读统计 ready active generation 的 scope topic 数。"""
        if (
            self._db is None
            or generation <= 0
            or not scope_key
            or chat_type not in _ALLOWED_CHAT_TYPES
            or privacy_level not in _ALLOWED_PRIVACY
        ):
            return 0
        try:
            async with self._db.execute(
                """
                SELECT COUNT(*)
                FROM scope_topics st
                JOIN topic_catalog_state state
                  ON state.id = 1
                 AND state.status = 'ready'
                 AND state.active_generation = st.generation
                WHERE st.generation = ? AND st.scope_key = ?
                  AND st.chat_type = ? AND st.privacy_level = ?
                """,
                (generation, scope_key, chat_type, privacy_level),
            ) as cursor:
                row = await cursor.fetchone()
            return int(row[0]) if row else 0
        except Exception:
            logger.warning("count_scope_topics 查询失败", exc_info=True)
            return 0

    async def select_bm25_candidates(
        self,
        fts_query: str,
        scope_key: str,
        privacy_level: str,
        chat_type: str,
        generation: int,
        *,
        resolver_revision: str | None = None,
        max_count: int = 100,
    ) -> list[dict[str, Any]]:
        """执行 BM25 主排序，并对每个 scope source fail-closed 复核。"""
        if (
            not fts_query
            or not self._valid_query_args(
                scope_key,
                privacy_level,
                chat_type,
                generation,
                resolver_revision,
                max_count,
            )
        ):
            return []
        try:
            async with self._db.execute(
                """
                SELECT CAST(fts.doc_id AS INTEGER) AS memory_id,
                       bm25(fts.memora_memories_fts) AS bm25_score,
                       mts.topic_key, mts.display_topic,
                       mts.source_revision, mts.observed_at,
                       st.active_source_count, st.last_seen_at,
                       (
                           SELECT COUNT(*) FROM memory_topic_sources all_mts
                           WHERE all_mts.generation = mts.generation
                             AND all_mts.scope_key = mts.scope_key
                             AND all_mts.chat_type = mts.chat_type
                             AND all_mts.privacy_level = mts.privacy_level
                             AND all_mts.topic_key = mts.topic_key
                       ) AS mapping_source_count,
                       d.metadata, d.created_at, d.updated_at
                FROM memora_memories_fts fts
                JOIN documents d ON d.id = CAST(fts.doc_id AS INTEGER)
                JOIN memory_topic_sources mts ON mts.memory_id = d.id
                  AND mts.generation = ?
                  AND mts.scope_key = ?
                  AND mts.chat_type = ?
                  AND mts.privacy_level = ?
                JOIN scope_topics st
                  ON st.generation = mts.generation
                 AND st.scope_key = mts.scope_key
                 AND st.chat_type = mts.chat_type
                 AND st.privacy_level = mts.privacy_level
                 AND st.topic_key = mts.topic_key
                JOIN topic_catalog_state state
                  ON state.id = 1
                 AND state.status = 'ready'
                 AND state.active_generation = mts.generation
                WHERE fts.memora_memories_fts MATCH ?
                ORDER BY bm25_score ASC,
                         st.active_source_count DESC,
                         st.last_seen_at DESC,
                         mts.topic_key ASC
                LIMIT ?
                """,
                (
                    generation,
                    scope_key,
                    chat_type,
                    privacy_level,
                    fts_query,
                    max_count * 3,
                ),
            ) as cursor:
                rows = await cursor.fetchall()
            if not rows:
                return []

            candidates: list[dict[str, Any]] = []
            seen_topics: set[str] = set()
            for row in rows:
                candidate = self._validated_bm25_candidate(
                    row,
                    scope_key=scope_key,
                    privacy_level=privacy_level,
                    chat_type=chat_type,
                    resolver_revision=resolver_revision,
                )
                if candidate is None:
                    return []
                topic_key = candidate["topic_key"]
                if topic_key in seen_topics:
                    continue
                seen_topics.add(topic_key)
                if len(candidates) >= max_count:
                    break
                candidates.append(candidate)
            return candidates
        except Exception:
            logger.exception("select_bm25_candidates 查询失败")
            return []

    async def select_recent_frequent_candidates(
        self,
        scope_key: str,
        privacy_level: str,
        chat_type: str,
        generation: int,
        *,
        resolver_revision: str | None = None,
        max_count: int = 100,
        overfetch_factor: int = 3,
        exclude_topic_keys: set[str] | tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        """从近期/频次两个流交替补足，并对 scope 统一 fail-closed。"""
        if not self._valid_query_args(
            scope_key,
            privacy_level,
            chat_type,
            generation,
            resolver_revision,
            max_count,
        ):
            return []
        try:
            scan_limit = max_count * max(1, overfetch_factor)
            excluded = tuple(
                sorted({key for key in exclude_topic_keys if key}))
            exclude_clause = ""
            exclude_params: tuple[Any, ...] = ()
            if excluded:
                placeholders = ",".join("?" for _ in excluded)
                exclude_clause = f" AND st.topic_key NOT IN ({placeholders})"
                exclude_params = excluded

            common_select = """
                SELECT st.topic_key, st.display_topic,
                       mts.memory_id, mts.source_revision, mts.observed_at,
                       st.active_source_count,
                       COUNT(mts.memory_id) OVER (
                           PARTITION BY st.generation, st.scope_key,
                                        st.chat_type, st.privacy_level,
                                        st.topic_key
                       ) AS mapping_source_count,
                       d.metadata, d.created_at, d.updated_at
                FROM scope_topics st
                JOIN topic_catalog_state state
                  ON state.id = 1
                 AND state.status = 'ready'
                 AND state.active_generation = st.generation
                LEFT JOIN memory_topic_sources mts
                  ON mts.generation = st.generation
                 AND mts.scope_key = st.scope_key
                 AND mts.chat_type = st.chat_type
                 AND mts.privacy_level = st.privacy_level
                 AND mts.topic_key = st.topic_key
                LEFT JOIN documents d ON d.id = mts.memory_id
                WHERE st.generation = ?
                  AND st.scope_key = ?
                  AND st.chat_type = ?
                  AND st.privacy_level = ?
            """
            query_params = (generation, scope_key, chat_type, privacy_level)
            async with self._db.execute(
                common_select
                + exclude_clause
                + """
                ORDER BY st.active_source_count DESC,
                         st.last_seen_at DESC,
                         st.topic_key ASC
                LIMIT ?
                """,
                (*query_params, *exclude_params, scan_limit),
            ) as cursor:
                frequent_rows = await cursor.fetchall()
            async with self._db.execute(
                common_select
                + exclude_clause
                + """
                ORDER BY st.last_seen_at DESC,
                         st.active_source_count DESC,
                         st.topic_key ASC
                LIMIT ?
                """,
                (*query_params, *exclude_params, scan_limit),
            ) as cursor:
                recent_rows = await cursor.fetchall()

            candidates: list[dict[str, Any]] = []
            seen_topics: set[str] = set()
            frequent_idx = 0
            recent_idx = 0
            while len(candidates) < max_count and (
                frequent_idx < len(
                    frequent_rows) or recent_idx < len(recent_rows)
            ):
                if frequent_idx < len(frequent_rows):
                    candidate = self._validated_standard_candidate(
                        frequent_rows[frequent_idx],
                        scope_key=scope_key,
                        privacy_level=privacy_level,
                        chat_type=chat_type,
                        resolver_revision=resolver_revision,
                    )
                    if candidate is None:
                        return []
                    topic_key = candidate["topic_key"]
                    if topic_key not in seen_topics:
                        seen_topics.add(topic_key)
                        candidates.append(candidate)
                    frequent_idx += 1

                if len(candidates) >= max_count:
                    break

                if recent_idx < len(recent_rows):
                    candidate = self._validated_standard_candidate(
                        recent_rows[recent_idx],
                        scope_key=scope_key,
                        privacy_level=privacy_level,
                        chat_type=chat_type,
                        resolver_revision=resolver_revision,
                    )
                    if candidate is None:
                        return []
                    topic_key = candidate["topic_key"]
                    if topic_key not in seen_topics:
                        seen_topics.add(topic_key)
                        candidates.append(candidate)
                    recent_idx += 1
            return candidates
        except Exception:
            logger.exception("select_recent_frequent_candidates 查询失败")
            return []

    @staticmethod
    def _valid_query_args(
        scope_key: str,
        privacy_level: str,
        chat_type: str,
        generation: int,
        resolver_revision: str | None,
        max_count: int,
    ) -> bool:
        """校验查询快照字段，不猜测缺失 scope 身份。"""
        if generation <= 0 or max_count <= 0:
            return False
        if (
            not isinstance(scope_key, str)
            or not scope_key.strip()
            or chat_type not in _ALLOWED_CHAT_TYPES
            or privacy_level not in _ALLOWED_PRIVACY
        ):
            return False
        return resolver_revision is None or (
            isinstance(resolver_revision, str) and bool(
                resolver_revision.strip())
        )

    def _validated_standard_candidate(
        self,
        row: tuple[Any, ...],
        *,
        scope_key: str,
        privacy_level: str,
        chat_type: str,
        resolver_revision: str | None,
    ) -> dict[str, Any] | None:
        """使用 store 的 canonical source validator 构造标准候选。"""
        if len(row) < 10 or row[2] is None:
            return None
        if int(row[6]) != int(row[5]):
            return None
        topic_key = str(row[0])
        source = self._validated_source(
            memory_id=int(row[2]),
            raw_metadata=row[7],
            created_at=row[8],
            updated_at=row[9],
            stored_revision=str(row[3]).strip(),
            topic_key=topic_key,
            scope_key=scope_key,
            privacy_level=privacy_level,
            chat_type=chat_type,
            resolver_revision=resolver_revision,
        )
        if source is None:
            return None
        return {
            "memory_id": int(row[2]),
            "source_revision": source["source_revision"],
            "topic_key": topic_key,
            "display_topic": str(row[1]),
            "observed_at": float(row[4]),
        }

    def _validated_bm25_candidate(
        self,
        row: tuple[Any, ...],
        *,
        scope_key: str,
        privacy_level: str,
        chat_type: str,
        resolver_revision: str | None,
    ) -> dict[str, Any] | None:
        """使用 store 的 canonical source validator 构造 BM25 候选。"""
        if len(row) < 12 or row[0] is None:
            return None
        if int(row[8]) != int(row[6]):
            return None
        topic_key = str(row[2])
        source = self._validated_source(
            memory_id=int(row[0]),
            raw_metadata=row[9],
            created_at=row[10],
            updated_at=row[11],
            stored_revision=str(row[4]).strip(),
            topic_key=topic_key,
            scope_key=scope_key,
            privacy_level=privacy_level,
            chat_type=chat_type,
            resolver_revision=resolver_revision,
        )
        if source is None:
            return None
        return {
            "memory_id": int(row[0]),
            "source_revision": source["source_revision"],
            "topic_key": topic_key,
            "display_topic": str(row[3]),
            "observed_at": float(row[5]),
            "bm25_score": float(row[1]),
        }

    def _validated_source(
        self,
        *,
        memory_id: int,
        raw_metadata: Any,
        created_at: Any,
        updated_at: Any,
        stored_revision: str,
        topic_key: str,
        scope_key: str,
        privacy_level: str,
        chat_type: str,
        resolver_revision: str | None,
    ) -> dict[str, str] | None:
        """调用 store 共享 validator 并复核 mapping 的 scope/topic 绑定。"""
        validator = getattr(self, "_source_from_values", None)
        if not callable(validator):
            return None
        source = validator(memory_id, raw_metadata, created_at, updated_at)
        if source is None:
            return None
        if (
            source.scope_key != scope_key
            or source.privacy_level != privacy_level
            or source.chat_type != chat_type
            or source.source_revision != stored_revision
            or (
                resolver_revision is not None
                and source.resolver_revision != resolver_revision.strip()
            )
            or not any(key == topic_key for key, _ in source.topics)
        ):
            return None
        return {"source_revision": source.source_revision}


__all__ = ["TopicCatalogQueryMixin"]
