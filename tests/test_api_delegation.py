"""core/api/delegation_api.py — DelegationApiMixin 测试。

验证端点响应和错误处理，包括新增的 provided-services 端点。
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
            "provided_memory_service": True,
            "provided_knowledge_service": True,
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
        assert all(
            not result["data"][k]
            for k in [
                "delegated_jargon",
                "delegated_expression",
                "delegated_affection",
                "delegated_reply",
            ]
        )

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


# ---------------------------------------------------------------------------
# Provided services endpoint
# ---------------------------------------------------------------------------


class TestProvidedServices:
    """GET /delegation/provided-services 端点测试。"""

    def _make_stub_with_services(
        self,
        has_delegation: bool = True,
        services_status: dict | None = None,
    ):
        """创建带有 Mock FeatureDelegation 的 DelegationApiMixin 存根。"""

        class Stub:
            get_provided_services = DelegationApiMixin.get_provided_services
            _get_feature_delegation = DelegationApiMixin._get_feature_delegation

        stub = Stub()
        if has_delegation:
            fd = MagicMock()
            default_svc = {
                "memora_available": True,
                "memora_aliases": ["astrbot_plugin_memora", "Memora"],
                "memory_service": True,
                "knowledge_service": True,
                "service_details": {
                    "memory_recall": "可用 — recall_memory(query, session_id, top_k)",
                    "knowledge_search": "可用 — search_knowledge(query, top_k)",
                },
            }
            fd.get_provided_services_status.return_value = (
                services_status or default_svc
            )
            stub.plugin = MagicMock()
            stub.plugin.feature_delegation = fd

        return stub

    @pytest.mark.asyncio
    async def test_returns_provided_services(self) -> None:
        stub = self._make_stub_with_services()
        result = await stub.get_provided_services()
        assert result["status"] == "ok"
        assert result["data"]["memora_available"] is True
        assert result["data"]["memory_service"] is True
        assert result["data"]["knowledge_service"] is True
        assert "astrbot_plugin_memora" in result["data"]["memora_aliases"]

    @pytest.mark.asyncio
    async def test_services_unavailable(self) -> None:
        svc_status = {
            "memora_available": True,
            "memora_aliases": ["astrbot_plugin_memora"],
            "memory_service": False,
            "knowledge_service": False,
            "service_details": {
                "memory_recall": "不可用 — MemoryEngine 未注入",
                "knowledge_search": "不可用 — KnowledgeManager 未注入",
            },
        }
        stub = self._make_stub_with_services(services_status=svc_status)
        result = await stub.get_provided_services()
        assert result["status"] == "ok"
        assert result["data"]["memory_service"] is False
        assert result["data"]["knowledge_service"] is False

    @pytest.mark.asyncio
    async def test_no_feature_delegation_returns_error(self) -> None:
        class Stub:
            get_provided_services = DelegationApiMixin.get_provided_services
            _get_feature_delegation = DelegationApiMixin._get_feature_delegation

        stub = Stub()
        result = await stub.get_provided_services()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_exception_returns_error(self) -> None:
        fd = MagicMock()
        fd.get_provided_services_status.side_effect = RuntimeError("service down")

        class Stub:
            get_provided_services = DelegationApiMixin.get_provided_services
            _get_feature_delegation = DelegationApiMixin._get_feature_delegation

        stub = Stub()
        stub.plugin = MagicMock()
        stub.plugin.feature_delegation = fd

        result = await stub.get_provided_services()
        assert result["status"] == "error"
