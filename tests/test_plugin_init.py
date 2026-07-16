"""测试插件初始化与版本检查 modules.

Covers:
- core/plugin_initializer.py — PluginInitializer
- core/version_check.py — version parsing and comparison
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _load_memora_plugin_class():
    root = Path(__file__).resolve().parents[1]
    package_name = "memora_testpkg"
    package = types.ModuleType(package_name)
    package.__path__ = [str(root)]
    sys.modules[package_name] = package
    star_mod = sys.modules["astrbot.api.star"]
    star_mod.Star = type(
        "TestStar",
        (object,),
        {"__init__": lambda self, context=None: None},
    )  # type: ignore[attr-defined]
    temp_data_dir = root / ".pytest_memora_data"
    temp_data_dir.mkdir(exist_ok=True)
    star_mod.StarTools = types.SimpleNamespace(
        get_data_dir=lambda: temp_data_dir
    )  # type: ignore[attr-defined]
    star_mod.register = lambda *args, **kwargs: (lambda cls: cls)  # type: ignore[attr-defined]
    event_mod = sys.modules["astrbot.api.event"]
    event_mod.filter.platform_adapter_type.side_effect = lambda *args, **kwargs: (lambda fn: fn)  # type: ignore[attr-defined]
    event_mod.filter.on_llm_request.side_effect = lambda *args, **kwargs: (lambda fn: fn)  # type: ignore[attr-defined]
    event_mod.filter.on_llm_response.side_effect = lambda *args, **kwargs: (lambda fn: fn)  # type: ignore[attr-defined]
    event_mod.filter.after_message_sent.side_effect = lambda *args, **kwargs: (lambda fn: fn)  # type: ignore[attr-defined]

    class _CommandGroup:
        def __call__(self, fn):
            return self

        def command(self, *args, **kwargs):
            return lambda fn: fn

    event_mod.filter.command_group.side_effect = lambda *args, **kwargs: _CommandGroup()  # type: ignore[attr-defined]

    filter_submodule = types.ModuleType("astrbot.api.event.filter")
    filter_submodule.PermissionType = types.SimpleNamespace(ADMIN="admin")
    filter_submodule.permission_type = lambda *args, **kwargs: (lambda fn: fn)
    sys.modules["astrbot.api.event.filter"] = filter_submodule

    spec = importlib.util.spec_from_file_location(
        f"{package_name}.main",
        root / "main.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.MemoraPlugin


# ============================================================================
# core/version_check.py
# ============================================================================


class TestParseVersion:
    """测试 _parse_version()."""

    def test_standard_version(self) -> None:
        from core.version_check import _parse_version

        assert _parse_version("4.24.2") == (4, 24, 2)

    def test_version_with_v_prefix(self) -> None:
        from core.version_check import _parse_version

        assert _parse_version("v4.24.2") == (4, 24, 2)

    def test_version_with_whitespace(self) -> None:
        from core.version_check import _parse_version

        assert _parse_version("  4.24.2  ") == (4, 24, 2)

    def test_empty_string_returns_empty_tuple(self) -> None:
        from core.version_check import _parse_version

        assert _parse_version("") == ()

    def test_nonsense_string_returns_empty_tuple(self) -> None:
        from core.version_check import _parse_version

        assert _parse_version("not-a-version") == ()

    def test_single_component(self) -> None:
        from core.version_check import _parse_version

        assert _parse_version("1") == (1,)

    def test_two_component_version(self) -> None:
        from core.version_check import _parse_version

        assert _parse_version("2.4") == (2, 4)

    def test_multi_digit_components(self) -> None:
        from core.version_check import _parse_version

        assert _parse_version("10.100.1000") == (10, 100, 1000)


class TestVersionLt:
    """测试 _version_lt()."""

    def test_lower_version_is_less(self) -> None:
        from core.version_check import _version_lt

        assert _version_lt("4.0.0", "4.24.2") is True

    def test_equal_version_is_not_less(self) -> None:
        from core.version_check import _version_lt

        assert _version_lt("4.24.2", "4.24.2") is False

    def test_higher_version_is_not_less(self) -> None:
        from core.version_check import _version_lt

        assert _version_lt("5.0.0", "4.24.2") is False

    def test_invalid_current_returns_false(self) -> None:
        from core.version_check import _version_lt

        assert _version_lt("invalid", "4.24.2") is False

    def test_invalid_minimum_returns_false(self) -> None:
        from core.version_check import _version_lt

        assert _version_lt("4.24.2", "invalid") is False

    def test_different_width_versions(self) -> None:
        from core.version_check import _version_lt

        # "4" should be treated as (4,0,0) vs (4,0,1)
        assert _version_lt("4", "4.0.1") is True
        assert _version_lt("4.0.1", "4") is False

    def test_with_v_prefix(self) -> None:
        from core.version_check import _version_lt

        assert _version_lt("v4.0.0", "v4.24.2") is True

    def test_minor_version_comparison(self) -> None:
        from core.version_check import _version_lt

        assert _version_lt("4.23.0", "4.24.0") is True
        assert _version_lt("4.25.0", "4.24.0") is False

    def test_patch_version_comparison(self) -> None:
        from core.version_check import _version_lt

        assert _version_lt("4.24.0", "4.24.2") is True
        assert _version_lt("4.24.5", "4.24.2") is False


class TestDetectAstrbotVersion:
    """测试 _detect_astrbot_version()."""

    def test_returns_none_when_package_not_found(self) -> None:
        """当 importlib_metadata can't find the package."""
        import importlib.metadata

        with patch.object(
            importlib.metadata,
            "version",
            side_effect=importlib.metadata.PackageNotFoundError,
        ):
            # Reload the module to re-run _detect_astrbot_version()
            import importlib
            import core.version_check

            importlib.reload(core.version_check)
            assert core.version_check._detect_astrbot_version() is None

    def test_returns_version_when_package_found(self) -> None:
        """当 importlib_metadata finds the package."""
        import importlib.metadata

        with patch.object(
            importlib.metadata,
            "version",
            return_value="4.24.2",
        ):
            import importlib
            import core.version_check

            importlib.reload(core.version_check)
            assert core.version_check._detect_astrbot_version() == "4.24.2"


