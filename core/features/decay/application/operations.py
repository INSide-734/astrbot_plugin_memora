"""记忆重要性衰减与访问强化操作。"""

import asyncio
import json
import time
from typing import Any

from astrbot.api import logger

from ....utils.number_utils import clamp_float, safe_float
from ...memory.application.write_coordinator import (
    ConnectionRegistry,
    check_db_alive,
    coordinated_transaction,
    is_connection_fatal,
)
from ...memory.infrastructure.write_op_serialization import safe_json_dict


def _normalize_batch_metadata(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把一批文档的 JSON 字符串 metadata 统一转换为字典。

    参数:
        docs: 待原地规范化的文档字典列表。

    返回:
        保持原顺序的同一文档列表。
    """

    for doc in docs:
        metadata = doc.get("metadata")
        if isinstance(metadata, str):
            try:
                doc["metadata"] = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                doc["metadata"] = {}
        elif not isinstance(metadata, dict):
            doc["metadata"] = {}
    return docs


class DecayOperationsMixin:
    """提供每日衰减与访问时间更新操作。"""

    @staticmethod
    def _type_decay_multiplier(memory_type: str | None) -> float:
        """返回不同记忆类型的衰减倍率，未知类型保持中性。"""

        if not memory_type:
            return 1.0
        multipliers = {
            "EPISODIC": 1.5,
            "FACTUAL": 0.5,
            "PREFERENCE": 0.7,
            "RELATIONAL": 0.6,
        }
        return multipliers.get(memory_type.upper(), 1.0)

    async def apply_daily_decay(self, decay_rate: float, days: int = 1) -> int:
        """在单一协调事务中批量应用重要性衰减。

        参数:
            decay_rate: 每日基础衰减率，运行时限制到 ``0..1``。
            days: 本次补偿执行覆盖的正整数天数。

        返回:
            实际更新的记忆记录数；可恢复失败时返回 0。
        """

        if decay_rate <= 0 or days <= 0:
            return 0

        if self._db is None:
            logger.error("[衰减] 数据库连接未初始化")
            return 0
        if not check_db_alive(self._db):
            logger.warning("[衰减] 数据库连接坏死，尝试自动重连……")
            if not await ConnectionRegistry.try_repair():
                logger.error("[衰减] 自动重连失败，跳过衰减")
                return 0

        try:
            if decay_rate >= 1:
                decay_rate = 1.0
            access_window_days = float(
                self._config.get("access_decay_window_days", 30.0)
            )
            max_access_count = float(self._config.get("access_decay_max_count", 10.0))
            access_decay_multiplier = float(
                self._config.get("access_count_decay_multiplier", 0.5)
            )
            access_window_start = time.time() - max(1.0, access_window_days) * 86400.0
            access_decay_multiplier = max(0.0, min(1.0, access_decay_multiplier))
            decay_run_date = time.strftime("%Y-%m-%d", time.localtime())
            async with coordinated_transaction(self._db) as db:
                cursor = await db.execute(
                    "SELECT id, metadata FROM documents WHERE "
                    "json_extract(metadata, '$.importance') IS NOT NULL "
                    "OR metadata LIKE '%\"importance\"%'"
                )
                rows = await cursor.fetchall()
                updates: list[tuple[str, int]] = []

                flashbulb_enabled = bool(self._config.get("flashbulb.enabled", True))
                flashbulb_threshold = float(
                    self._config.get("flashbulb.intensity_threshold", 0.90)
                )

                for row in rows:
                    metadata = safe_json_dict(row["metadata"])
                    if metadata.get("last_decay_date") == decay_run_date:
                        continue
                    importance = clamp_float(metadata.get("importance"), default=0.5)
                    access_count = safe_float(metadata.get("access_count"), 0.0)
                    last_access_time = safe_float(metadata.get("last_access_time"), 0.0)

                    if flashbulb_enabled:
                        intensity = safe_float(metadata.get("emotional_intensity"), 0.0)
                        if intensity >= flashbulb_threshold:
                            continue

                    recent_access_factor = (
                        1.0 if last_access_time >= access_window_start else 0.5
                    )
                    access_factor = min(1.0, access_count / max(1.0, max_access_count))
                    effective_decay_rate = decay_rate * (
                        1 - 0.5 * access_factor * recent_access_factor
                    )
                    if self._config.get(
                        "human_like_memory.type_aware_decay_enabled",
                        True,
                    ):
                        memory_type = metadata.get("memory_type")
                        type_mult = self._type_decay_multiplier(memory_type)
                        emotional_intensity = safe_float(
                            metadata.get("emotional_intensity"), 0.5
                        )
                        if emotional_intensity > 0.7:
                            emotion_protection = 0.3
                        elif emotional_intensity >= 0.3:
                            emotion_protection = 0.7
                        else:
                            emotion_protection = 1.0
                        effective_decay_rate *= type_mult * emotion_protection
                        effective_decay_rate = min(1.0, effective_decay_rate)
                    decay_factor = (1 - effective_decay_rate) ** days
                    metadata["importance"] = max(
                        0.01,
                        round(importance * decay_factor, 4),
                    )
                    metadata["access_count"] = int(
                        access_count * access_decay_multiplier
                    )
                    metadata["last_decay_date"] = decay_run_date
                    metadata["last_decay_days"] = days
                    updates.append(
                        (json.dumps(metadata, ensure_ascii=False), int(row["id"]))
                    )

                if not updates:
                    return 0

                await db.executemany(
                    "UPDATE documents SET metadata = ? WHERE id = ?",
                    updates,
                )
            affected = len(updates)

            logger.info(
                f"[衰减] 批量衰减完成: 衰减率={decay_rate}, 天数={days}, "
                f"访问窗口={access_window_days:.1f}天, 影响记录={affected}"
            )

            if self._invalidate_cache:
                self._invalidate_cache()
            return affected

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if is_connection_fatal(exc):
                logger.error(f"[衰减] 连接坏死，批量衰减中止: {exc}")
            else:
                logger.error(f"[衰减] 批量衰减失败: {exc}", exc_info=True)
            return 0

    async def update_access_time(
        self, memory_id: int, recall_type: str = "passive"
    ) -> bool:
        """更新单条记忆的最后访问时间、计数和重要性。

        高频调用场景应使用 ``update_access_times_batch``，避免 SQLite
        写锁竞争。

        参数:
            memory_id: 待更新的 canonical 记忆 ID。
            recall_type: ``active`` 使用较高强化增量，其余值按被动召回处理。

        返回:
            更新成功时返回真；记录缺失或可恢复失败时返回假。
        """

        current_time = time.time()

        try:
            if self._db is None:
                return False
            if not check_db_alive(self._db):
                logger.warning("[访问] 数据库连接坏死，尝试自动重连……")
                if not await ConnectionRegistry.try_repair():
                    logger.error("[访问] 自动重连失败，跳过更新时间")
                    return False

            async with coordinated_transaction(self._db) as db:
                cursor = await db.execute(
                    "SELECT metadata FROM documents WHERE id = ?", (memory_id,)
                )
                row = await cursor.fetchone()

                if not row:
                    return False

                metadata_str = row[0] if row[0] else "{}"
                try:
                    metadata = (
                        json.loads(metadata_str)
                        if isinstance(metadata_str, str)
                        else metadata_str
                    )
                    if not isinstance(metadata, dict):
                        metadata = {}
                except (json.JSONDecodeError, TypeError):
                    metadata = {}

                metadata["last_access_time"] = current_time
                try:
                    access_count = int(metadata.get("access_count", 0) or 0)
                except (TypeError, ValueError):
                    access_count = 0
                metadata["access_count"] = min(access_count + 1, 1_000_000)

                importance = float(metadata.get("importance", 0.5))
                if recall_type == "active":
                    importance = min(0.95, importance + 0.05)
                else:
                    importance = min(0.95, importance + 0.01)
                metadata["importance"] = round(importance, 4)

                await db.execute(
                    "UPDATE documents SET metadata = ? WHERE id = ?",
                    (json.dumps(metadata, ensure_ascii=False), memory_id),
                )

            return True

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if is_connection_fatal(exc):
                logger.warning(f"[访问] 连接坏死 (memory_id={memory_id}): {exc}")
            else:
                logger.warning(
                    f"更新访问时间失败 (memory_id={memory_id}): {exc}",
                    exc_info=True,
                )
            return False

    async def update_access_times_batch(
        self, memory_ids: list[int], recall_type: str = "passive"
    ) -> int:
        """批量更新最后访问时间、计数和重要性。

        该路径把多次读取、写入和提交压缩为一次批量读取、一次批量写入和
        一次提交，避免 SQLite 单写者串行化瓶颈。

        参数:
            memory_ids: 待更新的 canonical 记忆 ID，重复项会保序去重。
            recall_type: ``active`` 使用较高强化增量，其余值按被动召回处理。

        返回:
            实际更新的唯一记忆数量；可恢复失败时返回 0。
        """

        if not memory_ids:
            return 0
        if self._db is None:
            return 0
        if not check_db_alive(self._db):
            logger.warning("[访问] 数据库连接坏死，尝试自动重连……")
            if not await ConnectionRegistry.try_repair():
                logger.error("[访问] 自动重连失败，跳过批量更新")
                return 0

        current_time = time.time()
        unique_ids = list(dict.fromkeys(memory_ids))  # 去重保序

        try:
            async with coordinated_transaction(self._db) as db:
                # 批量读取：1 次 SELECT 替代 N 次。
                cursor = await db.execute(
                    """
                    SELECT id, metadata
                    FROM documents
                    WHERE id IN (SELECT value FROM json_each(:memory_ids_json))
                    """,
                    {"memory_ids_json": json.dumps(unique_ids)},
                )
                rows = await cursor.fetchall()
                if not rows:
                    return 0

                importance_delta = 0.05 if recall_type == "active" else 0.01
                updates: list[tuple[str, int]] = []

                for row in rows:
                    metadata_str = row["metadata"] if row["metadata"] else "{}"
                    try:
                        metadata = (
                            json.loads(metadata_str)
                            if isinstance(metadata_str, str)
                            else metadata_str
                        )
                        if not isinstance(metadata, dict):
                            metadata = {}
                    except (json.JSONDecodeError, TypeError):
                        metadata = {}

                    metadata["last_access_time"] = current_time
                    try:
                        access_count = int(metadata.get("access_count", 0) or 0)
                    except (TypeError, ValueError):
                        access_count = 0
                    metadata["access_count"] = min(access_count + 1, 1_000_000)

                    importance = float(metadata.get("importance", 0.5))
                    metadata["importance"] = round(
                        min(0.95, importance + importance_delta), 4
                    )

                    updates.append(
                        (json.dumps(metadata, ensure_ascii=False), int(row["id"]))
                    )

                if not updates:
                    return 0

                # 批量写入后由写协调器提交完整事务。
                await db.executemany(
                    "UPDATE documents SET metadata = ? WHERE id = ?", updates
                )

            return len(updates)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if is_connection_fatal(exc):
                logger.warning(f"[访问] 连接坏死 (ids={unique_ids[:5]}...): {exc}")
            else:
                logger.warning(
                    f"批量更新访问时间失败 (ids={unique_ids[:5]}...): {exc}",
                    exc_info=True,
                )
            return 0


__all__ = ["DecayOperationsMixin"]
