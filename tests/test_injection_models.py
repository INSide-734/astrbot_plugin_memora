"""Tests for immutable memory-injection strategy models."""

from dataclasses import FrozenInstanceError

import pytest

from core.injection.models import (
    ContentLevel,
    DeliveryMode,
    InjectionDecision,
    InjectionDecisionRecord,
    InjectionExecutionResult,
    InjectionOutcome,
    InjectionStrategyPreset,
    PresetName,
    RequestSignals,
    RoutingMode,
)
import core.injection as injection
import core.injection.executor as executor_module
import core.injection.models as model_module
import core.injection.presets as preset_module
import core.injection.router as router_module


def test_package_exports_are_exact_and_identity_preserving() -> None:
    expected = {
        "ContentLevel",
        "DeliveryMode",
        "InjectionDecision",
        "InjectionDecisionRecord",
        "InjectionExecutionResult",
        "InjectionOutcome",
        "InjectionStrategyPreset",
        "InjectionExecutionContext",
        "InjectionExecutor",
        "InjectionRoutingConfig",
        "InjectionStrategyRouter",
        "PRESETS",
        "PresetName",
        "RequestSignals",
        "RoutingMode",
        "candidate_utility",
        "get_preset",
        "resolve_preset",
    }
    assert set(injection.__all__) == expected
    assert len(injection.__all__) == len(expected)

    model_exports = {
        "ContentLevel",
        "DeliveryMode",
        "InjectionDecision",
        "InjectionDecisionRecord",
        "InjectionExecutionResult",
        "InjectionOutcome",
        "InjectionStrategyPreset",
        "PresetName",
        "RequestSignals",
        "RoutingMode",
    }
    executor_exports = {
        "InjectionExecutionContext",
        "InjectionExecutor",
        "candidate_utility",
    }
    preset_exports = {"PRESETS", "get_preset", "resolve_preset"}
    router_exports = {"InjectionRoutingConfig", "InjectionStrategyRouter"}
    assert router_module.__all__ == [
        "InjectionRoutingConfig",
        "InjectionStrategyRouter",
    ]
    for name in model_exports:
        assert getattr(injection, name) is getattr(model_module, name)
    for name in executor_exports:
        assert getattr(injection, name) is getattr(executor_module, name)
    for name in preset_exports:
        assert getattr(injection, name) is getattr(preset_module, name)
    for name in router_exports:
        assert getattr(injection, name) is getattr(router_module, name)


def test_request_signals_are_immutable_and_slotted() -> None:
    signals = RequestSignals(query_intent="temporal", explicit_history_request=True)

    with pytest.raises(FrozenInstanceError):
        signals.query_intent = "default"  # type: ignore[misc]

    assert not hasattr(signals, "__dict__")


@pytest.mark.parametrize(
    ("model", "attribute", "replacement"),
    [
        (
            InjectionStrategyPreset(
                name=PresetName.BALANCED,
                rank=2,
                auto_inject=True,
                memory_budget_chars=1200,
                max_memories=4,
                content_level=ContentLevel.COMPACT,
                cost_penalty_weight=0.18,
                minimum_utility=0.30,
            ),
            "rank",
            3,
        ),
        (
            InjectionDecision(
                routing_mode=RoutingMode.MANUAL,
                configured_preset=PresetName.BALANCED,
                recommended_preset=PresetName.BALANCED,
                resolved_preset=PresetName.BALANCED,
                content_level=ContentLevel.COMPACT,
                memory_budget_chars=1200,
                max_memories=4,
                preferred_delivery=DeliveryMode.EXTRA_USER_CONTENT,
                resolved_delivery=DeliveryMode.EXTRA_USER_CONTENT,
                skip_passive_recall=False,
                allow_tool_fallback=True,
            ),
            "skip_passive_recall",
            True,
        ),
        (
            InjectionExecutionResult(outcome=InjectionOutcome.INJECTED),
            "outcome",
            InjectionOutcome.ERROR,
        ),
        (
            InjectionDecisionRecord(
                decision_id="decision-1",
                created_at_ms=1,
                routing_mode="manual",
                configured_preset="balanced",
                recommended_preset="balanced",
                resolved_preset="balanced",
                preferred_delivery="extra_user_content",
                resolved_delivery="extra_user_content",
                fallback_applied=False,
                outcome="injected",
                primary_reason="manual_selection",
            ),
            "outcome",
            "error",
        ),
    ],
)
def test_public_models_are_immutable_and_slotted(
    model: object,
    attribute: str,
    replacement: object,
) -> None:
    with pytest.raises(FrozenInstanceError):
        setattr(model, attribute, replacement)

    assert not hasattr(model, "__dict__")


def test_reason_codes_default_to_immutable_tuples() -> None:
    decision = InjectionDecision(
        routing_mode=RoutingMode.MANUAL,
        configured_preset=PresetName.BALANCED,
        recommended_preset=PresetName.BALANCED,
        resolved_preset=PresetName.BALANCED,
        content_level=ContentLevel.COMPACT,
        memory_budget_chars=1200,
        max_memories=4,
        preferred_delivery=DeliveryMode.EXTRA_USER_CONTENT,
        resolved_delivery=DeliveryMode.EXTRA_USER_CONTENT,
        skip_passive_recall=False,
        allow_tool_fallback=True,
    )

    assert decision.reason_codes == ()
    assert isinstance(decision.reason_codes, tuple)