class TestModuleConstants:
    """测试 module-level constants."""

    def test_min_version_is_defined(self) -> None:
        from core.version_check import _MIN_ASTRBOT_VERSION

        assert isinstance(_MIN_ASTRBOT_VERSION, str)
        assert _parse_version_safe(_MIN_ASTRBOT_VERSION) != ()

    def test_current_version_is_str_or_none(self) -> None:
        from core.version_check import _CURRENT_ASTRBOT_VERSION

        assert _CURRENT_ASTRBOT_VERSION is None or isinstance(
            _CURRENT_ASTRBOT_VERSION, str
        )


def _parse_version_safe(v: str) -> tuple:
    from core.version_check import _parse_version

    return _parse_version(v)


# ============================================================================
# core/plugin_initializer.py
# ============================================================================


class TestPluginInitializerConstruction:
    """测试 PluginInitializer.__init__ 与属性默认值。"""

    def test_initial_state_not_initialized(self) -> None:
        from core.plugin_initializer import PluginInitializer

        init = PluginInitializer(
            context=MagicMock(),
            config_manager=MagicMock(),
            data_dir="/tmp/test",
        )
        assert init.is_initialized is False
        assert init.is_failed is False
        assert init.error_message is None

    def test_all_components_initially_none(self) -> None:
        from core.plugin_initializer import PluginInitializer

        init = PluginInitializer(
            context=MagicMock(),
            config_manager=MagicMock(),
            data_dir="/tmp/test",
        )
        assert init.embedding_provider is None
        assert init.llm_provider is None
        assert init.db is None
        assert init.graph_db is None
        assert init.memory_engine is None
        assert init.memory_processor is None
        assert init.conversation_manager is None
        assert init.index_validator is None
        assert init.decay_scheduler is None
        assert init.backfill_scheduler is None

    def test_sub_modules_created_on_init(self) -> None:
        from core.plugin_initializer import PluginInitializer

        init = PluginInitializer(
            context=MagicMock(),
            config_manager=MagicMock(),
            data_dir="/tmp/test",
        )
        assert init._provider_loader is not None
        assert init._provider_waiter is not None
        assert init._faiss_checker is not None
        assert init._db_setup is not None
        assert init._component_factory is not None

    def test_ensure_initialized_returns_false_when_not_initialized(self) -> None:
        from core.plugin_initializer import PluginInitializer

        init = PluginInitializer(
            context=MagicMock(),
            config_manager=MagicMock(),
            data_dir="/tmp/test",
        )
        import asyncio

        result = asyncio.run(init.ensure_initialized(timeout=0.5))
        assert result is False

    def test_ensure_initialized_returns_false_when_failed(self) -> None:
        from core.plugin_initializer import PluginInitializer

        init = PluginInitializer(
            context=MagicMock(),
            config_manager=MagicMock(),
            data_dir="/tmp/test",
        )
        init._initialization_failed = True
        import asyncio

        result = asyncio.run(init.ensure_initialized(timeout=0.5))
        assert result is False

    def test_stop_scheduler_with_none_scheduler(self) -> None:
        """stop_scheduler should be a no-op when decay_scheduler is None."""
        from core.plugin_initializer import PluginInitializer

        init = PluginInitializer(
            context=MagicMock(),
            config_manager=MagicMock(),
            data_dir="/tmp/test",
        )
        import asyncio

        # Should not raise
        asyncio.run(init.stop_scheduler())

    def test_stop_scheduler_with_active_scheduler(self) -> None:
        from core.plugin_initializer import PluginInitializer

        init = PluginInitializer(
            context=MagicMock(),
            config_manager=MagicMock(),
            data_dir="/tmp/test",
        )
        mock_scheduler = AsyncMock()
        init.decay_scheduler = mock_scheduler
        import asyncio

        asyncio.run(init.stop_scheduler())
        mock_scheduler.stop.assert_awaited_once()
        assert init.decay_scheduler is None

    def test_stop_background_tasks(self) -> None:
        from core.plugin_initializer import PluginInitializer

        init = PluginInitializer(
            context=MagicMock(),
            config_manager=MagicMock(),
            data_dir="/tmp/test",
        )
        init._provider_waiter = MagicMock()
        init._provider_waiter.cancel = AsyncMock()
        import asyncio

        asyncio.run(init.stop_background_tasks())
        init._provider_waiter.cancel.assert_awaited_once()


