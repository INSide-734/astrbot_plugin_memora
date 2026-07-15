"""Tests for deterministic adaptive memory-injection routing."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from math import inf, nan

import pytest

from core.injection.models import (
    DeliveryMode,
    PresetName,
    RequestSignals,
    RoutingMode,
)
from core.injection.presets import PRESETS
from core.injection.router import InjectionRoutingConfig, InjectionStrategyRouter


def make_signals(**overrides: object) -> RequestSignals:
    values: dict[str, object] = {
        "query_intent": "default",
        "explicit_history_request": False,
        "tools_supported": False,
        "memory_tool_available": False,
        "context_headroom_chars": 10_000,
        "candidate_count": 0,
        "top_confidence": 0.0,
        "score_gap": 0.0,
        "candidate_redundancy": 0.0,
        "estimated_payload_chars": 0,
    }
    values.update(overrides)
    return RequestSignals(**values)  # type: ignore[arg-type]


def test_routing_config_is_frozen_with_locked_defaults() -> None:
    config = InjectionRoutingConfig()

    assert config == InjectionRoutingConfig(
        mode=RoutingMode.MANUAL,
        manual_preset=PresetName.BALANCED,
        auto_fallback=PresetName.BALANCED,
        hybrid_base=PresetName.BALANCED,
        hybrid_min=PresetName.LOW_COST,
        hybrid_max=PresetName.QUALITY,
        delivery_override=DeliveryMode.AUTO,
        preset_overrides_enabled=False,
        budget_chars=0,
        memory_max_chars=0,
        metadata_max_chars=0,
        include_key_facts=True,
        include_topics=True,
        include_participants=False,
        compact_header=True,
        invalid_config_fallback=False,
    )
    assert not hasattr(config, "__dict__")
    with pytest.raises(FrozenInstanceError):
        config.mode = RoutingMode.AUTO  # type: ignore[misc]


@pytest.mark.parametrize(
    ("tools_supported", "memory_tool_available", "expected_preset", "skip"),
    [
        (False, False, PresetName.LOW_COST, False),
        (False, True, PresetName.LOW_COST, False),
        (True, False, PresetName.LOW_COST, False),
        (True, True, PresetName.TOOL_FIRST, True),
    ],
)
def test_manual_tool_first_preflight_requires_both_tool_flags(
    tools_supported: bool,
    memory_tool_available: bool,
    expected_preset: PresetName,
    skip: bool,
) -> None:
    decision = InjectionStrategyRouter().route_preflight(
        InjectionRoutingConfig(
            mode=RoutingMode.MANUAL,
            manual_preset=PresetName.TOOL_FIRST,
        ),
        make_signals(
            tools_supported=tools_supported,
            memory_tool_available=memory_tool_available,
        ),
    )

    assert decision.configured_preset is PresetName.TOOL_FIRST
    assert decision.recommended_preset is expected_preset
    assert decision.resolved_preset is expected_preset
    assert decision.skip_passive_recall is skip
    expected_reason = "MANUAL_SELECTED" if skip else "PROVIDER_TOOL_UNAVAILABLE"
    assert decision.reason_codes == (expected_reason,)


def test_manual_final_never_moves_for_candidate_or_headroom_signals() -> None:
    config = InjectionRoutingConfig(
        mode=RoutingMode.MANUAL,
        manual_preset=PresetName.QUALITY,
    )
    router = InjectionStrategyRouter()

    low = router.route_final(
        config,
        make_signals(context_headroom_chars=0, candidate_count=0),
    )
    high = router.route_final(
        config,
        make_signals(
            context_headroom_chars=100_000,
            candidate_count=20,
            top_confidence=1.0,
        ),
    )

    assert low.resolved_preset is PresetName.QUALITY
    assert high.resolved_preset is PresetName.QUALITY
    assert low.reason_codes == high.reason_codes == ("MANUAL_SELECTED",)


@pytest.mark.parametrize(
    ("signals", "expected_preset", "expected_reason"),
    [
        (
            make_signals(
                query_intent="temporal",
                explicit_history_request=True,
                context_headroom_chars=2_400,
            ),
            PresetName.QUALITY,
            "AUTO_HISTORY_INTENT",
        ),
        (
            make_signals(
                query_intent="temporal",
                explicit_history_request=True,
                tools_supported=True,
                memory_tool_available=True,
                context_headroom_chars=2_400,
            ),
            PresetName.QUALITY,
            "AUTO_HISTORY_INTENT",
        ),
        (
            make_signals(context_headroom_chars=1_199),
            PresetName.LOW_COST,
            "AUTO_LOW_CONTEXT_HEADROOM",
        ),
        (
            make_signals(
                tools_supported=True,
                memory_tool_available=True,
                context_headroom_chars=1_199,
            ),
            PresetName.LOW_COST,
            "AUTO_LOW_CONTEXT_HEADROOM",
        ),
        (
            make_signals(
                candidate_count=1,
                top_confidence=PRESETS[PresetName.BALANCED].minimum_utility,
            ),
            PresetName.BALANCED,
            "AUTO_FALLBACK",
        ),
        (
            make_signals(
                tools_supported=True,
                memory_tool_available=True,
                candidate_count=3,
                top_confidence=0.9,
            ),
            PresetName.TOOL_FIRST,
            "AUTO_MEMORY_UNCERTAIN",
        ),
        (
            make_signals(),
            PresetName.LOW_COST,
            "AUTO_FALLBACK",
        ),
    ],
)
def test_auto_routing_precedence(
    signals: RequestSignals,
    expected_preset: PresetName,
    expected_reason: str,
) -> None:
    decision = InjectionStrategyRouter().route_final(
        InjectionRoutingConfig(mode=RoutingMode.AUTO),
        signals,
    )

    assert decision.resolved_preset is expected_preset
    assert decision.reason_codes == (expected_reason,)
    if decision.resolved_preset is PresetName.TOOL_FIRST:
        assert decision.skip_passive_recall is True


def test_explicit_zero_headroom_is_not_replaced_by_default() -> None:
    decision = InjectionStrategyRouter().route_final(
        InjectionRoutingConfig(mode=RoutingMode.AUTO),
        make_signals(context_headroom_chars=0),
    )

    assert decision.resolved_preset is PresetName.LOW_COST
    assert decision.reason_codes == ("AUTO_LOW_CONTEXT_HEADROOM",)


def test_auto_useful_candidates_require_all_three_conditions() -> None:
    router = InjectionStrategyRouter()
    config = InjectionRoutingConfig(mode=RoutingMode.AUTO)
    threshold = PRESETS[PresetName.BALANCED].minimum_utility

    useful = router.route_final(
        config,
        make_signals(candidate_count=1, top_confidence=threshold),
    )
    below_threshold = router.route_final(
        config,
        make_signals(candidate_count=1, top_confidence=threshold - 0.01),
    )
    zero_candidates = router.route_final(
        config,
        make_signals(candidate_count=0, top_confidence=threshold),
    )
    usable_tool = router.route_final(
        config,
        make_signals(
            tools_supported=True,
            memory_tool_available=True,
            candidate_count=1,
            top_confidence=threshold,
        ),
    )

    assert useful.resolved_preset is PresetName.BALANCED
    assert below_threshold.resolved_preset is PresetName.LOW_COST
    assert zero_candidates.resolved_preset is PresetName.LOW_COST
    assert usable_tool.resolved_preset is PresetName.TOOL_FIRST
    assert usable_tool.skip_passive_recall is True
    assert usable_tool.reason_codes == ("AUTO_MEMORY_UNCERTAIN",)


@pytest.mark.parametrize("mode", [RoutingMode.AUTO, RoutingMode.HYBRID])
def test_auto_and_hybrid_preflight_never_skip_or_emit_manual(mode: RoutingMode) -> None:
    decision = InjectionStrategyRouter().route_preflight(
        InjectionRoutingConfig(mode=mode),
        make_signals(tools_supported=True, memory_tool_available=True),
    )

    assert decision.skip_passive_recall is False
    assert decision.reason_codes == ()


@pytest.mark.parametrize(
    "config",
    [
        InjectionRoutingConfig(
            mode=RoutingMode.AUTO,
            auto_fallback=PresetName.TOOL_FIRST,
        ),
        InjectionRoutingConfig(
            mode=RoutingMode.HYBRID,
            hybrid_base=PresetName.TOOL_FIRST,
            hybrid_min=PresetName.TOOL_FIRST,
            hybrid_max=PresetName.QUALITY,
        ),
    ],
)
@pytest.mark.parametrize(
    ("tools_supported", "memory_tool_available", "expected_preset", "skip"),
    [
        (False, False, PresetName.LOW_COST, False),
        (False, True, PresetName.LOW_COST, False),
        (True, False, PresetName.LOW_COST, False),
        (True, True, PresetName.TOOL_FIRST, True),
    ],
)
def test_auto_and_hybrid_tool_first_preflight_requires_both_tool_flags(
    config: InjectionRoutingConfig,
    tools_supported: bool,
    memory_tool_available: bool,
    expected_preset: PresetName,
    skip: bool,
) -> None:
    decision = InjectionStrategyRouter().route_preflight(
        config,
        make_signals(
            tools_supported=tools_supported,
            memory_tool_available=memory_tool_available,
        ),
    )

    assert decision.configured_preset is PresetName.TOOL_FIRST
    assert decision.recommended_preset is expected_preset
    assert decision.resolved_preset is expected_preset
    assert decision.skip_passive_recall is skip
    expected_reasons = () if skip else ("PROVIDER_TOOL_UNAVAILABLE",)
    assert decision.reason_codes == expected_reasons


@pytest.mark.parametrize(
    "config",
    [
        InjectionRoutingConfig(
            mode=RoutingMode.AUTO,
            auto_fallback=PresetName.TOOL_FIRST,
        ),
        InjectionRoutingConfig(
            mode=RoutingMode.HYBRID,
            hybrid_base=PresetName.TOOL_FIRST,
            hybrid_min=PresetName.TOOL_FIRST,
            hybrid_max=PresetName.QUALITY,
        ),
    ],
)
@pytest.mark.parametrize(
    ("signals", "expected_preset", "expected_reason"),
    [
        (
            make_signals(
                explicit_history_request=True,
                context_headroom_chars=PRESETS[PresetName.QUALITY].memory_budget_chars,
                tools_supported=True,
                memory_tool_available=True,
            ),
            PresetName.QUALITY,
            "AUTO_HISTORY_INTENT",
        ),
        (
            make_signals(
                context_headroom_chars=PRESETS[PresetName.BALANCED].memory_budget_chars - 1,
                tools_supported=True,
                memory_tool_available=True,
            ),
            PresetName.LOW_COST,
            "AUTO_LOW_CONTEXT_HEADROOM",
        ),
    ],
)
def test_tool_first_preflight_preserves_higher_auto_precedence(
    config: InjectionRoutingConfig,
    signals: RequestSignals,
    expected_preset: PresetName,
    expected_reason: str,
) -> None:
    decision = InjectionStrategyRouter().route_preflight(config, signals)

    assert decision.configured_preset is PresetName.TOOL_FIRST
    assert decision.recommended_preset is expected_preset
    assert decision.resolved_preset is expected_preset
    assert decision.skip_passive_recall is False
    assert decision.reason_codes == (expected_reason,)


@pytest.mark.parametrize(
    "overrides",
    [
        {"query_intent": ""},
        {"query_intent": 7},
        {"top_confidence": True},
        {"context_headroom_chars": True},
        {"candidate_count": True},
        {"estimated_payload_chars": True},
        {"candidate_count": -1},
        {"context_headroom_chars": -1},
        {"estimated_payload_chars": -1},
        {"top_confidence": nan},
        {"top_confidence": inf},
        {"top_confidence": -0.01},
        {"top_confidence": 1.01},
        {"score_gap": nan},
        {"score_gap": -0.01},
        {"score_gap": 1.01},
        {"candidate_redundancy": inf},
        {"candidate_redundancy": -0.01},
        {"candidate_redundancy": 1.01},
    ],
)
def test_invalid_signals_resolve_to_configured_auto_fallback(
    overrides: dict[str, object],
) -> None:
    decision = InjectionStrategyRouter().route_final(
        InjectionRoutingConfig(
            mode=RoutingMode.AUTO,
            auto_fallback=PresetName.QUALITY,
        ),
        make_signals(**overrides),
    )

    assert decision.configured_preset is PresetName.QUALITY
    assert decision.recommended_preset is PresetName.QUALITY
    assert decision.resolved_preset is PresetName.QUALITY
    assert decision.reason_codes == ("AUTO_FALLBACK",)


def test_invalid_auto_tool_first_fallback_downgrades_without_usable_tool() -> None:
    decision = InjectionStrategyRouter().route_final(
        InjectionRoutingConfig(
            mode=RoutingMode.AUTO,
            auto_fallback=PresetName.TOOL_FIRST,
        ),
        make_signals(query_intent=""),
    )

    assert decision.configured_preset is PresetName.TOOL_FIRST
    assert decision.recommended_preset is PresetName.TOOL_FIRST
    assert decision.resolved_preset is PresetName.LOW_COST
    assert decision.reason_codes == (
        "AUTO_FALLBACK",
        "PROVIDER_TOOL_UNAVAILABLE",
    )


def test_hybrid_tool_first_clamp_downgrades_without_usable_tool() -> None:
    decision = InjectionStrategyRouter().route_final(
        InjectionRoutingConfig(
            mode=RoutingMode.HYBRID,
            hybrid_min=PresetName.TOOL_FIRST,
            hybrid_max=PresetName.TOOL_FIRST,
        ),
        make_signals(),
    )

    assert decision.recommended_preset is PresetName.LOW_COST
    assert decision.resolved_preset is PresetName.LOW_COST
    assert decision.reason_codes == (
        "AUTO_FALLBACK",
        "HYBRID_CLAMPED_MAX",
        "PROVIDER_TOOL_UNAVAILABLE",
    )


def test_huge_confidence_is_invalid_without_raising() -> None:
    decision = InjectionStrategyRouter().route_final(
        InjectionRoutingConfig(
            mode=RoutingMode.AUTO,
            auto_fallback=PresetName.QUALITY,
        ),
        make_signals(top_confidence=10**1000),
    )

    assert decision.configured_preset is PresetName.QUALITY
    assert decision.recommended_preset is PresetName.QUALITY
    assert decision.resolved_preset is PresetName.QUALITY
    assert decision.reason_codes == ("AUTO_FALLBACK",)


def test_hybrid_clamps_quality_to_max_by_preset_rank() -> None:
    decision = InjectionStrategyRouter().route_final(
        InjectionRoutingConfig(
            mode=RoutingMode.HYBRID,
            hybrid_base=PresetName.BALANCED,
            hybrid_min=PresetName.LOW_COST,
            hybrid_max=PresetName.BALANCED,
        ),
        make_signals(
            query_intent="temporal",
            explicit_history_request=True,
            context_headroom_chars=2_400,
        ),
    )

    assert decision.configured_preset is PresetName.BALANCED
    assert decision.recommended_preset is PresetName.QUALITY
    assert decision.resolved_preset is PresetName.BALANCED
    assert decision.reason_codes == (
        "AUTO_HISTORY_INTENT",
        "HYBRID_CLAMPED_MAX",
    )


def test_hybrid_clamps_low_cost_to_min_by_preset_rank() -> None:
    decision = InjectionStrategyRouter().route_final(
        InjectionRoutingConfig(
            mode=RoutingMode.HYBRID,
            hybrid_base=PresetName.BALANCED,
            hybrid_min=PresetName.BALANCED,
            hybrid_max=PresetName.QUALITY,
        ),
        make_signals(context_headroom_chars=1_199),
    )

    assert decision.recommended_preset is PresetName.LOW_COST
    assert decision.resolved_preset is PresetName.BALANCED
    assert decision.reason_codes == (
        "AUTO_LOW_CONTEXT_HEADROOM",
        "HYBRID_CLAMPED_MIN",
    )


def test_final_tool_first_downgrades_when_tool_is_unusable() -> None:
    decision = InjectionStrategyRouter().route_final(
        InjectionRoutingConfig(
            mode=RoutingMode.MANUAL,
            manual_preset=PresetName.TOOL_FIRST,
        ),
        make_signals(),
    )

    assert decision.recommended_preset is PresetName.TOOL_FIRST
    assert decision.resolved_preset is PresetName.LOW_COST
    assert decision.reason_codes == (
        "MANUAL_SELECTED",
        "PROVIDER_TOOL_UNAVAILABLE",
    )


def test_delivery_override_and_resolved_preset_overrides_are_copied() -> None:
    before = PRESETS[PresetName.BALANCED]
    decision = InjectionStrategyRouter().route_final(
        InjectionRoutingConfig(
            mode=RoutingMode.MANUAL,
            manual_preset=PresetName.BALANCED,
            delivery_override=DeliveryMode.USER_MESSAGE_AFTER,
            preset_overrides_enabled=True,
            budget_chars=2_000,
            memory_max_chars=900,
            metadata_max_chars=400,
            include_key_facts=False,
            include_topics=False,
            include_participants=True,
            compact_header=False,
        ),
        make_signals(),
    )

    assert decision.preferred_delivery is DeliveryMode.EXTRA_USER_CONTENT
    assert decision.resolved_delivery is DeliveryMode.USER_MESSAGE_AFTER
    assert decision.memory_budget_chars == 2_000
    assert decision.memory_max_chars == 900
    assert decision.metadata_max_chars == 400
    assert decision.include_key_facts is False
    assert decision.include_topics is False
    assert decision.include_participants is True
    assert decision.compact_header is False
    assert PRESETS[PresetName.BALANCED] is before


def test_auto_delivery_resolves_before_provider_adaptation() -> None:
    decision = InjectionStrategyRouter().route_final(
        InjectionRoutingConfig(
            mode=RoutingMode.MANUAL,
            delivery_override=DeliveryMode.AUTO,
        ),
        make_signals(),
    )

    assert decision.preferred_delivery is DeliveryMode.EXTRA_USER_CONTENT
    assert decision.resolved_delivery is DeliveryMode.EXTRA_USER_CONTENT


def test_invalid_config_reason_is_stably_deduplicated() -> None:
    config = InjectionRoutingConfig(
        mode=RoutingMode.MANUAL,
        invalid_config_fallback=True,
    )
    decision = InjectionStrategyRouter().route_final(config, make_signals())

    assert decision.reason_codes == (
        "MANUAL_SELECTED",
        "INVALID_CONFIG_FALLBACK",
    )
    assert decision.reason_codes.count("INVALID_CONFIG_FALLBACK") == 1


def test_one_hundred_identical_calls_return_equal_frozen_decisions() -> None:
    router = InjectionStrategyRouter()
    config = InjectionRoutingConfig(mode=RoutingMode.AUTO)
    signals = make_signals(
        tools_supported=True,
        memory_tool_available=True,
        candidate_count=3,
        top_confidence=0.9,
    )

    decisions = [router.route_final(config, signals) for _ in range(100)]

    assert all(decision == decisions[0] for decision in decisions)
    assert all(decision.skip_passive_recall is True for decision in decisions)
    with pytest.raises(FrozenInstanceError):
        decisions[0].resolved_preset = PresetName.QUALITY  # type: ignore[misc]
