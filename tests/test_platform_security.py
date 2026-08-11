"""平台提示词保护适配器与生命周期契约测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.platform.security import build_prompt_protection_port, close_prompt_protection
from core.shared.contracts import PromptProtectionPort


def test_prompt_protection_adapter_exposes_port_and_consumes_scope() -> None:
    """平台适配器应通过共享端口登记、清洗并消费请求作用域。"""

    protection = build_prompt_protection_port(enable_double_check=False)

    assert isinstance(protection, PromptProtectionPort)
    protection.wrap_prompt(
        "内部记忆内容",
        scope_id="request-1",
    )
    assert protection.has_scope("request-1")

    sanitized, report = protection.sanitize_response(
        "正常回复",
        scope_id="request-1",
        consume_scope=True,
    )

    assert sanitized == "正常回复"
    assert report["validation_passed"] is True
    assert not protection.has_scope("request-1")


def test_prompt_protection_adapter_close_releases_registered_scopes() -> None:
    """关闭平台适配器时应清理全部已登记作用域并拒绝后续包装。"""

    protection = build_prompt_protection_port(enable_double_check=False)
    protection.wrap_prompt("第一条内部记忆", scope_id="request-1")
    protection.wrap_prompt("第二条内部记忆", scope_id="request-2")

    protection.close()

    assert not protection.has_scope("request-1")
    assert not protection.has_scope("request-2")
    with pytest.raises(RuntimeError, match="prompt_protection_closed"):
        protection.wrap_prompt("关闭后内容", scope_id="request-3")


@pytest.mark.asyncio
async def test_shutdown_lifecycle_releases_prompt_protection_port() -> None:
    """运行时关停协作应关闭适配器并撤销组合根上的端口引用。"""

    protection = build_prompt_protection_port(enable_double_check=False)
    protection.wrap_prompt("内部记忆", scope_id="request-1")
    initializer = SimpleNamespace(prompt_protection=protection)

    await close_prompt_protection(initializer)

    assert initializer.prompt_protection is None
    assert not protection.has_scope("request-1")