class TestComponentFactoryConfig:
    """测试 ComponentFactory 引擎配置构建。"""

    def test_engine_config_includes_data_dir(self, tmp_path) -> None:
        from core.initializer.component_factory import ComponentFactory

        config = MagicMock()
        config.get.side_effect = lambda _key, default=None: default
        factory = ComponentFactory(
            context=MagicMock(),
            config_manager=config,
            data_dir=str(tmp_path),
        )

        engine_config = factory._build_engine_config(
            stopwords_dir=tmp_path / "stopwords",
            graph_memory_enabled=True,
        )

        assert engine_config["data_dir"] == str(tmp_path)


class TestMemoraPluginConfig:
    """测试 MemoraPlugin 对 AstrBot 注入配置的持有关系。"""

    def test_keeps_injected_astrbot_config_as_manager_source(self) -> None:
        MemoraPlugin = _load_memora_plugin_class()
        astrbot_config = {"bot_language": "zh"}

        with patch.object(
            MemoraPlugin, "_register_official_page_api_if_available"
        ), patch.object(
            MemoraPlugin,
            "_create_tracked_task",
            side_effect=lambda coro: coro.close(),
        ):
            plugin = MemoraPlugin(MagicMock(), astrbot_config)

        assert plugin.astrbot_config is astrbot_config
        assert plugin.config_manager._source_config is astrbot_config

    def test_assigns_unique_instance_id_to_each_plugin_instance(self) -> None:
        MemoraPlugin = _load_memora_plugin_class()

        with patch.object(
            MemoraPlugin, "_register_official_page_api_if_available"
        ), patch.object(
            MemoraPlugin,
            "_create_tracked_task",
            side_effect=lambda coro: coro.close(),
        ):
            first = MemoraPlugin(MagicMock(), {})
            second = MemoraPlugin(MagicMock(), {})

        assert len(first.instance_id) == 32
        assert len(second.instance_id) == 32
        assert first.instance_id != second.instance_id


