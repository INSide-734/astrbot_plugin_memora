"""Grounding Judge 归因、预算与质量门安全边界回归。"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from core.features.quality.application.memory_quality_gate import MemoryQualityGate
from core.features.quality.domain.gate_config import GateJudge, GateProfile
from core.features.quality.infrastructure.quarantine_store import MemoryQuarantineStore
from core.features.recall.processors.grounding_judge import GroundingJudgeMixin
from core.features.recall.processors.memory_grounding import (
    GroundingResult,
    MemoryGroundingValidator,
)
from core.features.recall.processors.memory_processor import MemoryProcessor
from core.features.recall.processors.memory_processor_candidate_mixin import (
    MemoryProcessorCandidateMixin,
)
from core.shared.contracts.conversation import Message
from core.shared.cost_control import CostControl
from core.shared.extra_llm_budget import ExtraLlmBudget, extra_llm_budget_scope
from tests.stable_approval_source import (
    StableApprovalConversation,
    stable_source_window,
)

_CANARIES = (
    "GROUNDING-CLAIM-CANARY",
    "GROUNDING-SOURCE-CANARY",
    "GROUNDING-EXCEPTION-CANARY",
)


def _needs_judge(
    *, claim: str = "候选声明", source: str = "来源片段"
) -> GroundingResult:
    """构造需要 Judge 复核的确定性结论。"""
    return GroundingResult(
        allowed=False,
        status="needs_judge",
        reason_codes=("grounding_needs_judge",),
        evidence=[],
        source_text=source,
        claim_text=claim,
        requires_judge=True,
    )


def _profile(*, enabled: bool) -> GateProfile:
    """构造仅改变 Judge 开关的 profile。"""
    return GateProfile(name="test", judge=GateJudge(enabled=enabled))


def _processor(
    judge: AsyncMock,
    *,
    cost_control: CostControl | None = None,
) -> MemoryProcessor:
    """构造使用注入 Judge 的处理器。"""
    context = MagicMock()
    context.get_using_provider.return_value = None
    context.persona_manager = None
    context.get_registered_llm_tools.return_value = []
    return MemoryProcessor(
        context=context,
        llm_provider=MagicMock(),
        config={},
        cost_control=cost_control,
        grounding_judge=judge,
    )


def test_processor_mro_owns_judge_logic_without_forwarding() -> None:
    """处理器保留候选 mixin 优先级，Judge 方法直接来自新 owner。"""
    assert GroundingJudgeMixin in MemoryProcessor.__mro__
    assert MemoryProcessor.__mro__.index(MemoryProcessorCandidateMixin) < (
        MemoryProcessor.__mro__.index(GroundingJudgeMixin)
    )
    assert MemoryProcessor.resolve_grounding_judge is (
        GroundingJudgeMixin.resolve_grounding_judge
    )
    assert MemoryProcessor._inject_topic_candidates is (
        MemoryProcessorCandidateMixin._inject_topic_candidates
    )


def _judge_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """筛选 Grounding Judge 的常开失败日志。"""
    return [
        record for record in caplog.records if "[GroundingJudge]" in record.getMessage()
    ]


def _assert_safe_record(
    record: logging.LogRecord,
    *,
    cause: str,
    canaries: tuple[str, ...] = _CANARIES,
) -> None:
    """断言日志只包含固定阶段、组件、原因、异常类别和计数。"""
    message = record.getMessage()
    assert "stage=resolve" in message
    assert "component=grounding_judge" in message
    assert f"cause={cause}" in message
    assert "exception_type=" in message
    assert "count=1" in message
    assert all(canary not in message for canary in canaries)


@pytest.mark.asyncio
async def test_disabled_judge_is_fail_closed_and_logs_without_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """生产等价关闭配置不调用 Judge，并记录 judge_disabled。"""
    caplog.set_level(logging.WARNING, logger="astrbot.test")
    judge = AsyncMock()
    cost_control = MagicMock()
    cost_control.allow.return_value = True
    processor = _processor(
        judge,
        cost_control=cast(CostControl, cost_control),
    )
    result = await processor.resolve_grounding_judge(
        _needs_judge(claim=_CANARIES[0], source=_CANARIES[1]),
        is_group_chat=False,
        profile=_profile(enabled=False),
    )

    assert result.reason_codes == ("grounding_judge_unavailable",)
    judge.assert_not_awaited()
    records = _judge_records(caplog)
    assert len(records) == 1
    _assert_safe_record(records[0], cause="judge_disabled")


@pytest.mark.asyncio
async def test_missing_budget_scope_is_attributed_without_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Judge 开启但缺少请求预算上下文时保持隔离。"""
    caplog.set_level(logging.WARNING, logger="astrbot.test")
    judge = AsyncMock()
    processor = _processor(judge)

    result = await processor.resolve_grounding_judge(
        _needs_judge(),
        is_group_chat=False,
        profile=_profile(enabled=True),
    )

    assert result.reason_codes == ("grounding_judge_unavailable",)
    judge.assert_not_awaited()
    records = _judge_records(caplog)
    assert len(records) == 1
    _assert_safe_record(records[0], cause="budget_scope_missing")


