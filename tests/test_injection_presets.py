"""Tests for the built-in memory-injection strategy presets."""

from types import MappingProxyType

import pytest

from core.injection.models import ContentLevel, DeliveryMode, InjectionStrategyPreset, PresetName
from core.injection.presets import PRESETS, get_preset, resolve_preset


def test_builtin_preset_gradient_is_locked() -> None:
    assert list(PRESETS) == [
        PresetName.TOOL_FIRST,
        PresetName.LOW_COST,
        PresetName.BALANCED,
        PresetName.QUALITY,
    ]
    assert [preset.rank for preset in PRESETS.values()] == [0, 1, 2, 3]
    assert get_preset(PresetName.TOOL_FIRST).memory_budget_chars == 0
    assert get_preset(PresetName.LOW_COST).content_level is ContentLevel.FACTS
    assert get_preset(PresetName.BALANCED).memory_budget_chars == 1200
    assert get_preset(PresetName.QUALITY).max_memories == 6


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            PresetName.TOOL_FIRST,
            InjectionStrategyPreset(
                name=PresetName.TOOL_FIRST,
                rank=0,
                auto_inject=False,
                memory_budget_chars=0,
                max_memories=0,
                content_level=ContentLevel.NONE,
                cost_penalty_weight=1.0,
                minimum_utility=1.0,
                allow_tool_fallback=True,
                preferred_delivery=DeliveryMode.EXTRA_USER_CONTENT,
                memory_max_chars=0,
                metadata_max_chars=0,
                include_key_facts=False,
                include_topics=False,
                include_participants=False,
                compact_header=True,
            ),
        ),
        (
            PresetName.LOW_COST,
            InjectionStrategyPreset(
                name=PresetName.LOW_COST,
                rank=1,
                auto_inject=True,
                memory_budget_chars=800,
                max_memories=2,
                content_level=ContentLevel.FACTS,
                cost_penalty_weight=0.30,
                minimum_utility=0.45,
                allow_tool_fallback=True,
                preferred_delivery=DeliveryMode.EXTRA_USER_CONTENT,
                memory_max_chars=180,
                metadata_max_chars=80,
                include_key_facts=True,
                include_topics=False,
                include_participants=False,
                compact_header=True,
            ),
        ),
        (
            PresetName.BALANCED,
            InjectionStrategyPreset(
                name=PresetName.BALANCED,
                rank=2,
                auto_inject=True,
                memory_budget_chars=1200,
                max_memories=4,
                content_level=ContentLevel.COMPACT,
                cost_penalty_weight=0.18,
                minimum_utility=0.30,
                allow_tool_fallback=True,
                preferred_delivery=DeliveryMode.EXTRA_USER_CONTENT,
                memory_max_chars=300,
                metadata_max_chars=180,
                include_key_facts=True,
                include_topics=True,
                include_participants=False,
                compact_header=True,
            ),
        ),
        (
            PresetName.QUALITY,
            InjectionStrategyPreset(
                name=PresetName.QUALITY,
                rank=3,
                auto_inject=True,
                memory_budget_chars=2400,
                max_memories=6,
                content_level=ContentLevel.DETAILED,
                cost_penalty_weight=0.08,
                minimum_utility=0.20,
                allow_tool_fallback=True,
                preferred_delivery=DeliveryMode.EXTRA_USER_CONTENT,
                memory_max_chars=800,
                metadata_max_chars=300,
                include_key_facts=True,
                include_topics=True,
                include_participants=True,
                compact_header=False,
            ),
        ),
    ],
)
def test_builtin_presets_match_complete_dataclass_values(
    name: PresetName,
    expected: InjectionStrategyPreset,
) -> None:
    assert PRESETS[name] == expected


def test_builtin_registry_is_read_only() -> None:
    assert isinstance(PRESETS, MappingProxyType)

    with pytest.raises(TypeError):
        PRESETS[PresetName.BALANCED] = get_preset(PresetName.QUALITY)  # type: ignore[index]


def test_get_preset_accepts_enum_and_string_names() -> None:
    assert get_preset(PresetName.BALANCED) is PRESETS[PresetName.BALANCED]
    assert get_preset("balanced") is PRESETS[PresetName.BALANCED]