class TestMemoraPluginReloadScheduling:
    """配置应用后的插件重载必须延迟执行且不进入常规任务集合。"""

    @staticmethod
    def _make_plugin(context: MagicMock):
        MemoraPlugin = _load_memora_plugin_class()
        with patch.object(
            MemoraPlugin, "_register_official_page_api_if_available"
        ), patch.object(
            MemoraPlugin,
            "_create_tracked_task",
            side_effect=lambda coro: coro.close(),
        ):
            plugin = MemoraPlugin(context, {})
        return plugin

    def test_reports_false_when_star_manager_reload_is_missing(self) -> None:
        context = MagicMock()
        context._star_manager = types.SimpleNamespace()
        plugin = self._make_plugin(context)

        assert plugin.supports_plugin_reload() is False
        assert plugin.schedule_plugin_reload() is False
        assert plugin._background_tasks == set()

    @pytest.mark.asyncio
    async def test_delays_reload_and_uses_memora_plugin_name(self) -> None:
        context = MagicMock()
        reload_called = asyncio.Event()
        sleep_entered = asyncio.Event()
        release_sleep = asyncio.Event()
        reload_names: list[str] = []

        async def reload_plugin(name: str) -> bool:
            reload_names.append(name)
            reload_called.set()
            return True

        async def delayed_sleep(delay: float) -> None:
            assert delay == 0.5
            sleep_entered.set()
            await release_sleep.wait()

        context._star_manager = types.SimpleNamespace(reload=reload_plugin)
        plugin = self._make_plugin(context)
        module = sys.modules[plugin.__class__.__module__]

        with patch.object(module.asyncio, "sleep", side_effect=delayed_sleep):
            assert plugin.schedule_plugin_reload() is True
            await asyncio.wait_for(sleep_entered.wait(), timeout=1.0)
            assert reload_called.is_set() is False
            assert plugin._background_tasks == set()
            release_sleep.set()
            await asyncio.wait_for(reload_called.wait(), timeout=1.0)

        await asyncio.sleep(0)
        assert reload_names == ["astrbot_plugin_memora"]

    @pytest.mark.asyncio
    async def test_skips_reload_when_termination_starts_during_delay(self) -> None:
        context = MagicMock()
        sleep_entered = asyncio.Event()
        release_sleep = asyncio.Event()
        reload_names: list[str] = []
        created_tasks: list[asyncio.Task] = []

        async def reload_plugin(name: str) -> bool:
            reload_names.append(name)
            return True

        async def delayed_sleep(delay: float) -> None:
            assert delay == 0.5
            sleep_entered.set()
            await release_sleep.wait()

        context._star_manager = types.SimpleNamespace(reload=reload_plugin)
        plugin = self._make_plugin(context)
        module = sys.modules[plugin.__class__.__module__]
        create_task = asyncio.create_task

        def capture_task(coro) -> asyncio.Task:
            task = create_task(coro)
            created_tasks.append(task)
            return task

        with patch.object(
            module.asyncio, "sleep", side_effect=delayed_sleep
        ), patch.object(module.asyncio, "create_task", side_effect=capture_task):
            assert plugin.schedule_plugin_reload() is True
            await asyncio.wait_for(sleep_entered.wait(), timeout=1.0)
            plugin._terminating = True
            release_sleep.set()
            await asyncio.wait_for(created_tasks[0], timeout=1.0)

        assert reload_names == []
        assert created_tasks[0].exception() is None
        assert plugin._background_tasks == set()

    @pytest.mark.asyncio
    async def test_logs_false_reload_result(self) -> None:
        context = MagicMock()
        reload_called = asyncio.Event()

        async def reload_plugin(_name: str) -> bool:
            reload_called.set()
            return False

        async def no_delay(_delay: float) -> None:
            return None

        context._star_manager = types.SimpleNamespace(reload=reload_plugin)
        plugin = self._make_plugin(context)
        module = sys.modules[plugin.__class__.__module__]

        with patch.object(module.asyncio, "sleep", side_effect=no_delay), patch.object(
            module.logger, "warning"
        ) as warning:
            assert plugin.schedule_plugin_reload() is True
            await asyncio.wait_for(reload_called.wait(), timeout=1.0)
            await asyncio.sleep(0)

        assert any("重载" in str(call) for call in warning.call_args_list)

    @pytest.mark.asyncio
    async def test_logs_reload_exception(self) -> None:
        context = MagicMock()
        reload_called = asyncio.Event()
        error_logged = asyncio.Event()

        async def reload_plugin(_name: str) -> bool:
            reload_called.set()
            raise RuntimeError("reload failed")

        async def no_delay(_delay: float) -> None:
            return None

        context._star_manager = types.SimpleNamespace(reload=reload_plugin)
        plugin = self._make_plugin(context)
        module = sys.modules[plugin.__class__.__module__]

        def record_error(*_args, **_kwargs) -> None:
            error_logged.set()

        with patch.object(module.asyncio, "sleep", side_effect=no_delay), patch.object(
            module.logger, "error", side_effect=record_error
        ) as error:
            assert plugin.schedule_plugin_reload() is True
            await asyncio.wait_for(reload_called.wait(), timeout=1.0)
            await asyncio.wait_for(error_logged.wait(), timeout=1.0)

        assert any("重载" in str(call) for call in error.call_args_list)


