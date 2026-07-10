"""FeatureDelegation 测试 — 伴侣插件检测和委托决策。

Covers:
- No companion plugin detected → no delegation
- self_learning detected → delegate jargon/expression/affection/persona/style
- GroupChatPlus detected → delegate reply
- Both detected → delegate all relevant functions
- Config switch off → no delegation even if plugin active (dual-gate)
- Debounce log: no repeat logging when status unchanged
- get_delegation_status() structured output
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from core.feature_delegation import FeatureDelegation


# ---------------------------------------------------------------------------
# Helper: build a mock Star instance
# ---------------------------------------------------------------------------

def _make_star(
    name: str = "test_plugin",
    display_name: str = "Test Plugin",
    root_dir_name: str = "test_plugin",
    module_path: str = "test_plugin",
    activated: bool = True,
    star_cls: Any = True,  # non-None = has class
) -> MagicMock:
    """Create a minimal mock Star with the given metadata."""
    star = MagicMock()
    star.name = name
    star.display_name = display_name
    star.root_dir_name = root_dir_name
    star.module_path = module_path
    star.activated = activated
    star.star_cls = star_cls
    return star


def _make_context(
    registered_stars: dict[str, MagicMock] | None = None,
    all_stars: list[MagicMock] | None = None,
) -> MagicMock:
    """Create a mock AstrBot Context with a star registry.

    Args:
        registered_stars: dict mapping alias → star (for get_registered_star).
            None means get_registered_star raises / returns None for all.
        all_stars: list of stars returned by get_all_stars().
    """
    ctx = MagicMock()

    if registered_stars is not None:
        def _get_star(alias: str) -> MagicMock | None:
            return registered_stars.get(alias)
        ctx.get_registered_star = _get_star
    else:
        ctx.get_registered_star = None

    if all_stars is not None:
        ctx.get_all_stars.return_value = list(all_stars)
    else:
        ctx.get_all_stars = None

    return ctx


# ---------------------------------------------------------------------------
# No companion plugin
# ---------------------------------------------------------------------------

class TestNoCompanionPlugin:
    """When no companion plugin is active, all delegation checks return False."""

    def test_no_self_learning_detected(self) -> None:
        ctx = _make_context(registered_stars={}, all_stars=[])
        fd = FeatureDelegation(ctx)
        assert fd.self_learning_plugin() is None

    def test_no_chatplus_detected(self) -> None:
        ctx = _make_context(registered_stars={}, all_stars=[])
        fd = FeatureDelegation(ctx)
        assert fd.chatplus_plugin() is None

    def test_should_delegate_jargon_false(self) -> None:
        ctx = _make_context(registered_stars={}, all_stars=[])
        fd = FeatureDelegation(ctx)
        assert fd.should_delegate_jargon() is False

    def test_should_delegate_expression_false(self) -> None:
        ctx = _make_context(registered_stars={}, all_stars=[])
        fd = FeatureDelegation(ctx)
        assert fd.should_delegate_expression() is False

    def test_should_delegate_affection_false(self) -> None:
        ctx = _make_context(registered_stars={}, all_stars=[])
        fd = FeatureDelegation(ctx)
        assert fd.should_delegate_affection() is False

    def test_should_skip_persona_processing_false(self) -> None:
        ctx = _make_context(registered_stars={}, all_stars=[])
        fd = FeatureDelegation(ctx)
        assert fd.should_skip_persona_processing() is False

    def test_should_skip_style_extraction_false(self) -> None:
        ctx = _make_context(registered_stars={}, all_stars=[])
        fd = FeatureDelegation(ctx)
        assert fd.should_skip_style_extraction() is False

    def test_should_delegate_reply_false(self) -> None:
        ctx = _make_context(registered_stars={}, all_stars=[])
        fd = FeatureDelegation(ctx)
        assert fd.should_delegate_reply() is False

    def test_get_delegation_status_none_active(self) -> None:
        ctx = _make_context(registered_stars={}, all_stars=[])
        fd = FeatureDelegation(ctx)
        status = fd.get_delegation_status()
        assert status == {
            "self_learning_active": False,
            "self_learning_label": None,
            "chatplus_active": False,
            "chatplus_label": None,
            "delegated_jargon": False,
            "delegated_expression": False,
            "delegated_affection": False,
            "delegated_reply": False,
            "provided_memory_service": False,
            "provided_knowledge_service": False,
        }


# ---------------------------------------------------------------------------
# self_learning detected
# ---------------------------------------------------------------------------

class TestSelfLearningDetected:
    """When self_learning is active, Memora delegates jargon/expression/affection."""

    def test_self_learning_via_get_registered_star(self) -> None:
        sl_star = _make_star(
            name="self_learning",
            display_name="Self Learning",
            root_dir_name="astrbot_plugin_self_learning",
            module_path="astrbot_plugin_self_learning",
        )
        ctx = _make_context(registered_stars={"self_learning": sl_star}, all_stars=[])
        fd = FeatureDelegation(ctx)
        assert fd.self_learning_plugin() is sl_star

    def test_self_learning_via_alias_match(self) -> None:
        """get_registered_star with alias 'SelfLearning' should match."""
        sl_star = _make_star(
            name="SelfLearning",
            display_name="Self Learning",
            root_dir_name="astrbot_plugin_self_learning",
            module_path="astrbot_plugin_self_learning",
        )
        ctx = _make_context(registered_stars={"SelfLearning": sl_star}, all_stars=[])
        fd = FeatureDelegation(ctx)
        assert fd.self_learning_plugin() is sl_star

    def test_self_learning_via_all_stars_fallback(self) -> None:
        """When get_registered_star fails, get_all_stars() fallback should work."""
        sl_star = _make_star(
            name="self_learning",
            display_name="Self Learning",
            root_dir_name="astrbot_plugin_self_learning",
            module_path="astrbot_plugin_self_learning",
        )
        # get_registered_star returns None for everything
        ctx = _make_context(
            registered_stars={},
            all_stars=[sl_star],
        )
        # Override get_registered_star behavior: always returns None
        ctx.get_registered_star = lambda alias: None
        fd = FeatureDelegation(ctx)
        assert fd.self_learning_plugin() is sl_star

    def test_all_delegation_methods_return_true(self) -> None:
        sl_star = _make_star(
            name="self_learning",
            display_name="Self Learning",
            root_dir_name="astrbot_plugin_self_learning",
            module_path="astrbot_plugin_self_learning",
        )
        ctx = _make_context(registered_stars={"self_learning": sl_star})
        fd = FeatureDelegation(ctx)

        assert fd.should_delegate_jargon() is True
        assert fd.should_delegate_expression() is True
        assert fd.should_delegate_affection() is True
        assert fd.should_skip_persona_processing() is True
        assert fd.should_skip_style_extraction() is True
        # reply should NOT be delegated (no ChatPlus)
        assert fd.should_delegate_reply() is False

    def test_get_delegation_status_with_self_learning(self) -> None:
        sl_star = _make_star(
            name="self_learning",
            display_name="Self Learning",
            root_dir_name="astrbot_plugin_self_learning",
            module_path="astrbot_plugin_self_learning",
        )
        ctx = _make_context(registered_stars={"self_learning": sl_star})
        fd = FeatureDelegation(ctx)
        status = fd.get_delegation_status()
        assert status["self_learning_active"] is True
        assert status["self_learning_label"] == "Self Learning"
        assert status["delegated_jargon"] is True
        assert status["delegated_expression"] is True
        assert status["delegated_affection"] is True
        assert status["delegated_reply"] is False  # ChatPlus not present

    def test_module_path_component_match(self) -> None:
        """Module path components should be included in candidate name matching."""
        sl_star = _make_star(
            name="some_other_name",
            display_name="Custom Display",
            root_dir_name="custom_dir",
            module_path="plugins.astrbot_plugin_self_learning.main",
        )
        ctx = _make_context(registered_stars={}, all_stars=[sl_star])
        ctx.get_registered_star = lambda alias: None
        fd = FeatureDelegation(ctx)
        # Should match via module_path component "astrbot_plugin_self_learning"
        assert fd.self_learning_plugin() is sl_star


# ---------------------------------------------------------------------------
# GroupChatPlus detected
# ---------------------------------------------------------------------------

class TestChatPlusDetected:
    """When GroupChatPlus is active, Memora delegates reply influence."""

    def test_chatplus_via_get_registered_star(self) -> None:
        cp_star = _make_star(
            name="astrbot_plugin_group_chat_plus",
            display_name="Group Chat Plus",
            root_dir_name="astrbot_plugin_group_chat_plus",
            module_path="astrbot_plugin_group_chat_plus",
        )
        ctx = _make_context(
            registered_stars={"astrbot_plugin_group_chat_plus": cp_star},
            all_stars=[],
        )
        fd = FeatureDelegation(ctx)
        assert fd.chatplus_plugin() is cp_star

    def test_chatplus_via_alias_display_name(self) -> None:
        """get_all_stars fallback should match 'Group Chat Plus' display name."""
        cp_star = _make_star(
            name="chatplus",
            display_name="Group Chat Plus",
            root_dir_name="chatplus",
            module_path="chatplus",
        )
        ctx = _make_context(registered_stars={}, all_stars=[cp_star])
        ctx.get_registered_star = lambda alias: None
        fd = FeatureDelegation(ctx)
        assert fd.chatplus_plugin() is cp_star

    def test_delegate_reply_true(self) -> None:
        cp_star = _make_star(
            name="astrbot_plugin_group_chat_plus",
            display_name="Group Chat Plus",
            root_dir_name="astrbot_plugin_group_chat_plus",
            module_path="astrbot_plugin_group_chat_plus",
        )
        ctx = _make_context(
            registered_stars={"astrbot_plugin_group_chat_plus": cp_star},
        )
        fd = FeatureDelegation(ctx)
        assert fd.should_delegate_reply() is True
        # self_learning features should NOT be delegated
        assert fd.should_delegate_jargon() is False

    def test_get_delegation_status_with_chatplus(self) -> None:
        cp_star = _make_star(
            name="astrbot_plugin_group_chat_plus",
            display_name="Group Chat Plus",
            root_dir_name="astrbot_plugin_group_chat_plus",
            module_path="astrbot_plugin_group_chat_plus",
        )
        ctx = _make_context(
            registered_stars={"astrbot_plugin_group_chat_plus": cp_star},
        )
        fd = FeatureDelegation(ctx)
        status = fd.get_delegation_status()
        assert status["chatplus_active"] is True
        assert status["chatplus_label"] == "Group Chat Plus"
        assert status["delegated_reply"] is True
        assert status["self_learning_active"] is False


# ---------------------------------------------------------------------------
# Both plugins detected
# ---------------------------------------------------------------------------

class TestBothPluginsDetected:
    """When both companions are active, all delegations should be active."""

    def test_both_active(self) -> None:
        sl_star = _make_star(
            name="self_learning",
            display_name="Self Learning",
            root_dir_name="astrbot_plugin_self_learning",
            module_path="astrbot_plugin_self_learning",
        )
        cp_star = _make_star(
            name="astrbot_plugin_group_chat_plus",
            display_name="Group Chat Plus",
            root_dir_name="astrbot_plugin_group_chat_plus",
            module_path="astrbot_plugin_group_chat_plus",
        )
        ctx = _make_context(
            registered_stars={
                "self_learning": sl_star,
                "astrbot_plugin_group_chat_plus": cp_star,
            },
        )
        fd = FeatureDelegation(ctx)

        assert fd.should_delegate_jargon() is True
        assert fd.should_delegate_expression() is True
        assert fd.should_delegate_affection() is True
        assert fd.should_delegate_reply() is True

    def test_both_in_status(self) -> None:
        sl_star = _make_star(
            name="self_learning",
            display_name="Self Learning",
            root_dir_name="astrbot_plugin_self_learning",
            module_path="astrbot_plugin_self_learning",
        )
        cp_star = _make_star(
            name="astrbot_plugin_group_chat_plus",
            display_name="Group Chat Plus",
            root_dir_name="astrbot_plugin_group_chat_plus",
            module_path="astrbot_plugin_group_chat_plus",
        )
        ctx = _make_context(
            registered_stars={
                "self_learning": sl_star,
                "astrbot_plugin_group_chat_plus": cp_star,
            },
        )
        fd = FeatureDelegation(ctx)
        status = fd.get_delegation_status()
        assert status["self_learning_active"] is True
        assert status["chatplus_active"] is True
        assert status["delegated_jargon"] is True
        assert status["delegated_reply"] is True


# ---------------------------------------------------------------------------
# Inactive / unactivated plugins are ignored
# ---------------------------------------------------------------------------

class TestInactivePluginIgnored:
    """Plugins with activated=False or missing star_cls should be ignored."""

    def test_not_activated(self) -> None:
        sl_star = _make_star(
            name="self_learning",
            display_name="Self Learning",
            activated=False,  # <-- not activated
        )
        ctx = _make_context(registered_stars={"self_learning": sl_star})
        fd = FeatureDelegation(ctx)
        assert fd.self_learning_plugin() is None
        assert fd.should_delegate_jargon() is False

    def test_no_star_cls(self) -> None:
        sl_star = _make_star(
            name="self_learning",
            display_name="Self Learning",
            star_cls=None,  # <-- no class
        )
        ctx = _make_context(registered_stars={"self_learning": sl_star})
        fd = FeatureDelegation(ctx)
        assert fd.self_learning_plugin() is None

    def test_chatplus_not_activated(self) -> None:
        cp_star = _make_star(
            name="astrbot_plugin_group_chat_plus",
            display_name="Group Chat Plus",
            activated=False,
        )
        ctx = _make_context(
            registered_stars={"astrbot_plugin_group_chat_plus": cp_star},
        )
        fd = FeatureDelegation(ctx)
        assert fd.chatplus_plugin() is None
        assert fd.should_delegate_reply() is False


# ---------------------------------------------------------------------------
# Context with no star API (graceful degradation)
# ---------------------------------------------------------------------------

class TestNoStarAPIAvailable:
    """When context has no get_registered_star or get_all_stars, degrade gracefully."""

    def test_no_get_registered_star_no_get_all_stars(self) -> None:
        ctx = MagicMock()
        ctx.get_registered_star = None
        ctx.get_all_stars = None
        fd = FeatureDelegation(ctx)
        assert fd.self_learning_plugin() is None
        assert fd.chatplus_plugin() is None
        assert fd.should_delegate_jargon() is False
        assert fd.should_delegate_reply() is False

    def test_empty_all_stars(self) -> None:
        ctx = _make_context(registered_stars={}, all_stars=[])
        fd = FeatureDelegation(ctx)
        assert fd.self_learning_plugin() is None
        assert fd.chatplus_plugin() is None


# ---------------------------------------------------------------------------
# Debounce log behavior
# ---------------------------------------------------------------------------

class TestDebounceLog:
    """log_status() should only log on state change."""

    def test_first_call_logs(self) -> None:
        ctx = _make_context(registered_stars={}, all_stars=[])
        fd = FeatureDelegation(ctx)
        with patch.object(logging.getLogger("astrbot.test"), "info") as mock_info:
            fd.log_status()
            # First call should log (status from None → False/False)
            assert mock_info.call_count == 2  # one for each plugin

    def test_repeat_call_no_log(self) -> None:
        ctx = _make_context(registered_stars={}, all_stars=[])
        fd = FeatureDelegation(ctx)
        fd.log_status()  # prime the cache

        with patch.object(logging.getLogger("astrbot.test"), "info") as mock_info:
            fd.log_status()
            # Should NOT log again — state unchanged
            mock_info.assert_not_called()

    def test_state_change_logs_again(self) -> None:
        ctx = _make_context(registered_stars={}, all_stars=[])
        fd = FeatureDelegation(ctx)
        fd.log_status()  # prime: no plugins active

        # Now inject self_learning
        sl_star = _make_star(
            name="self_learning",
            display_name="Self Learning",
            root_dir_name="astrbot_plugin_self_learning",
            module_path="astrbot_plugin_self_learning",
        )
        ctx.get_registered_star = lambda alias: sl_star if alias in FeatureDelegation.SELF_LEARNING_ALIASES else None

        with patch.object(logging.getLogger("astrbot.test"), "info") as mock_info:
            fd.log_status()
            # Should log again — state changed from no_plugins → self_learning
            assert mock_info.call_count == 2  # one for sl detected, one for cp not detected


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_context_is_none(self) -> None:
        fd = FeatureDelegation(None)
        assert fd.self_learning_plugin() is None
        assert fd.chatplus_plugin() is None
        assert fd.should_delegate_jargon() is False
        assert fd.should_delegate_reply() is False

    def test_star_name_case_insensitive_match(self) -> None:
        """Alias matching should be case-insensitive."""
        sl_star = _make_star(
            name="SELF_LEARNING",
            display_name="SELF LEARNING",
            root_dir_name="ASTRBOT_PLUGIN_SELF_LEARNING",
            module_path="ASTRBOT_PLUGIN_SELF_LEARNING",
        )
        ctx = _make_context(registered_stars={}, all_stars=[sl_star])
        ctx.get_registered_star = lambda alias: None
        fd = FeatureDelegation(ctx)
        assert fd.self_learning_plugin() is sl_star

    def test_get_registered_star_raises_exception(self) -> None:
        """If get_registered_star raises, fall back to get_all_stars."""
        sl_star = _make_star(
            name="self_learning",
            display_name="Self Learning",
            root_dir_name="astrbot_plugin_self_learning",
            module_path="astrbot_plugin_self_learning",
        )
        ctx = _make_context(all_stars=[sl_star])

        def _exploding_getter(alias: str) -> None:
            raise RuntimeError("simulated failure")
        ctx.get_registered_star = _exploding_getter

        fd = FeatureDelegation(ctx)
        # Should fall through to get_all_stars and find it
        assert fd.self_learning_plugin() is sl_star

    def test_star_label_priority(self) -> None:
        """_star_label should prefer display_name over name over root_dir_name."""
        star = _make_star(
            name="internal_name",
            display_name="Pretty Display",
            root_dir_name="dir_name",
            module_path="some.module.path",
        )
        label = FeatureDelegation._star_label(star)
        assert label == "Pretty Display"

    def test_star_label_fallback(self) -> None:
        """_star_label should fall back through the chain."""
        star = MagicMock()
        star.display_name = None
        star.name = None
        star.root_dir_name = None
        star.module_path = "fallback.module"
        label = FeatureDelegation._star_label(star)
        assert label == "fallback.module"

    def test_star_label_none_for_none_star(self) -> None:
        assert FeatureDelegation._star_label(None) is None


# ---------------------------------------------------------------------------
# Memora service aliases
# ---------------------------------------------------------------------------


class TestMemoraServiceAliases:
    """验证 MEMORA_SERVICE_ALIASES 常量正确导出。"""

    def test_aliases_contain_expected_names(self) -> None:
        aliases = FeatureDelegation.MEMORA_SERVICE_ALIASES
        assert "astrbot_plugin_memora" in aliases
        assert "Memora" in aliases
        assert "memora" in aliases

    def test_aliases_is_tuple(self) -> None:
        assert isinstance(FeatureDelegation.MEMORA_SERVICE_ALIASES, tuple)


# ---------------------------------------------------------------------------
# Provided services — engine not injected (graceful degradation)
# ---------------------------------------------------------------------------


class TestProvidedServicesWithoutEngine:
    """未注入 MemoryEngine / KnowledgeManager 时，服务方法优雅降级。"""

    def test_cannot_provide_memory_without_engine(self) -> None:
        ctx = _make_context(registered_stars={}, all_stars=[])
        fd = FeatureDelegation(ctx)
        assert fd.can_provide_memory_service() is False

    def test_cannot_provide_knowledge_without_manager(self) -> None:
        ctx = _make_context(registered_stars={}, all_stars=[])
        fd = FeatureDelegation(ctx)
        assert fd.can_provide_knowledge_service() is False

    @pytest.mark.asyncio
    async def test_recall_memory_returns_empty_without_engine(self) -> None:
        ctx = _make_context(registered_stars={}, all_stars=[])
        fd = FeatureDelegation(ctx)
        results = await fd.recall_memory("test query")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_knowledge_returns_empty_without_manager(self) -> None:
        ctx = _make_context(registered_stars={}, all_stars=[])
        fd = FeatureDelegation(ctx)
        results = await fd.search_knowledge("test query")
        assert results == []

    def test_get_delegation_status_includes_provided_false(self) -> None:
        ctx = _make_context(registered_stars={}, all_stars=[])
        fd = FeatureDelegation(ctx)
        status = fd.get_delegation_status()
        assert status["provided_memory_service"] is False
        assert status["provided_knowledge_service"] is False

    def test_get_provided_services_status_reports_unavailable(self) -> None:
        ctx = _make_context(registered_stars={}, all_stars=[])
        fd = FeatureDelegation(ctx)
        svc = fd.get_provided_services_status()
        assert svc["memora_available"] is True
        assert svc["memory_service"] is False
        assert svc["knowledge_service"] is False
        assert "不可用" in svc["service_details"]["memory_recall"]
        assert "不可用" in svc["service_details"]["knowledge_search"]


# ---------------------------------------------------------------------------
# Provided services — engine injected
# ---------------------------------------------------------------------------


class TestProvidedServicesWithEngine:
    """注入 MemoryEngine / KnowledgeManager 后，服务方法可用。"""

    def _make_fd_with_engines(
        self,
        has_self_learning: bool = True,
        mock_recall_result: list | None = None,
        mock_search_result: list | None = None,
    ) -> FeatureDelegation:
        """创建带有 mock engine/manager 的 FeatureDelegation。"""
        mock_engine = MagicMock()
        mock_engine.recall = MagicMock()
        mock_engine.recall.return_value = mock_recall_result or [
            {"content": "memory 1", "score": 0.9},
        ]

        mock_knowledge = MagicMock()
        mock_knowledge.search = MagicMock()
        mock_knowledge.search.return_value = mock_search_result or [
            {"content": "knowledge 1", "source": "doc"},
        ]

        # 构建 context
        if has_self_learning:
            sl_star = _make_star(
                name="self_learning",
                display_name="Self Learning",
                root_dir_name="astrbot_plugin_self_learning",
                module_path="astrbot_plugin_self_learning",
            )
            ctx = _make_context(
                registered_stars={"self_learning": sl_star},
                all_stars=[],
            )
        else:
            ctx = _make_context(registered_stars={}, all_stars=[])

        return FeatureDelegation(
            ctx,
            memory_engine=mock_engine,
            knowledge_manager=mock_knowledge,
        )

    def test_can_provide_memory_with_engine_and_self_learning(self) -> None:
        fd = self._make_fd_with_engines(has_self_learning=True)
        assert fd.can_provide_memory_service() is True

    def test_can_provide_knowledge_with_manager_and_self_learning(self) -> None:
        fd = self._make_fd_with_engines(has_self_learning=True)
        assert fd.can_provide_knowledge_service() is True

    def test_cannot_provide_memory_without_self_learning(self) -> None:
        """engine 已注入但 self_learning 未激活 → 不应提供服务（无使用场景）。"""
        fd = self._make_fd_with_engines(has_self_learning=False)
        assert fd.can_provide_memory_service() is False

    def test_cannot_provide_knowledge_without_self_learning(self) -> None:
        fd = self._make_fd_with_engines(has_self_learning=False)
        assert fd.can_provide_knowledge_service() is False

    @pytest.mark.asyncio
    async def test_recall_memory_delegates_to_engine(self) -> None:
        fd = self._make_fd_with_engines(has_self_learning=True)
        results = await fd.recall_memory("hello", session_id="s1", top_k=3)
        fd._memory_engine.recall.assert_called_once_with(
            query="hello", session_id="s1", top_k=3,
        )
        assert results == [{"content": "memory 1", "score": 0.9}]

    @pytest.mark.asyncio
    async def test_search_knowledge_delegates_to_manager(self) -> None:
        fd = self._make_fd_with_engines(has_self_learning=True)
        results = await fd.search_knowledge("hello", top_k=3)
        fd._knowledge_manager.search.assert_called_once_with(
            query="hello", top_k=3,
        )
        assert results == [{"content": "knowledge 1", "source": "doc"}]

    @pytest.mark.asyncio
    async def test_recall_memory_handles_exception(self) -> None:
        fd = self._make_fd_with_engines(has_self_learning=True)
        fd._memory_engine.recall.side_effect = RuntimeError("engine down")
        results = await fd.recall_memory("test")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_knowledge_handles_exception(self) -> None:
        fd = self._make_fd_with_engines(has_self_learning=True)
        fd._knowledge_manager.search.side_effect = RuntimeError("manager down")
        results = await fd.search_knowledge("test")
        assert results == []

    @pytest.mark.asyncio
    async def test_recall_memory_non_list_result(self) -> None:
        fd = self._make_fd_with_engines(has_self_learning=True)
        fd._memory_engine.recall.return_value = None
        results = await fd.recall_memory("test")
        assert results == []

    def test_get_delegation_status_includes_provided_true(self) -> None:
        fd = self._make_fd_with_engines(has_self_learning=True)
        status = fd.get_delegation_status()
        assert status["provided_memory_service"] is True
        assert status["provided_knowledge_service"] is True

    def test_get_provided_services_status_reports_available(self) -> None:
        fd = self._make_fd_with_engines(has_self_learning=True)
        svc = fd.get_provided_services_status()
        assert svc["memora_available"] is True
        assert svc["memory_service"] is True
        assert svc["knowledge_service"] is True
        assert "可用" in svc["service_details"]["memory_recall"]
        assert "可用" in svc["service_details"]["knowledge_search"]
        assert "memora_aliases" in svc
        assert "astrbot_plugin_memora" in svc["memora_aliases"]

    def test_setter_methods_update_engines(self) -> None:
        """setter 方法应更新内部引用。"""
        ctx = _make_context(registered_stars={}, all_stars=[])
        fd = FeatureDelegation(ctx)
        assert fd._memory_engine is None
        assert fd._knowledge_manager is None

        mock_engine = MagicMock()
        mock_knowledge = MagicMock()
        fd.set_memory_engine(mock_engine)
        fd.set_knowledge_manager(mock_knowledge)

        assert fd._memory_engine is mock_engine
        assert fd._knowledge_manager is mock_knowledge
