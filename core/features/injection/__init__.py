"""记忆注入策略 feature 的公开边界。"""

from .application.executor import (
    InjectionExecutionContext,
    InjectionExecutor,
    candidate_utility,
)
from .application.presets import PRESETS, get_preset, resolve_preset
from .application.router import InjectionRoutingConfig, InjectionStrategyRouter
from .domain.models import (
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
from .infrastructure.recorder import InjectionDecisionRecorder

__all__ = [
    "ContentLevel",
    "DeliveryMode",
    "InjectionDecision",
    "InjectionDecisionRecord",
    "InjectionExecutionResult",
    "InjectionDecisionRecorder",
    "InjectionOutcome",
    "InjectionExecutionContext",
    "InjectionExecutor",
    "InjectionRoutingConfig",
    "InjectionStrategyRouter",
    "InjectionStrategyPreset",
    "PRESETS",
    "PresetName",
    "RequestSignals",
    "RoutingMode",
    "get_preset",
    "resolve_preset",
    "candidate_utility",
]
