"""记忆来源忠实性校验契约。"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.base.cost_control import CostControl
from core.base.extra_llm_budget import ExtraLlmBudget, extra_llm_budget_scope
from core.models.conversation_models import Message
from core.processors.conversation_formatter import ConversationFormatter
from core.processors.memory_grounding import MemoryGroundingValidator
from core.processors.memory_processor import MemoryProcessor


def _message(
    index: int,
    content: str,
    *,
    sender_id: str = "user-1",
    sender_name: str = "Alice",
    group_id: str | None = None,
    timestamp: float | None = None,
) -> Message:
    """构造带稳定顺序的测试消息。"""

    return Message(
        id=index + 1,
        session_id="session-1",
        role="user",
        content=content,
        sender_id=sender_id,
        sender_name=sender_name,
        group_id=group_id,
        timestamp=time.time() + index if timestamp is None else timestamp,
    )


def _candidate(
    summary: str,
    *,
    source_refs: list[dict[str, int]] | None = None,
    participants: list[str] | None = None,
) -> dict[str, object]:
    """构造待校验的抽取候选。"""

    return {
        "summary": summary,
        "key_facts": [summary],
        "participants": participants or [],
        "source_refs": source_refs or [],
    }


def test_grounding_blocks_hallucinated_fact() -> None:
    """来源未支持的合成事实必须被隔离。"""

    source = "我喜欢喝咖啡。"
    messages = [_message(0, source)]
    result = MemoryGroundingValidator().validate(
        _candidate(
            "用户住在北京。",
            source_refs=[{"message_index": 0, "start": 0, "end": len(source)}],
        ),
        messages,
        is_group_chat=False,
    )

    assert result.allowed is False
    assert "grounding_claim_unsupported" in result.reason_codes


def test_grounding_accepts_reasonable_paraphrase() -> None:
    """含同义改写的事实不能因逐字不一致被误杀。"""

    content = "我打算周五去上海出差。"
    messages = [_message(0, content)]
    result = MemoryGroundingValidator().validate(
        _candidate(
            "用户计划星期五前往上海。",
            source_refs=[
                {"message_index": 0, "start": 0, "end": len(content)},
            ],
        ),
        messages,
        is_group_chat=False,
    )

    assert result.allowed is True
    assert result.status == "grounded"


@pytest.mark.parametrize(
    ("source", "claim", "timestamp"),
    [
        (
            "The meeting is on 8 May, 2023.",
            "The meeting is on 2023-05-08.",
            None,
        ),
        (
            "Observation date: 8 May, 2023. Caroline visited the museum yesterday.",
            "Caroline visited the museum on 2023-05-07.",
            None,
        ),
        (
            "Observation date: 8 May, 2023. Melanie moved to Spain last year.",
            "Melanie moved to Spain in 2022.",
            None,
        ),
        (
            "Observation date: 8 May, 2023. Melanie moved to Spain three years ago.",
            "Melanie moved to Spain in 2020.",
            None,
        ),
        (
            "Observation date: 8 May, 2023. Caroline visited the museum last Saturday.",
            "Caroline visited the museum on 2023-05-06.",
            None,
        ),
        (
            "Caroline visited the museum yesterday.",
            "Caroline visited the museum on 2023-05-07.",
            datetime(2023, 5, 8, 12, 0).timestamp(),
        ),
    ],
)
def test_grounding_accepts_supported_date_normalization(
    source: str,
    claim: str,
    timestamp: float | None,
) -> None:
    """绝对日期和有可靠锚点的相对日期允许确定性规范化。"""

    result = MemoryGroundingValidator().validate(
        _candidate(
            claim,
            source_refs=[{"message_index": 0, "start": 0, "end": len(source)}],
        ),
        [_message(0, source, timestamp=timestamp)],
        is_group_chat=False,
    )

    assert result.allowed is True
    assert result.status == "grounded"


@pytest.mark.parametrize(
    ("source", "claim"),
    [
        (
            "Observation date: 8:56 pm on 20 July, 2023. 记录日期。",
            "记录日期是2023年7月20日。",
        ),
        (
            "Observation date: 1:51 pm on 15 July, 2023. "
            "The workshop was the previous Friday.",
            "The workshop was on 2023-07-14.",
        ),
    ],
)
def test_grounding_accepts_unambiguous_two_digit_dates(
    source: str,
    claim: str,
) -> None:
    """两位中文日期和明确的 previous weekday 应按正文锚点规范化。"""

    result = MemoryGroundingValidator().validate(
        _candidate(
            claim,
            source_refs=[{"message_index": 0, "start": 0, "end": len(source)}],
        ),
        [_message(0, source)],
        is_group_chat=False,
    )

    assert result.allowed is True


def test_grounding_rejects_clock_date_with_source_observation_date() -> None:
    """正文已有观察日期时，不得用插件当前日期替代来源锚点。"""

    source = "Observation date: 8 May, 2023. The event happened yesterday."
    claim = "The event happened on 2026-08-01."
    result = MemoryGroundingValidator().validate(
        _candidate(
            claim,
            source_refs=[{"message_index": 0, "start": 0, "end": len(source)}],
        ),
        [_message(0, source)],
        is_group_chat=False,
    )

    assert result.allowed is False
    assert "grounding_numeric_conflict" in result.reason_codes


@pytest.mark.parametrize(
    ("source", "claim", "reason"),
    [
        ("这次预算是300元。", "这次预算是500元。", "grounding_numeric_conflict"),
        (
            "Observation date: 8 May, 2023. The budget is 300.",
            "The budget is 5.",
            "grounding_numeric_conflict",
        ),
        (
            "Observation date: 8 May, 2023. The budget changed last year.",
            "The budget is 2022.",
            "grounding_numeric_conflict",
        ),
        ("我喜欢香菜。", "用户不喜欢香菜。", "grounding_negation_conflict"),
    ],
)
def test_grounding_blocks_high_impact_conflicts(
    source: str,
    claim: str,
    reason: str,
) -> None:
    """数字和否定极性冲突不得由模糊相似度放行。"""

    result = MemoryGroundingValidator().validate(
        _candidate(
            claim,
            source_refs=[{"message_index": 0, "start": 0, "end": len(source)}],
        ),
        [_message(0, source)],
        is_group_chat=False,
    )

    assert result.allowed is False
    assert reason in result.reason_codes


def test_grounding_rejects_out_of_bounds_source_reference() -> None:
    """越界引用必须失败，且不能静默回退到自动推断。"""

    result = MemoryGroundingValidator().validate(
        _candidate(
            "用户喜欢咖啡。",
            source_refs=[{"message_index": 3, "start": 0, "end": 10}],
        ),
        [_message(0, "我喜欢咖啡。")],
        is_group_chat=False,
    )

    assert result.allowed is False
    assert "grounding_reference_invalid" in result.reason_codes


def test_grounding_rejects_ambiguous_group_subject() -> None:
    """群聊引用跨越多个用户且未声明主体时必须隔离。"""

    first = "我周五有空。"
    second = "我周五没空。"
    messages = [
        _message(0, first, sender_id="u-1", sender_name="Alice", group_id="g-1"),
        _message(1, second, sender_id="u-2", sender_name="Bob", group_id="g-1"),
    ]
    result = MemoryGroundingValidator().validate(
        _candidate(
            "群成员周五有空。",
            source_refs=[
                {"message_index": 0, "start": 0, "end": len(first)},
                {"message_index": 1, "start": 0, "end": len(second)},
            ],
        ),
        messages,
        is_group_chat=True,
    )

    assert result.allowed is False
    assert "grounding_subject_ambiguous" in result.reason_codes


def test_grounding_can_infer_controlled_reference_for_legacy_output() -> None:
    """旧输出缺少引用时，只允许由本地证据唯一推断受控引用。"""

    result = MemoryGroundingValidator().validate(
        _candidate("用户喜欢喝咖啡。"),
        [_message(0, "你好，我喜欢喝咖啡。")],
        is_group_chat=False,
    )

    assert result.allowed is True
    assert result.evidence[0]["inferred"] is True


def test_grounded_conversation_uses_stable_anonymous_source_labels() -> None:
    """来源 Prompt 必须为每条消息生成稳定且不重复的 S<n> 标签。"""

    formatted = ConversationFormatter().format_conversation_with_source_refs(
        [_message(0, "第一条"), _message(1, "第二条")]
    )

    lines = formatted.splitlines()
    assert lines[0].startswith("[S0 chars=3] ")
    assert lines[1].startswith("[S1 chars=3] ")
    assert "第一条" in lines[0]
    assert "第二条" in lines[1]


def test_grounding_prompt_requires_source_language_and_exact_offsets() -> None:
    """抽取 Prompt 必须约束来源主语言，并解释 chars 与正文 offset 边界。"""

    contract = MemoryGroundingValidator().prompt_contract(2)

    assert "主要语言" in contract
    assert "chars" in contract
    assert "消息头中的时间" in contract
    assert "Observation date/观察日期/对话日期优先于插件当前时间" in contract
    assert "不得猜测绝对年月日" in contract


@pytest.mark.asyncio
async def test_grounding_judge_only_receives_current_referenced_scope() -> None:
    """Judge 只能看到当前候选引用的消息片段。"""

    provider = MagicMock()
    response = MagicMock(
        completion_text=(
            '{"memories":[{"content":"用户准备更换工作。",'
            '"key_facts":["用户准备更换工作。"],"topics":["工作"],'
            '"importance":0.7,"sentiment":"neutral",'
            '"source_refs":[{"message_index":0,"start":0,"end":9}]}],'
            '"confidence":0.8,"extraction_quality":"high"}'
        )
    )
    provider.text_chat = AsyncMock(return_value=response)
    judge = AsyncMock(return_value=True)
    processor = MemoryProcessor(
        llm_provider=provider,
        cost_control=CostControl(mode="quality", max_extra_llm_calls_per_turn=1),
        grounding_judge=judge,
    )
    messages = [
        _message(0, "我最近在考虑换工作。"),
        _message(1, "银行卡密码是秘密。"),
    ]

    with extra_llm_budget_scope(ExtraLlmBudget(1)):
        await processor.process_conversation(messages)

    judge.assert_awaited_once()
    judge_payload = judge.await_args.args[0]
    assert "换工作" in judge_payload["source_text"]
    assert "银行卡" not in judge_payload["source_text"]


@pytest.mark.asyncio
async def test_grounding_judge_cancellation_propagates() -> None:
    """Judge 取消属于控制流，必须穿透处理器。"""

    provider = MagicMock()
    response = MagicMock(
        completion_text=(
            '{"memories":[{"content":"用户准备更换工作。",'
            '"key_facts":["用户准备更换工作。"],"topics":["工作"],'
            '"importance":0.7,"sentiment":"neutral",'
            '"source_refs":[{"message_index":0,"start":0,"end":9}]}],'
            '"confidence":0.8,"extraction_quality":"high"}'
        )
    )
    provider.text_chat = AsyncMock(return_value=response)
    judge = AsyncMock(side_effect=asyncio.CancelledError)
    processor = MemoryProcessor(
        llm_provider=provider,
        cost_control=CostControl(mode="quality", max_extra_llm_calls_per_turn=1),
        grounding_judge=judge,
    )

    with extra_llm_budget_scope(ExtraLlmBudget(1)):
        with pytest.raises(asyncio.CancelledError):
            await processor.process_conversation(
                [_message(0, "我最近在考虑换工作。")],
            )
