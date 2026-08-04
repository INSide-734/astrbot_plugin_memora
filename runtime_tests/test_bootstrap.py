"""真实 AstrBot 启动与关停契约。"""

from __future__ import annotations

import socket
import subprocess
from pathlib import Path
from typing import Any

import pytest

from runtime_tests.harness import AstrBotScenario, AstrBotTemplate
from runtime_tests.harness import process as process_module


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


def test_bind_conflict_retry_ignores_externally_owned_port(
    astrbot_template: AstrBotTemplate,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """首次端口被外部抢占时，成功重试不应把旧端口判为资源泄漏。"""
    scenario = AstrBotScenario.prepare(astrbot_template.root, tmp_path / "scenario")
    external_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    real_popen = process_module.subprocess.Popen
    launch_count = 0

    def launch_with_first_port_occupied(
        command: list[str],
        **kwargs: Any,
    ) -> subprocess.Popen[bytes]:
        """只在第一次真实启动前抢占已释放的候选端口。"""
        nonlocal launch_count
        launch_count += 1
        if launch_count == 1:
            external_listener.bind(("127.0.0.1", int(command[-1])))
            external_listener.listen()
        return real_popen(command, **kwargs)

    monkeypatch.setattr(
        process_module.subprocess,
        "Popen",
        launch_with_first_port_occupied,
    )
    try:
        scenario.start()
        assert launch_count == 2
        scenario.stop()
        scenario.assert_resources_released()
    finally:
        scenario.close()
        external_listener.close()
