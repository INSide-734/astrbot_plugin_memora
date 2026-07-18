import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from core.models.memory_evolution import MemorySourceRef
from core.processors.memory_consolidator import MemoryConsolidator


UTC = timezone.utc


def source(memory_id: int, content: str = "同一事件证据") -> MemorySourceRef:
    return MemorySourceRef(
        memory_id,
        f"r-{memory_id}",
        "private:user-a",
        "shared",
        datetime(2026, 7, 18, tzinfo=UTC),
        content,
    )


def limits() -> dict[str, int]:
    return {
        "max_input_chars": 1_200,
        "max_output_relations": 4,
        "max_output_projections": 2,
        "projection_budget_chars": 240,
    }


@pytest.mark.asyncio
async def test_consolidator_assigns_ephemeral_aliases() -> None:
    caller = AsyncMock(
        return_value=json.dumps(
            {
                "relations": [
                    {
                        "source_alias": "M1",
                        "target_alias": "M2",
                        "relation_type": "same_episode",
                        "confidence": 0.8,
                    }
                ],
                "projections": [],
            }
        )
    )
    consolidator = MemoryConsolidator(caller, limits())
    proposal = await consolidator.propose([source(17), source(18)])
    assert proposal.relations[0].source_alias == "M1"
    assert proposal.relations[0].target_alias == "M2"
    caller.assert_awaited_once()
    prompt = caller.await_args.kwargs["prompt"]
    assert "alias=M1" in prompt
    assert "真实 memory id" in prompt


@pytest.mark.asyncio
async def test_unknown_alias_is_preserved_for_manager_validation() -> None:
    caller = AsyncMock(
        return_value=json.dumps(
            {
                "relations": [
                    {
                        "source_alias": "M99",
                        "target_alias": "M1",
                        "relation_type": "related",
                        "confidence": 0.4,
                    }
                ],
                "projections": [],
            }
        )
    )
    proposal = await MemoryConsolidator(caller, limits()).propose([source(17)])
    assert proposal.relations[0].source_alias == "M99"


@pytest.mark.asyncio
async def test_output_limits_are_enforced() -> None:
    caller = AsyncMock(
        return_value=json.dumps(
            {
                "relations": [
                    {
                        "source_alias": "M1",
                        "target_alias": "M1",
                        "relation_type": "related",
                        "confidence": 0.4,
                    }
                ],
                "projections": [],
            }
        )
    )
    with pytest.raises(ValueError, match="relation proposal"):
        await MemoryConsolidator(caller, {**limits(), "max_output_relations": 0}).propose(
            [source(17)]
        )


@pytest.mark.asyncio
async def test_cancelled_provider_call_is_propagated() -> None:
    caller = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await MemoryConsolidator(caller, limits()).propose([source(17)])