@pytest.mark.asyncio
async def test_exhausted_budget_is_attributed_without_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """请求预算耗尽时不调用 Judge，并记录 budget_exhausted。"""
    caplog.set_level(logging.WARNING, logger="astrbot.test")
    judge = AsyncMock()
    processor = _processor(judge)

    with extra_llm_budget_scope(ExtraLlmBudget(0)):
        result = await processor.resolve_grounding_judge(
            _needs_judge(),
            is_group_chat=False,
            profile=_profile(enabled=True),
        )

    assert result.reason_codes == ("grounding_judge_unavailable",)
    judge.assert_not_awaited()
    records = _judge_records(caplog)
    assert len(records) == 1
    _assert_safe_record(records[0], cause="budget_exhausted")


class _DeniedBudget:
    """模拟仍有余量但拒绝预留的请求预算。"""

    def snapshot(self) -> SimpleNamespace:
        """返回不含请求内容的剩余额度。"""
        return SimpleNamespace(remaining=1)

    async def reserve(self, _feature: str) -> None:
        """拒绝本次槽位预留。"""
        return None


@pytest.mark.asyncio
async def test_budget_denial_is_attributed_without_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """预算对象拒绝预留时记录 budget_denied 而非猜测 Provider 故障。"""
    caplog.set_level(logging.WARNING, logger="astrbot.test")
    judge = AsyncMock()
    processor = _processor(judge)

    with extra_llm_budget_scope(cast(Any, _DeniedBudget())):
        result = await processor.resolve_grounding_judge(
            _needs_judge(),
            is_group_chat=False,
            profile=_profile(enabled=True),
        )

    assert result.reason_codes == ("grounding_judge_unavailable",)
    judge.assert_not_awaited()
    records = _judge_records(caplog)
    assert len(records) == 1
    _assert_safe_record(records[0], cause="budget_denied")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "cause"),
    [
        (ValueError("grounding_judge_invalid_response"), "response_invalid"),
        (RuntimeError(_CANARIES[2]), "call_failed"),
    ],
)
async def test_judge_failures_are_closed_and_release_budget(
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    cause: str,
) -> None:
    """固定非法响应与普通异常分别归因，且失败释放预算。"""
    caplog.set_level(logging.WARNING, logger="astrbot.test")
    judge = AsyncMock(side_effect=error)
    processor = _processor(
        judge,
        cost_control=CostControl(mode="quality", max_extra_llm_calls_per_turn=1),
    )
    budget = ExtraLlmBudget(1)

    with extra_llm_budget_scope(budget):
        result = await processor.resolve_grounding_judge(
            _needs_judge(claim=_CANARIES[0], source=_CANARIES[1]),
            is_group_chat=False,
            profile=_profile(enabled=True),
        )

    assert result.reason_codes == ("grounding_judge_unavailable",)
    assert budget.snapshot().used == 0
    assert budget.snapshot().reserved == 0
    records = _judge_records(caplog)
    assert len(records) == 1
    _assert_safe_record(records[0], cause=cause)
    assert _CANARIES[2] not in caplog.text


