"""协议身份名称观察服务的业务语义测试。"""

from __future__ import annotations

import asyncio

import aiosqlite
import pytest
import pytest_asyncio

from core.features.identity import ProtocolIdentityStore
from core.identity import IdentityTrust, NameFieldState, ResolvedIdentity
from core.identity.service import ProtocolIdentityService


def _identity(
    *,
    user_id: str = "10001",
    group_id: str = "20001",
    global_name: str | None = "昵称甲",
    scope_name: str | None = "群名片甲",
    observed_at: float = 100.0,
    trust_status: IdentityTrust = IdentityTrust.TRUSTED,
    nickname_state: NameFieldState = NameFieldState.VALID,
    card_state: NameFieldState = NameFieldState.VALID,
) -> ResolvedIdentity:
    """构造名称观察服务使用的可信或不可信身份快照。"""

    return ResolvedIdentity(
        protocol="onebot11",
        identity_namespace="qq",
        stable_user_id=user_id if trust_status is IdentityTrust.TRUSTED else None,
        canonical_user_id=user_id if trust_status is IdentityTrust.TRUSTED else None,
        scope_type="group",
        scope_id=group_id,
        global_name=global_name,
        scope_name=scope_name,
        display_name=scope_name or global_name or user_id,
        observed_at=observed_at,
        trust_status=trust_status,
        name_field_states={
            "nickname": nickname_state,
            "card": card_state,
        },
        conversation_sender_id=user_id,
        identity_label=f"QQ:{user_id}",
    )


@pytest_asyncio.fixture
async def identity_service(tmp_db_path: str):
    """提供已初始化的身份 Store 和 Service。"""

    store = ProtocolIdentityStore(tmp_db_path)
    await store.initialize()
    yield ProtocolIdentityService(store), store
    await store.close()


@pytest.mark.asyncio
async def test_newer_name_wins_and_old_name_becomes_alias(identity_service) -> None:
    """较新的昵称和群名片应覆盖当前值，旧值进入对应别名作用域。"""

    service, store = identity_service
    await service.observe(_identity())
    await service.observe(
        _identity(global_name="新昵称", scope_name="新群名片", observed_at=200.0)
    )

    current = await store.get_identity("qq", "10001", "group", "20001")
    assert current is not None
    assert current.global_name == "新昵称"
    assert current.scope_name == "新群名片"
    assert current.display_name == "新群名片"
    assert await store.find_aliases("qq", "10001", "global", "") == ["昵称甲"]
    assert await store.find_aliases("qq", "10001", "group", "20001") == ["群名片甲"]


@pytest.mark.asyncio
async def test_older_observation_adds_alias_without_rolling_back_current_name(
    identity_service,
) -> None:
    """较旧事件只能补充历史别名，不得回滚当前昵称或群名片。"""

    service, store = identity_service
    await service.observe(
        _identity(global_name="当前昵称", scope_name="当前群名片", observed_at=200.0)
    )
    await service.observe(
        _identity(global_name="旧昵称", scope_name="旧群名片", observed_at=100.0)
    )

    current = await store.get_identity("qq", "10001", "group", "20001")
    assert current is not None
    assert current.global_name == "当前昵称"
    assert current.scope_name == "当前群名片"
    assert await store.find_aliases("qq", "10001", "global", "") == ["旧昵称"]
    assert await store.find_aliases("qq", "10001", "group", "20001") == ["旧群名片"]


@pytest.mark.asyncio
async def test_same_observation_time_latest_arrival_wins(identity_service) -> None:
    """观察时间相同应按到达顺序采用最新名称并保留旧别名。"""

    service, store = identity_service
    await service.observe(
        _identity(global_name="第一次", scope_name="第一次", observed_at=100.0)
    )
    await service.observe(
        _identity(global_name="第二次", scope_name="第二次", observed_at=100.0)
    )

    current = await store.get_identity("qq", "10001", "group", "20001")
    assert current is not None
    assert current.global_name == "第二次"
    assert current.scope_name == "第二次"
    assert await store.find_aliases("qq", "10001", "global", "") == ["第一次"]
    assert await store.find_aliases("qq", "10001", "group", "20001") == ["第一次"]


@pytest.mark.asyncio
async def test_group_name_isolated_between_scopes(identity_service) -> None:
    """同一 QQ 在不同群的群名片必须彼此隔离。"""

    service, store = identity_service
    await service.observe(_identity(group_id="20001", scope_name="一群名片"))
    await service.observe(_identity(group_id="20002", scope_name="二群名片"))

    first = await store.get_identity("qq", "10001", "group", "20001")
    second = await store.get_identity("qq", "10001", "group", "20002")
    assert first is not None and second is not None
    assert first.scope_name == "一群名片"
    assert second.scope_name == "二群名片"
    assert first.global_name == second.global_name == "昵称甲"


@pytest.mark.asyncio
async def test_explicit_empty_card_clears_current_card_and_preserves_alias(
    identity_service,
) -> None:
    """较新的显式空 card 应清除当前群名片并留下旧别名。"""

    service, store = identity_service
    await service.observe(_identity(scope_name="待删除", observed_at=100.0))
    await service.observe(
        _identity(
            global_name="昵称甲",
            scope_name=None,
            card_state=NameFieldState.EMPTY,
            observed_at=200.0,
        )
    )

    current = await store.get_identity("qq", "10001", "group", "20001")
    assert current is not None
    assert current.scope_name is None
    assert current.display_name == "昵称甲"
    assert await store.find_aliases("qq", "10001", "group", "20001") == ["待删除"]