class TestMemoraPluginTerminate:
    """测试 MemoraPlugin.terminate 生命周期清理。"""

    @pytest.mark.asyncio
    async def test_terminate_cancels_tracked_background_tasks(self) -> None:
        MemoraPlugin = _load_memora_plugin_class()

        with patch.object(
            MemoraPlugin, "_register_official_page_api_if_available"
        ), patch.object(
            MemoraPlugin,
            "_create_tracked_task",
            side_effect=lambda coro: coro.close(),
        ):
            plugin = MemoraPlugin(MagicMock(), {})

        plugin.initializer.stop_background_tasks = AsyncMock()
        plugin.initializer.stop_scheduler = AsyncMock()
        plugin.initializer.close_extension_components = AsyncMock()
        plugin.initializer.conversation_manager = None
        plugin.initializer.memory_engine = None
        plugin.initializer.db = None
        plugin._perf_tracker = MagicMock()
        plugin._perf_tracker.get_perf_data.return_value = {}
        plugin._backfill_scheduler = None

        async def _blocked() -> None:
            await asyncio.Future()

        task = asyncio.create_task(_blocked())
        plugin._background_tasks = {task}

        await plugin.terminate()

        assert task.cancelled() is True
        assert plugin._background_tasks == set()

    @pytest.mark.asyncio
    async def test_terminate_stops_backfill_scheduler(self) -> None:
        MemoraPlugin = _load_memora_plugin_class()

        with patch.object(
            MemoraPlugin, "_register_official_page_api_if_available"
        ), patch.object(
            MemoraPlugin,
            "_create_tracked_task",
            side_effect=lambda coro: coro.close(),
        ):
            plugin = MemoraPlugin(MagicMock(), {})

        plugin.initializer.stop_background_tasks = AsyncMock()
        plugin.initializer.stop_scheduler = AsyncMock()
        plugin.initializer.close_extension_components = AsyncMock()
        plugin.initializer.conversation_manager = None
        plugin.initializer.memory_engine = None
        plugin.initializer.db = None
        plugin._perf_tracker = MagicMock()
        plugin._perf_tracker.get_perf_data.return_value = {}
        plugin._backfill_scheduler = MagicMock()
        plugin._backfill_scheduler.stop = AsyncMock()

        await plugin.terminate()

        plugin._backfill_scheduler.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_terminate_closes_event_handler_and_core_components(self) -> None:
        MemoraPlugin = _load_memora_plugin_class()

        with patch.object(
            MemoraPlugin, "_register_official_page_api_if_available"
        ), patch.object(
            MemoraPlugin,
            "_create_tracked_task",
            side_effect=lambda coro: coro.close(),
        ):
            plugin = MemoraPlugin(MagicMock(), {})

        plugin.initializer.stop_background_tasks = AsyncMock()
        plugin.initializer.stop_scheduler = AsyncMock()
        plugin.initializer.close_extension_components = AsyncMock()
        plugin.event_handler = MagicMock()
        plugin.event_handler.shutdown = AsyncMock()
        plugin.initializer.conversation_manager = MagicMock()
        plugin.initializer.conversation_manager.store = MagicMock()
        plugin.initializer.conversation_manager.store.close = AsyncMock()
        plugin.initializer.memory_engine = MagicMock()
        plugin.initializer.memory_engine.close = AsyncMock()
        plugin.initializer.db = MagicMock()
        plugin.initializer.db.close = AsyncMock()
        plugin._perf_tracker = MagicMock()
        plugin._perf_tracker.get_perf_data.return_value = {}
        plugin._backfill_scheduler = None

        await plugin.terminate()

        plugin.event_handler.shutdown.assert_awaited_once()
        plugin.initializer.conversation_manager.store.close.assert_awaited_once()
        plugin.initializer.memory_engine.close.assert_awaited_once()
        plugin.initializer.db.close.assert_awaited_once()


