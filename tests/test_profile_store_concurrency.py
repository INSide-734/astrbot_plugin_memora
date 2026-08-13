"""验证自动画像写入与管理员并发修改之间的持久化边界。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch

import aiosqlite
import pytest

from core.features.profiles.application.profile_manager import ProfileManager
from core.features.profiles.domain.models import TagCategory, UserPreferences, UserTag
from core.features.profiles.infrastructure.profile_store import ProfileStore
from core.shared.domain_provenance import DomainObjectOrigin, DomainProvenance
from core.shared.contracts import MemorySourceRef
from core.shared.entity_editing import (
    EntityNotFoundError,
    compute_entity_revision,
)

_DERIVED_PROVENANCE = DomainProvenance(
    DomainObjectOrigin.DERIVED,
    (
        MemorySourceRef(
            17,
            "rev-17",
            "private:user1",
            "confidential",
            datetime(2026, 7, 21, tzinfo=timezone.utc),
        ),
    ),
)


async def _create_canonical_source(db_path: str) -> None:
    """写入并发测试所需的匿名 canonical source。"""

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS documents (
                   id INTEGER PRIMARY KEY, text TEXT NOT NULL, metadata TEXT,
                   created_at TEXT NOT NULL, updated_at TEXT NOT NULL
               )"""
        )
        await db.execute(
            """INSERT INTO documents(id,text,metadata,created_at,updated_at)
               VALUES(?,?,?,?,?)""",
            (
                17,
                "匿名 canonical 正文",
                json.dumps(
                    {
                        "scope_key": "private:user1",
                        "privacy_level": "confidential",
                    },
                    ensure_ascii=False,
                ),
                "2026-07-21T00:00:00+00:00",
                "rev-17",
            ),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_ingest_tags_does_not_restore_stale_admin_fields(
    tmp_db_path: str,
) -> None:
    """自动标签合并不得用旧画像快照覆盖管理员刚提交的字段。"""

    store = ProfileStore(tmp_db_path)
    await store.init_table()
    await _create_canonical_source(tmp_db_path)
    await store.create_profile_strict(
        "interleaved-user",
        display_name="Before admin",
        preferences=UserPreferences(reply_style="casual"),
    )
    manager = ProfileManager(profile_store=store)
    original_ensure = manager.ensure_profile

    async def ensure_then_admin_update(user_id: str):
        """先返回旧快照，再模拟管理员提交更新。"""

        stale_snapshot = await original_ensure(user_id)
        current = await store.get_profile(user_id)
        assert current is not None
        await store.replace_editable_fields(
            user_id,
            display_name="Admin name",
            preferences=UserPreferences(
                reply_style="formal",
                preferred_topics=["admin-topic"],
            ),
            tags=current.tags,
            expected_revision=compute_entity_revision(current.to_dict()),
        )
        return stale_snapshot

    with patch.object(
        manager,
        "ensure_profile",
        new=ensure_then_admin_update,
    ):
        result = await manager.ingest_tags(
            "interleaved-user",
            [
                UserTag(
                    category=TagCategory.INTEREST,
                    value="automatic-tag",
                    confidence=0.8,
                )
            ],
            provenance=_DERIVED_PROVENANCE,
        )

    persisted = await store.get_profile("interleaved-user")
    assert persisted is not None
    assert result.display_name == "Admin name"
    assert persisted.display_name == "Admin name"
    assert persisted.preferences.reply_style == "formal"
    assert persisted.preferences.preferred_topics == ["admin-topic"]
    assert "automatic-tag" in [tag.value for tag in persisted.tags]


@pytest.mark.asyncio
async def test_ingest_tags_does_not_restore_profile_deleted_after_ensure(
    tmp_db_path: str,
) -> None:
    """画像在 ensure 后被删除时，自动标签不得恢复旧对象。"""

    store = ProfileStore(tmp_db_path)
    await store.init_table()
    await _create_canonical_source(tmp_db_path)
    await store.create_profile_strict("deleted-during-ingest")
    manager = ProfileManager(profile_store=store)
    original_ensure = manager.ensure_profile

    async def ensure_then_delete(user_id: str):
        """先返回旧快照，再模拟管理员删除画像。"""

        stale_snapshot = await original_ensure(user_id)
        assert await store.delete_profile(user_id) is True
        return stale_snapshot

    with patch.object(manager, "ensure_profile", new=ensure_then_delete):
        with pytest.raises(EntityNotFoundError, match="画像不存在"):
            await manager.ingest_tags(
                "deleted-during-ingest",
                [UserTag(value="must-not-persist")],
                provenance=_DERIVED_PROVENANCE,
            )

    assert await store.get_profile("deleted-during-ingest") is None
    assert await store._get_tags("deleted-during-ingest") == []