@pytest.mark.asyncio
async def test_missing_or_invalid_card_preserves_previous_card(
    identity_service,
) -> None:
    """缺失或非法 card 不得误删已有群名片。"""

    service, store = identity_service
    await service.observe(_identity(scope_name="保留名片", observed_at=100.0))
    await service.observe(
        _identity(scope_name=None, card_state=NameFieldState.MISSING, observed_at=200.0)
    )
    await service.observe(
        _identity(scope_name=None, card_state=NameFieldState.INVALID, observed_at=300.0)
    )

    current = await store.get_identity("qq", "10001", "group", "20001")
    assert current is not None
    assert current.scope_name == "保留名片"


@pytest.mark.asyncio
async def test_empty_nickname_preserves_previous_global_name(identity_service) -> None:
    """空昵称不得覆盖已有有效全局昵称。"""

    service, store = identity_service
    await service.observe(_identity(global_name="有效昵称", observed_at=100.0))
    await service.observe(
        _identity(
            global_name=None, nickname_state=NameFieldState.EMPTY, observed_at=200.0
        )
    )

    current = await store.get_identity("qq", "10001", "group", "20001")
    assert current is not None
    assert current.global_name == "有效昵称"


@pytest.mark.asyncio
async def test_reobserving_same_names_blocks_intermediate_replay(
    identity_service,
) -> None:
    """较晚重复观察同名时也应推进时间，阻止中间时间旧名回滚。"""

    service, store = identity_service
    await service.observe(
        _identity(global_name="当前昵称", scope_name="当前名片", observed_at=100.0)
    )
    await service.observe(
        _identity(global_name="当前昵称", scope_name="当前名片", observed_at=300.0)
    )
    await service.observe(
        _identity(global_name="旧昵称", scope_name="旧名片", observed_at=200.0)
    )

    current = await store.get_identity("qq", "10001", "group", "20001")
    assert current is not None
    assert current.global_name == "当前昵称"
    assert current.scope_name == "当前名片"
    assert current.global_name_updated_at == 300.0
    assert current.scope_name_updated_at == 300.0
    assert await store.find_aliases("qq", "10001", "global", "") == ["旧昵称"]
    assert await store.find_aliases("qq", "10001", "group", "20001") == ["旧名片"]


@pytest.mark.asyncio
async def test_older_card_cannot_restore_explicitly_cleared_card(
    identity_service,
) -> None:
    """显式清空群名片后，更旧的有效名片只能成为别名。"""

    service, store = identity_service
    await service.observe(_identity(scope_name="历史名片", observed_at=100.0))
    await service.observe(
        _identity(scope_name=None, card_state=NameFieldState.EMPTY, observed_at=300.0)
    )
    await service.observe(_identity(scope_name="重放名片", observed_at=200.0))

    current = await store.get_identity("qq", "10001", "group", "20001")
    assert current is not None
    assert current.scope_name is None
    assert current.scope_name_updated_at == 300.0
    assert set(await store.find_aliases("qq", "10001", "group", "20001")) == {
        "历史名片",
        "重放名片",
    }


@pytest.mark.asyncio
async def test_admin_display_name_has_priority_and_is_read_only(
    identity_service,
) -> None:
    """管理员画像备注应优先展示且不被协议观察覆盖。"""

    service, store = identity_service
    async with aiosqlite.connect(store.db_path) as connection:
        await connection.execute(
            "CREATE TABLE user_profiles (user_id TEXT PRIMARY KEY, display_name TEXT)"
        )
        await connection.execute(
            "INSERT INTO user_profiles(user_id, display_name) VALUES (?, ?)",
            ("10001", "管理员备注"),
        )
        await connection.commit()

    await service.observe(_identity(global_name="昵称甲", scope_name="群名片甲"))
    await service.observe(
        _identity(global_name="新昵称", scope_name="新群名片", observed_at=200.0)
    )

    current = await store.get_identity("qq", "10001", "group", "20001")
    assert current is not None
    assert current.display_name == "管理员备注"
    assert current.global_name == "新昵称"
    assert current.scope_name == "新群名片"


@pytest.mark.asyncio
async def test_same_name_for_two_users_does_not_merge(identity_service) -> None:
    """不同 QQ 使用同名时仍应保持两个独立身份和别名目录。"""

    service, store = identity_service
    await service.observe(_identity(user_id="10001", global_name="同名"))
    await service.observe(_identity(user_id="10002", global_name="同名"))

    first = await store.get_identity("qq", "10001", "group", "20001")
    second = await store.get_identity("qq", "10002", "group", "20001")
    assert first is not None and second is not None
    assert first.canonical_user_id == "10001"
    assert second.canonical_user_id == "10002"


@pytest.mark.asyncio
async def test_untrusted_identity_is_ignored(identity_service) -> None:
    """冲突、非法或匿名身份不得写入身份目录。"""

    service, store = identity_service
    await service.observe(_identity(trust_status=IdentityTrust.CONFLICT))
    await service.observe(_identity(trust_status=IdentityTrust.ANONYMOUS))

    assert await store.get_identity("qq", "10001", "group", "20001") is None


@pytest.mark.asyncio
async def test_cancelled_error_propagates_from_store(
    identity_service, monkeypatch
) -> None:
    """身份观察不能吞掉底层 Store 的取消信号。"""

    service, _store = identity_service

    async def cancel(*args, **kwargs):
        """模拟底层数据库操作被取消。"""

        raise asyncio.CancelledError

    monkeypatch.setattr(_store, "merge_observation", cancel)

    with pytest.raises(asyncio.CancelledError):
        await service.observe(_identity())
