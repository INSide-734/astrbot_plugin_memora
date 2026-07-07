"""core/api/delegation_api.py — DelegationApiMixin 测试。

验证端点响应和错误处理。
委托端点不需要请求参数/正文，因此无需 patch quart.request。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.api.delegation_api import DelegationApiMixin


def _make_stub(*, has_delegation=True, delegation_status=None):
    """创建带有 Mock FeatureDelegation 的 DelegationApiMixin 存根。"""

    class Stub:
        get_delegation_status = DelegationApiMixin.get_delegation_status
        _get_feature_delegation = DelegationApiMixin._get_feature_delegation

    stub = Stub()
    if has_delegation:
        fd = MagicMock()
        default_status = {
            "self_learning_active": True,
            "self_learning_label": "SelfLearning v2.1",
            "chatplus_active": False,
            "chatplus_label": None,
            "delegated_jargon": True,
            "delegated_expression": True,
            "delegated_affection": True,
            "delegated_reply": False,
        }
        fd.get_delegation_status.return_value = delegation_status or default_status
        stub.plugin = MagicMock()
        stub.plugin.feature_delegation = fd

    return stub


# ---------------------------------------------------------------------------
# 委托状态
# ---------------------------------------------------------------------------


class TestDelegationStatus:
    @pytest.mark.asyncio
    async def test_returns_status_when_both_active(self) -> None:
        status = {
            "self_learning_active": True,
            "self_learning_label": "SelfLearning v2.1",
            "chatplus_active": True,
            "chatplus_label": "GroupChatPlus v1.0",
            "delegated_jargon": True,
            "delegated_expression": True,
            "delegated_affection": True,
            "delegated_reply": True,
        }
        stub = _make_stub(delegation_status=status)
        result = await stub.get_delegation_status()
        assert result["status"] == "ok"
        assert result["data"]["self_learning_active"] is True
        assert result["data"]["chatplus_active"] is True
        assert result["data"]["delegated_jargon"] is True
        assert result["data"]["delegated_reply"] is True

    @pytest.mark.asyncio
    async def test_returns_status_when_none_active(self) -> None:
        status = {
            "self_learning_active": False,
            "self_learning_label": None,
            "chatplus_active": False,
            "chatplus_label": None,
            "delegated_jargon": False,
            "delegated_expression": False,
            "delegated_affection": False,
            "delegated_reply": False,
        }
        stub = _make_stub(delegation_status=status)
        result = await stub.get_delegation_status()
        assert result["status"] == "ok"
        assert result["data"]["self_learning_active"] is False
        assert result["data"]["chatplus_active"] is False
        assert all(not result["data"][k] for k in [
            "delegated_jargon", "delegated_expression",
            "delegated_affection", "delegated_reply",
        ])

    @pytest.mark.asyncio
    async def test_no_feature_delegation_returns_error(self) -> None:
        class Stub:
            get_delegation_status = DelegationApiMixin.get_delegation_status
            _get_feature_delegation = DelegationApiMixin._get_feature_delegation

        stub = Stub()
        result = await stub.get_delegation_status()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_delegation_from_initializer_fallback(self) -> None:
        class Stub:
            get_delegation_status = DelegationApiMixin.get_delegation_status
            _get_feature_delegation = DelegationApiMixin._get_feature_delegation

        stub = Stub()
        fd = MagicMock()
        fd.get_delegation_status.return_value = {
            "self_learning_active": True,
            "self_learning_label": "SelfLearning",
            "chatplus_active": False,
            "chatplus_label": None,
            "delegated_jargon": True,
            "delegated_expression": True,
            "delegated_affection": True,
            "delegated_reply": False,
        }
        stub.plugin = MagicMock()
        stub.plugin.feature_delegation = None  # simulate not on plugin direct
        stub.plugin.initializer = MagicMock()
        stub.plugin.initializer.feature_delegation = fd  # fallback path

        result = await stub.get_delegation_status()
        assert result["status"] == "ok"
        assert result["data"]["self_learning_active"] is True

    @pytest.mark.asyncio
    async def test_exception_in_get_status_returns_error(self) -> None:
        fd = MagicMock()
        fd.get_delegation_status.side_effect = RuntimeError("connection lost")

        class Stub:
            get_delegation_status = DelegationApiMixin.get_delegation_status
            _get_feature_delegation = DelegationApiMixin._get_feature_delegation

        stub = Stub()
        stub.plugin = MagicMock()
        stub.plugin.feature_delegation = fd

        result = await stub.get_delegation_status()
        assert result["status"] == "error"
