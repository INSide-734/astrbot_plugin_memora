"""显式启用的真实第三方 OpenAI-compatible Provider 黑盒门禁。"""

from __future__ import annotations

import uuid

from runtime_tests.harness import AstrBotScenario


def test_live_provider_message_and_memory_round_trip(
    astrbot_live_scenario: AstrBotScenario,
) -> None:
    """经真实第三方 Provider 完成回复与 Memora 记忆落库。"""
    marker = f"MEMORA_LIVE_CANARY_{uuid.uuid4().hex}"
    message = f"请记住精确测试标识 {marker}，并在回复中原样包含该标识。"
    client = astrbot_live_scenario.client

    assert client.wait_for_driver_ready()["memora_loaded"] is True
    assert client.wait_for_memora_ready()["provider"]["is_initialized"] is True
    accepted = client.submit_group_message(message)
    if accepted.status_code != 202:
        raise AssertionError("真实 Provider 场景未接受测试消息")
    submission = accepted.json()
    result = client.wait_for_message_result(submission["message_id"], timeout=90.0)
    replies = result.get("replies")
    if not isinstance(replies, list) or not replies:
        raise AssertionError("真实 Provider 场景未返回文本回复")
    if not any(marker in str(reply) for reply in replies):
        raise AssertionError("真实 Provider 回复未保留测试标识")

    memories = client.wait_for_memory(
        session_id=submission["session_id"],
        keyword=marker,
        timeout=120.0,
    )
    if int(memories.get("total", 0)) < 1:
        raise AssertionError("Memora Page API 未找到 live 测试记忆")

    astrbot_live_scenario.stop()
    astrbot_live_scenario.assert_resources_released()
