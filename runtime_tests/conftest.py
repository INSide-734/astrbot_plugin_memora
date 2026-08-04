"""真实 AstrBot 运行时测试的隔离 fixtures。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from runtime_tests.harness import AstrBotScenario, AstrBotTemplate


@pytest.fixture(scope="session")
def astrbot_template(tmp_path_factory: pytest.TempPathFactory) -> AstrBotTemplate:
    """为当前 Pytest 会话构建不含秘密的 AstrBot 基础模板。"""
    return AstrBotTemplate.build(tmp_path_factory.mktemp("astrbot-template"))


@pytest.fixture
def astrbot_scenario(
    astrbot_template: AstrBotTemplate,
    tmp_path: Path,
) -> Iterator[AstrBotScenario]:
    """为单个测试准备并启动隔离场景，结束时幂等释放资源。"""
    scenario = AstrBotScenario.prepare(astrbot_template, tmp_path)
    scenario.start()
    try:
        yield scenario
    finally:
        scenario.close()
