"""反思批次的请求级额外 LLM 预算协作。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from ..base.cost_control import CostControl
from ..shared.extra_llm_budget import (
    budgeted_extra_llm_call,
    current_extra_llm_budget,
)

MemoryBatch = list[Any]
MemoryResult = dict[str, Any]
ProcessConversation = Callable[..., Awaitable[list[MemoryResult]]]


class ExtraLlmBudgetDenied(RuntimeError):
    """表示额外反思批次在执行前未获得请求预算。"""


def fit_batches_to_extra_llm_budget(
    batches: list[MemoryBatch],
    cost_control: CostControl,
) -> list[MemoryBatch]:
    """按剩余额度合并溢出批次，确保所有消息仍进入基础反思。"""

    if len(batches) <= 1:
        return batches
    budget = current_extra_llm_budget()
    if budget is None or not cost_control.allow("reflection_extra_batch"):
        return [[item for batch in batches for item in batch]]
    allowed_batch_count = min(len(batches), 1 + budget.snapshot().remaining)
    if allowed_batch_count >= len(batches):
        return batches
    retained = batches[: max(0, allowed_batch_count - 1)]
    merged_tail = [
        item for batch in batches[max(0, allowed_batch_count - 1) :] for item in batch
    ]
    return [*retained, merged_tail]


async def process_reflection_batches(
    batches: list[MemoryBatch],
    *,
    process_conversation: ProcessConversation,
    cost_control: CostControl,
    is_group_chat: bool,
    persona_id: str | None,
) -> list[list[MemoryResult] | BaseException]:
    """执行基础批次和受预算约束的额外批次，并保留普通失败供上游处理。"""

    if len(batches) == 1:
        return [
            await process_conversation(
                messages=batches[0],
                is_group_chat=is_group_chat,
                persona_id=persona_id,
            )
        ]

    semaphore = asyncio.Semaphore(
        max(1, cost_control.max_reflection_parallel_llm_calls)
    )

    async def _process_batch(
        batch_index: int,
        batch: MemoryBatch,
    ) -> list[MemoryResult]:
        """执行一个基础批次或仅允许单次 Provider 请求的额外批次。"""

        if batch_index == 0:
            async with semaphore:
                return await process_conversation(
                    messages=batch,
                    is_group_chat=is_group_chat,
                    persona_id=persona_id,
                )
        async with budgeted_extra_llm_call(
            cost_control,
            "reflection_extra_batch",
        ) as allowed:
            if not allowed:
                raise ExtraLlmBudgetDenied("extra_llm_budget_exhausted")
            async with semaphore:
                return await process_conversation(
                    messages=batch,
                    is_group_chat=is_group_chat,
                    persona_id=persona_id,
                    llm_max_retries=1,
                )

    results = await asyncio.gather(
        *[_process_batch(index, batch) for index, batch in enumerate(batches)],
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, asyncio.CancelledError):
            raise result
    return results


__all__ = [
    "ExtraLlmBudgetDenied",
    "fit_batches_to_extra_llm_budget",
    "process_reflection_batches",
]
