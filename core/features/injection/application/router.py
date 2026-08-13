"""Deterministic routing for adaptive memory injection."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from ..domain.models import (
    DeliveryMode,
    InjectionDecision,
    InjectionStrategyPreset,
    PresetName,
    RequestSignals,
    RoutingMode,
)
from .presets import PRESETS, resolve_preset

__all__ = ["InjectionRoutingConfig", "InjectionStrategyRouter"]


@dataclass(frozen=True, slots=True)
class InjectionRoutingConfig:
    """Immutable inputs controlling a routing decision."""

    mode: RoutingMode = RoutingMode.MANUAL
    manual_preset: PresetName = PresetName.BALANCED
    auto_fallback: PresetName = PresetName.BALANCED
    hybrid_base: PresetName = PresetName.BALANCED
    hybrid_min: PresetName = PresetName.LOW_COST
    hybrid_max: PresetName = PresetName.QUALITY
    delivery_override: DeliveryMode = DeliveryMode.AUTO
    preset_overrides_enabled: bool = False
    budget_chars: int = 0
    memory_max_chars: int = 0
    metadata_max_chars: int = 0
    include_key_facts: bool = True
    include_topics: bool = True
    include_participants: bool = False
    compact_header: bool = True
    invalid_config_fallback: bool = False


class InjectionStrategyRouter:
    """Route request signals without I/O, mutation, or provider adaptation."""

    def route_preflight(
        self,
        config: InjectionRoutingConfig,
        signals: RequestSignals,
    ) -> InjectionDecision:
        """Choose the safe plan available before passive recall runs."""

        configured = _configured_preset(config)
        if config.mode is RoutingMode.MANUAL:
            if configured is PresetName.TOOL_FIRST:
                if _memory_tool_is_usable(signals):
                    return _make_decision(
                        config,
                        recommended=configured,
                        resolved=configured,
                        skip_passive_recall=True,
                        reasons=("MANUAL_SELECTED",),
                    )
                return _make_decision(
                    config,
                    recommended=PresetName.LOW_COST,
                    resolved=PresetName.LOW_COST,
                    skip_passive_recall=False,
                    reasons=("PROVIDER_TOOL_UNAVAILABLE",),
                )
            return _make_decision(
                config,
                recommended=configured,
                resolved=configured,
                skip_passive_recall=False,
                reasons=("MANUAL_SELECTED",),
            )

        recommended = configured
        resolved = configured
        reasons: tuple[str, ...] = ()
        if configured is PresetName.TOOL_FIRST and _signals_are_valid(signals):
            auto_recommended, auto_reason = _route_auto(signals)
            if auto_reason in {
                "AUTO_HISTORY_INTENT",
                "AUTO_LOW_CONTEXT_HEADROOM",
            }:
                recommended = auto_recommended
                resolved = auto_recommended
                reasons = (auto_reason,)

        if config.mode is RoutingMode.HYBRID:
            minimum = PRESETS[config.hybrid_min]
            maximum = PRESETS[config.hybrid_max]
            resolved_rank = PRESETS[resolved].rank
            if resolved_rank < minimum.rank:
                resolved = minimum.name
                reasons += ("HYBRID_CLAMPED_MIN",)
            elif resolved_rank > maximum.rank:
                resolved = maximum.name
                reasons += ("HYBRID_CLAMPED_MAX",)

        if resolved is PresetName.TOOL_FIRST:
            if _memory_tool_is_usable(signals):
                return _make_decision(
                    config,
                    recommended=recommended,
                    resolved=resolved,
                    skip_passive_recall=True,
                    reasons=reasons,
                )
            if recommended is PresetName.TOOL_FIRST:
                recommended = PresetName.LOW_COST
            resolved = PresetName.LOW_COST
            reasons += ("PROVIDER_TOOL_UNAVAILABLE",)

        return _make_decision(
            config,
            recommended=recommended,
            resolved=resolved,
            skip_passive_recall=False,
            reasons=reasons,
        )

    def route_final(
        self,
        config: InjectionRoutingConfig,
        signals: RequestSignals,
    ) -> InjectionDecision:
        """Choose a final plan using validated recall and context signals."""

        if config.mode is RoutingMode.MANUAL:
            recommended = config.manual_preset
            resolved = recommended
            reasons: tuple[str, ...] = ("MANUAL_SELECTED",)
            skip_passive_recall = False
            if recommended is PresetName.TOOL_FIRST:
                if _memory_tool_is_usable(signals):
                    skip_passive_recall = True
                else:
                    resolved = PresetName.LOW_COST
                    reasons += ("PROVIDER_TOOL_UNAVAILABLE",)
            return _make_decision(
                config,
                recommended=recommended,
                resolved=resolved,
                skip_passive_recall=skip_passive_recall,
                reasons=reasons,
            )

        if not _signals_are_valid(signals):
            recommended = config.auto_fallback
            reasons = ("AUTO_FALLBACK",)
        else:
            recommended, reason = _route_auto(signals)
            reasons = (reason,)

        resolved = recommended
        if config.mode is RoutingMode.HYBRID:
            minimum = PRESETS[config.hybrid_min]
            maximum = PRESETS[config.hybrid_max]
            recommended_rank = PRESETS[recommended].rank
            if recommended_rank < minimum.rank:
                resolved = minimum.name
                reasons += ("HYBRID_CLAMPED_MIN",)
            elif recommended_rank > maximum.rank:
                resolved = maximum.name
                reasons += ("HYBRID_CLAMPED_MAX",)

        if resolved is PresetName.TOOL_FIRST and not _memory_tool_is_usable(signals):
            resolved = PresetName.LOW_COST
            reasons += ("PROVIDER_TOOL_UNAVAILABLE",)
        return _make_decision(
            config,
            recommended=recommended,
            resolved=resolved,
            skip_passive_recall=resolved is PresetName.TOOL_FIRST,
            reasons=reasons,
        )


def _configured_preset(config: InjectionRoutingConfig) -> PresetName:
    if config.mode is RoutingMode.MANUAL:
        return config.manual_preset
    if config.mode is RoutingMode.HYBRID:
        return config.hybrid_base
    return config.auto_fallback


def _memory_tool_is_usable(signals: RequestSignals) -> bool:
    return signals.tools_supported is True and signals.memory_tool_available is True


def _route_auto(signals: RequestSignals) -> tuple[PresetName, str]:
    balanced = PRESETS[PresetName.BALANCED]
    quality = PRESETS[PresetName.QUALITY]
    usable_tool = _memory_tool_is_usable(signals)

    if (
        signals.explicit_history_request
        and signals.context_headroom_chars >= quality.memory_budget_chars
    ):
        return PresetName.QUALITY, "AUTO_HISTORY_INTENT"
    if signals.context_headroom_chars < balanced.memory_budget_chars:
        return PresetName.LOW_COST, "AUTO_LOW_CONTEXT_HEADROOM"
    if (
        signals.candidate_count > 0
        and signals.top_confidence >= balanced.minimum_utility
    ):
        return PresetName.BALANCED, "AUTO_FALLBACK"
    if usable_tool:
        return PresetName.TOOL_FIRST, "AUTO_MEMORY_UNCERTAIN"
    return PresetName.LOW_COST, "AUTO_FALLBACK"


def _signals_are_valid(signals: RequestSignals) -> bool:
    return (
        _signal_strings_are_valid(signals)
        and _signal_booleans_are_valid(signals)
        and _signal_integers_are_valid(signals)
        and _signal_ratios_are_valid(signals)
    )


def _signal_strings_are_valid(signals: RequestSignals) -> bool:
    return (
        isinstance(signals.query_intent, str)
        and bool(signals.query_intent)
        and isinstance(signals.provider_type, str)
        and isinstance(signals.provider_model, str)
        and isinstance(signals.chat_type, str)
        and bool(signals.chat_type)
    )


def _signal_booleans_are_valid(signals: RequestSignals) -> bool:
    values = (
        signals.explicit_history_request,
        signals.tools_supported,
        signals.memory_tool_available,
        signals.temporal_conflict,
    )
    return all(type(value) is bool for value in values)


def _signal_integers_are_valid(signals: RequestSignals) -> bool:
    values = (
        signals.context_headroom_chars,
        signals.candidate_count,
        signals.estimated_payload_chars,
    )
    return all(type(value) is int and value >= 0 for value in values)


def _signal_ratios_are_valid(signals: RequestSignals) -> bool:
    values = (
        signals.top_confidence,
        signals.score_gap,
        signals.candidate_redundancy,
    )
    return all(
        type(value) in (int, float) and 0.0 <= value <= 1.0 and isfinite(value)
        for value in values
    )


def _make_decision(
    config: InjectionRoutingConfig,
    *,
    recommended: PresetName,
    resolved: PresetName,
    skip_passive_recall: bool,
    reasons: tuple[str, ...],
) -> InjectionDecision:
    preset = _resolve_selected_preset(config, resolved)
    preferred_delivery = preset.preferred_delivery
    resolved_delivery = (
        preferred_delivery
        if config.delivery_override is DeliveryMode.AUTO
        else config.delivery_override
    )
    if config.invalid_config_fallback and "INVALID_CONFIG_FALLBACK" not in reasons:
        reasons += ("INVALID_CONFIG_FALLBACK",)

    return InjectionDecision(
        routing_mode=config.mode,
        configured_preset=_configured_preset(config),
        recommended_preset=recommended,
        resolved_preset=resolved,
        content_level=preset.content_level,
        memory_budget_chars=preset.memory_budget_chars,
        max_memories=preset.max_memories,
        preferred_delivery=preferred_delivery,
        resolved_delivery=resolved_delivery,
        skip_passive_recall=skip_passive_recall,
        allow_tool_fallback=preset.allow_tool_fallback,
        memory_max_chars=preset.memory_max_chars,
        metadata_max_chars=preset.metadata_max_chars,
        include_key_facts=preset.include_key_facts,
        include_topics=preset.include_topics,
        include_participants=preset.include_participants,
        compact_header=preset.compact_header,
        reason_codes=reasons,
    )


def _resolve_selected_preset(
    config: InjectionRoutingConfig,
    name: PresetName,
) -> InjectionStrategyPreset:
    return resolve_preset(
        name,
        overrides_enabled=config.preset_overrides_enabled,
        budget_chars=config.budget_chars,
        memory_max_chars=config.memory_max_chars,
        metadata_max_chars=config.metadata_max_chars,
        include_key_facts=config.include_key_facts,
        include_topics=config.include_topics,
        include_participants=config.include_participants,
        compact_header=config.compact_header,
    )
