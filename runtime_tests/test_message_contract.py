"""真实 AstrBot 消息、Provider 与 Memora 持久化黑盒契约。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from runtime_tests.harness import AstrBotScenario

if TYPE_CHECKING:
    from runtime_tests.harness.openai_stub import OpenAIContractStub

CONTRACT_MESSAGE = "请记住精确测试标识 MEMORA_CONTRACT_CANARY。"
CONTRACT_REPLY = "MEMORA_CONTRACT_REPLY"
CONTRACT_MEMORY = "MEMORA_CONTRACT_MEMORY"


def test_message_crosses_real_openai_adapter_and_memora_hooks(
    astrbot_contract_scenario: tuple[AstrBotScenario, OpenAIContractStub],
) -> None:
    """验证 HTTP 注入、真实 AstrBot adapter、回复与记忆落库完整链路。"""
    scenario, stub = astrbot_contract_scenario
    client = scenario.client

    assert client.wait_for_driver_ready()["memora_loaded"] is True
    assert client.wait_for_memora_ready()["provider"]["is_initialized"] is True
    assert (
        client.submit_group_message(
            CONTRACT_MESSAGE,
            include_test_token=False,
        ).status_code
        == 403
    )

    accepted = client.submit_group_message(CONTRACT_MESSAGE)
    assert accepted.status_code == 202
    submission = accepted.json()
    result = client.wait_for_message_result(submission["message_id"])
    assert result["replies"] == [CONTRACT_REPLY]

    memories = client.wait_for_memory(
        session_id=submission["session_id"],
        keyword=CONTRACT_MEMORY,
    )
    assert memories["total"] == 1
    memory_content = memories["items"][0]["content"]
    assert CONTRACT_MEMORY in memory_content
    assert "MEMORA_CONTRACT_CANARY" in memory_content
    assert memories["items"][0]["metadata"]["session_id"] == submission["session_id"]

    observations = stub.observations
    assert [item.purpose for item in observations] == ["chat", "memory"]
    assert all(item.authorization_valid for item in observations)
    assert all(item.model == stub.model for item in observations)
    assert all(item.contains_canary for item in observations)

    scenario.stop()
    scenario.assert_resources_released()
