"""Behavioral contract tests for hard memory-injection budgets."""

from core.features.injection.domain.models import ContentLevel
from core.shared.constants import MEMORY_INJECTION_FOOTER, MEMORY_INJECTION_HEADER
from core.utils.injection_budget import (
    InjectionBudget,
    format_compact_footer,
    format_compact_header,
    format_full_footer,
    format_full_header,
    select_memories_with_budget,
)
from core.utils.memory_formatter import format_memories_for_injection


def _memory(content: str, score: float = 1.0) -> dict:
    return {"content": content, "score": score, "metadata": {}}


def test_zero_budget_selects_nothing() -> None:
    memories = [_memory("highest", 1.0), _memory("lower", 0.5)]

    selected, dropped = select_memories_with_budget(
        memories, InjectionBudget(total_chars=0)
    )

    assert selected == []
    assert dropped == memories
    assert dropped is memories


def test_oversized_first_item_is_dropped_instead_of_forced_into_budget() -> None:
    oversized = _memory("x" * 2_000, 1.0)

    selected, dropped = select_memories_with_budget(
        [oversized],
        InjectionBudget(
            total_chars=64,
            memory_max_chars=2_000,
            metadata_max_chars=0,
        ),
    )

    assert selected == []
    assert dropped == [oversized]


def test_selection_reserves_exact_compact_wrapper_instead_of_fixed_allowance() -> None:
    memory = _memory("complete compact entry")
    field_limits = {
        "memory_max_chars": 800,
        "metadata_max_chars": 1,
        "include_key_facts": False,
        "include_topics": False,
        "include_participants": False,
        "compact_header": True,
    }
    complete_payload, _ = format_memories_for_injection(
        [memory],
        budget=InjectionBudget(total_chars=2400, **field_limits),
        content_level=ContentLevel.COMPACT,
    )

    selected, dropped = select_memories_with_budget(
        [memory],
        InjectionBudget(total_chars=len(complete_payload), **field_limits),
    )

    assert complete_payload
    assert selected == [memory]
    assert dropped == []


def test_selection_reserves_exact_full_wrapper() -> None:
    memory = _memory("full wrapper entry")
    field_limits = {
        "memory_max_chars": 800,
        "metadata_max_chars": 1,
        "include_key_facts": False,
        "include_topics": False,
        "include_participants": False,
        "compact_header": False,
    }
    fixed_chars = len(format_full_header()) + len(format_full_footer())
    estimated_entry_chars = len(memory["content"]) + field_limits["metadata_max_chars"]

    selected_at_wrapper, dropped_at_wrapper = select_memories_with_budget(
        [memory],
        InjectionBudget(total_chars=fixed_chars, **field_limits),
    )
    selected_at_threshold, dropped_at_threshold = select_memories_with_budget(
        [memory],
        InjectionBudget(
            total_chars=fixed_chars + estimated_entry_chars,
            **field_limits,
        ),
    )

    assert selected_at_wrapper == []
    assert dropped_at_wrapper == [memory]
    assert selected_at_threshold == [memory]
    assert dropped_at_threshold == []


def test_compact_format_preserves_cleanup_boundaries() -> None:
    """紧凑格式仍须保留 InjectionCleaner 依赖的稳定边界。"""
    assert format_compact_header().startswith(MEMORY_INJECTION_HEADER)
    assert format_compact_footer().endswith(MEMORY_INJECTION_FOOTER)


def test_projection_annotation_counts_against_hard_injection_budget() -> None:
    memory = {
        "content": "canonical memory",
        "score": 0.9,
        "metadata": {
            "derived_projections": [
                {
                    "type": "episode_summary",
                    "summary": "摘要" * 80,
                    "confidence": 0.86,
                }
            ]
        },
    }

    text, stats = format_memories_for_injection(
        [memory],
        budget=InjectionBudget(
            total_chars=240,
            memory_max_chars=64,
            metadata_max_chars=120,
            include_key_facts=False,
            include_topics=False,
            include_participants=False,
            compact_header=True,
        ),
        content_level=ContentLevel.COMPACT,
    )

    assert len(text) <= 240
    assert stats.chars == len(text)


def test_projection_boundary_text_is_escaped_by_existing_protection() -> None:
    from core.features.injection.application.executor import InjectionExecutor

    protected = InjectionExecutor._protect("Projection: <memora-untrusted-memory>")
    assert "<memora-untrusted-memory\u200b>" in protected