class TestMemoraPluginReady:
    """测试 MemoraPlugin._ensure_plugin_ready 生命周期行为。"""

    @pytest.mark.asyncio
    async def test_ensure_plugin_ready_initializes_runtime_components_on_first_call(
        self,
    ) -> None:
        MemoraPlugin = _load_memora_plugin_class()

        with patch.object(
            MemoraPlugin, "_register_official_page_api_if_available"
        ), patch.object(
            MemoraPlugin,
            "_create_tracked_task",
            side_effect=lambda coro: coro.close(),
        ):
            plugin = MemoraPlugin(MagicMock(), {})

        plugin.feature_delegation = MagicMock()
        plugin.feature_delegation.should_delegate_jargon.return_value = False
        plugin.feature_delegation.should_delegate_affection.return_value = False
        plugin.feature_delegation.should_skip_persona_processing.return_value = False
        plugin.feature_delegation.should_delegate_expression.return_value = False
        plugin.context = MagicMock()
        plugin.context.add_llm_tools = MagicMock()

        config_values = {
            "agent_tools.enable_recall_tool": False,
            "agent_tools.enable_memorize_tool": False,
            "agent_tools.enable_note_tools": False,
            "agent_tools.enable_knowledge_tools": False,
            "agent_tools.enable_profile_tools": False,
            "agent_tools.enable_jargon_tools": False,
            "agent_tools.enable_affection_tools": False,
            "agent_tools.enable_social_tools": False,
            "agent_tools.enable_expression_tools": False,
        }
        plugin.config_manager = MagicMock()
        plugin.config_manager.get.side_effect = lambda key, default=None: config_values.get(
            key, default
        )

        plugin.initializer.ensure_initialized = AsyncMock(return_value=True)
        plugin.initializer._initialization_complete = True
        plugin.initializer.memory_engine = MagicMock()
        plugin.initializer.memory_processor = MagicMock()
        plugin.initializer.conversation_manager = MagicMock()
        plugin.initializer.index_validator = MagicMock()
        plugin.initializer.backfill_scheduler = MagicMock()
        plugin.initializer.jargon_filter = None
        plugin.initializer.jargon_miner = None
        plugin.initializer.jargon_query_service = None
        plugin.initializer.affection_manager = None
        plugin.initializer.expression_learner = None
        plugin.initializer.relation_manager = None
        plugin.initializer.prompt_protection = None

        ready, message = await plugin._ensure_plugin_ready()

        assert ready is True
        assert message == ""
        assert plugin.event_handler is not None
        assert plugin.event_handler._memory_tool_available is False
        assert plugin.command_handler is not None
        assert plugin._backfill_scheduler is plugin.initializer.backfill_scheduler

    @pytest.mark.asyncio
    async def test_ensure_plugin_ready_registers_agent_tools_on_first_call(self) -> None:
        MemoraPlugin = _load_memora_plugin_class()

        with patch.object(
            MemoraPlugin, "_register_official_page_api_if_available"
        ), patch.object(
            MemoraPlugin,
            "_create_tracked_task",
            side_effect=lambda coro: coro.close(),
        ):
            plugin = MemoraPlugin(MagicMock(), {})

        plugin.feature_delegation = MagicMock()
        plugin.feature_delegation.should_delegate_jargon.return_value = False
        plugin.feature_delegation.should_delegate_affection.return_value = False
        plugin.feature_delegation.should_skip_persona_processing.return_value = False
        plugin.feature_delegation.should_delegate_expression.return_value = False
        plugin.context = MagicMock()
        plugin.context.add_llm_tools = MagicMock()

        config_values = {
            "agent_tools.enable_recall_tool": True,
            "agent_tools.enable_memorize_tool": False,
            "agent_tools.enable_note_tools": False,
            "agent_tools.enable_knowledge_tools": False,
            "agent_tools.enable_profile_tools": False,
            "agent_tools.enable_jargon_tools": False,
            "agent_tools.enable_affection_tools": False,
            "agent_tools.enable_social_tools": False,
            "agent_tools.enable_expression_tools": False,
        }
        plugin.config_manager = MagicMock()
        plugin.config_manager.get.side_effect = lambda key, default=None: config_values.get(
            key, default
        )

        plugin.initializer.ensure_initialized = AsyncMock(return_value=True)
        plugin.initializer._initialization_complete = True
        plugin.initializer.memory_engine = MagicMock()
        plugin.initializer.memory_processor = MagicMock()
        plugin.initializer.conversation_manager = MagicMock()
        plugin.initializer.index_validator = MagicMock()
        plugin.initializer.backfill_scheduler = None
        plugin.initializer.jargon_filter = None
        plugin.initializer.jargon_miner = None
        plugin.initializer.jargon_query_service = None
        plugin.initializer.affection_manager = None
        plugin.initializer.expression_learner = None
        plugin.initializer.relation_manager = None
        plugin.initializer.prompt_protection = None

        fake_tool = MagicMock(name="memory-search-tool")
        module = sys.modules[MemoraPlugin.__module__]

        with patch.object(module, "MemorySearchTool", return_value=fake_tool):
            ready, message = await plugin._ensure_plugin_ready()

        assert ready is True
        assert message == ""
        plugin.context.add_llm_tools.assert_called_once_with(fake_tool)
        assert plugin._llm_tools_registered is True


