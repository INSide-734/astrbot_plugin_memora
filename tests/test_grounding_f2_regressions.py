"""F2 grounding 与记忆抽取边界回归。"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.recall.processors.grounding_checks import canonical_numbers
from core.features.recall.processors.memory_grounding import MemoryGroundingValidator
from core.features.recall.processors.memory_processor import MemoryProcessor
from core.shared.contracts.conversation import Message


def _message(
    index: int,
    content: str,
    *,
    role: str = "user",
    sender_id: str | None = None,
    sender_name: str | None = None,
    metadata: dict[str, object] | None = None,
    timestamp: float | None = None,
) -> Message:
    """构造带稳定消息身份的测试消息。"""

    return Message(
        id=index + 1,
        session_id="grounding-f2",
        role=role,
        content=content,
        sender_id=sender_id or f"user-{index}",
        sender_name=sender_name,
        group_id="group-f2" if sender_id else None,
        timestamp=(
            timestamp
            if timestamp is not None
            else datetime(2026, 8, 18, 12, 0).timestamp()
        ),
        metadata=metadata or {},
    )


def _candidate(
    summary: str,
    *,
    source_refs: list[dict[str, int]] | None = None,
    participants: list[str] | None = None,
) -> dict[str, object]:
    """构造最小可验证记忆候选。"""

    candidate: dict[str, object] = {
        "summary": summary,
        "key_facts": [summary],
    }
    if source_refs is not None:
        candidate["source_refs"] = source_refs
    if participants is not None:
        candidate["participants"] = participants
    return candidate


def _full_ref(index: int, content: str) -> dict[str, int]:
    """构造一条完整正文引用。"""

    return {"message_index": index, "start": 0, "end": len(content)}


def test_inferred_references_prefer_user_evidence_over_assistant_paraphrase() -> None:
    """缺少 source_refs 时，用户证据不被更高分的助手复述排挤。"""

    user = "我喜欢喝咖啡。"
    assistant = "用户喜欢喝咖啡。"
    messages = [
        _message(0, user),
        _message(1, assistant, role="assistant"),
    ]

    result = MemoryGroundingValidator().validate(
        _candidate("用户喜欢喝咖啡。"),
        messages,
        is_group_chat=False,
    )

    assert result.allowed is True
    assert [item["message_index"] for item in result.evidence] == [0]
    assert {item["role"] for item in result.evidence} == {"user"}
    assert result.evidence[0]["inferred"] is True


def test_explicit_assistant_only_reference_is_not_replaced_by_user_evidence() -> None:
    """显式 assistant-only 引用仍须因缺少用户来源而隔离。"""

    user = "我喜欢喝茶。"
    assistant = "用户喜欢喝咖啡。"
    messages = [
        _message(0, user),
        _message(1, assistant, role="assistant"),
    ]

    result = MemoryGroundingValidator().validate(
        _candidate(
            "用户喜欢喝咖啡。",
            source_refs=[_full_ref(1, assistant)],
        ),
        messages,
        is_group_chat=False,
    )

    assert result.allowed is False
    assert result.status == "quarantine"
    assert "grounding_user_source_missing" in result.reason_codes
    assert result.evidence[0]["role"] == "assistant"


def test_inferred_group_reference_keeps_multi_subject_validation() -> None:
    """推断只选中一名用户时，群内其他主体仍不能被绕过。"""

    first = "我周五有空。"
    second = "我周六有空。"
    messages = [
        _message(0, first, sender_id="member-1", sender_name="Alice"),
        _message(1, second, sender_id="member-2", sender_name="Bob"),
    ]

    result = MemoryGroundingValidator().validate(
        _candidate("群成员周五有空。"),
        messages,
        is_group_chat=True,
    )

    assert result.allowed is False
    assert result.status == "quarantine"
    assert "grounding_subject_ambiguous" in result.reason_codes
    assert [item["message_index"] for item in result.evidence] == [0]


@pytest.mark.parametrize(
    ("source", "claim"),
    [
        ("我喜欢喝咖啡。", "用户一直喜欢喝咖啡。"),
        ("我和朋友去公园。", "我和朋友一起去公园。"),
        ("团队使用这个方案。", "团队统一使用这个方案。"),
        ("明天下雨就取消。", "万一明天下雨就取消。"),
    ],
)
def test_non_quantity_cjk_words_do_not_trigger_numeric_conflict(
    source: str, claim: str
) -> None:
    """常见非数量词中的中文数字字符不应被当成数值。"""

    words = ("一直", "一起", "统一", "万一")
    assert all(not canonical_numbers(word) for word in words)

    result = MemoryGroundingValidator().validate(
        _candidate(claim, source_refs=[_full_ref(0, source)]),
        [_message(0, source)],
        is_group_chat=False,
    )

    assert result.allowed is True
    assert "grounding_numeric_conflict" not in result.reason_codes


@pytest.mark.parametrize(
    ("source", "claim", "source_number", "claim_number"),
    [
        (
            "我的身高是一百七十八厘米。",
            "用户身高是一百八十厘米。",
            "178",
            "180",
        ),
        (
            "我正在读第十二章。",
            "用户正在读第十三章。",
            "12",
            "13",
        ),
    ],
)
def test_genuine_cjk_quantity_conflicts_remain_rejected(
    source: str,
    claim: str,
    source_number: str,
    claim_number: str,
) -> None:
    """带量词或序数语境的真实中文数量变化仍须隔离。"""

    assert canonical_numbers(source) == {source_number}
    assert canonical_numbers(claim) == {claim_number}

    result = MemoryGroundingValidator().validate(
        _candidate(claim, source_refs=[_full_ref(0, source)]),
        [_message(0, source)],
        is_group_chat=False,
    )

    assert result.allowed is False
    assert result.status == "quarantine"
    assert "grounding_numeric_conflict" in result.reason_codes


def test_chinese_weekday_conflict_remains_rejected() -> None:
    """中文星期表达仍参与真实日期数值冲突校验。"""

    source = "我周五有空。"
    claim = "用户周六有空。"
    result = MemoryGroundingValidator().validate(
        _candidate(claim, source_refs=[_full_ref(0, source)]),
        [_message(0, source)],
        is_group_chat=False,
    )

    assert result.allowed is False
    assert "grounding_numeric_conflict" in result.reason_codes


def test_arabic_decimal_temperature_and_ratio_behavior_is_preserved() -> None:
    """阿拉伯小数、温度和比例的既有匹配边界保持不变。"""

    source = "我测得温度是92.50°C，冲煮比例是1:15。"
    claim = "用户测得温度是92.5°C，冲煮比例是1:15。"
    result = MemoryGroundingValidator().validate(
        _candidate(claim, source_refs=[_full_ref(0, source)]),
        [_message(0, source)],
        is_group_chat=False,
    )

    assert result.allowed is True
    assert "grounding_numeric_conflict" not in result.reason_codes


def test_supported_date_normalization_behavior_is_preserved() -> None:
    """正文日期锚点支持的相对日期规范化仍可通过。"""

    source = "Observation date: 8 May, 2023. Caroline visited the museum yesterday."
    claim = "Caroline visited the museum on 2023-05-07."
    result = MemoryGroundingValidator().validate(
        _candidate(claim, source_refs=[_full_ref(0, source)]),
        [_message(0, source)],
        is_group_chat=False,
    )

    assert result.allowed is True
    assert "grounding_numeric_conflict" not in result.reason_codes


def test_trusted_identity_label_numbers_remain_exempt() -> None:
    """运行时确认的身份标签数字仍不被当作编造数量。"""

    source = "我喜欢喝咖啡。"
    message = _message(
        0,
        source,
        sender_id="member-1",
        sender_name="阿明",
        metadata={
            "identity_trusted": True,
            "identity_label": "QQ:123456",
            "stable_user_id": "123456",
            "canonical_user_id": "123456",
        },
    )
    result = MemoryGroundingValidator().validate(
        _candidate(
            "阿明（QQ:123456）喜欢喝咖啡。",
            source_refs=[_full_ref(0, source)],
        ),
        [message],
        is_group_chat=False,
    )

    assert result.allowed is True
    assert "grounding_numeric_conflict" not in result.reason_codes


@pytest.mark.asyncio
async def test_processor_keeps_assistant_only_claim_out_of_writable_atoms() -> None:
    """处理器收到 assistant-only 候选时只返回隔离结果，不生成 Atom。"""

    assistant = "我可以访问天气接口。"
    response = json.dumps(
        {
            "memories": [
                {
                    "summary": assistant,
                    "topics": ["能力"],
                    "key_facts": [assistant],
                    "sentiment": "neutral",
                    "importance": 0.7,
                    "source_refs": [_full_ref(1, assistant)],
                }
            ]
        },
        ensure_ascii=False,
    )
    provider = MagicMock()
    provider.text_chat = AsyncMock(return_value=MagicMock(completion_text=response))
    processor = MemoryProcessor(llm_provider=provider)
    messages = [
        _message(0, "请告诉我今天的天气。"),
        _message(1, assistant, role="assistant"),
    ]

    results = await processor.process_conversation(messages)

    assert len(results) == 1
    metadata = results[0]["metadata"]
    assert metadata["quality_gate_action"] == "quarantine"
    assert "grounding_user_source_missing" in metadata["grounding_reason_codes"]
    assert results[0]["atoms"] == []


def test_inferred_group_evidence_with_unrelated_member_stays_attributed() -> None:
    """推断引用命中单个主体时，窗口内的无关成员不再让候选误判为主体不匹配。"""

    related = "我周五有空。"
    unrelated = "我周六要加班。"
    messages = [
        _message(0, related, sender_id="member-1", sender_name="Alice"),
        _message(1, unrelated, sender_id="member-2", sender_name="Bob"),
    ]

    result = MemoryGroundingValidator().validate(
        _candidate("Alice 说周五有空。", participants=["Alice"]),
        messages,
        is_group_chat=True,
    )

    assert result.allowed is True
    assert [item["message_index"] for item in result.evidence] == [0]
    assert "grounding_subject_ambiguous" not in result.reason_codes
    assert "grounding_subject_mismatch" not in result.reason_codes


@pytest.mark.parametrize("explicit_reference", [False, True])
def test_group_evidence_cannot_be_attributed_to_other_member(
    explicit_reference: bool,
) -> None:
    """显式或推断的单一主体证据都不能归属给窗口里的其他成员。"""

    related = "我周五有空。"
    unrelated = "我周六要加班。"
    messages = [
        _message(0, related, sender_id="member-1", sender_name="Alice"),
        _message(1, unrelated, sender_id="member-2", sender_name="Bob"),
    ]

    result = MemoryGroundingValidator().validate(
        _candidate(
            "Bob 周五有空。",
            source_refs=[_full_ref(0, related)] if explicit_reference else None,
            participants=["Bob"],
        ),
        messages,
        is_group_chat=True,
    )

    assert result.allowed is False
    assert "grounding_subject_mismatch" in result.reason_codes


def test_inferred_group_evidence_checks_same_name_ambiguity_across_window() -> None:
    """推断引用只命中一名用户时，窗口内同名成员仍让归属保持歧义隔离。"""

    first = "我周五有空。"
    second = "我周六要加班。"
    messages = [
        _message(0, first, sender_id="member-1", sender_name="小明"),
        _message(1, second, sender_id="member-2", sender_name="小明"),
    ]

    result = MemoryGroundingValidator().validate(
        _candidate("小明周五有空。", participants=["小明"]),
        messages,
        is_group_chat=True,
    )

    assert result.allowed is False
    assert "grounding_subject_ambiguous" in result.reason_codes
    assert [item["message_index"] for item in result.evidence] == [0]


def test_explicit_group_references_require_referenced_members_in_participants() -> None:
    """显式引用跨主体时，participants 未覆盖被引用主体仍按不匹配隔离。"""

    first = "我周五有空。"
    second = "我周六要加班。"
    messages = [
        _message(0, first, sender_id="member-1", sender_name="Alice"),
        _message(1, second, sender_id="member-2", sender_name="Bob"),
    ]

    result = MemoryGroundingValidator().validate(
        _candidate(
            "Alice 周五有空。",
            source_refs=[_full_ref(0, first), _full_ref(1, second)],
            participants=["Alice"],
        ),
        messages,
        is_group_chat=True,
    )

    assert result.allowed is False
    assert "grounding_subject_mismatch" in result.reason_codes


@pytest.mark.parametrize(
    "text",
    [
        "有一起去看电影的吗？",
        "我认为一起做更好。",
        "但是一起走更好。",
        "我最近一起去爬山了。",
        "没有一起去的。",
    ],
)
def test_prefixed_cjk_words_are_not_quantities(text: str) -> None:
    """「是/为/有/近」等前缀后的词中数字字符不得被当成数量。"""

    assert canonical_numbers(text) == set()

    source = "我准备出门。"
    result = MemoryGroundingValidator().validate(
        _candidate(text, source_refs=[_full_ref(0, source)]),
        [_message(0, source)],
        is_group_chat=False,
    )

    assert "grounding_numeric_conflict" not in result.reason_codes


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("二十", {"20"}),
        ("二十。", {"20"}),
        ("一千二百五", {"1250"}),
        ("两千三", {"2300"}),
        ("一百一", {"110"}),
        ("二十来个", {"20"}),
        ("三点二五", {"3.25"}),
        ("三点零五", {"3.05"}),
        ("零点七五", {"0.75"}),
        ("一百多万", {"100"}),
        ("一万多", {"10000"}),
        ("万一", set()),
        ("一来", set()),
    ],
)
def test_cjk_quantities_keep_expected_canonical_values(
    text: str, expected: set[str]
) -> None:
    """独立数量、口语省略、近似尾缀与小数位都归一为准确数值。"""

    assert canonical_numbers(text) == expected


@pytest.mark.parametrize(
    ("source", "claim"),
    [
        ("我量的是零点七五米。", "用户量的是0.75米。"),
        ("价格是两千三。", "价格是2300元。"),
        ("我买的是三点二五米的线。", "用户买的是3.25米的线。"),
        ("我存款是一百多万。", "用户存款是100多万。"),
    ],
)
def test_equivalent_cjk_and_arabic_quantities_do_not_conflict(
    source: str, claim: str
) -> None:
    """同一数量的中文写法与阿拉伯写法不再误报数值冲突。"""

    result = MemoryGroundingValidator().validate(
        _candidate(claim, source_refs=[_full_ref(0, source)]),
        [_message(0, source)],
        is_group_chat=False,
    )

    assert "grounding_numeric_conflict" not in result.reason_codes


@pytest.mark.parametrize(
    ("source", "claim", "claim_number"),
    [
        ("价格是两千三。", "价格是两千五。", "2500"),
        ("我量的是零点七五米。", "用户量的是零点八五米。", "0.85"),
        ("我存款是一百多万。", "用户存款是两百多万。", "200"),
    ],
)
def test_same_context_cjk_quantity_changes_still_conflict(
    source: str, claim: str, claim_number: str
) -> None:
    """同一语境下的中文数量差异仍须隔离，不受语境放宽影响。"""

    assert claim_number in canonical_numbers(claim)

    result = MemoryGroundingValidator().validate(
        _candidate(claim, source_refs=[_full_ref(0, source)]),
        [_message(0, source)],
        is_group_chat=False,
    )

    assert result.allowed is False
    assert "grounding_numeric_conflict" in result.reason_codes


@pytest.mark.asyncio
async def test_custom_template_keeps_appended_extraction_constraints() -> None:
    """自定义模板可被使用，但最终抽取约束仍在其后追加，不能被静默覆盖。"""

    provider = MagicMock()
    provider.text_chat = AsyncMock(
        return_value=MagicMock(completion_text='{"memories": []}')
    )
    processor = MemoryProcessor(
        llm_provider=provider,
        config={
            "group_chat_template": "自定义群聊模板 {conversation}",
            "private_chat_template": "自定义私聊模板 {conversation}",
        },
    )

    await processor.process_conversation([_message(0, "我喜欢喝咖啡。")])

    prompt = provider.text_chat.await_args.kwargs["prompt"]
    assert prompt.index("自定义私聊模板") < prompt.index("可写字段边界（必须遵守）")
    assert "source_refs" in prompt
    assert "[S0 chars=" in prompt
