"""记忆注入策略的稳定公共模型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RoutingMode(StrEnum):
    """记忆注入策略路由模式。"""

    MANUAL = "manual"
    AUTO = "auto"
    HYBRID = "hybrid"


class PresetName(StrEnum):
    """内置记忆注入预设名称。"""

    TOOL_FIRST = "tool_first"
    LOW_COST = "low_cost"
    BALANCED = "balanced"
    QUALITY = "quality"


class ContentLevel(StrEnum):
    """注入内容的详细程度。"""

    NONE = "NONE"
    FACTS = "FACTS"
    COMPACT = "COMPACT"
    DETAILED = "DETAILED"


class DeliveryMode(StrEnum):
    """记忆内容的临时投递方式。"""

    AUTO = "auto"
    EXTRA_USER_CONTENT = "extra_user_content"
    USER_MESSAGE_BEFORE = "user_message_before"
    USER_MESSAGE_AFTER = "user_message_after"
    FAKE_TOOL_CALL = "fake_tool_call"
    FAKE_TOOL_CALL_DEEPSEEK_V4 = "fake_tool_call_deepseek_v4"


class InjectionOutcome(StrEnum):
    """一次生产注入决策的最终结果。"""

    INJECTED = "injected"
    SKIPPED = "skipped"
    EMPTY = "empty"
    FALLBACK = "fallback"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class InjectionStrategyPreset:
    """一个不可变的内置注入策略预设。"""

    name: PresetName
    rank: int
    auto_inject: bool
    memory_budget_chars: int
    max_memories: int
    content_level: ContentLevel
    cost_penalty_weight: float
    minimum_utility: float
    allow_tool_fallback: bool = True
    preferred_delivery: DeliveryMode = DeliveryMode.EXTRA_USER_CONTENT
    memory_max_chars: int = 220
    metadata_max_chars: int = 180
    include_key_facts: bool = True
    include_topics: bool = True
    include_participants: bool = False
    compact_header: bool = True


@dataclass(frozen=True, slots=True)
class RequestSignals:
    """路由器评估一次请求时使用的非敏感信号。"""

    query_intent: str = "default"
    explicit_history_request: bool = False
    provider_type: str = ""
    provider_model: str = ""
    tools_supported: bool = False
    memory_tool_available: bool = False
    context_headroom_chars: int = 10_000
    candidate_count: int = 0
    top_confidence: float = 0.0
    score_gap: float = 0.0
    candidate_redundancy: float = 0.0
    temporal_conflict: bool = False
    estimated_payload_chars: int = 0
    chat_type: str = "private"


@dataclass(frozen=True, slots=True)
class InjectionDecision:
    """路由器输出的不可变注入决策。"""

    routing_mode: RoutingMode
    configured_preset: PresetName
    recommended_preset: PresetName
    resolved_preset: PresetName
    content_level: ContentLevel
    memory_budget_chars: int
    max_memories: int
    preferred_delivery: DeliveryMode
    resolved_delivery: DeliveryMode
    skip_passive_recall: bool
    allow_tool_fallback: bool
    memory_max_chars: int = 220
    metadata_max_chars: int = 180
    include_key_facts: bool = True
    include_topics: bool = True
    include_participants: bool = False
    compact_header: bool = True
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InjectionExecutionResult:
    """执行器输出的不可变统计结果。"""

    outcome: InjectionOutcome
    configured_budget_chars: int = 0
    effective_budget_chars: int = 0
    actual_payload_chars: int = 0
    selected_count: int = 0
    dropped_count: int = 0
    truncated_count: int = 0
    fallback_applied: bool = False
    actual_resolved_delivery: DeliveryMode | None = None
    error_code: str | None = None
    decision_ms: float = 0.0
    format_ms: float = 0.0
    inject_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class InjectionDecisionRecord:
    """可安全持久化的注入决策记录。"""

    decision_id: str
    created_at_ms: int
    routing_mode: str
    configured_preset: str
    recommended_preset: str
    resolved_preset: str
    preferred_delivery: str
    resolved_delivery: str
    fallback_applied: bool
    outcome: str
    primary_reason: str
    reason_codes: tuple[str, ...] = ()
    trace_id: str | None = None
    error_code: str | None = None
    provider_type: str = ""
    provider_model: str = ""
    candidate_count: int = 0
    selected_count: int = 0
    dropped_count: int = 0
    truncated_count: int = 0
    configured_budget_chars: int = 0
    effective_budget_chars: int = 0
    actual_payload_chars: int = 0
    context_headroom_chars: int = 0
    decision_ms: float = 0.0
    format_ms: float = 0.0
    inject_ms: float = 0.0