def test_disabled_overrides_return_the_identical_registry_object() -> None:
    preset = resolve_preset(
        PresetName.BALANCED,
        overrides_enabled=False,
        budget_chars=9999,
        memory_max_chars=9999,
        metadata_max_chars=9999,
        include_key_facts=False,
        include_topics=False,
        include_participants=True,
        compact_header=False,
    )

    assert preset is PRESETS[PresetName.BALANCED]


def test_zero_numeric_overrides_keep_builtin_values() -> None:
    base = get_preset(PresetName.BALANCED)
    resolved = resolve_preset(
        PresetName.BALANCED,
        overrides_enabled=True,
        budget_chars=0,
        memory_max_chars=0,
        metadata_max_chars=0,
    )

    assert resolved.memory_budget_chars == base.memory_budget_chars
    assert resolved.memory_max_chars == base.memory_max_chars
    assert resolved.metadata_max_chars == base.metadata_max_chars


@pytest.mark.parametrize(
    ("name", "expected_cap"),
    [
        (PresetName.LOW_COST, 1200),
        (PresetName.BALANCED, 2400),
        (PresetName.QUALITY, 10_000),
    ],
)
def test_budget_overrides_use_preset_specific_hard_caps(
    name: PresetName,
    expected_cap: int,
) -> None:
    resolved = resolve_preset(
        name,
        overrides_enabled=True,
        budget_chars=20_000,
    )

    assert resolved.memory_budget_chars == expected_cap


def test_length_overrides_are_clamped_to_global_hard_caps() -> None:
    resolved = resolve_preset(
        PresetName.QUALITY,
        overrides_enabled=True,
        memory_max_chars=3000,
        metadata_max_chars=800,
    )

    assert resolved.memory_max_chars == 2000
    assert resolved.metadata_max_chars == 500

def test_negative_numeric_overrides_clamp_to_one() -> None:
    budget_resolved = resolve_preset(
        PresetName.BALANCED,
        overrides_enabled=True,
        budget_chars=-1,
    )
    memory_resolved = resolve_preset(
        PresetName.BALANCED,
        overrides_enabled=True,
        memory_max_chars=-1,
    )
    metadata_resolved = resolve_preset(
        PresetName.BALANCED,
        overrides_enabled=True,
        metadata_max_chars=-1,
    )

    assert budget_resolved.memory_budget_chars == 1
    assert memory_resolved.memory_max_chars == 1
    assert metadata_resolved.metadata_max_chars == 1


def test_boolean_flags_apply_only_to_returned_copy() -> None:
    base = get_preset(PresetName.BALANCED)
    resolved = resolve_preset(
        PresetName.BALANCED,
        overrides_enabled=True,
        include_key_facts=False,
        include_topics=False,
        include_participants=True,
        compact_header=False,
    )

    assert resolved is not base
    assert resolved.include_key_facts is False
    assert resolved.include_topics is False
    assert resolved.include_participants is True
    assert resolved.compact_header is False
    assert get_preset(PresetName.BALANCED) is base
    assert base.include_key_facts is True
    assert base.include_topics is True
    assert base.include_participants is False
    assert base.compact_header is True


def test_facts_content_level_never_enables_participants() -> None:
    resolved = resolve_preset(
        PresetName.LOW_COST,
        overrides_enabled=True,
        include_participants=True,
    )

    assert resolved.content_level is ContentLevel.FACTS
    assert resolved.include_participants is False


def test_tool_first_always_stays_at_zero_and_returns_registry_object() -> None:
    resolved = resolve_preset(
        PresetName.TOOL_FIRST,
        overrides_enabled=True,
        budget_chars=10_000,
        memory_max_chars=2000,
        metadata_max_chars=500,
        include_key_facts=True,
        include_topics=True,
        include_participants=True,
        compact_header=False,
    )

    assert resolved is PRESETS[PresetName.TOOL_FIRST]
    assert resolved.memory_budget_chars == 0
    assert resolved.memory_max_chars == 0
    assert resolved.metadata_max_chars == 0
    assert resolved.include_key_facts is False
    assert resolved.include_topics is False
    assert resolved.include_participants is False