@pytest.mark.asyncio
async def test_invalid_provider_response_uses_fixed_marker_without_leak(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Provider 非法响应仅归为固定 response_invalid，且不回显正文。"""
    caplog.set_level(logging.WARNING, logger="astrbot.test")
    provider = MagicMock()
    provider.text_chat = AsyncMock(
        return_value=SimpleNamespace(completion_text=_CANARIES[2])
    )
    processor = MemoryProcessor(
        llm_provider=provider,
        cost_control=CostControl(mode="quality", max_extra_llm_calls_per_turn=1),
    )
    budget = ExtraLlmBudget(1)

    with extra_llm_budget_scope(budget):
        result = await processor.resolve_grounding_judge(
            _needs_judge(claim=_CANARIES[0], source=_CANARIES[1]),
            is_group_chat=False,
            profile=_profile(enabled=True),
        )

    assert result.reason_codes == ("grounding_judge_unavailable",)
    assert provider.text_chat.await_count == 1
    records = _judge_records(caplog)
    assert len(records) == 1
    _assert_safe_record(records[0], cause="response_invalid")
    assert all(canary not in caplog.text for canary in _CANARIES)


@pytest.mark.asyncio
async def test_judge_cancellation_propagates_and_releases_budget(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """取消继续向上传播，且不会伪造 unavailable 日志。"""
    caplog.set_level(logging.WARNING, logger="astrbot.test")
    judge = AsyncMock(side_effect=asyncio.CancelledError())
    processor = _processor(judge)
    budget = ExtraLlmBudget(1)

    with extra_llm_budget_scope(budget):
        with pytest.raises(asyncio.CancelledError):
            await processor.resolve_grounding_judge(
                _needs_judge(),
                is_group_chat=False,
                profile=_profile(enabled=True),
            )

    assert budget.snapshot().used == 0
    assert budget.snapshot().reserved == 0
    assert _judge_records(caplog) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("supported", [True, False])
async def test_supported_and_rejected_results_keep_existing_disposition(
    caplog: pytest.LogCaptureFixture,
    supported: bool,
) -> None:
    """Judge 正常返回时保持 supported/rejected 结果且不写失败日志。"""
    caplog.set_level(logging.WARNING, logger="astrbot.test")
    judge = AsyncMock(return_value=supported)
    processor = _processor(judge)
    budget = ExtraLlmBudget(1)

    with extra_llm_budget_scope(budget):
        result = await processor.resolve_grounding_judge(
            _needs_judge(),
            is_group_chat=False,
            profile=_profile(enabled=True),
        )

    assert result.allowed is supported
    expected_reason = (
        "grounding_judge_supported" if supported else "grounding_judge_rejected"
    )
    assert result.reason_codes == (expected_reason,)
    assert _judge_records(caplog) == []


def _message(index: int, content: str) -> Message:
    """构造来源校验使用的用户消息。"""
    return Message(
        id=index + 1,
        session_id="grounding-session",
        role="user",
        content=content,
        sender_id="user-1",
        sender_name="用户",
        timestamp=float(index + 1),
    )


def _candidate(summary: str, source: str) -> dict[str, object]:
    """构造带完整正文引用的候选。"""
    return {
        "summary": summary,
        "key_facts": [summary],
        "source_refs": [{"message_index": 0, "start": 0, "end": len(source)}],
    }


def test_numeric_conflict_remains_fail_closed() -> None:
    """数字冲突仍按既有确定性规则隔离。"""
    source = "我有300元。"
    result = MemoryGroundingValidator().validate(
        _candidate("用户有500元。", source),
        [_message(0, source)],
        is_group_chat=False,
    )

    assert result.allowed is False
    assert "grounding_numeric_conflict" in result.reason_codes


def test_negation_conflict_remains_fail_closed() -> None:
    """用户否定极性翻转仍按既有规则隔离。"""
    source = "我还没吃饭"
    result = MemoryGroundingValidator().validate(
        _candidate("用户已经吃过饭。", source),
        [_message(0, source)],
        is_group_chat=False,
    )

    assert result.allowed is False
    assert "grounding_negation_conflict" in result.reason_codes


def _approval_message(content: str = "用户喜欢咖啡。") -> Message:
    """构造质量门批准复核使用的固定来源消息。"""
    return Message(
        id=1,
        session_id="approval-session",
        role="user",
        content=content,
        sender_id="approval-user",
        sender_name="用户",
        timestamp=1.0,
    )


@pytest_asyncio.fixture
async def quarantine_store(tmp_path):
    """创建独立隔离候选 Store。"""
    store = MemoryQuarantineStore(tmp_path / "quarantine.sqlite3")
    await store.initialize()
    return store


@pytest.mark.asyncio
async def test_approval_missing_profile_logs_and_stays_fail_closed(
    quarantine_store: MemoryQuarantineStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """批准复核缺少 profile 时记录 profile_unresolved 并阻塞写入。"""
    caplog.set_level(logging.WARNING, logger="astrbot.test")
    source = _approval_message(_CANARIES[0])
    staged = await quarantine_store.stage_candidate(
        candidate_key="grounding-profile-unresolved",
        reason_codes=["grounding_needs_judge"],
        content=_CANARIES[1],
        metadata={"key_facts": [_CANARIES[0]]},
        importance=0.7,
        session_id=source.session_id,
        persona_id=None,
        source_window=stable_source_window(source),
        is_group_chat=False,
    )

    engine = MagicMock()
    engine.add_memory = AsyncMock(return_value=9)
    processor = MagicMock()
    processor.classify_atoms_from_metadata.return_value = []
    validator = MagicMock()
    validator.revalidate_stored_evidence.return_value = _needs_judge()
    gate_runtime = MagicMock()
    gate_runtime.resolve_profile.return_value = None
    gate = MemoryQualityGate(
        quarantine_store,
        memory_engine=engine,
        memory_processor=processor,
        conversation_manager=StableApprovalConversation(source),
        grounding_validator=validator,
        gate_runtime=gate_runtime,
    )
    blocked = await gate.approve(
        staged["candidate_id"],
        expected_revision=staged["revision"],
        actor_id=None,
    )

    assert blocked["status"] == "blocked"
    assert blocked["failure_reason"] == "grounding_judge_unavailable"
    engine.add_memory.assert_not_awaited()
    messages = [record.getMessage() for record in caplog.records]
    profile_logs = [message for message in messages if "profile_unresolved" in message]
    assert len(profile_logs) == 1
    assert "stage=approval" in profile_logs[0]
    assert "component=memory_quality_gate" in profile_logs[0]
    assert all(canary not in caplog.text for canary in _CANARIES)
