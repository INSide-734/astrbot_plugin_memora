"""验证 ProfileStore 人工入口不会接受 derived 偏好来源。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.base.entity_editing import compute_entity_revision
from core.models.domain_provenance import DomainObjectOrigin, DomainProvenance
from core.models.memory_evolution import MemorySourceRef
from core.models.user_profile import UserPreferences
from core.storage.profile_store import ProfileStore


_DERIVED_PREFERENCES = UserPreferences(
    reply_style="formal",
    provenance=DomainProvenance(
        DomainObjectOrigin.DERIVED,
        (
            MemorySourceRef(
                memory_id=17,
                revision_token="rev-17",
                scope_key="private:user-a",
                privacy_level="confidential",
                occurred_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
            ),
        ),
    ),
)


@pytest.mark.asyncio
async def test_create_profile_strict_rejects_derived_preferences(tmp_db_path: str) -> None:
    """严格创建只能接收人工偏好。"""

    store = ProfileStore(tmp_db_path)
    await store.init_table()

    with pytest.raises(ValueError, match="derived_preferences_not_allowed"):
        await store.create_profile_strict(
            "manual-boundary-create",
            preferences=_DERIVED_PREFERENCES,
        )

    assert await store.get_profile("manual-boundary-create") is None


@pytest.mark.asyncio
async def test_replace_editable_fields_rejects_derived_preferences(
    tmp_db_path: str,
) -> None:
    """管理员字段替换不能把自动派生偏好写入人工入口。"""

    store = ProfileStore(tmp_db_path)
    await store.init_table()
    await store.create_profile("manual-boundary-replace")
    current = await store.get_profile("manual-boundary-replace")
    assert current is not None

    with pytest.raises(ValueError, match="derived_preferences_not_allowed"):
        await store.replace_editable_fields(
            "manual-boundary-replace",
            display_name="管理员名称",
            preferences=_DERIVED_PREFERENCES,
            tags=[],
            expected_revision=compute_entity_revision(current.to_dict()),
        )

    unchanged = await store.get_profile("manual-boundary-replace")
    assert unchanged is not None
    assert unchanged.preferences.reply_style == "casual"


@pytest.mark.asyncio
async def test_update_profile_fields_atomic_rejects_derived_preferences(
    tmp_db_path: str,
) -> None:
    """局部人工更新不能绕过来源边界。"""

    store = ProfileStore(tmp_db_path)
    await store.init_table()
    await store.create_profile("manual-boundary-partial")

    with pytest.raises(ValueError, match="derived_preferences_not_allowed"):
        await store.update_profile_fields_atomic(
            "manual-boundary-partial",
            preferences=_DERIVED_PREFERENCES,
        )

    unchanged = await store.get_profile("manual-boundary-partial")
    assert unchanged is not None
    assert unchanged.preferences.reply_style == "casual"


@pytest.mark.asyncio
async def test_update_profile_rejects_derived_preferences_without_mutating_model(
    tmp_db_path: str,
) -> None:
    """完整人工快照拒绝 derived 偏好，且不修改传入模型时间。"""

    store = ProfileStore(tmp_db_path)
    await store.init_table()
    profile = await store.create_profile("manual-boundary-full")
    profile.preferences = _DERIVED_PREFERENCES
    original_updated_at = profile.updated_at

    with pytest.raises(ValueError, match="derived_preferences_not_allowed"):
        await store.update_profile(profile)

    assert profile.updated_at == original_updated_at
    persisted = await store.get_profile("manual-boundary-full")
    assert persisted is not None
    assert persisted.preferences.reply_style == "casual"
