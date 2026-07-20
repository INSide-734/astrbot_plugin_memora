"""验证 P0 proposal-only、候选复核门和 Agent canonical 写边界。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.models.memory_evolution import (
    DerivedApplyPlan,
    DerivedState,
    RelationType,
    RelationView,
)
from core.storage.memory_evolution_store import MemoryEvolutionStore
from core.tools import __all__ as exported_tools
from core.tools.memory_memorize_tool import MemoryMemorizeTool


def test_agent_canonical_tools_expose_add_only_contract() -> None:
    """公开 memory 工具只能召回或新增，不能更新、删除已有 canonical。"""

    memory_tools = {name for name in exported_tools if name.startswith("Memory")}

    assert memory_tools == {"MemoryMemorizeTool", "MemorySearchTool"}
    properties = MemoryMemorizeTool().parameters["properties"]
    assert set(properties) == {
        "memory",
        "topics",
        "key_facts",
        "sentiment",
        "importance",
        "reason",
    }
    assert {
        "memory_id",
        "expected_revision",
        "update",
        "delete",
    }.isdisjoint(properties)


def test_agent_memorize_tool_is_disabled_by_default() -> None:
    """主动 canonical 新增工具必须由显式配置开启。"""

    schema_path = Path(__file__).parents[1] / "_conf_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["agent_tools"]["items"]["enable_memorize_tool"]["default"] is False


@pytest.mark.asyncio
async def test_memorize_call_rejects_update_delete_arguments() -> None:
    """工具调用协议不能夹带已有 memory ID 或 expected revision。"""

    tool = MemoryMemorizeTool(
        context=MagicMock(),
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
    )

    with pytest.raises(TypeError):
        await tool.call(
            MagicMock(),
            memory="尝试覆盖已有记忆",
            memory_id=17,
            expected_revision="r17",
        )


@pytest.mark.asyncio
async def test_high_impact_candidate_is_not_visible_to_active_reader(tmp_path) -> None:
    """高影响 relation 可进入候选复核门，但 active reader 不得读取。"""

    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    try:
        await store.apply_derived_plan(
            DerivedApplyPlan(
                relations=(
                    RelationView(
                        relation_id="candidate:contradiction",
                        source_memory_id=17,
                        target_memory_id=18,
                        relation_type=RelationType.CONTRADICTS,
                        confidence=0.99,
                        scope_key="private:user-a",
                        privacy_level="shared",
                        state=DerivedState.CANDIDATE,
                        source_revision="r17",
                        target_revision="r18",
                    ),
                ),
                source_revisions={17: "r17", 18: "r18"},
            )
        )

        assert await store.active_relations_for_seeds(
            [17, 18],
            scope_key="private:user-a",
        ) == []
    finally:
        await store.close()
