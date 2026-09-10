"""门禁 dry-run 页面接口测试。

覆盖：显式 profile 解析、字段上限拒绝、绑定上下文不回显、规则命中报告。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.features.quality.application.gate_runtime import (
    GateRuntime,
    build_gate_snapshot,
    default_gate_snapshot,
)
from core.features.quality.domain.gate_config import GateConfig
from core.platform.transport.page_api.gate_api import GateApiMixin


class _Request:
    """最小页面请求桩：仅提供 dry-run 读取 JSON 所需能力。"""

    def __init__(
        self,
        body: Any = None,
        json_error: Exception | None = None,
    ) -> None:
        self._body = body
        self._json_error = json_error

    async def get_json(self, **_kwargs: Any) -> Any:
        if self._json_error is not None:
            raise self._json_error
        return self._body


def _make_api(
    *,
    body: Any = None,
    runtime: GateRuntime | None = None,
    json_error: Exception | None = None,
) -> GateApiMixin:
    """构造只挂接门禁运行时的页面接口实例。"""
    plugin = MagicMock()
    plugin.context = SimpleNamespace(
        request=_Request(body=body, json_error=json_error),
    )
    if runtime is not None:
        plugin.initializer = SimpleNamespace(gate_runtime=runtime)
    api = GateApiMixin()
    api.plugin = plugin
    return api


def _runtime_with_rule() -> GateRuntime:
    """构造带规则 r1（content 含“猫”→force discard）的快照运行时。"""
    config = GateConfig.model_validate(
        {
            "profiles": [
                {
                    "name": "private",
                    "rules": [
                        {
                            "id": "r1",
                            "description": "猫相关内容直接丢弃",
                            "when": {
                                "op": "contains",
                                "field": "content",
                                "values": ["猫"],
                            },
                            "action": {
                                "kind": "force_disposition",
                                "value": "discard",
                            },
                        }
                    ],
                },
                {"name": "group"},
            ]
        }
    )
    return GateRuntime(build_gate_snapshot(config))


_VALID_BODY: dict[str, Any] = {
    "profile": "private",
    "content": "我养了两只猫，它们每天都在阳台晒太阳",
    "key_facts": ["用户养了两只猫"],
    "importance": 0.6,
}


@pytest.mark.asyncio
async def test_dry_run_resolves_profile_and_rules() -> None:
    """显式 profile 解析；默认配置无规则 → 低质 normal、无命中、按 profile 处置。"""
    api = _make_api(
        body=_VALID_BODY,
        runtime=GateRuntime(default_gate_snapshot()),
    )

    result = await api.dry_run_gate()

    assert result["status"] == "ok"
    assert result["data"] == {
        "profile": "private",
        "quality": "normal",
        "matched_rules": [],
        "disposition": "quarantine",
    }


@pytest.mark.asyncio
async def test_dry_run_rejects_oversize_content() -> None:
    """content 超 2000 字符 → 稳定错误码 gate_dry_run_invalid。"""
    body = dict(_VALID_BODY)
    body["content"] = "猫" * 2001
    api = _make_api(body=body, runtime=GateRuntime(default_gate_snapshot()))

    result = await api.dry_run_gate()

    assert result["status"] == "error"
    assert result["code"] == "gate_dry_run_invalid"


@pytest.mark.asyncio
async def test_dry_run_binding_context_not_echoed() -> None:
    """绑定上下文只用于解析 profile，响应不回显身份字段与样例正文。"""
    body = dict(_VALID_BODY)
    body.pop("profile")
    body["group_id"] = "g1"
    body["persona_id"] = "p1"
    api = _make_api(body=body, runtime=GateRuntime(default_gate_snapshot()))

    result = await api.dry_run_gate()

    assert result["status"] == "ok"
    assert result["data"]["profile"] == "private"
    assert set(result["data"]) == {
        "profile",
        "quality",
        "matched_rules",
        "disposition",
    }


@pytest.mark.asyncio
async def test_dry_run_rule_match_reported() -> None:
    """规则命中 → matched_rules 报告 r1，处置输出 force 值 discard。"""
    api = _make_api(body=_VALID_BODY, runtime=_runtime_with_rule())

    result = await api.dry_run_gate()

    assert result["status"] == "ok"
    assert result["data"]["matched_rules"] == ["r1"]
    assert result["data"]["disposition"] == "discard"


@pytest.mark.asyncio
async def test_dry_run_explicit_empty_profile_errors() -> None:
    """显式 profile="" 不得回落绑定解析，直接报 profile 不存在。"""
    body = dict(_VALID_BODY)
    body["profile"] = ""
    api = _make_api(body=body, runtime=GateRuntime(default_gate_snapshot()))

    result = await api.dry_run_gate()

    assert result["status"] == "error"
    assert result["code"] == "gate_profile_not_found"


@pytest.mark.asyncio
async def test_dry_run_explicit_profile_takes_priority_over_binding() -> None:
    """显式 profile 优先于绑定上下文：chat_type 指向 private 时仍解析 group。"""
    body = dict(_VALID_BODY)
    body["profile"] = "group"
    body["chat_type"] = "private"
    api = _make_api(body=body, runtime=GateRuntime(default_gate_snapshot()))

    result = await api.dry_run_gate()

    assert result["status"] == "ok"
    assert result["data"]["profile"] == "group"
