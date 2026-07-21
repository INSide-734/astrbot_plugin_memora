"""验证前瞻 Atom 查询把可信聊天作用域转发到 Store。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.handlers.recall_handler import RecallHandler


@pytest.mark.asyncio
async def test_prospective_recall_forwards_chat_type_to_atom_store() -> None:
    """前瞻读取必须把当前 chat_type 交给 Store 执行隐私过滤。"""

    config = MagicMock()
    config.get.side_effect = lambda key, default=None: {
        "recall_engine.prospective_recall_enabled": True,
        "recall_engine.prospective_lookahead_hours": 24.0,
        "recall_engine.prospective_recall_k": 3,
    }.get(key, default)
    atom_store = MagicMock()
    atom_store.query_upcoming_planned = AsyncMock(return_value=[])
    engine = MagicMock(atom_store=atom_store)
    handler = RecallHandler(
        context=MagicMock(),
        config_manager=config,
        memory_engine=engine,
        conversation_manager=MagicMock(),
        injection_adapter=MagicMock(),
        enforce_limit_cb=MagicMock(),
    )

    result = await handler._maybe_prospective_recall(
        session_id="session-a",
        persona_id="persona-a",
        chat_type="group",
    )

    assert result == []
    atom_store.query_upcoming_planned.assert_awaited_once_with(
        lookahead_sec=24.0 * 3600.0,
        session_id="session-a",
        persona_id="persona-a",
        chat_type="group",
        limit=3,
    )
