"""AstrBot live 黑盒档位的外部配置安全契约。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from runtime_tests.harness.live import LiveProviderSettings

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _live_environment(**overrides: str) -> dict[str, str]:
    """返回不含真实凭据的完整 live 测试环境。"""
    environment = {
        "MEMORA_LIVE_API_BASE": "https://llm.example.test/v1",
        "MEMORA_LIVE_API_KEY": "synthetic-live-key-123456",
        "MEMORA_LIVE_MODEL": "test-model",
        "MEMORA_LIVE_ALLOWED_HOSTS": "llm.example.test",
    }
    environment.update(overrides)
    return environment


def test_live_settings_require_all_protected_values() -> None:
    """缺少任一受保护配置时应在联网前失败。"""
    with pytest.raises(ValueError, match="MEMORA_LIVE_API_KEY"):
        LiveProviderSettings.from_environment(
            {
                "MEMORA_LIVE_API_BASE": "https://llm.example.test/v1",
                "MEMORA_LIVE_MODEL": "test-model",
                "MEMORA_LIVE_ALLOWED_HOSTS": "llm.example.test",
            }
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"MEMORA_LIVE_API_BASE": "http://llm.example.test/v1"}, "HTTPS"),
        ({"MEMORA_LIVE_API_BASE": "https://other.example.test/v1"}, "白名单"),
        ({"MEMORA_LIVE_API_BASE": "https://user@llm.example.test/v1"}, "用户信息"),
        ({"MEMORA_LIVE_API_BASE": "https://127.0.0.1/v1"}, "IP 地址"),
    ],
)
def test_live_settings_reject_unsafe_api_base(
    overrides: dict[str, str],
    message: str,
) -> None:
    """live API 地址必须是白名单中的公开 HTTPS 主机。"""
    with pytest.raises(ValueError, match=message):
        LiveProviderSettings.from_environment(_live_environment(**overrides))


def test_live_settings_build_openai_provider_without_exposing_secret() -> None:
    """合法配置应生成 AstrBot 内置 OpenAI adapter 所需字段。"""
    settings = LiveProviderSettings.from_environment(_live_environment())

    assert "synthetic-live-key-123456" not in repr(settings)

    assert settings.provider_config() == {
        "id": "memora-test-chat",
        "provider": "openai-compatible-live",
        "type": "openai_chat_completion",
        "provider_type": "chat_completion",
        "enable": True,
        "model": "test-model",
        "key": ["synthetic-live-key-123456"],
        "api_base": "https://llm.example.test/v1",
        "timeout": 30,
        "proxy": "",
        "custom_headers": {},
    }


def test_ci_keeps_live_secret_out_of_pr_jobs_and_requires_manual_main() -> None:
    """真实模型密钥只能进入受保护、手动且 main 限定的 live 测试步骤。"""
    workflow = yaml.load(
        (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )

    dispatch_input = workflow["on"]["workflow_dispatch"]["inputs"]["run_live_blackbox"]
    assert dispatch_input["default"] == "false"
    pr_job = workflow["jobs"]["blackbox-bootstrap"]
    assert pr_job["strategy"]["matrix"]["os"] == [
        "ubuntu-latest",
        "windows-latest",
    ]
    assert "MEMORA_LIVE_API_KEY" not in str(pr_job)
    assert any("--profile pr" in step.get("run", "") for step in pr_job["steps"])

    live_job = workflow["jobs"]["blackbox-live"]
    assert live_job["environment"] == "astrbot-blackbox-live"
    assert live_job["timeout-minutes"] == "5"
    assert "workflow_dispatch" in live_job["if"]
    assert "refs/heads/main" in live_job["if"]
    live_step = next(
        step for step in live_job["steps"] if "--profile live" in step.get("run", "")
    )
    assert live_step["env"]["MEMORA_LIVE_API_KEY"] == (
        "${{ secrets.MEMORA_LIVE_API_KEY }}"
    )
    assert all(
        "MEMORA_LIVE_API_KEY" not in step.get("env", {})
        for step in live_job["steps"]
        if step is not live_step
    )
    assert not any(
        "upload-artifact" in step.get("uses", "") for step in live_job["steps"]
    )
