"""用户画像标签的 SQLite 事务操作与行转换。"""

from __future__ import annotations

from typing import Any

import aiosqlite

from ....models.domain_provenance import (
    DomainObjectOrigin,
    DomainProvenance,
    merge_domain_provenance,
)
from ...memory.infrastructure.base import BaseStore
from ...memory.infrastructure.canonical_source_validation import (
    validate_domain_provenance,
)
from ...memory.infrastructure.domain_object_integrity import (
    filter_current_domain_objects,
)
from ..domain.models import TagCategory, UserTag


class ProfileTagMixin(BaseStore):
    """为 ProfileStore 提供标签读取、写入和来源过滤能力。"""

    async def _get_tags(self, user_id: str) -> list[UserTag]:
        """读取指定画像的标签，并过滤已经失效的派生来源。"""

        async with self._connect() as db:
            cursor = await db.execute(
                """SELECT * FROM user_tags WHERE user_id = ?
                   ORDER BY confidence DESC, category ASC, value ASC""",
                (user_id,),
            )
            rows = await cursor.fetchall()
            return await filter_current_domain_objects(
                db,
                [self._row_to_tag(row) for row in rows],
            )

    async def _replace_tags_with_db(
        self,
        db: aiosqlite.Connection,
        user_id: str,
        tags: list[UserTag],
        now: float,
    ) -> None:
        """在调用方事务内替换管理员标签并强制写入人工来源。"""

        await db.execute("DELETE FROM user_tags WHERE user_id = ?", (user_id,))
        for tag in tags:
            await db.execute(
                """INSERT INTO user_tags
                   (user_id, category, value, confidence, source,
                    created_at, last_seen_at, occurrence_count, provenance_json)
                   VALUES (?, ?, ?, ?, 'manual', ?, ?, 1, ?)""",
                (
                    user_id,
                    tag.category.value,
                    tag.value,
                    tag.confidence,
                    now,
                    now,
                    self._to_json(
                        DomainProvenance(DomainObjectOrigin.MANUAL).to_dict()
                    ),
                ),
            )

    async def _upsert_tag_with_db(
        self,
        db: aiosqlite.Connection,
        user_id: str,
        tag: UserTag,
    ) -> bool:
        """合并单个标签，并保证人工来源不会被派生来源替换。"""

        if tag.provenance is not None:
            await validate_domain_provenance(db, tag.provenance)
        cursor = await db.execute(
            """SELECT id, occurrence_count, confidence, source, provenance_json
               FROM user_tags
               WHERE user_id = ? AND category = ? AND value = ?""",
            (user_id, tag.category.value, tag.value),
        )
        existing = await cursor.fetchone()
        if existing:
            existing_provenance = None
            if existing[4]:
                existing_provenance = DomainProvenance.from_dict(
                    self._from_json(existing[4])
                )
            elif str(existing[3] or "") == "manual":
                existing_provenance = DomainProvenance(DomainObjectOrigin.MANUAL)
            merged_provenance = (
                merge_domain_provenance(existing_provenance, tag.provenance)
                if tag.provenance is not None
                else existing_provenance
            )
            merged_source = (
                "manual"
                if merged_provenance is not None
                and merged_provenance.origin is DomainObjectOrigin.MANUAL
                else tag.source
            )
            await db.execute(
                """UPDATE user_tags SET confidence = ?, last_seen_at = ?,
                   occurrence_count = ?, source = ?, provenance_json = ?
                   WHERE id = ?""",
                (
                    max(existing[2], tag.confidence),
                    tag.last_seen_at,
                    existing[1] + 1,
                    merged_source,
                    (
                        self._to_json(merged_provenance.to_dict())
                        if merged_provenance is not None
                        else None
                    ),
                    existing[0],
                ),
            )
            return False
        await db.execute(
            """INSERT INTO user_tags
               (user_id, category, value, confidence, source,
                created_at, last_seen_at, occurrence_count, provenance_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                tag.category.value,
                tag.value,
                tag.confidence,
                tag.source,
                tag.created_at,
                tag.last_seen_at,
                tag.occurrence_count,
                (
                    self._to_json(tag.provenance.to_dict())
                    if tag.provenance is not None
                    else None
                ),
            ),
        )
        return True

    async def add_tag(self, user_id: str, tag: UserTag) -> bool:
        """添加人工标签并保留人工来源。"""

        async with self._connect() as db:
            inserted = await self._upsert_tag_with_db(db, user_id, tag)
            await db.commit()
            return inserted

    async def remove_tag(self, user_id: str, category: str, value: str) -> bool:
        """删除指定画像标签。"""

        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM user_tags WHERE user_id = ? AND category = ? AND value = ?",
                (user_id, category, value),
            )
            await db.commit()
            return cursor.rowcount > 0

    @staticmethod
    def _row_to_tag(row: Any) -> UserTag:
        """把标签行转换为模型，并兼容缺少 provenance 的旧行。"""

        provenance_data = (
            BaseStore._from_json(row[9]) if len(row) > 9 and row[9] else None
        )
        return UserTag(
            category=TagCategory(str(row[2] or "custom")),
            value=str(row[3] or ""),
            confidence=float(row[4] or 0.5),
            source=str(row[5] or "auto"),
            created_at=float(row[6] or 0),
            last_seen_at=float(row[7] or 0),
            occurrence_count=int(row[8] or 1),
            provenance=(
                DomainProvenance.from_dict(provenance_data)
                if isinstance(provenance_data, dict)
                else None
            ),
        )


__all__ = ["ProfileTagMixin"]
