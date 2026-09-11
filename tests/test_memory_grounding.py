"""记忆来源忠实性校验契约。"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.quality.domain.gate_config import GateProfile
from core.features.recall.processors.conversation_formatter import ConversationFormatter
from core.features.recall.processors.memory_grounding import MemoryGroundingValidator
from core.features.recall.processors.memory_processor import MemoryProcessor
from core.shared.contracts.conversation import Message
from core.shared.cost_control import CostControl
from core.shared.extra_llm_budget import ExtraLlmBudget, extra_llm_budget_scope


def _message(
    index: int,
    content: str,
    *,
    sender_id: str = "user-1",
    sender_name: str = "Alice",
    group_id: str | None = None,
    timestamp: float | None = None,
    role: str = "user",
    metadata: dict[str, Any] | None = None,
) -> Message:
    """构造带稳定顺序的测试消息。"""

    return Message(
        id=index + 1,
        session_id="session-1",
        role=role,
        content=content,
        sender_id=sender_id,
        sender_name=sender_name,
        group_id=group_id,
        timestamp=time.time() + index if timestamp is None else timestamp,
        metadata=metadata or {},
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


def test_grounding_prompt_declares_reference_budget() -> None:
    """来源 Prompt 应声明与门禁 profile 相同的最大引用数。"""
    contract = MemoryGroundingValidator().prompt_contract(2, max_references=16)

    assert "每条记忆最多 16 条 source_refs" in contract


def test_grounding_accepts_trusted_identity_label_numbers() -> None:
    """可信身份标签中的数字不应被当作候选编造数字。"""
    source = "我喜欢喝咖啡。"
    message = _message(0, source, sender_name="阿明")
    message.metadata = {
        "identity_trusted": True,
        "identity_namespace": "qq",
        "stable_user_id": "123456",
        "canonical_user_id": "123456",
        "identity_label": "QQ:123456",
    }

    result = MemoryGroundingValidator().validate(
        _candidate(
            "阿明（QQ:123456）喜欢喝咖啡。",
            source_refs=[{"message_index": 0, "start": 0, "end": len(source)}],
        ),
        [message],
        is_group_chat=False,
    )

    assert result.allowed is True
    assert "grounding_numeric_conflict" not in result.reason_codes


def test_grounding_rejects_untrusted_identity_label_numbers() -> None:
    """无可信标志的身份标签不得豁免候选数字校验。"""
    source = "我喜欢喝咖啡。"
    message = _message(0, source, sender_name="阿明")
    message.metadata = {"identity_label": "QQ:123456"}

    result = MemoryGroundingValidator().validate(
        _candidate(
            "阿明（QQ:123456）喜欢喝咖啡。",
            source_refs=[{"message_index": 0, "start": 0, "end": len(source)}],
        ),
        [message],
        is_group_chat=False,
    )

    assert "grounding_numeric_conflict" in result.reason_codes


def test_grounding_accepts_today_normalized_from_message_timestamp() -> None:
    """消息时间戳可为“今天”提供确定的日期归一化锚点。"""
    timestamp = datetime(2026, 8, 18, 12, 0).timestamp()
    source = "我今天去了北京。"

    result = MemoryGroundingValidator().validate(
        _candidate(
            "我在2026-08-18去了北京。",
            source_refs=[{"message_index": 0, "start": 0, "end": len(source)}],
        ),
        [_message(0, source, timestamp=timestamp)],
        is_group_chat=False,
    )

    assert result.allowed is True
    assert "grounding_numeric_conflict" not in result.reason_codes


def test_grounding_rejects_today_for_tomorrow_noon() -> None:
    """“明天中午”只能支持次日，不能同时支持消息当天。"""
    timestamp = datetime(2026, 8, 18, 12, 0).timestamp()
    source = "我明天中午去北京。"

    result = MemoryGroundingValidator().validate(
        _candidate(
            "我在2026-08-18去北京。",
            source_refs=[{"message_index": 0, "start": 0, "end": len(source)}],
        ),
        [_message(0, source, timestamp=timestamp)],
        is_group_chat=False,
    )

    assert "grounding_numeric_conflict" in result.reason_codes


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
    judge_call = judge.await_args
    assert judge_call is not None
    judge_payload = judge_call.args[0]
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


def test_negation_whitelist_avoids_false_positive() -> None:
    """内置白名单短语剔除后，肯定句不再被误判为否定冲突。"""

    source = "这个方案不错，我很满意，就按这个来吧"
    messages = [_message(0, source)]
    result = MemoryGroundingValidator().validate(
        _candidate(
            "用户喜欢这个方案",
            source_refs=[{"message_index": 0, "start": 0, "end": len(source)}],
        ),
        messages,
        is_group_chat=False,
    )

    assert result.allowed is True or result.status == "needs_judge"
    assert "grounding_negation_conflict" not in result.reason_codes


def test_custom_negation_whitelist_extends() -> None:
    """profile 白名单与内置白名单取并集后剔除。"""

    profile = GateProfile(name="p", word_lists={"negation_whitelist": ["没意见"]})  # type: ignore[arg-type]
    source = "我没意见"
    messages = [_message(0, source)]
    result = MemoryGroundingValidator().validate(
        _candidate(
            "用户同意",
            source_refs=[{"message_index": 0, "start": 0, "end": len(source)}],
        ),
        messages,
        is_group_chat=False,
        profile=profile,
    )

    assert "grounding_negation_conflict" not in result.reason_codes


def test_negation_markers_replace_mode() -> None:
    """标记集 replace 模式后，内置「不」不再触发极性判定。"""

    profile = GateProfile(
        name="p",
        word_lists={"negation_markers": {"mode": "replace", "items": ["never"]}},  # type: ignore[arg-type]
    )
    source = "我不去"
    messages = [_message(0, source)]
    result = MemoryGroundingValidator().validate(
        _candidate(
            "用户要去",
            source_refs=[{"message_index": 0, "start": 0, "end": len(source)}],
        ),
        messages,
        is_group_chat=False,
        profile=profile,
    )

    assert "grounding_negation_conflict" not in result.reason_codes


def test_cjk_number_normalization_avoids_false_positive() -> None:
    """中文数字归一为阿拉伯数字后，书写形式差异不再误报数字冲突。"""

    source = "我养了两只猫，一只橘猫一只狸花"
    messages = [_message(0, source)]
    result = MemoryGroundingValidator().validate(
        _candidate(
            "用户养了2只猫",
            source_refs=[{"message_index": 0, "start": 0, "end": len(source)}],
        ),
        messages,
        is_group_chat=False,
    )

    assert "grounding_numeric_conflict" not in result.reason_codes


def test_genuine_number_conflict_still_rejected() -> None:
    """真实数值冲突（300 vs 500）仍被拦截。"""

    source = "这次预算是300元"
    messages = [_message(0, source)]
    result = MemoryGroundingValidator().validate(
        _candidate(
            "这次预算是500元",
            source_refs=[{"message_index": 0, "start": 0, "end": len(source)}],
        ),
        messages,
        is_group_chat=False,
    )

    assert "grounding_numeric_conflict" in result.reason_codes


def test_single_bad_ref_no_longer_rejects_candidate() -> None:
    """单条非法引用被跳过，剩余有效引用继续支撑候选。"""

    source = "我养了两只猫"
    messages = [_message(0, source)]
    result = MemoryGroundingValidator().validate(
        _candidate(
            "用户养了两只猫",
            source_refs=[
                {"message_index": 0, "start": 0, "end": 999},
                {"message_index": 0, "start": 0, "end": len(source)},
            ],
        ),
        messages,
        is_group_chat=False,
    )

    assert "grounding_reference_invalid" not in result.reason_codes


def test_all_bad_refs_still_rejected() -> None:
    """零条有效引用时仍整体拒绝。"""

    messages = [_message(0, "我养了两只猫")]
    result = MemoryGroundingValidator().validate(
        _candidate(
            "用户养了两只猫",
            source_refs=[{"message_index": 5, "start": 0, "end": 1}],
        ),
        messages,
        is_group_chat=False,
    )

    assert "grounding_reference_invalid" in result.reason_codes


def test_numeric_check_disabled_skips_numeric_conflict() -> None:
    """关闭数字检查后，300 vs 500 不再产生数字冲突原因码。"""

    profile = GateProfile(name="p", checks={"numeric_check": False})  # type: ignore[arg-type]
    source = "这次预算是300元"
    messages = [_message(0, source)]
    result = MemoryGroundingValidator().validate(
        _candidate(
            "这次预算是500元",
            source_refs=[{"message_index": 0, "start": 0, "end": len(source)}],
        ),
        messages,
        is_group_chat=False,
        profile=profile,
    )

    assert "grounding_numeric_conflict" not in result.reason_codes


def test_negation_check_disabled_skips_negation_conflict() -> None:
    """关闭否定检查后，极性冲突不再触发。"""

    profile = GateProfile(name="p", checks={"negation_check": False})  # type: ignore[arg-type]
    source = "我喜欢香菜"
    messages = [_message(0, source)]
    result = MemoryGroundingValidator().validate(
        _candidate(
            "用户不喜欢香菜",
            source_refs=[{"message_index": 0, "start": 0, "end": len(source)}],
        ),
        messages,
        is_group_chat=False,
        profile=profile,
    )

    assert "grounding_negation_conflict" not in result.reason_codes


def test_group_subject_check_disabled_skips_subject_verdict() -> None:
    """关闭群聊主体检查后，多用户且未声明主体不再隔离。"""

    profile = GateProfile(name="p", checks={"group_subject_check": False})  # type: ignore[arg-type]
    first = "我周五有空"
    second = "我周六有空"
    messages = [
        _message(0, first, sender_id="u-1", sender_name="Alice", group_id="g-1"),
        _message(1, second, sender_id="u-2", sender_name="Bob", group_id="g-1"),
    ]
    result = MemoryGroundingValidator().validate(
        _candidate(
            "群成员周末有空",
            source_refs=[
                {"message_index": 0, "start": 0, "end": len(first)},
                {"message_index": 1, "start": 0, "end": len(second)},
            ],
        ),
        messages,
        is_group_chat=True,
        profile=profile,
    )

    assert "grounding_subject_ambiguous" not in result.reason_codes
    assert "grounding_subject_mismatch" not in result.reason_codes


def test_custom_synonym_pairs_improve_support_score() -> None:
    """profile 同义对并入归一化后，词面差异不再压低支持分。"""

    profile = GateProfile(
        name="p",
        word_lists={"synonym_pairs": [{"source": "喵星人", "target": "猫"}]},  # type: ignore[arg-type]
    )
    source = "我喜欢猫"
    messages = [_message(0, source)]
    result = MemoryGroundingValidator().validate(
        _candidate(
            "用户喜欢喵星人",
            source_refs=[{"message_index": 0, "start": 0, "end": len(source)}],
        ),
        messages,
        is_group_chat=False,
        profile=profile,
    )

    assert result.status == "grounded"


def test_profile_scoring_configuration_applies() -> None:
    """评分权重由 profile 驱动：关闭序列分且 token 权重为 0 时不再放行。"""

    source = "我喜欢猫"
    messages = [_message(0, source)]
    candidate = _candidate(
        "用户喜欢猫",
        source_refs=[{"message_index": 0, "start": 0, "end": len(source)}],
    )
    baseline = MemoryGroundingValidator().validate(
        candidate, messages, is_group_chat=False
    )
    assert baseline.status == "grounded"

    profile = GateProfile(
        name="p",
        scoring={"sequence_enabled": False, "token_weight": 0.0},  # type: ignore[arg-type]
    )
    result = MemoryGroundingValidator().validate(
        candidate,
        messages,
        is_group_chat=False,
        profile=profile,
    )

    assert "grounding_claim_unsupported" in result.reason_codes


def test_revalidate_skips_damaged_evidence_items() -> None:
    """复核时单条畸形证据被跳过，剩余有效证据继续验证。"""

    validator = MemoryGroundingValidator()
    source = "我养了两只猫"
    message = _message(0, source)
    good = {
        "message_fingerprint": validator.message_fingerprint(message),
        "start": 0,
        "end": len(source),
    }
    result = validator.revalidate_stored_evidence(
        _candidate("用户养了两只猫"),
        [message],
        ["not-a-dict", good],
        is_group_chat=False,
    )

    assert result.allowed is True
    assert "grounding_source_evidence_invalid" not in result.reason_codes


def test_revalidate_all_malformed_evidence_invalid() -> None:
    """全部证据畸形时整体返回证据无效。"""

    messages = [_message(0, "我养了两只猫")]
    result = MemoryGroundingValidator().revalidate_stored_evidence(
        _candidate("用户养了两只猫"),
        messages,
        ["junk", 42, None],
        is_group_chat=False,
    )

    assert result.allowed is False
    assert "grounding_source_evidence_invalid" in result.reason_codes


def test_revalidate_all_unmatched_evidence_changed() -> None:
    """证据存在但零条可匹配时整体拒绝并报来源变更。"""

    messages = [_message(0, "我养了两只猫")]
    result = MemoryGroundingValidator().revalidate_stored_evidence(
        _candidate("用户养了两只猫"),
        messages,
        [
            {"message_fingerprint": "deadbeef", "start": 0, "end": 4},
            {"message_fingerprint": "", "start": 0, "end": 2},
        ],
        is_group_chat=False,
    )

    assert result.allowed is False
    assert "grounding_source_changed" in result.reason_codes


def test_custom_whitelist_casefold_matches_uppercase_source() -> None:
    """白名单配置对英文大小写不敏感。"""

    profile = GateProfile(name="p", word_lists={"negation_whitelist": ["no problem"]})  # type: ignore[arg-type]
    source = "NO PROBLEM"
    messages = [_message(0, source)]
    result = MemoryGroundingValidator().validate(
        _candidate(
            "用户同意",
            source_refs=[{"message_index": 0, "start": 0, "end": len(source)}],
        ),
        messages,
        is_group_chat=False,
        profile=profile,
    )

    assert "grounding_negation_conflict" not in result.reason_codes


def test_replace_markers_casefold_recognizes_uppercase() -> None:
    """replace 标记集大小写不敏感：NEVER 能识别 never go。"""

    profile = GateProfile(
        name="p",
        word_lists={"negation_markers": {"mode": "replace", "items": ["NEVER"]}},  # type: ignore[arg-type]
    )
    source = "never go"
    messages = [_message(0, source)]
    result = MemoryGroundingValidator().validate(
        _candidate(
            "用户要去",
            source_refs=[{"message_index": 0, "start": 0, "end": len(source)}],
        ),
        messages,
        is_group_chat=False,
        profile=profile,
    )

    assert "grounding_negation_conflict" in result.reason_codes


def _assistant_message(index: int, content: str) -> Message:
    """构造 assistant 角色引用消息。"""

    return _message(index, content, role="assistant")


def test_negation_ignores_assistant_rhetorical_negation() -> None:
    """生产样本复刻：claim 无标记，仅 assistant 引用含「不/无」→ 放行。"""

    user_source = "工作里我还是写 Python 居多，数据处理方便。"
    assistant_a = "功能不大，但麻雀虽小五脏俱全。"
    assistant_b = "无压力，数据处理本来就该省事。"
    claim = "用户工作中主要写 Python，数据处理方便。"
    messages = [
        _assistant_message(0, assistant_a),
        _message(1, user_source),
        _assistant_message(2, assistant_b),
    ]
    result = MemoryGroundingValidator().validate(
        _candidate(
            claim,
            source_refs=[
                {"message_index": 0, "start": 0, "end": len(assistant_a)},
                {"message_index": 1, "start": 0, "end": len(user_source)},
                {"message_index": 2, "start": 0, "end": len(assistant_b)},
            ],
        ),
        messages,
        is_group_chat=False,
    )

    assert result.status == "grounded"
    assert "grounding_negation_conflict" not in result.reason_codes


def test_negation_still_quarantines_user_polarity_flip() -> None:
    """user「我还没吃饭」vs claim 断言已吃 → 仍按否定冲突隔离。"""

    user_source = "我还没吃饭"
    messages = [_message(0, user_source)]
    result = MemoryGroundingValidator().validate(
        _candidate(
            "用户已经吃过饭。",
            source_refs=[{"message_index": 0, "start": 0, "end": len(user_source)}],
        ),
        messages,
        is_group_chat=False,
    )

    assert result.allowed is False
    assert "grounding_negation_conflict" in result.reason_codes


def test_negation_skips_check_without_user_references() -> None:
    """引用全部为 assistant 时跳过否定检查（无 user 极性基准）。"""

    assistant_source = "这个功能毫无悬念，你不用再确认了。"
    messages = [_assistant_message(0, assistant_source)]
    result = MemoryGroundingValidator().validate(
        _candidate(
            "助手确认功能可用。",
            source_refs=[
                {"message_index": 0, "start": 0, "end": len(assistant_source)}
            ],
        ),
        messages,
        is_group_chat=False,
    )

    assert "grounding_negation_conflict" not in result.reason_codes


def test_negation_whitelist_applies_to_user_snippets() -> None:
    """whitelist「不错」在 user 片段中被剔除，不触发极性冲突。"""

    user_source = "这个方案不错，我很满意"
    messages = [_message(0, user_source)]
    result = MemoryGroundingValidator().validate(
        _candidate(
            "用户认可这个方案。",
            source_refs=[{"message_index": 0, "start": 0, "end": len(user_source)}],
        ),
        messages,
        is_group_chat=False,
    )

    assert "grounding_negation_conflict" not in result.reason_codes


def test_revalidate_negation_matches_validate_verdict() -> None:
    """复核入口与首次校验使用同一 user 极性判定，结论一致。"""

    user_source = "工作里我还是写 Python 居多，数据处理方便。"
    assistant_source = "功能不大，但麻雀虽小五脏俱全。"
    claim = "用户工作中主要使用 Python，因为数据处理方便。"
    messages = [
        _message(0, user_source),
        _assistant_message(1, assistant_source),
    ]
    validator = MemoryGroundingValidator()
    direct = validator.validate(
        _candidate(
            claim,
            source_refs=[
                {"message_index": 0, "start": 0, "end": len(user_source)},
                {"message_index": 1, "start": 0, "end": len(assistant_source)},
            ],
        ),
        messages,
        is_group_chat=False,
    )
    evidence = [
        {
            "message_fingerprint": validator.message_fingerprint(messages[0]),
            "start": 0,
            "end": len(user_source),
        },
        {
            "message_fingerprint": validator.message_fingerprint(messages[1]),
            "start": 0,
            "end": len(assistant_source),
        },
    ]
    replay = validator.revalidate_stored_evidence(
        _candidate(claim),
        messages,
        evidence,
        is_group_chat=False,
    )

    assert direct.status == "grounded"
    assert replay.status == direct.status
    assert "grounding_negation_conflict" not in replay.reason_codes


def test_evidence_carries_stable_message_identity() -> None:
    """证据必须带稳定消息标识、窗口序号与角色。"""

    source = "我喜欢喝咖啡。"
    messages = [_message(0, source)]

    result = MemoryGroundingValidator().validate(
        _candidate(
            source,
            source_refs=[{"message_index": 0, "start": 0, "end": len(source)}],
        ),
        messages,
        is_group_chat=False,
        message_seqs=[42],
    )

    assert result.allowed is True
    evidence = result.evidence[0]
    assert evidence["message_id"] == messages[0].id
    assert evidence["message_seq"] == 42
    assert evidence["role"] == "user"
    assert evidence["message_index"] == 0


def test_assistant_restatement_alone_is_quarantined() -> None:
    """仅由助手复述支撑的声明不得进入 canonical。"""

    assistant_source = "用户说他下周六要搬家。"
    messages = [_assistant_message(0, assistant_source)]

    result = MemoryGroundingValidator().validate(
        _candidate(
            "用户下周六要搬家。",
            source_refs=[
                {"message_index": 0, "start": 0, "end": len(assistant_source)}
            ],
        ),
        messages,
        is_group_chat=False,
    )

    assert result.allowed is False
    assert result.status == "quarantine"
    assert "grounding_user_source_missing" in result.reason_codes
    assert result.evidence[0]["role"] == "assistant"


def test_system_role_evidence_cannot_support_fact() -> None:
    """系统内容不能充当用户事实的唯一来源。"""

    system_source = "系统记录：该用户已订阅会员。"
    messages = [_message(0, system_source, role="system")]

    result = MemoryGroundingValidator().validate(
        _candidate(
            "该用户已订阅会员。",
            source_refs=[{"message_index": 0, "start": 0, "end": len(system_source)}],
        ),
        messages,
        is_group_chat=False,
    )

    assert result.allowed is False
    assert "grounding_user_source_missing" in result.reason_codes


def test_user_expression_supports_fact_beside_restatement() -> None:
    """用户直接表达与助手复述同时出现时，按用户片段判定并保留全部证据。"""

    user_source = "我下周六要搬家。"
    assistant_source = "好的，下周六搬家，我先记下来。"
    messages = [
        _message(0, user_source),
        _assistant_message(1, assistant_source),
    ]

    result = MemoryGroundingValidator().validate(
        _candidate(
            user_source,
            source_refs=[
                {"message_index": 0, "start": 0, "end": len(user_source)},
                {"message_index": 1, "start": 0, "end": len(assistant_source)},
            ],
        ),
        messages,
        is_group_chat=False,
    )

    assert result.allowed is True
    assert {item["role"] for item in result.evidence} == {"user", "assistant"}


def test_group_same_name_members_are_not_merged_by_display_name() -> None:
    """群聊同名成员不能因昵称相同被当成同一主体。"""

    first = "我周五有空。"
    second = "我周五没空。"
    messages = [
        _message(0, first, sender_id="u-1", sender_name="小明", group_id="g-1"),
        _message(1, second, sender_id="u-2", sender_name="小明", group_id="g-1"),
    ]

    result = MemoryGroundingValidator().validate(
        _candidate(
            "群成员周五有空。",
            source_refs=[
                {"message_index": 0, "start": 0, "end": len(first)},
                {"message_index": 1, "start": 0, "end": len(second)},
            ],
            participants=["小明"],
        ),
        messages,
        is_group_chat=True,
    )

    assert result.allowed is False
    assert "grounding_subject_ambiguous" in result.reason_codes


def test_group_same_name_members_are_distinguished_by_stable_label() -> None:
    """带稳定身份标签的同名成员可按标识分别归属，不误放行错误主体。"""

    first = "我周五有空。"
    second = "我周五没空。"
    messages = [
        _message(
            0,
            first,
            sender_id="qq-1",
            sender_name="小明",
            group_id="g-1",
            metadata={
                "identity_trusted": True,
                "identity_label": "QQ:1001",
                "canonical_user_id": "qq-1",
            },
        ),
        _message(
            1,
            second,
            sender_id="qq-2",
            sender_name="小明",
            group_id="g-1",
            metadata={
                "identity_trusted": True,
                "identity_label": "QQ:1002",
                "canonical_user_id": "qq-2",
            },
        ),
    ]

    result = MemoryGroundingValidator().validate(
        _candidate(
            "群成员周五有空。",
            source_refs=[
                {"message_index": 0, "start": 0, "end": len(first)},
                {"message_index": 1, "start": 0, "end": len(second)},
            ],
            participants=["QQ:1001"],
        ),
        messages,
        is_group_chat=True,
    )

    assert result.allowed is False
    assert "grounding_subject_mismatch" in result.reason_codes


def test_revalidate_detects_changed_message_content() -> None:
    """同一消息标识但正文被改写时，旧证据不得继续生效。"""

    validator = MemoryGroundingValidator()
    original = "我养了两只猫"
    message = _message(0, original)
    stored = [
        {
            "message_index": 0,
            "message_id": message.id,
            "role": "user",
            "start": 0,
            "end": len(original),
            "message_fingerprint": validator.message_fingerprint(message),
        }
    ]

    result = validator.revalidate_stored_evidence(
        {"summary": original, "key_facts": [original]},
        [_message(0, "我养了三只狗")],
        stored,
        is_group_chat=False,
    )

    assert result.allowed is False
    assert "grounding_source_changed" in result.reason_codes


def test_revalidate_does_not_substitute_same_text_from_other_subject() -> None:
    """带稳定标识的证据不得回退到正文相同的另一条消息。"""

    validator = MemoryGroundingValidator()
    content = "我周五有空。"
    original = _message(0, content, sender_id="u-1", sender_name="小明")
    stored = [
        {
            "message_index": 0,
            "message_id": original.id,
            "role": "user",
            "start": 0,
            "end": len(content),
            "message_fingerprint": validator.message_fingerprint(original),
        }
    ]
    other_subject = _message(1, content, sender_id="u-2", sender_name="小红")

    result = validator.revalidate_stored_evidence(
        {"summary": content, "key_facts": [content]},
        [other_subject],
        stored,
        is_group_chat=True,
    )

    assert result.allowed is False
    assert "grounding_source_changed" in result.reason_codes


def test_revalidate_keeps_persisted_window_sequence() -> None:
    """复核重建的证据保留已持久化的窗口序号。"""

    validator = MemoryGroundingValidator()
    source = "我养了两只猫"
    message = _message(0, source)
    stored = [
        {
            "message_index": 0,
            "message_id": message.id,
            "message_seq": 11,
            "role": "user",
            "start": 0,
            "end": len(source),
            "message_fingerprint": validator.message_fingerprint(message),
        }
    ]

    result = validator.revalidate_stored_evidence(
        {"summary": source, "key_facts": [source]},
        [message],
        stored,
        is_group_chat=False,
    )

    assert result.allowed is True
    assert result.evidence[0]["message_seq"] == 11


@pytest.mark.asyncio
async def test_assistant_only_window_produces_quarantine_candidate() -> None:
    """助手单方面声称只能产出隔离候选，不能产出可写候选。"""

    assistant_source = "用户说他下周六要搬家。"
    provider = MagicMock()
    response = MagicMock(
        completion_text=json.dumps(
            {
                "memories": [
                    {
                        "content": "用户下周六要搬家。",
                        "key_facts": ["用户下周六要搬家。"],
                        "topics": ["搬家"],
                        "importance": 0.7,
                        "sentiment": "neutral",
                        "source_refs": [
                            {
                                "message_index": 0,
                                "start": 0,
                                "end": len(assistant_source),
                            }
                        ],
                    }
                ],
                "confidence": 0.8,
                "extraction_quality": "high",
            },
            ensure_ascii=False,
        )
    )
    provider.text_chat = AsyncMock(return_value=response)
    processor = MemoryProcessor(llm_provider=provider)
    messages = [_assistant_message(0, assistant_source)]

    results = await processor.process_conversation(messages)

    assert len(results) == 1
    metadata = results[0]["metadata"]
    assert metadata["quality_gate_action"] == "quarantine"
    assert "grounding_user_source_missing" in metadata["grounding_reason_codes"]
    assert metadata["source_evidence"][0]["role"] == "assistant"
