"""验证 Agent 工具只依赖 AstrBot 公开执行契约。"""

from __future__ import annotations

import pytest

from core import tools as tool_exports


@pytest.mark.parametrize("tool_name", tool_exports.__all__)
def test_agent_tool_uses_public_handler_contract(tool_name: str) -> None:
    """每个工具都应使用公开 handler，不能覆盖版本敏感的内部 call。"""

    tool_factory = getattr(tool_exports, tool_name)
    tool = tool_factory()

    assert "call" not in type(tool).__dict__
    assert callable(tool.handler)
