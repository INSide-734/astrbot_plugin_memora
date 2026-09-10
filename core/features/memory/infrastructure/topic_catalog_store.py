"""支持 generation 的 topic catalog canonical SQLite 存储。"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any

from astrbot.api import logger

from ....shared.memory_status import is_memory_recallable
from ...quality.application.gate_disposition_filter import is_mark_write
from ...reflection.domain.summary_models import (
    normalize_topic_key,
    normalize_topic_label,
)
from .topic_catalog_metrics import TopicCatalogMetricsMixin
from .topic_catalog_models import TopicCatalogDirty, TopicCatalogSource
from .topic_catalog_query import TopicCatalogQueryMixin
from .topic_catalog_rebuild import TopicCatalogRebuildMixin
from .topic_catalog_schema import create_topic_catalog_schema

_ALLOWED_CHAT_TYPES = frozenset({"private", "group"})
_ALLOWED_PRIVACY = frozenset({"public", "shared", "confidential"})


class TopicCatalogStore(
    TopicCatalogQueryMixin, TopicCatalogRebuildMixin, TopicCatalogMetricsMixin
):
    """管理 canonical SQLite 中的 topic 派生 mapping 与安全读取。"""

    def __init__(self, db_connection: Any | None = None) -> None:
        """保存由 MemoryEngine 共享的 canonical SQLite 连接。"""

        self._db = db_connection

    @property
    def db_connection(self) -> Any | None:
        """返回当前 canonical SQLite 连接。"""

        return self._db

    async def get_catalog_summary(self) -> dict[str, Any]:
        """返回管理面板允许的状态、dirty 数量和 scope 规模聚合，不暴露标识。"""
        if self._db is None:
            return {
                "catalog_status": "unavailable",
                "dirty_count": None,
                "scope_buckets": None,
            }
        cursor = await self._db.execute(
            """
            WITH scope_counts AS (
                SELECT COUNT(*) AS topic_count
                FROM scope_topics AS topics
                JOIN topic_catalog_state AS state
                  ON state.id = 1 AND state.active_generation = topics.generation
                 AND state.status = 'ready'
                GROUP BY topics.scope_key, topics.chat_type, topics.privacy_level
            )
            SELECT status, active_generation,
                   (SELECT COUNT(*) FROM topic_catalog_dirty WHERE state != 'completed'),
                   (SELECT COUNT(*) FROM scope_counts WHERE topic_count < 10),
                   (SELECT COUNT(*) FROM scope_counts WHERE topic_count >= 10
                     AND topic_count < 30),
                   (SELECT COUNT(*) FROM scope_counts WHERE topic_count >= 30
                     AND topic_count < 100),
                   (SELECT COUNT(*) FROM scope_counts WHERE topic_count >= 100
                     AND topic_count < 300),
                   (SELECT COUNT(*) FROM scope_counts WHERE topic_count >= 300
                     AND topic_count < 1000),
                   (SELECT COUNT(*) FROM scope_counts WHERE topic_count >= 1000)
            FROM topic_catalog_state WHERE id = 1
            """
        )
        row = await cursor.fetchone()
        if row is None:
            return {
                "catalog_status": "unavailable",
                "dirty_count": None,
                "scope_buckets": None,
            }
        status = str(row[0])
        if status not in {"ready", "degraded", "backfilling", "empty"}:
            status = "unavailable"
        if status == "ready" and (not isinstance(row[1], int) or row[1] <= 0):
            status = "unavailable"
        # 桶词表与 selector/_map_topic_count_to_bucket 及 manager/CLI 统一：
        # tiny/small/medium/large/xlarge/huge
        return {
            "catalog_status": status,
            "dirty_count": int(row[2]),
            "scope_buckets": dict(
                zip(
                    ("tiny", "small", "medium", "large", "xlarge", "huge"),
                    map(int, row[3:9]),
                    strict=True,
                )
            ),
        }

    async def initialize(self) -> None:
        """在独立事务内幂等创建 topic catalog schema。"""

        if self._db is None:
            raise RuntimeError("TopicCatalogStore 尚未绑定数据库连接")
        async with self._catalog_transaction() as db:
            await create_topic_catalog_schema(db)

    async def replace_memory_mappings(
        self,
        memory_id: int,
        generation: int,
        *,
        owner_token: str | None = None,
        now: float | None = None,
    ) -> bool:
        """按 memory ID 重读当前 canonical source 并原子替换 mapping。

        ``owner_token`` 只用于 staging generation；active generation 的普通增量
        修复不需要 rebuild lease。source 缺失或不合格时仅删除旧 mapping。
        """

        if self._db is None or memory_id <= 0 or generation <= 0:
            return False
        current_time = max(0.0, time.time() if now is None else now)
        try:
            async with self._catalog_transaction() as db:
                state = await (
                    await db.execute(
                        """
                        SELECT active_generation, staging_generation, status,
                               rebuild_owner_token, rebuild_lease_until
                        FROM topic_catalog_state WHERE id = 1
                        """
                    )
                ).fetchone()
                if state is None:
                    return False
                if owner_token is None:
                    if state[0] != generation or state[2] != "ready":
                        return False
                elif (
                    state[1] != generation
                    or state[3] != owner_token
                    or state[4] is None
                    or float(state[4]) <= current_time
                ):
                    return False
                old_cursor = await db.execute(
                    """
                    SELECT DISTINCT scope_key, chat_type, privacy_level
                    FROM memory_topic_sources
                    WHERE generation = ? AND memory_id = ?
                    """,
                    (generation, memory_id),
                )
                affected_scopes = {
                    (str(row[0]), str(row[1]), str(row[2]))
                    for row in await old_cursor.fetchall()
                }
                await db.execute(
                    "DELETE FROM memory_topic_sources WHERE generation = ? AND memory_id = ?",
                    (generation, memory_id),
                )
                source = await self._read_current_source(db, memory_id)
                if source is not None:
                    await db.executemany(
                        """
                        INSERT INTO memory_topic_sources(
                            generation, memory_id, source_revision, scope_key,
                            chat_type, privacy_level, topic_key, display_topic, observed_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                generation,
                                source.memory_id,
                                source.source_revision,
                                source.scope_key,
                                source.chat_type,
                                source.privacy_level,
                                topic_key,
                                display_topic,
                                source.observed_at,
                            )
                            for topic_key, display_topic in source.topics
                        ],
                    )
                    affected_scopes.add(
                        (source.scope_key, source.chat_type, source.privacy_level)
                    )
                for scope in affected_scopes:
                    await self._rebuild_scope_aggregate(db, generation, *scope)
            return True
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("话题目录来源映射替换失败", exc_info=True)
            return False

    async def list_scope_topics(
        self,
        generation: int,
        scope_key: str,
        privacy_level: str,
        *,
        resolver_revision: str,
        chat_type: str | None = None,
        limit: int = 100,
    ) -> tuple[dict[str, Any], ...]:
        """在单条 SQLite 读快照中返回经过当前 source 复核的 topic。"""

        if (
            self._db is None
            or generation <= 0
            or not isinstance(scope_key, str)
            or not scope_key.strip()
            or privacy_level not in _ALLOWED_PRIVACY
            or not isinstance(resolver_revision, str)
            or not resolver_revision.strip()
            or len(resolver_revision.strip()) > 256
            or limit <= 0
        ):
            return ()
        if chat_type is not None and chat_type not in _ALLOWED_CHAT_TYPES:
            return ()
        normalized_scope = scope_key.strip()
        normalized_resolver_revision = resolver_revision.strip()
        chat_clause = ""
        params: list[Any] = [generation, normalized_scope, privacy_level]
        if chat_type is not None:
            chat_clause = " AND topic.chat_type = ?"
            params.append(chat_type)
        params.append(min(limit, 1000))
        try:
            cursor = await self._db.execute(
                f"""
                WITH selected_topics AS (
                    SELECT topic.topic_key, topic.display_topic,
                           topic.active_source_count, topic.first_seen_at,
                           topic.last_seen_at, topic.chat_type, topic.privacy_level
                    FROM scope_topics AS topic
                    JOIN topic_catalog_state AS state
                      ON state.id = 1 AND state.status = 'ready'
                     AND state.active_generation = topic.generation
                    WHERE topic.generation = ? AND topic.scope_key = ?
                      AND topic.privacy_level = ?{chat_clause}
                      AND NOT EXISTS (
                        SELECT 1 FROM scope_topics AS conflict
                        WHERE conflict.generation = topic.generation
                          AND conflict.scope_key = topic.scope_key
                          AND conflict.privacy_level = topic.privacy_level
                          AND conflict.chat_type <> topic.chat_type
                      )
                    ORDER BY topic.active_source_count DESC,
                             topic.last_seen_at DESC, topic.topic_key ASC
                    LIMIT ?
                )
                SELECT topic.topic_key, topic.display_topic,
                       topic.active_source_count, topic.first_seen_at,
                       topic.last_seen_at, topic.chat_type, topic.privacy_level,
                       mapping.memory_id, mapping.source_revision,
                       document.metadata, document.created_at, document.updated_at
                FROM selected_topics AS topic
                LEFT JOIN memory_topic_sources AS mapping
                  ON mapping.generation = ? AND mapping.scope_key = ?
                 AND mapping.privacy_level = topic.privacy_level
                 AND mapping.chat_type = topic.chat_type
                 AND mapping.topic_key = topic.topic_key
                LEFT JOIN documents AS document ON document.id = mapping.memory_id
                ORDER BY topic.active_source_count DESC,
                         topic.last_seen_at DESC, topic.topic_key ASC,
                         mapping.memory_id ASC
                """,
                (*params, generation, normalized_scope),
            )
            grouped: dict[str, dict[str, Any]] = {}
            valid_counts: dict[str, int] = {}
            for row in await cursor.fetchall():
                topic_key = str(row[0])
                grouped.setdefault(
                    topic_key,
                    {
                        "topic_key": topic_key,
                        "display_topic": str(row[1]),
                        "active_source_count": int(row[2]),
                        "first_seen_at": float(row[3]),
                        "last_seen_at": float(row[4]),
                        "chat_type": str(row[5]),
                        "privacy_level": str(row[6]),
                    },
                )
                if row[7] is None:
                    return ()
                source = self._source_from_values(int(row[7]), row[9], row[10], row[11])
                if (
                    source is None
                    or source.scope_key != normalized_scope
                    or source.privacy_level != privacy_level
                    or source.chat_type != row[5]
                    or source.resolver_revision != normalized_resolver_revision
                    or source.source_revision != str(row[8])
                    or not any(key == topic_key for key, _ in source.topics)
                ):
                    return ()
                valid_counts[topic_key] = valid_counts.get(topic_key, 0) + 1
            if any(
                valid_counts.get(key, 0) != int(item["active_source_count"])
                for key, item in grouped.items()
            ):
                return ()
            return tuple(grouped.values())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("话题目录作用域读取失败", exc_info=True)
            return ()

    async def _read_current_source(
        self,
        db: Any,
        memory_id: int,
    ) -> TopicCatalogSource | None:
        """读取并 fail-closed 校验当前 canonical source。"""

        cursor = await db.execute(
            "SELECT id, metadata, created_at, updated_at FROM documents WHERE id = ?",
            (memory_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._source_from_values(int(row[0]), row[1], row[2], row[3])

    def _source_from_values(
        self,
        memory_id: int,
        raw_metadata: Any,
        created_at: Any,
        updated_at: Any,
    ) -> TopicCatalogSource | None:
        """从同一 SQLite 查询快照校验并构造 canonical source。"""

        try:
            metadata = (
                json.loads(raw_metadata or "{}")
                if isinstance(raw_metadata, str)
                else raw_metadata
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(metadata, dict):
            return None
        if not is_memory_recallable(metadata) or is_mark_write(metadata):
            return None
        if metadata.get("summary_source_orphan") is True:
            return None
        if metadata.get("source_provenance_complete") is not True:
            return None
        scope_key = metadata.get("scope_key")
        privacy_level = metadata.get("privacy_level")
        chat_type = metadata.get("chat_type")
        resolver_revision = metadata.get("resolver_revision")
        if (
            not isinstance(scope_key, str)
            or not scope_key.strip()
            or len(scope_key.strip()) > 256
            or chat_type not in _ALLOWED_CHAT_TYPES
            or privacy_level not in _ALLOWED_PRIVACY
            or not isinstance(resolver_revision, str)
            or not resolver_revision.strip()
        ):
            return None
        raw_topics = metadata.get("topics")
        if not isinstance(raw_topics, (list, tuple)):
            return None
        topics: dict[str, str] = {}
        for raw_topic in raw_topics:
            display_topic = normalize_topic_label(raw_topic)
            topic_key = normalize_topic_key(raw_topic)
            if display_topic is not None and topic_key is not None:
                topics.setdefault(topic_key, display_topic)
        revision = updated_at or created_at
        if not topics or revision is None or not str(revision).strip():
            return None
        observed_at = self._timestamp(metadata.get("topic_observed_at") or created_at)
        return TopicCatalogSource(
            memory_id=memory_id,
            source_revision=str(revision).strip(),
            scope_key=scope_key.strip(),
            chat_type=str(chat_type),
            privacy_level=str(privacy_level),
            resolver_revision=resolver_revision.strip(),
            topics=tuple(sorted(topics.items())),
            observed_at=observed_at,
        )

    async def _rebuild_scope_aggregate(
        self,
        db: Any,
        generation: int,
        scope_key: str,
        chat_type: str,
        privacy_level: str,
    ) -> None:
        """从当前 source mappings 重算一个 scope 的聚合行。"""

        await db.execute(
            """
            DELETE FROM scope_topics
            WHERE generation = ? AND scope_key = ?
              AND chat_type = ? AND privacy_level = ?
            """,
            (generation, scope_key, chat_type, privacy_level),
        )
        cursor = await db.execute(
            """
            SELECT topic_key, COUNT(*) AS source_count,
                   MIN(observed_at) AS first_seen_at, MAX(observed_at) AS last_seen_at
            FROM memory_topic_sources
            WHERE generation = ? AND scope_key = ?
              AND chat_type = ? AND privacy_level = ?
            GROUP BY topic_key
            """,
            (generation, scope_key, chat_type, privacy_level),
        )
        rows = await cursor.fetchall()
        for row in rows:
            display = await (
                await db.execute(
                    """
                    SELECT display_topic
                    FROM memory_topic_sources
                    WHERE generation = ? AND scope_key = ?
                      AND chat_type = ? AND privacy_level = ? AND topic_key = ?
                    ORDER BY observed_at DESC, memory_id DESC
                    LIMIT 1
                    """,
                    (generation, scope_key, chat_type, privacy_level, row[0]),
                )
            ).fetchone()
            if display is None:
                continue
            await db.execute(
                """
                INSERT INTO scope_topics(
                    generation, scope_key, chat_type, privacy_level, topic_key,
                    display_topic, active_source_count, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    generation,
                    scope_key,
                    chat_type,
                    privacy_level,
                    str(row[0]),
                    str(display[0]),
                    int(row[1]),
                    float(row[2]),
                    float(row[3]),
                ),
            )

    def _catalog_transaction(self):
        """返回 catalog 写事务上下文，保持统一进程写锁与回滚语义。"""

        from ..application.write_coordinator import coordinated_transaction

        if self._db is None:
            raise RuntimeError("TopicCatalogStore 尚未绑定数据库连接")
        return coordinated_transaction(self._db)

    @staticmethod
    def _timestamp(value: Any) -> float:
        """把 SQLite UTC 时间值转换为非负 Unix 秒。"""

        try:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return max(0.0, float(value))
            text = str(value).strip()
            if not text:
                return 0.0
            try:
                return max(0.0, float(text))
            except ValueError:
                normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
                parsed = datetime.fromisoformat(normalized)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return max(0.0, parsed.timestamp())
        except (TypeError, ValueError, OverflowError):
            return 0.0


__all__ = ["TopicCatalogDirty", "TopicCatalogSource", "TopicCatalogStore"]
