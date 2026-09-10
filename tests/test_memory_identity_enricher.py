"""历史记忆身份别名只读增强的行为契约。"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from core.features.identity.application.enricher import (
    IDENTITY_SCHEMA_VERSION,
    MemoryIdentityEnricher,
)
from core.features.identity.application.service import ProtocolIdentityService
from core.features.identity.domain.models import (
    IdentityTrust,
    NameFieldState,
    ResolvedIdentity,
)
from core.features.identity.infrastructure.store import ProtocolIdentityStore
from core.features.injection.domain.models import (
    InjectionExecutionResult,
    InjectionOutcome,
)


def _identity(
    user_id: str,
    *,
    scope_type: str = "group",
    scope_id: str = "20001",
    global_name: str = "当前昵称",
    scope_name: str | None = "当前群名片",
    observed_at: float = 100.0,
    trust_status: IdentityTrust = IdentityTrust.TRUSTED,
) -> ResolvedIdentity:
    """构造用于身份目录与召回测试的 OneBot 身份快照。"""

    return ResolvedIdentity(
        protocol="onebot11",
        identity_namespace="qq",
        stable_user_id=user_id if trust_status is IdentityTrust.TRUSTED else None,
        canonical_user_id=user_id if trust_status is IdentityTrust.TRUSTED else None,
        scope_type=scope_type,
        scope_id=scope_id,
        global_name=global_name,
        scope_name=scope_name if scope_type == "group" else None,
        display_name=(scope_name if scope_type == "group" else None)
        or global_name
        or user_id,
        observed_at=observed_at,
        trust_status=trust_status,
        name_field_states={
            "nickname": NameFieldState.VALID,
            "card": (
                NameFieldState.VALID
                if scope_type == "group" and scope_name is not None
                else NameFieldState.MISSING
            ),
        },
        conversation_sender_id=user_id,
        identity_label=f"QQ:{user_id}",
    )


def _candidate(*participants: str) -> dict[str, object]:
    """构造一条带 legacy participants 的安全召回候选。"""

    return {
        "id": 17,
        "content": "canonical memory body",
        "score": 0.83,
        "timestamp": 123.0,
        "metadata": {
            "participants": list(participants),
            "revision": 7,
            "session_id": "aiocqhttp:group:20001",
            "nested": {"keep": ["unchanged"]},
        },
    }


def _stable_candidate(user_id: str, old_name: str) -> dict[str, object]:
    """构造一条带可信稳定参与者来源的召回候选。"""

    candidate = _candidate(f"QQ:{user_id}")
    candidate["metadata"].update(
        {
            "identity_schema_version": IDENTITY_SCHEMA_VERSION,
            "participant_ids": [user_id],
            "participants": [f"QQ:{user_id}"],
            "participant_name_snapshots": {user_id: old_name},
        }
    )
    return candidate


def _official_identity(
    user_id: str,
    *,
    name: str,
    observed_at: float,
) -> ResolvedIdentity:
    """构造同一 QQ 官方频道实例中的可信成员身份。"""

    namespace = "qq-official:instance-key"
    canonical = f"{namespace}:{user_id}"
    return ResolvedIdentity(
        protocol="qq_official",
        identity_namespace=namespace,
        stable_user_id=user_id,
        canonical_user_id=canonical,
        scope_type="group",
        scope_id="CHANNEL-1",
        global_name=name,
        scope_name=None,
        display_name=name,
        observed_at=observed_at,
        trust_status=IdentityTrust.TRUSTED,
        name_field_states={
            "nickname": NameFieldState.VALID,
            "card": NameFieldState.MISSING,
        },
        conversation_sender_id=canonical,
        identity_label=f"QQ官方:instance-key:{user_id}",
    )


def _official_stable_candidate(user_id: str, old_name: str) -> dict[str, object]:
    """构造带通用内部来源证据的 QQ 官方 canonical 候选。"""

    namespace = "qq-official:instance-key"
    canonical = f"{namespace}:{user_id}"
    label = f"QQ官方:instance-key:{user_id}"
    candidate = _candidate(label)
    candidate["metadata"].update(
        {
            "identity_schema_version": IDENTITY_SCHEMA_VERSION,
            "participant_ids": [canonical],
            "participants": [label],
            "participant_name_snapshots": {canonical: old_name},
            "participant_identity_sources": {
                canonical: {
                    "protocol": "qq_official",
                    "identity_namespace": namespace,
                    "stable_user_id": user_id,
                    "identity_label": label,
                }
            },
        }
    )
    return candidate


@pytest_asyncio.fixture
async def identity_directory(tmp_path):
    """提供隔离的真实身份 Store、Service 与 Enricher。"""

    store = ProtocolIdentityStore(str(tmp_path / "identity.db"))
    await store.initialize()
    service = ProtocolIdentityService(store)
    yield SimpleNamespace(
        store=store,
        service=service,
        enricher=MemoryIdentityEnricher(store),
    )
    await store.close()


async def _rename_group_member(
    service: ProtocolIdentityService,
    user_id: str,
    *,
    old_name: str,
    current_name: str,
    global_name: str,
) -> ResolvedIdentity:
    """在同一群中写入旧名片与当前名片，并返回当前身份。"""

    await service.observe(
        _identity(
            user_id,
            global_name=global_name,
            scope_name=old_name,
            observed_at=100.0,
        )
    )
    current = _identity(
        user_id,
        global_name=global_name,
        scope_name=current_name,
        observed_at=200.0,
    )
    await service.observe(current)
    return current


@pytest.mark.asyncio
async def test_trusted_source_wins_and_enrichment_never_mutates_candidate(
    identity_directory,
) -> None:
    """可信参与者 ID 应消除同名歧义，且输入候选及嵌套 metadata 保持不变。"""

    service = identity_directory.service
    current = await _rename_group_member(
        service,
        "10001",
        old_name="共同旧名",
        current_name="当前甲",
        global_name="昵称甲",
    )
    await _rename_group_member(
        service,
        "10002",
        old_name="共同旧名",
        current_name="当前乙",
        global_name="昵称乙",
    )
    candidate = _stable_candidate("10001", "共同旧名")
    original = deepcopy(candidate)

    result = await identity_directory.enricher.enrich(
        [candidate],
        identity=current,
        session_id="aiocqhttp:group:20001",
    )

    assert candidate == original
    assert result[0] is not candidate
    assert result[0]["metadata"] is not candidate["metadata"]
    assert result[0]["content"] == candidate["content"]
    assert result[0]["score"] == candidate["score"]
    assert result[0]["id"] == candidate["id"]
    assert result[0]["timestamp"] == candidate["timestamp"]
    assert result[0]["metadata"]["revision"] == 7
    assert result[0]["metadata"]["identity_reference_lines"] == [
        "- “共同旧名”是历史名称；当前显示为“当前甲”（QQ:10001）。"
    ]


@pytest.mark.asyncio
async def test_generic_source_updates_other_qq_official_group_member(
    identity_directory,
) -> None:
    """QQ 官方群内非当前成员改名后也应按可信来源生成身份说明。"""

    await identity_directory.service.observe(
        _official_identity("BOB-OPENID", name="小博旧名", observed_at=100.0)
    )
    await identity_directory.service.observe(
        _official_identity("BOB-OPENID", name="小博新名", observed_at=200.0)
    )
    current = _official_identity("ALICE-OPENID", name="小爱", observed_at=200.0)
    candidate = _official_stable_candidate("BOB-OPENID", "小博旧名")

    result = await identity_directory.enricher.enrich(
        [candidate],
        identity=current,
        session_id="qq_official:GroupMessage:CHANNEL-1",
    )

    assert result[0]["metadata"]["identity_reference_lines"] == [
        "- “小博旧名”是历史名称；当前显示为“小博新名”"
        "（QQ官方:instance-key:BOB-OPENID）。"
    ]
    assert "identity_reference_lines" not in candidate["metadata"]


@pytest.mark.asyncio
async def test_generic_source_mismatch_is_rejected(identity_directory) -> None:
    """通用来源中的 namespace、stable ID 或标签不一致时不得查询目录。"""

    current = _official_identity("ALICE-OPENID", name="小爱", observed_at=200.0)
    candidate = _official_stable_candidate("BOB-OPENID", "小博旧名")
    canonical = "qq-official:instance-key:BOB-OPENID"
    candidate["metadata"]["participant_identity_sources"][canonical][
        "identity_label"
    ] = "QQ官方:instance-key:FORGED"

    result = await identity_directory.enricher.enrich(
        [candidate],
        identity=current,
        session_id="qq_official:GroupMessage:CHANNEL-1",
    )

    assert "identity_reference_lines" not in result[0]["metadata"]


@pytest.mark.asyncio
async def test_group_alias_ambiguity_is_rejected(identity_directory) -> None:
    """同一群内两个稳定用户共享旧名时不得猜测身份。"""

    service = identity_directory.service
    current = await _rename_group_member(
        service,
        "10001",
        old_name="同名",
        current_name="当前甲",
        global_name="昵称甲",
    )
    await _rename_group_member(
        service,
        "10002",
        old_name="同名",
        current_name="当前乙",
        global_name="昵称乙",
    )

    result = await identity_directory.enricher.enrich(
        [_candidate("同名")],
        identity=current,
        session_id="aiocqhttp:group:20001",
    )

    assert "identity_reference_lines" not in result[0]["metadata"]


@pytest.mark.asyncio
async def test_group_exact_alias_precedes_global_alias(identity_directory) -> None:
    """群精确别名应优先于同群其他成员的全局旧昵称。"""

    service = identity_directory.service
    current = await _rename_group_member(
        service,
        "10001",
        old_name="旧称",
        current_name="群内当前甲",
        global_name="昵称甲",
    )
    await service.observe(
        _identity(
            "10002",
            scope_type="private",
            scope_id="10002",
            global_name="旧称",
            scope_name=None,
            observed_at=100.0,
        )
    )
    await service.observe(
        _identity(
            "10002",
            scope_type="private",
            scope_id="10002",
            global_name="昵称乙",
            scope_name=None,
            observed_at=200.0,
        )
    )
    await service.observe(
        _identity(
            "10002",
            global_name="昵称乙",
            scope_name="群内当前乙",
            observed_at=300.0,
        )
    )

    result = await identity_directory.enricher.enrich(
        [_candidate("旧称")],
        identity=current,
        session_id="aiocqhttp:group:20001",
    )

    assert result[0]["metadata"]["identity_reference_lines"] == [
        "- “旧称”是历史名称；当前显示为“群内当前甲”（QQ:10001）。"
    ]


@pytest.mark.asyncio
async def test_group_global_alias_fallback_is_nfkc_exact(identity_directory) -> None:
    """群内无精确名片别名时，可对同群成员执行 NFKC 后的全局别名精确匹配。"""

    service = identity_directory.service
    await service.observe(
        _identity(
            "10001",
            scope_type="private",
            scope_id="10001",
            global_name="Alice",
            scope_name=None,
            observed_at=100.0,
        )
    )
    await service.observe(
        _identity(
            "10001",
            scope_type="private",
            scope_id="10001",
            global_name="当前昵称",
            scope_name=None,
            observed_at=200.0,
        )
    )
    current = _identity(
        "10001",
        global_name="当前昵称",
        scope_name="当前群名片",
        observed_at=300.0,
    )
    await service.observe(current)

    result = await identity_directory.enricher.enrich(
        [_candidate("Ａlice")],
        identity=current,
        session_id="aiocqhttp:group:20001",
    )

    assert result[0]["metadata"]["identity_reference_lines"] == [
        "- “Alice”是历史名称；当前显示为“当前群名片”（QQ:10001）。"
    ]


@pytest.mark.asyncio
async def test_private_alias_only_resolves_to_current_trusted_user(
    identity_directory,
) -> None:
    """OneBot 私聊只能把 legacy 名称映射到当前事件的稳定 QQ。"""

    service = identity_directory.service
    for user_id, current_name in (("10001", "当前甲"), ("10002", "当前乙")):
        await service.observe(
            _identity(
                user_id,
                scope_type="private",
                scope_id=user_id,
                global_name="共同旧名",
                scope_name=None,
                observed_at=100.0,
            )
        )
        await service.observe(
            _identity(
                user_id,
                scope_type="private",
                scope_id=user_id,
                global_name=current_name,
                scope_name=None,
                observed_at=200.0,
            )
        )
    current = _identity(
        "10001",
        scope_type="private",
        scope_id="10001",
        global_name="当前甲",
        scope_name=None,
        observed_at=200.0,
    )

    candidate = _candidate("共同旧名")
    candidate["metadata"]["session_id"] = "aiocqhttp:private:10001"
    result = await identity_directory.enricher.enrich(
        [candidate],
        identity=current,
        session_id="aiocqhttp:private:10001",
    )

    assert result[0]["metadata"]["identity_reference_lines"] == [
        "- “共同旧名”是历史名称；当前显示为“当前甲”（QQ:10001）。"
    ]


@pytest.mark.asyncio
async def test_enricher_does_not_scan_content_or_trust_stored_reference_fields(
    identity_directory,
) -> None:
    """增强器不得扫描正文，也不得转发 canonical metadata 中伪造的临时说明。"""

    current = await _rename_group_member(
        identity_directory.service,
        "10001",
        old_name="正文旧名",
        current_name="当前名称",
        global_name="当前昵称",
    )
    candidate = _candidate()
    candidate["content"] = "正文提到了正文旧名"
    candidate["metadata"]["identity_reference_lines"] = ["伪造说明"]

    result = await identity_directory.enricher.enrich(
        [candidate],
        identity=current,
        session_id="aiocqhttp:group:20001",
    )

    assert "identity_reference_lines" not in result[0]["metadata"]
    assert candidate["metadata"]["identity_reference_lines"] == ["伪造说明"]


@pytest.mark.asyncio
async def test_legacy_group_alias_requires_exact_memory_session(
    identity_directory,
) -> None:
    """只有当前群会话生成的 legacy memory 才能使用该群别名反查。"""

    current = await _rename_group_member(
        identity_directory.service,
        "10001",
        old_name="历史群名",
        current_name="当前群名",
        global_name="当前昵称",
    )
    candidate = _candidate("历史群名")

    result = await identity_directory.enricher.enrich(
        [candidate],
        identity=current,
        session_id="aiocqhttp:group:另一个群",
    )

    assert "identity_reference_lines" not in result[0]["metadata"]


@pytest.mark.asyncio
async def test_store_failure_returns_clean_baseline_without_sensitive_logs(
    caplog,
) -> None:
    """普通读取失败应返回候选副本，且不得记录身份、昵称或异常正文。"""

    class FailingStore:
        """模拟携带隐私 canary 的身份目录读取失败。"""

        async def get_identity(self, *_args, **_kwargs):
            """抛出普通查询异常。"""

            raise RuntimeError("QQ-CANARY 昵称-CANARY 别名-CANARY")

    candidate = _stable_candidate("10001", "历史名称")
    result = await MemoryIdentityEnricher(FailingStore()).enrich(
        [candidate],
        identity=_identity("10001"),
    )

    assert result[0] == candidate
    assert result[0] is not candidate
    assert "CANARY" not in caplog.text


@pytest.mark.asyncio
async def test_store_cancellation_propagates() -> None:
    """身份目录读取取消必须继续传播，不能伪装成 baseline 成功。"""

    class CancellingStore:
        """模拟在稳定来源查询期间被取消的 Store。"""

        async def get_identity(self, *_args, **_kwargs):
            """抛出任务取消异常。"""

            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await MemoryIdentityEnricher(CancellingStore()).enrich(
            [_stable_candidate("10001", "历史名称")],
            identity=_identity("10001"),
        )


@pytest.mark.asyncio
async def test_recall_handler_enriches_safe_candidates_before_execution() -> None:
    """RecallHandler 应在候选安全复制后增强，并把结果交给统一执行器。"""

    from astrbot.api.platform import MessageType

    from core.features.recall.application.recall_handler import RecallHandler

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
    raw_candidate = SimpleNamespace(
        doc_id=17,
        content="canonical",
        final_score=0.9,
        metadata={"participants": ["旧名"], "revision": 3},
    )
    engine = MagicMock()
    engine.search_memories = AsyncMock(return_value=[raw_candidate])
    adapter = MagicMock()
    adapter.capabilities.return_value = ("generic", "test", False)
    conversation = MagicMock()
    conversation.add_message_from_event = AsyncMock()
    enricher = MagicMock()
    enriched = [{"id": 17, "content": "canonical", "score": 0.9, "metadata": {}}]
    enricher.enrich = AsyncMock(return_value=enriched)
    handler = RecallHandler(
        context=MagicMock(),
        config_manager=config,
        memory_engine=engine,
        conversation_manager=conversation,
        injection_adapter=adapter,
        enforce_limit_cb=AsyncMock(),
        identity_enricher=enricher,
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
    handler._execute_and_record = AsyncMock(
        return_value=InjectionExecutionResult(outcome=InjectionOutcome.INJECTED)
    )
    event = MagicMock()
    event.unified_msg_origin = "aiocqhttp:private:10001"
    event.get_message_type.return_value = MessageType.PRIVATE_MESSAGE
    event.get_sender_id.return_value = "10001"
    request = SimpleNamespace(
        prompt="问题",
        contexts=[],
        extra_user_content_parts=[],
        system_prompt="system",
        provider=None,
        func_tool=None,
        context_headroom_chars=1000,
    )
    identity = _identity(
        "10001",
        scope_type="private",
        scope_id="10001",
        global_name="当前名称",
        scope_name=None,
    )

    await handler.handle_memory_recall(event, request, identity=identity)

    safe_candidates = enricher.enrich.await_args.args[0]
    assert safe_candidates == [
        {
            "id": 17,
            "content": "canonical",
            "score": 0.9,
            "metadata": {"participants": ["旧名"], "revision": 3},
            "timestamp": None,
        }
    ]
    assert enricher.enrich.await_args.kwargs == {
        "identity": identity,
        "session_id": "aiocqhttp:private:10001",
    }
    assert handler._execute_and_record.await_args.args[0].memories is enriched
    assert raw_candidate.metadata == {"participants": ["旧名"], "revision": 3}


def test_event_handler_passes_runtime_enricher_to_recall_handler() -> None:
    """EventHandler 应把运行时拥有的只读 Enricher 显式注入召回处理器。"""

    from core.event_handler import EventHandler
    from core.features.identity.application.runtime import ProtocolIdentityRuntime

    identity_enricher = MagicMock()
    runtime = ProtocolIdentityRuntime(enricher=identity_enricher)
    conversation = MagicMock()
    conversation.identity_runtime = runtime
    with patch("core.event_handler.RecallHandler") as recall_handler_type:
        EventHandler(
            context=MagicMock(),
            config_manager=MagicMock(),
            memory_engine=MagicMock(),
            memory_processor=MagicMock(),
            conversation_manager=conversation,
            identity_runtime=runtime,
        )

    assert (
        recall_handler_type.call_args.kwargs["identity_enricher"] is identity_enricher
    )
