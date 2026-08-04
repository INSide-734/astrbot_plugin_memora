"""真实 AstrBot 启动与关停契约。"""

from runtime_tests.harness import AstrBotScenario


def test_real_astrbot_bootstrap(astrbot_scenario: AstrBotScenario) -> None:
    """验证真实 AstrBot 的加载、鉴权、注册表、Memora 就绪和干净关停。"""
    assert astrbot_scenario.client.driver_status(authenticated=False).status_code == 401
    astrbot_scenario.client.login()
    assert (
        astrbot_scenario.client.driver_status(include_test_token=False).status_code
        == 403
    )

    driver = astrbot_scenario.client.wait_for_driver_ready()
    assert driver == {
        "driver_loaded": True,
        "memora_loaded": True,
        "chat_provider_loaded": True,
        "embedding_provider_loaded": True,
        "platform_loaded": True,
    }
    memora = astrbot_scenario.client.wait_for_memora_ready()
    assert memora["provider"]["status"] == "ready"
    assert memora["provider"]["is_initialized"] is True

    astrbot_scenario.stop()
    astrbot_scenario.assert_resources_released()
