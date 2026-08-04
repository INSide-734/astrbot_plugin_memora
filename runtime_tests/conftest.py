"""真实 AstrBot 运行时测试的隔离 fixtures。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from runtime_tests.harness import AstrBotScenario, AstrBotTemplate
from runtime_tests.harness.live import LiveProviderSettings
from runtime_tests.harness.openai_stub import OpenAIContractStub

_MESSAGE_CONTRACT_CONFIG: dict[str, object] = {
    "reflection_engine": {"summary_trigger_rounds": 1},
    "topic_segmentation": {"enabled": False},
    "memory_evolution": {"enabled": False, "mode": "disabled"},
}


@pytest.fixture(scope="session")
def astrbot_template(tmp_path_factory: pytest.TempPathFactory) -> AstrBotTemplate:
    """为当前 Pytest 会话构建不含秘密的 AstrBot 基础模板。"""
    repo_root = Path(__file__).resolve().parent.parent
    return AstrBotTemplate.build(
        repo_root,
        tmp_path_factory.mktemp("astrbot-template"),
        tmp_path_factory.mktemp("astrbot-staging"),
    )


@pytest.fixture
def astrbot_scenario(
    astrbot_template: AstrBotTemplate,
    tmp_path: Path,
) -> Iterator[AstrBotScenario]:
    """为单个测试准备并启动隔离场景，结束时幂等释放资源。"""
    scenario = AstrBotScenario.prepare(astrbot_template.root, tmp_path / "scenario")
    try:
        scenario.start()
        yield scenario
    finally:
        scenario.close()


@pytest.fixture
def astrbot_contract_scenario(
    astrbot_template: AstrBotTemplate,
    tmp_path: Path,
) -> Iterator[tuple[AstrBotScenario, OpenAIContractStub]]:
    """启动使用内置 OpenAI adapter 和回环 stub 的完整消息契约场景。"""
    stub = OpenAIContractStub()
    scenario: AstrBotScenario | None = None
    stub.start()
    try:
        scenario = AstrBotScenario.prepare(
            astrbot_template.root,
            tmp_path / "contract-scenario",
            chat_provider_config=stub.provider_config(),
            memora_config=_MESSAGE_CONTRACT_CONFIG,
            sensitive_values=(stub.api_key,),
            purge_sensitive_artifacts=True,
        )
        scenario.start()
        yield scenario, stub
    finally:
        if scenario is not None:
            scenario.close()
        stub.close()


@pytest.fixture
def live_provider_settings() -> LiveProviderSettings:
    """在构建模板或启动任何进程前校验 live 环境。"""
    return LiveProviderSettings.from_process_environment()


@pytest.fixture
def astrbot_live_scenario(
    live_provider_settings: LiveProviderSettings,
    astrbot_template: AstrBotTemplate,
    tmp_path: Path,
) -> Iterator[AstrBotScenario]:
    """从显式受保护环境启动真实第三方 OpenAI-compatible 场景。"""
    scenario = AstrBotScenario.prepare(
        astrbot_template.root,
        tmp_path / "live-scenario",
        chat_provider_config=live_provider_settings.provider_config(),
        memora_config=_MESSAGE_CONTRACT_CONFIG,
        sensitive_values=(live_provider_settings.api_key,),
        purge_sensitive_artifacts=True,
    )
    try:
        scenario.start()
        yield scenario
    finally:
        scenario.close()