class TestInjectionDecisionLifecycle:
    @pytest.mark.asyncio
    async def test_component_factory_builds_started_injection_recorder(
        self, monkeypatch, tmp_path
    ) -> None:
        from core.initializer.component_factory import ComponentFactory

        store = MagicMock()
        store.initialize = AsyncMock()
        store.close = AsyncMock()
        recorder = MagicMock()
        recorder.start = AsyncMock()
        recorder.close = AsyncMock()
        store_type = MagicMock(return_value=store)
        recorder_type = MagicMock(return_value=recorder)
        monkeypatch.setattr(
            "core.initializer.component_factory.InjectionDecisionStore", store_type
        )
        monkeypatch.setattr(
            "core.initializer.component_factory.InjectionDecisionRecorder", recorder_type
        )
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "recall_engine.injection_decision_retention_days": 17,
            "recall_engine.injection_decision_max_rows": 321,
        }.get(key, default)
        factory = ComponentFactory(MagicMock(), config, str(tmp_path))

        components = await factory._build_injection_components(tmp_path / "memora.db")

        store_type.assert_called_once_with(tmp_path / "memora.db")
        store.initialize.assert_awaited_once()
        recorder_type.assert_called_once_with(store, retention_days=17, max_rows=321)
        recorder.start.assert_awaited_once()
        recorder.schedule_cleanup.assert_called_once_with()
        assert components == {
            "injection_decision_store": store,
            "injection_decision_recorder": recorder,
        }

    @pytest.mark.asyncio
    async def test_component_factory_closes_partial_injection_components_on_failure(
        self, monkeypatch, tmp_path
    ) -> None:
        from core.initializer.component_factory import ComponentFactory

        order: list[str] = []
        store = MagicMock()
        store.initialize = AsyncMock()
        store.close = AsyncMock(side_effect=lambda: order.append("store"))
        recorder = MagicMock()
        recorder.start = AsyncMock(side_effect=RuntimeError("start failed"))
        recorder.close = AsyncMock(side_effect=lambda **_kwargs: order.append("recorder"))
        monkeypatch.setattr(
            "core.initializer.component_factory.InjectionDecisionStore",
            MagicMock(return_value=store),
        )
        monkeypatch.setattr(
            "core.initializer.component_factory.InjectionDecisionRecorder",
            MagicMock(return_value=recorder),
        )
        config = MagicMock()
        config.get.side_effect = lambda _key, default=None: default
        factory = ComponentFactory(MagicMock(), config, str(tmp_path))

        with pytest.raises(RuntimeError, match="start failed"):
            await factory._build_injection_components(tmp_path / "memora.db")

        assert order == ["recorder", "store"]
        recorder.close.assert_awaited_once_with(timeout=5.0)

    @pytest.mark.asyncio
    async def test_build_all_awaits_and_merges_injection_components(
        self, monkeypatch, tmp_path
    ) -> None:
        from astrbot.core.provider.provider import Provider
        from core.initializer.component_factory import ComponentFactory

        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "graph_memory.enabled": False,
            "importance_decay.decay_rate": 0,
            "forgetting_agent.auto_cleanup_enabled": False,
        }.get(key, default)
        config.session_manager = {}
        factory = ComponentFactory(MagicMock(), config, str(tmp_path))
        injection_components = {
            "injection_decision_store": object(),
            "injection_decision_recorder": object(),
        }
        factory._build_injection_components = AsyncMock(
            return_value=injection_components
        )
        db = MagicMock()
        db.initialize = AsyncMock()
        db_type = MagicMock(return_value=db)
        engine = MagicMock()
        engine.initialize = AsyncMock()
        engine.text_processor = None
        monkeypatch.setattr(
            "core.initializer.component_factory.MemoryEngine",
            MagicMock(return_value=engine),
        )
        conversation_store = MagicMock()
        conversation_store.initialize = AsyncMock()
        monkeypatch.setattr(
            "core.initializer.component_factory.ConversationStore",
            MagicMock(return_value=conversation_store),
        )
        faiss_checker = MagicMock()
        faiss_checker.check_and_fix_dimension_mismatch = AsyncMock()
        db_setup = MagicMock()
        db_setup.repair_message_counts = AsyncMock()
        db_setup.auto_rebuild_index_if_needed = AsyncMock()

        components = await factory.build_all(
            MagicMock(), MagicMock(spec=Provider), db_type, faiss_checker, db_setup
        )

        factory._build_injection_components.assert_awaited_once_with(
            tmp_path / "memora.db"
        )
        assert components["injection_decision_store"] is injection_components[
            "injection_decision_store"
        ]
        assert components["injection_decision_recorder"] is injection_components[
            "injection_decision_recorder"
        ]

    @pytest.mark.asyncio
    async def test_plugin_initializer_retains_and_closes_injection_components_once(
        self, tmp_path
    ) -> None:
        from core.plugin_initializer import PluginInitializer

        initializer = PluginInitializer(MagicMock(), MagicMock(), str(tmp_path))
        assert initializer.injection_decision_store is None
        assert initializer.injection_decision_recorder is None
        order: list[str] = []
        recorder = MagicMock()
        recorder.close = AsyncMock(side_effect=lambda **_kwargs: order.append("recorder"))
        store = MagicMock()
        store.close = AsyncMock(side_effect=lambda: order.append("store"))
        initializer.injection_decision_recorder = recorder
        initializer.injection_decision_store = store

        await asyncio.gather(
            initializer.close_injection_components(),
            initializer.close_injection_components(),
        )
        await initializer.close_injection_components()

        assert order == ["recorder", "store"]
        recorder.close.assert_awaited_once_with(timeout=5.0)
        store.close.assert_awaited_once()
        assert initializer.injection_decision_recorder is None
        assert initializer.injection_decision_store is None

    @pytest.mark.asyncio
    async def test_plugin_initializer_propagates_cancellation_after_store_close(
        self, tmp_path
    ) -> None:
        from core.plugin_initializer import PluginInitializer

        initializer = PluginInitializer(MagicMock(), MagicMock(), str(tmp_path))
        recorder = MagicMock()
        recorder.close = AsyncMock(side_effect=asyncio.CancelledError())
        store = MagicMock()
        store.close = AsyncMock()
        initializer.injection_decision_recorder = recorder
        initializer.injection_decision_store = store

        with pytest.raises(asyncio.CancelledError):
            await initializer.close_injection_components()

        store.close.assert_awaited_once()
        assert initializer.injection_decision_recorder is None
        assert initializer.injection_decision_store is None

    @pytest.mark.asyncio
    async def test_run_full_init_retains_injection_components(self, tmp_path) -> None:
        from core.plugin_initializer import PluginInitializer

        initializer = PluginInitializer(MagicMock(), MagicMock(), str(tmp_path))
        initializer._faiss_checker.load_vec_db_class = MagicMock(return_value=MagicMock())
        store = MagicMock()
        recorder = MagicMock()
        memory_processor = MagicMock()
        initializer._component_factory.build_all = AsyncMock(
            return_value={
                "db": MagicMock(),
                "graph_db": None,
                "memory_engine": MagicMock(),
                "memory_processor": memory_processor,
                "conversation_manager": MagicMock(),
                "index_validator": MagicMock(),
                "decay_scheduler": None,
                "injection_decision_store": store,
                "injection_decision_recorder": recorder,
            }
        )
        initializer._create_prompt_protection_service = MagicMock(return_value=MagicMock())
        initializer._initialize_cognitive_components = AsyncMock()

        await initializer._run_full_init()

        assert initializer.injection_decision_store is store
        assert initializer.injection_decision_recorder is recorder


