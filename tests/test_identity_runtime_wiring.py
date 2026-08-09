"""协议身份运行时与事件主链接线契约。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.identity import IdentityTrust, ResolvedIdentity


def trusted_identity() -> ResolvedIdentity:
    """构造用于主链测试的可信 OneBot 身份。"""

    return ResolvedIdentity(
        protocol="onebot11",
        identity_namespace="qq",
        stable_user_id="10001",
        canonical_user_id="10001",
        scope_type="private",
        scope_id="10001",
        global_name="昵称甲",
        scope_name=None,
        display_name="昵称甲",
        observed_at=100.0,
        trust_status=IdentityTrust.TRUSTED,
        name_field_states={},
        conversation_sender_id="10001",
        identity_label="QQ:10001",
    )


def conflict_identity() -> ResolvedIdentity:
    """构造不允许用户写入但仍可参与普通召回的冲突身份。"""

    return replace(
        trusted_identity(),
        stable_user_id=None,
        canonical_user_id=None,
        display_name=None,
        trust_status=IdentityTrust.CONFLICT,
        conversation_sender_id=None,
        identity_label=None,
    )


def group_identity() -> ResolvedIdentity:
    """构造带群作用域的可信 OneBot 身份。"""

    return replace(
        trusted_identity(),
        scope_type="group",
        scope_id="20001",
        scope_name="群名片甲",
        display_name="群名片甲",
    )


def unsupported_identity() -> ResolvedIdentity:
    """构造未注册协议使用的安全兼容结果。"""

    return replace(
        conflict_identity(),
        protocol="",
        identity_namespace="",
        scope_type=None,
        scope_id=None,
        observed_at=0.0,
        trust_status=IdentityTrust.UNSUPPORTED,
    )


def anonymous_identity() -> ResolvedIdentity:
    """构造只能用于当前群会话的匿名 opaque 身份。"""

    return replace(
        conflict_identity(),
        identity_namespace="",
        scope_type="group",
        scope_id="20001",
        scope_name="匿名用户",
        display_name="匿名用户",
        trust_status=IdentityTrust.ANONYMOUS,
        conversation_sender_id="anonymous:opaque",
    )


def recall_case() -> SimpleNamespace:
    """构造可执行完整召回入口的最小隔离场景。"""

    from astrbot.api.platform import MessageType

    from core.handlers.recall_handler import RecallHandler

    config = MagicMock()
    config.filtering_settings = {
        "use_persona_filtering": True,
        "use_session_filtering": True,
    }
    config.get.side_effect = lambda key, default=None: {
        "recall_engine.auto_remove_injected": False,
        "recall_engine.top_k": 3,
        "recall_engine.injection_routing_mode": "manual",
        "recall_engine.injection_manual_preset": "balanced",
        "recall_engine.injection_budget_chars": 1000,
        "recall_engine.cognitive_context_budget_chars": 0,
        "recall_engine.proactive_plan_budget_chars": 0,
        "recall_engine.spontaneous_recall_enabled": False,
    }.get(key, default)
    engine = MagicMock()
    engine.search_memories = AsyncMock(return_value=[])
    conversation = MagicMock()
    conversation.add_message_from_event = AsyncMock()
    adapter = MagicMock()
    adapter.capabilities.return_value = ("generic", "test", False)
    handler = RecallHandler(
        context=MagicMock(),
        config_manager=config,
        memory_engine=engine,
        conversation_manager=conversation,
        injection_adapter=adapter,
        enforce_limit_cb=AsyncMock(),
    )
    handler._extractor.get_event_message_str = AsyncMock(return_value="问题")
    handler._query_rewriter.rewrite = AsyncMock(
        return_value=SimpleNamespace(
            intent="default",
            rewritten_queries=[],
            memory_types=[],
            extracted_entities=[],
        )
    )
    handler._maybe_spontaneous_recall = AsyncMock(return_value=[])
    handler._maybe_prospective_recall = AsyncMock(return_value=[])
    handler._build_cognitive_context = AsyncMock(return_value="")
    event = MagicMock()
    event.unified_msg_origin = "aiocqhttp:private:10001"
    event.get_message_type.return_value = MessageType.PRIVATE_MESSAGE
    event.get_sender_id.return_value = "legacy-name"
    request = SimpleNamespace(
        prompt="问题",
        contexts=[],
        extra_user_content_parts=[],
        system_prompt="system",
        provider=None,
        func_tool=None,
        context_headroom_chars=1000,
    )
    return SimpleNamespace(
        handler=handler,
        engine=engine,
        conversation=conversation,
        event=event,
        request=request,
    )


@pytest.mark.asyncio
async def test_runtime_persists_only_trusted_and_unblocked() -> None:
    """可信且未写保护时同步名称，写保护或不可信时只返回解析结果。"""

    from core.identity.runtime import ProtocolIdentityRuntime

    resolver = MagicMock()
    resolver.resolve.return_value = trusted_identity()
    synchronizer = MagicMock()
    synchronizer.synchronize = AsyncMock()
    runtime = ProtocolIdentityRuntime(resolver, synchronizer=synchronizer)
    event = SimpleNamespace(unified_msg_origin="aiocqhttp:private:10001")

    assert runtime.resolve(event) == resolver.resolve.return_value
    resolver.resolve.assert_called_once_with(event)

    resolved = await runtime.prepare(event, writes_blocked=False)
    assert resolved.trust_status is IdentityTrust.TRUSTED
    synchronizer.synchronize.assert_awaited_once_with(
        resolved,
        session_id=event.unified_msg_origin,
    )

    synchronizer.synchronize.reset_mock()
    await runtime.prepare(event, writes_blocked=True)
    synchronizer.synchronize.assert_not_awaited()

    resolver.resolve.return_value = conflict_identity()
    await runtime.prepare(event, writes_blocked=False)
    synchronizer.synchronize.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_degrades_storage_errors_but_propagates_cancellation() -> None:
    """身份目录普通失败不阻断聊天，取消信号必须继续传播。"""

    from core.identity.runtime import ProtocolIdentityRuntime

    resolver = MagicMock()
    resolver.resolve.return_value = trusted_identity()
    synchronizer = MagicMock()
    synchronizer.synchronize = AsyncMock(side_effect=RuntimeError("private"))
    runtime = ProtocolIdentityRuntime(resolver, synchronizer=synchronizer)
    event = SimpleNamespace(unified_msg_origin="session-1")

    assert await runtime.prepare(event) == resolver.resolve.return_value

    synchronizer.synchronize.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await runtime.prepare(event)


@pytest.mark.asyncio
async def test_event_handler_defers_and_deduplicates_identity_sync() -> None:
    """请求与响应共享事件时目录同步只调度一次且不阻塞召回。"""

    from core.event_handler import EventHandler
    from core.identity.runtime import ProtocolIdentityRuntime

    started = asyncio.Event()
    release = asyncio.Event()

    async def synchronize(*_args, **_kwargs) -> None:
        """用事件屏障模拟慢身份目录，不依赖不稳定的计时断言。"""

        started.set()
        await release.wait()

    runtime = ProtocolIdentityRuntime()
    runtime.resolve = MagicMock(return_value=trusted_identity())
    runtime.synchronize = AsyncMock(side_effect=synchronize)
    conversation = MagicMock(identity_runtime=runtime)
    handler = EventHandler(
        context=MagicMock(),
        config_manager=MagicMock(),
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=conversation,
    )
    handler._recall_handler.handle_memory_recall = AsyncMock()
    handler._reflection_handler.handle_memory_reflection = AsyncMock()
    event = MagicMock()

    await handler.handle_memory_recall(event, MagicMock())
    handler._recall_handler.handle_memory_recall.assert_awaited_once()
    assert handler._maintenance_tasks
    await started.wait()

    await handler.handle_memory_reflection(event, MagicMock())
    assert runtime.synchronize.await_count == 1

    release.set()
    await asyncio.gather(*handler._maintenance_tasks)


def test_event_handler_retries_identity_sync_after_scheduling_failure() -> None:
    """目录任务创建失败时清除事件标记，使后续钩子能够重试。"""

    from core.event_handler import EventHandler
    from core.identity.runtime import ProtocolIdentityRuntime

    runtime = ProtocolIdentityRuntime()
    identity = trusted_identity()
    runtime.resolve = MagicMock(return_value=identity)
    runtime.synchronize = MagicMock()
    conversation = MagicMock(identity_runtime=runtime)
    handler = EventHandler(
        context=MagicMock(),
        config_manager=MagicMock(),
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=conversation,
    )
    handler._create_maintenance_task = MagicMock(side_effect=RuntimeError("boom"))
    event = SimpleNamespace()

    assert handler._resolve_identity(event, writes_blocked=False) is identity
    assert handler._resolve_identity(event, writes_blocked=False) is identity

    assert handler._create_maintenance_task.call_count == 2
    assert getattr(event, handler._IDENTITY_SYNC_MARKER_ATTR) is False


@pytest.mark.asyncio
async def test_runtime_exposes_read_only_current_identity_lookup() -> None:
    """身份运行时通过只读边界返回当前目录记录，并在无 Store 时安全降级。"""

    from core.identity.runtime import ProtocolIdentityRuntime

    store = MagicMock()
    stored = SimpleNamespace(
        identity_namespace="qq",
        stable_user_id="10001",
        display_name="新昵称",
    )
    store.get_identity = AsyncMock(return_value=stored)
    runtime = ProtocolIdentityRuntime(store=store)

    assert await runtime.get_identity("qq", "10001") is stored
    store.get_identity.assert_awaited_once_with("qq", "10001")
    assert await ProtocolIdentityRuntime().get_identity("qq", "10001") is None


@pytest.mark.asyncio
async def test_event_handler_uses_trusted_canonical_id_for_group_capture() -> None:
    """群捕获的去重、消息写入和用户级认知投喂使用 QQ canonical ID。"""

    from astrbot.api.platform import MessageType

    from core.event_handler import EventHandler
    from core.identity.runtime import ProtocolIdentityRuntime

    config = MagicMock()
    config.get.return_value = True
    conversation = MagicMock()
    conversation.add_message_from_event = AsyncMock()
    runtime = ProtocolIdentityRuntime()
    runtime.resolve = MagicMock(return_value=group_identity())
    runtime.synchronize = AsyncMock()
    conversation.identity_runtime = runtime
    event = MagicMock()
    event.unified_msg_origin = "aiocqhttp:group:20001"
    event.get_message_type.return_value = MessageType.GROUP_MESSAGE
    event.get_sender_id.return_value = "legacy-name"
    event.get_self_id.return_value = "bot"
    relation = MagicMock()
    relation.apply_delta = AsyncMock()

    handler = EventHandler(
        context=MagicMock(),
        config_manager=config,
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=conversation,
        relation_manager=relation,
    )
    handler._extractor.extract_message_content = AsyncMock(return_value="正文")
    handler._dedup.build_dedup_key = AsyncMock(return_value="dedup")
    handler._dedup.is_duplicate = AsyncMock(return_value=False)
    handler._dedup.mark_processed = AsyncMock()
    handler._create_maintenance_task = MagicMock(
        side_effect=lambda coroutine, **_kwargs: coroutine.close()
    )

    await handler.handle_all_group_messages(event)

    runtime.resolve.assert_called_once_with(event)
    assert (
        handler._dedup.build_dedup_key.await_args.kwargs["sender_id_override"]
        == "10001"
    )
    assert (
        conversation.add_message_from_event.await_args.kwargs[
            "identity"
        ].canonical_user_id
        == "10001"
    )
    assert relation.apply_delta.await_args.kwargs["from_user"] == "10001"


@pytest.mark.asyncio
async def test_conflict_recall_skips_private_user_write_but_searches_without_user_id() -> (
    None
):
    """冲突身份不写私聊用户消息，但召回仍执行且不带用户级过滤。"""

    case = recall_case()

    await case.handler.handle_memory_recall(
        case.event,
        case.request,
        identity=conflict_identity(),
    )

    case.conversation.add_message_from_event.assert_not_awaited()
    case.engine.search_memories.assert_awaited_once()
    assert case.engine.search_memories.await_args.kwargs["user_id"] is None


@pytest.mark.asyncio
async def test_trusted_recall_uses_canonical_id_and_persists_identity() -> None:
    """可信 OneBot 私聊写入与长期记忆检索都使用 canonical QQ。"""

    case = recall_case()
    identity = trusted_identity()

    await case.handler.handle_memory_recall(
        case.event,
        case.request,
        identity=identity,
    )

    assert (
        case.conversation.add_message_from_event.await_args.kwargs["identity"]
        is identity
    )
    assert case.engine.search_memories.await_args.kwargs["user_id"] == "10001"


@pytest.mark.asyncio
async def test_unsupported_recall_preserves_generic_sender_behavior() -> None:
    """未注册协议继续写私聊消息，并使用 AstrBot 原始发送者召回。"""

    case = recall_case()
    identity = unsupported_identity()

    await case.handler.handle_memory_recall(
        case.event,
        case.request,
        identity=identity,
    )

    assert (
        case.conversation.add_message_from_event.await_args.kwargs["identity"]
        is identity
    )
    assert case.engine.search_memories.await_args.kwargs["user_id"] == "legacy-name"


def test_anonymous_identity_only_exposes_conversation_override() -> None:
    """匿名身份可用于群会话去重，但不能进入用户级认知状态。"""

    from core.event_handler import EventHandler

    identity = anonymous_identity()
    event = MagicMock()
    event.get_sender_id.return_value = "legacy-anonymous"

    assert EventHandler._conversation_sender_override(identity) == "anonymous:opaque"
    assert EventHandler._user_id_for_identity(event, identity) is None


@pytest.mark.asyncio
async def test_conflict_group_capture_skips_user_message_and_cognitive_state() -> None:
    """冲突群身份不写用户消息，也不会更新用户级认知状态。"""

    from astrbot.api.platform import MessageType

    from core.event_handler import EventHandler
    from core.identity.runtime import ProtocolIdentityRuntime

    config = MagicMock()
    config.get.return_value = True
    conversation = MagicMock()
    conversation.add_message_from_event = AsyncMock()
    runtime = ProtocolIdentityRuntime()
    runtime.resolve = MagicMock(return_value=conflict_identity())
    runtime.synchronize = AsyncMock()
    conversation.identity_runtime = runtime
    relation = MagicMock()
    relation.apply_delta = AsyncMock()
    handler = EventHandler(
        context=MagicMock(),
        config_manager=config,
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=conversation,
        relation_manager=relation,
    )
    handler._extractor.extract_message_content = AsyncMock(return_value="正文")
    event = MagicMock()
    event.get_message_type.return_value = MessageType.GROUP_MESSAGE
    event.get_sender_id.return_value = "legacy-name"
    event.get_self_id.return_value = "bot"
    event.unified_msg_origin = "aiocqhttp:group:20001"

    await handler.handle_all_group_messages(event)

    handler._extractor.extract_message_content.assert_not_awaited()
    conversation.add_message_from_event.assert_not_awaited()
    relation.apply_delta.assert_not_awaited()


@pytest.mark.asyncio
async def test_reflection_passes_identity_and_uses_canonical_affection_user(
    monkeypatch,
) -> None:
    """助手写入保留身份作用域，用户级好感度只使用可信 canonical QQ。"""

    from core.handlers.reflection_handler import ReflectionHandler

    config = MagicMock()
    config.get.side_effect = lambda _key, default=None: default
    conversation = MagicMock()
    conversation.add_message_from_event = AsyncMock()
    conversation.get_session_info = AsyncMock(return_value=None)
    conversation.get_context = AsyncMock(
        return_value=[{"role": "user", "content": "用户问题"}]
    )
    affection = MagicMock()
    affection.process_interaction = AsyncMock()
    monkeypatch.setattr(
        "core.handlers.reflection_handler.get_persona_id",
        AsyncMock(return_value="persona-1"),
    )
    handler = ReflectionHandler(
        context=MagicMock(),
        config_manager=config,
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=conversation,
        enforce_limit_cb=AsyncMock(),
        affection_manager=affection,
    )
    event = MagicMock()
    event.unified_msg_origin = "aiocqhttp:private:10001"
    event.get_sender_id.return_value = "legacy-name"
    response = SimpleNamespace(
        role="assistant",
        tools_call_name=None,
        tools_call_extra_content=None,
        completion_text="回复",
    )
    identity = trusted_identity()

    await handler.handle_memory_reflection(event, response, identity=identity)

    assert conversation.add_message_from_event.await_args.kwargs["identity"] is identity
    assert affection.process_interaction.await_args.kwargs["user_id"] == "10001"


@pytest.mark.asyncio
async def test_factory_identity_store_failure_returns_resolver_only_runtime(
    monkeypatch, tmp_path
) -> None:
    """身份表初始化普通失败时工厂继续启动并返回解析器降级运行时。"""

    from core.identity.runtime import ProtocolIdentityRuntime
    from core.initializer.component_factory import ComponentFactory

    factory = ComponentFactory(MagicMock(), MagicMock(), str(tmp_path))
    store = MagicMock()
    store.initialize = AsyncMock(side_effect=RuntimeError("private"))
    store.close = AsyncMock()
    monkeypatch.setattr(
        "core.initializer.component_factory.ProtocolIdentityStore",
        MagicMock(return_value=store),
    )

    manager = MagicMock()
    runtime = await factory._build_identity_runtime(manager)

    assert isinstance(runtime, ProtocolIdentityRuntime)
    assert runtime.service is None
    assert runtime.synchronizer is None
    assert runtime.enricher is None
    store.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_identity_store_partial_initialization_remains_closable(
    monkeypatch, tmp_path
) -> None:
    """连接成功但建表准备失败时，Store 仍能释放部分初始化连接。"""

    from core.features.identity import ProtocolIdentityStore

    connection = MagicMock()
    connection.close = AsyncMock()
    monkeypatch.setattr(
        "core.features.identity.infrastructure.store.aiosqlite.connect",
        AsyncMock(return_value=connection),
    )
    monkeypatch.setattr(
        "core.features.identity.infrastructure.store.apply_identity_store_pragmas",
        AsyncMock(side_effect=RuntimeError("private")),
    )
    store = ProtocolIdentityStore(str(tmp_path / "memora.db"))

    with pytest.raises(RuntimeError, match="private"):
        await store.initialize()
    await store.close()

    connection.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_factory_identity_runtime_propagates_initialization_cancellation(
    monkeypatch, tmp_path
) -> None:
    """工厂不能把身份 Store 初始化取消误降级为普通解析模式。"""

    from core.initializer.component_factory import ComponentFactory

    factory = ComponentFactory(MagicMock(), MagicMock(), str(tmp_path))
    store = MagicMock()
    store.initialize = AsyncMock(side_effect=asyncio.CancelledError())
    store.close = AsyncMock()
    monkeypatch.setattr(
        "core.initializer.component_factory.ProtocolIdentityStore",
        MagicMock(return_value=store),
    )

    with pytest.raises(asyncio.CancelledError):
        await factory._build_identity_runtime(MagicMock())

    store.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_factory_identity_runtime_builds_service_and_closes_owned_store(
    monkeypatch, tmp_path
) -> None:
    """正常工厂运行时绑定服务和同步器，并由运行时负责关闭 Store。"""

    from core.initializer.component_factory import ComponentFactory

    factory = ComponentFactory(MagicMock(), MagicMock(), str(tmp_path))
    store = MagicMock()
    store.initialize = AsyncMock()
    store.close = AsyncMock()
    monkeypatch.setattr(
        "core.initializer.component_factory.ProtocolIdentityStore",
        MagicMock(return_value=store),
    )
    manager = MagicMock()
    manager.invalidate_cache = AsyncMock()

    runtime = await factory._build_identity_runtime(manager)

    assert runtime.service is not None
    assert runtime.synchronizer is not None
    assert runtime.enricher is not None
    await runtime.close()
    store.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_initializer_closes_identity_runtime_without_event_handler(
    tmp_path,
) -> None:
    """未创建事件处理器时，初始化器关闭链仍释放身份 Store。"""

    from core.identity.runtime import ProtocolIdentityRuntime
    from core.plugin_initializer import PluginInitializer

    store = MagicMock()
    store.close = AsyncMock()
    runtime = ProtocolIdentityRuntime(store=store)
    initializer = PluginInitializer(MagicMock(), MagicMock(), str(tmp_path))
    initializer.conversation_manager = SimpleNamespace(identity_runtime=None)
    initializer.identity_runtime = runtime

    await initializer.close_extension_components()

    store.close.assert_awaited_once()


@pytest.mark.parametrize(
    "cancel_initialization", [False, True], ids=["error", "cancel"]
)
@pytest.mark.asyncio
async def test_initializer_closes_published_identity_runtime_after_init_failure(
    tmp_path,
    cancel_initialization: bool,
) -> None:
    """运行时发布后的初始化失败或取消都必须释放身份 Store。"""

    from core.base.exceptions import InitializationError
    from core.identity.runtime import ProtocolIdentityRuntime
    from core.plugin_initializer import PluginInitializer

    store = MagicMock()
    store.close = AsyncMock()
    runtime = ProtocolIdentityRuntime(store=store)
    memory_processor = MagicMock()
    initializer = PluginInitializer(MagicMock(), MagicMock(), str(tmp_path))
    initializer._faiss_checker.load_vec_db_class = MagicMock(return_value=MagicMock())
    initializer._component_factory.build_all = AsyncMock(
        return_value={
            "db": MagicMock(),
            "graph_db": None,
            "memory_engine": MagicMock(),
            "memory_processor": memory_processor,
            "memory_quarantine_store": MagicMock(),
            "memory_quality_gate": MagicMock(),
            "conversation_manager": SimpleNamespace(identity_runtime=runtime),
            "identity_runtime": runtime,
            "index_validator": MagicMock(),
            "decay_scheduler": None,
            "injection_decision_store": None,
            "injection_decision_recorder": None,
        }
    )
    initializer._create_prompt_protection_service = MagicMock(return_value=None)

    if cancel_initialization:
        cognitive_started = asyncio.Event()

        async def block_cognitive_initialization() -> None:
            """等待测试任务取消，模拟发布后的初始化中断。"""

            cognitive_started.set()
            await asyncio.Future()

        initializer._initialize_cognitive_components = block_cognitive_initialization
        init_task = asyncio.create_task(initializer._run_full_init())
        await cognitive_started.wait()
        init_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await init_task
    else:
        initializer._initialize_cognitive_components = AsyncMock(
            side_effect=RuntimeError("cognitive failed")
        )
        with pytest.raises(InitializationError, match="cognitive failed"):
            await initializer._run_full_init()

    store.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_init_identity_cleanup_propagates_only_cancellation() -> None:
    """身份关闭普通错误应降级，取消信号必须继续传播。"""

    from core.initializer.identity_lifecycle import (
        close_identity_runtime_after_failure,
    )

    runtime = MagicMock()
    runtime.close = AsyncMock(side_effect=RuntimeError("private"))

    await close_identity_runtime_after_failure(runtime)

    runtime.close.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await close_identity_runtime_after_failure(runtime)

    assert runtime.close.await_count == 2
