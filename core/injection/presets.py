"""内置记忆注入预设及高级覆盖解析。"""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType
from typing import Mapping

from .models import ContentLevel, InjectionStrategyPreset, PresetName

_BUDGET_CAPS: Mapping[PresetName, int] = MappingProxyType(
    {
        PresetName.LOW_COST: 1_200,
        PresetName.BALANCED: 2_400,
        PresetName.QUALITY: 10_000,
    }
)


PRESETS: Mapping[PresetName, InjectionStrategyPreset] = MappingProxyType(
    {
        PresetName.TOOL_FIRST: InjectionStrategyPreset(
            name=PresetName.TOOL_FIRST,
            rank=0,
            auto_inject=False,
            memory_budget_chars=0,
            max_memories=0,
            content_level=ContentLevel.NONE,
            cost_penalty_weight=1.0,
            minimum_utility=1.0,
            memory_max_chars=0,
            metadata_max_chars=0,
            include_key_facts=False,
            include_topics=False,
            include_participants=False,
            compact_header=True,
        ),
        PresetName.LOW_COST: InjectionStrategyPreset(
            name=PresetName.LOW_COST,
            rank=1,
            auto_inject=True,
            memory_budget_chars=800,
            max_memories=2,
            content_level=ContentLevel.FACTS,
            cost_penalty_weight=0.30,
            minimum_utility=0.45,
            memory_max_chars=180,
            metadata_max_chars=80,
            include_key_facts=True,
            include_topics=False,
            include_participants=False,
            compact_header=True,
        ),
        PresetName.BALANCED: InjectionStrategyPreset(
            name=PresetName.BALANCED,
            rank=2,
            auto_inject=True,
            memory_budget_chars=1200,
            max_memories=4,
            content_level=ContentLevel.COMPACT,
            cost_penalty_weight=0.18,
            minimum_utility=0.30,
            memory_max_chars=300,
            metadata_max_chars=180,
            include_key_facts=True,
            include_topics=True,
            include_participants=False,
            compact_header=True,
        ),
        PresetName.QUALITY: InjectionStrategyPreset(
            name=PresetName.QUALITY,
            rank=3,
            auto_inject=True,
            memory_budget_chars=2400,
            max_memories=6,
            content_level=ContentLevel.DETAILED,
            cost_penalty_weight=0.08,
            minimum_utility=0.20,
            memory_max_chars=800,
            metadata_max_chars=300,
            include_key_facts=True,
            include_topics=True,
            include_participants=True,
            compact_header=False,
        ),
    }
)


def get_preset(name: PresetName | str) -> InjectionStrategyPreset:
    """按枚举或字符串名称读取内置预设。"""

    return PRESETS[PresetName(name)]


def resolve_preset(
    name: PresetName | str,
    *,
    overrides_enabled: bool,
    budget_chars: int = 0,
    memory_max_chars: int = 0,
    metadata_max_chars: int = 0,
    include_key_facts: bool = True,
    include_topics: bool = True,
    include_participants: bool = False,
    compact_header: bool = True,
) -> InjectionStrategyPreset:
    """在硬上限内解析高级覆盖，保留内置预设不可变性。"""

    base = get_preset(name)
    if not overrides_enabled or base.name is PresetName.TOOL_FIRST:
        return base

    resolved_budget = base.memory_budget_chars if budget_chars == 0 else budget_chars
    resolved_participants = (
        False if base.content_level is ContentLevel.FACTS else include_participants
    )
    return replace(
        base,
        memory_budget_chars=min(max(1, resolved_budget), _BUDGET_CAPS[base.name]),
        memory_max_chars=(
            base.memory_max_chars
            if memory_max_chars == 0
            else min(max(1, memory_max_chars), 2_000)
        ),
        metadata_max_chars=(
            base.metadata_max_chars
            if metadata_max_chars == 0
            else min(max(1, metadata_max_chars), 500)
        ),
        include_key_facts=include_key_facts,
        include_topics=include_topics,
        include_participants=resolved_participants,
        compact_header=compact_header,
    )