class TestMemoraInjectionLifecycle:
    @pytest.mark.asyncio
    async def test_terminate_closes_injection_components_without_event_handler(
        self,
    ) -> None:
        MemoraPlugin = _load_memora_plugin_class()
        with patch.object(
            MemoraPlugin, "_register_official_page_api_if_available"
        ), patch.object(
            MemoraPlugin, "_create_tracked_task", side_effect=lambda coro: coro.close()
        ):
            plugin = MemoraPlugin(MagicMock(), {})

        plugin.event_handler = None
        plugin.initializer.stop_background_tasks = AsyncMock()
        plugin.initializer.stop_scheduler = AsyncMock()
        plugin.initializer.close_extension_components = AsyncMock()
        plugin.initializer.close_injection_components = AsyncMock()
        plugin.initializer.conversation_manager = None
        plugin.initializer.memory_engine = None
        plugin.initializer.db = None
        plugin._perf_tracker = MagicMock()
        plugin._perf_tracker.get_perf_data.return_value = {}
        plugin._backfill_scheduler = None

        await plugin.terminate()

        plugin.initializer.close_injection_components.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_runtime_registers_tools_before_constructing_event_handler(
        self,
    ) -> None:
        MemoraPlugin = _load_memora_plugin_class()
        with patch.object(
            MemoraPlugin, "_register_official_page_api_if_available"
        ), patch.object(
            MemoraPlugin, "_create_tracked_task", side_effect=lambda coro: coro.close()
        ):
            plugin = MemoraPlugin(MagicMock(), {})

        plugin.initializer._initialization_complete = True
        plugin.initializer.memory_engine = MagicMock()
        plugin.initializer.memory_processor = MagicMock()
        plugin.initializer.conversation_manager = MagicMock()
        plugin.initializer.injection_decision_recorder = MagicMock()
        plugin.config_manager.get = MagicMock(
            side_effect=lambda key, default=None: True
            if key == "agent_tools.enable_recall_tool"
            else default
        )
        plugin._register_agent_tools_if_needed = MagicMock(
            side_effect=lambda: setattr(plugin, "_llm_tools_registered", True)
        )
        module = sys.modules[MemoraPlugin.__module__]
        event_handler = MagicMock()

        def build_event_handler(**kwargs):
            assert plugin._llm_tools_registered is True
            assert kwargs["injection_recorder"] is plugin.initializer.injection_decision_recorder
            assert kwargs["memory_tool_available"] is True
            return event_handler

        with patch.object(module, "EventHandler", side_effect=build_event_handler):
            await plugin._ensure_runtime_components()

        plugin._register_agent_tools_if_needed.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_runtime_does_not_construct_handler_when_tool_registration_fails(
        self,
    ) -> None:
        MemoraPlugin = _load_memora_plugin_class()
        with patch.object(
            MemoraPlugin, "_register_official_page_api_if_available"
        ), patch.object(
            MemoraPlugin, "_create_tracked_task", side_effect=lambda coro: coro.close()
        ):
            plugin = MemoraPlugin(MagicMock(), {})

        plugin.initializer._initialization_complete = True
        plugin.initializer.memory_engine = MagicMock()
        plugin.initializer.memory_processor = MagicMock()
        plugin.initializer.conversation_manager = MagicMock()
        plugin._llm_tools_registered = False
        plugin._register_agent_tools_if_needed = MagicMock(
            side_effect=RuntimeError("registration failed")
        )
        module = sys.modules[MemoraPlugin.__module__]

        with patch.object(module, "EventHandler") as event_handler_type:
            with pytest.raises(RuntimeError, match="registration failed"):
                await plugin._ensure_runtime_components()

        assert plugin._llm_tools_registered is False
        event_handler_type.assert_not_called()
