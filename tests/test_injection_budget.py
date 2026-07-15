"""Behavioral contract tests for hard memory-injection budgets."""

from core.utils.injection_budget import (
    InjectionBudget,
    select_memories_with_budget,
)


def _memory(content: str, score: float = 1.0) -> dict:
    return {"content": content, "score": score, "metadata": {}}


def test_zero_budget_selects_nothing() -> None:
    memories = [_memory("highest", 1.0), _memory("lower", 0.5)]

    selected, dropped = select_memories_with_budget(
        memories, InjectionBudget(total_chars=0)
    )

    assert selected == []
    assert dropped == memories


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
