"""验证黑话发现开关的默认值与禁用边界。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.base.config_validator import JargonConfig, MemoraConfig
from core.platform.transport.page_api.jargon_api import JargonApiMixin


def test_jargon_discovery_defaults_to_disabled_in_schema_and_model() -> None:
    """Schema 与 Pydantic 模型必须共同声明黑话发现默认关闭。"""
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "_conf_schema.json").read_text(
            encoding="utf-8"
        )
    )

    assert schema["jargon"]["items"]["enabled"]["default"] is False
    assert JargonConfig().enabled is False
    assert MemoraConfig().jargon.enabled is False
    assert MemoraConfig(jargon={"enabled": True}).jargon.enabled is True


@pytest.mark.asyncio
async def test_disabled_jargon_discovery_does_not_create_miner() -> None:
    """关闭发现功能时，页面 API 不得绕过初始化器惰性创建 Miner。"""
    config_manager = SimpleNamespace(get=MagicMock(return_value=False))
    api = JargonApiMixin()
    api.plugin = SimpleNamespace(config_manager=config_manager)
    api._get_jargon_filter = MagicMock(
        side_effect=AssertionError("禁用时不应解析黑话过滤器")
    )

    assert await api._get_jargon_miner() is None
    config_manager.get.assert_called_once_with("jargon.enabled", False)
    api._get_jargon_filter.assert_not_called()
