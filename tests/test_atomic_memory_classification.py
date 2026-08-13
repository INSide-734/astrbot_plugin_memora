"""原子记忆分类、否定极性与事件时间契约测试。"""

from __future__ import annotations

from datetime import datetime

import pytest

from core.features.memory.domain.memory_atom import AtomType
from core.features.recall.processors import atom_classifier
from core.features.recall.processors.atom_classifier import classify_atoms
from core.security.guardrails import MemoryAtomSchema


def _classify(text: str):
    """关闭质量过滤并返回单条文本产生的原子。"""

    atoms = classify_atoms(
        [text],
        parent_importance=0.7,
        enable_quality_filter=False,
    )
    assert len(atoms) == 1
    return atoms[0]


@pytest.mark.parametrize(
    ("text", "expected_type"),
    [
        ("我不喜欢咖啡", AtomType.PREFERENCE),
        ("我们不是同事", AtomType.RELATIONAL),
        ("I dislike noisy restaurants", AtomType.PREFERENCE),
        ("Alice is my colleague", AtomType.RELATIONAL),
    ],
)
def test_negative_and_english_signals_keep_semantic_type(
    text: str,
    expected_type: AtomType,
) -> None:
    """否定和英文表达仍应保留偏好或关系语义类型。"""

    atom = _classify(text)

    assert atom.atom_type == expected_type
    if "不" in text or "dislike" in text:
        assert atom.metadata["polarity"] == "negative"


def test_past_event_is_episodic_and_future_event_is_planned() -> None:
    """带动作的过去事件不得误判为未来计划。"""

    assert _classify("昨天去了医院").atom_type == AtomType.EPISODIC
    assert _classify("明天去医院复诊").atom_type == AtomType.PLANNED


def test_relative_date_uses_longest_phrase(monkeypatch: pytest.MonkeyPatch) -> None:
    """“大后天”必须先于“后天”匹配并解析为三天后。"""

    now = 1_735_689_600.0
    monkeypatch.setattr(atom_classifier.time, "time", lambda: now)

    atom = _classify("大后天去参加会议")

    assert atom.event_time == pytest.approx(now + 3 * 86400.0)


def test_iso_and_full_chinese_dates_are_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ISO 与中文完整日期都应解析为绝对事件时间。"""

    now = datetime(2026, 1, 1, 8, 0).timestamp()
    monkeypatch.setattr(atom_classifier.time, "time", lambda: now)

    iso_atom = _classify("计划在 2026-02-03 参加发布会")
    chinese_atom = _classify("计划在2026年2月4日参加复盘会")

    assert iso_atom.event_time is not None
    assert chinese_atom.event_time is not None
    assert (
        datetime.fromtimestamp(iso_atom.event_time).date().isoformat() == "2026-02-03"
    )
    assert (
        datetime.fromtimestamp(chinese_atom.event_time).date().isoformat()
        == "2026-02-04"
    )


def test_emotion_evidence_is_preserved_in_atom_metadata() -> None:
    """情绪标签与强度必须进入 Atom metadata 供 Store 计算 TTL。"""

    atoms = classify_atoms(
        ["用户喜欢雨天散步"],
        parent_importance=0.8,
        emotion_tags=["怀念"],
        emotional_intensity=0.92,
        enable_quality_filter=False,
    )

    assert atoms[0].metadata["emotion_tags"] == ["怀念"]
    assert atoms[0].metadata["emotional_intensity"] == pytest.approx(0.92)


def test_guardrail_atom_type_is_optional_and_accepts_domain_values() -> None:
    """护栏不得缺省强塞 fact，并应兼容领域枚举与历史类型词表。"""

    defaulted = MemoryAtomSchema(content="一条足够长的事实")
    assert defaulted.atom_type == "fact"
    assert "atom_type" not in defaulted.model_fields_set
    assert (
        MemoryAtomSchema(
            content="用户明确不喜欢浓缩咖啡",
            atom_type="preference",
        ).atom_type
        == "preference"
    )
    assert (
        MemoryAtomSchema(content="用户昨天参加会议", atom_type="episodic").atom_type
        == "episodic"
    )


@pytest.mark.parametrize(
    ("hint", "expected"),
    [
        ("fact", AtomType.FACTUAL),
        ("event", AtomType.EPISODIC),
        ("factual", AtomType.FACTUAL),
        ("relational", AtomType.RELATIONAL),
        ("planned", AtomType.PLANNED),
    ],
)
def test_explicit_structured_type_is_only_a_fallback_hint(
    hint: str,
    expected: AtomType,
) -> None:
    """显式结构类型可辅助无关键词文本，但不能取代更强的规则信号。"""

    atoms = classify_atoms(
        ["关于蓝色纸盒的完整描述信息"],
        parent_importance=0.8,
        atom_type_hint=hint,
        enable_quality_filter=False,
    )

    assert atoms[0].atom_type == expected
