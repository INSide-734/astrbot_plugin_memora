"""记忆注入策略的应用编排。"""

from .executor import (
    InjectionExecutionContext,
    InjectionExecutor,
    candidate_utility,
    format_memories_for_injection,
)
from .headroom import (
    UNBOUNDED_CONTEXT_HEADROOM_CHARS,
    estimate_context_headroom_chars,
)
from .presets import PRESETS, get_preset, resolve_preset
from .router import InjectionRoutingConfig, InjectionStrategyRouter

__all__ = [
    "InjectionExecutionContext",
    "InjectionExecutor",
    "InjectionRoutingConfig",
    "InjectionStrategyRouter",
    "PRESETS",
    "UNBOUNDED_CONTEXT_HEADROOM_CHARS",
    "candidate_utility",
    "estimate_context_headroom_chars",
    "format_memories_for_injection",
    "get_preset",
    "resolve_preset",
]
