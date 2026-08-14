"""测试插件初始化与版本检查模块。

覆盖范围：
- core/platform/composition/plugin_initializer.py — PluginInitializer；
- core/version_check.py — 版本解析与比较。
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
    """在隔离包名下加载插件类并安装所需 AstrBot 测试桩。"""

    root = Path(__file__).resolve().parents[1]
    package_name = "memora_testpkg"
    package = types.ModuleType(package_name)
    package.__path__ = [str(root)]
    sys.modules[package_name] = package
    star_mod = sys.modules["astrbot.api.star"]
    setattr(
        star_mod,
        "Star",
        type(
            "TestStar",
            (object,),
            {"__init__": lambda self, context=None: None},
        ),
    )
    temp_data_dir = root / ".pytest_memora_data"
    temp_data_dir.mkdir(exist_ok=True)
    star_mod.StarTools = types.SimpleNamespace(get_data_dir=lambda: temp_data_dir)  # type: ignore[attr-defined]
    star_mod.register = lambda *args, **kwargs: lambda cls: cls  # type: ignore[attr-defined]
    event_mod = sys.modules["astrbot.api.event"]
    event_mod.filter.platform_adapter_type.side_effect = lambda *args, **kwargs: (
        lambda fn: fn
    )  # type: ignore[attr-defined]
    event_mod.filter.on_llm_request.side_effect = lambda *args, **kwargs: lambda fn: fn  # type: ignore[attr-defined]
    event_mod.filter.on_llm_response.side_effect = lambda *args, **kwargs: lambda fn: fn  # type: ignore[attr-defined]
    event_mod.filter.after_message_sent.side_effect = lambda *args, **kwargs: (
        lambda fn: fn
    )  # type: ignore[attr-defined]

    class _CommandGroup:
        def __call__(self, fn):
            return self

        def command(self, *args, **kwargs):
            return lambda fn: fn

    event_mod.filter.command_group.side_effect = lambda *args, **kwargs: _CommandGroup()  # type: ignore[attr-defined]

    filter_submodule = types.ModuleType("astrbot.api.event.filter")
    setattr(filter_submodule, "PermissionType", types.SimpleNamespace(ADMIN="admin"))
    setattr(
        filter_submodule,
        "permission_type",
        lambda *args, **kwargs: lambda fn: fn,
    )
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
        from core.platform.version_check import _parse_version

        assert _parse_version("4.24.2") == (4, 24, 2)

    def test_version_with_v_prefix(self) -> None:
        from core.platform.version_check import _parse_version

        assert _parse_version("v4.24.2") == (4, 24, 2)

    def test_version_with_whitespace(self) -> None:
        from core.platform.version_check import _parse_version

        assert _parse_version("  4.24.2  ") == (4, 24, 2)

    def test_empty_string_returns_empty_tuple(self) -> None:
        from core.platform.version_check import _parse_version

        assert _parse_version("") == ()

    def test_nonsense_string_returns_empty_tuple(self) -> None:
        from core.platform.version_check import _parse_version

        assert _parse_version("not-a-version") == ()

    def test_single_component(self) -> None:
        from core.platform.version_check import _parse_version

        assert _parse_version("1") == (1,)

    def test_two_component_version(self) -> None:
        from core.platform.version_check import _parse_version

        assert _parse_version("2.4") == (2, 4)

    def test_multi_digit_components(self) -> None:
        from core.platform.version_check import _parse_version

        assert _parse_version("10.100.1000") == (10, 100, 1000)


class TestVersionLt:
    """测试 _version_lt()."""

    def test_lower_version_is_less(self) -> None:
        from core.platform.version_check import _version_lt

        assert _version_lt("4.0.0", "4.24.2") is True

    def test_equal_version_is_not_less(self) -> None:
        from core.platform.version_check import _version_lt

        assert _version_lt("4.24.2", "4.24.2") is False

    def test_higher_version_is_not_less(self) -> None:
        from core.platform.version_check import _version_lt

        assert _version_lt("5.0.0", "4.24.2") is False

    def test_invalid_current_returns_false(self) -> None:
        from core.platform.version_check import _version_lt

        assert _version_lt("invalid", "4.24.2") is False

    def test_invalid_minimum_returns_false(self) -> None:
        from core.platform.version_check import _version_lt

        assert _version_lt("4.24.2", "invalid") is False

    def test_different_width_versions(self) -> None:
        from core.platform.version_check import _version_lt

        # 单独的 "4" 按 (4, 0, 0) 与 (4, 0, 1) 比较。
        assert _version_lt("4", "4.0.1") is True
        assert _version_lt("4.0.1", "4") is False

    def test_with_v_prefix(self) -> None:
        from core.platform.version_check import _version_lt

        assert _version_lt("v4.0.0", "v4.24.2") is True

    def test_minor_version_comparison(self) -> None:
        from core.platform.version_check import _version_lt

        assert _version_lt("4.23.0", "4.24.0") is True
        assert _version_lt("4.25.0", "4.24.0") is False

    def test_patch_version_comparison(self) -> None:
        from core.platform.version_check import _version_lt

        assert _version_lt("4.24.0", "4.24.2") is True
        assert _version_lt("4.24.5", "4.24.2") is False


class TestDetectAstrbotVersion:
    """测试 _detect_astrbot_version()."""

    def test_returns_none_when_package_not_found(self) -> None:
        """验证 importlib_metadata 找不到包时的结果。"""
        import importlib.metadata

        with patch.object(
            importlib.metadata,
            "version",
            side_effect=importlib.metadata.PackageNotFoundError,
        ):
            # 重新加载模块以再次执行 _detect_astrbot_version()。
            import importlib

            import core.platform.version_check

            importlib.reload(core.platform.version_check)
            assert core.platform.version_check._detect_astrbot_version() is None

    def test_returns_version_when_package_found(self) -> None:
        """验证 importlib_metadata 找到包时的结果。"""
        import importlib.metadata

        with patch.object(
            importlib.metadata,
            "version",
            return_value="4.24.2",
        ):
            import importlib

            import core.platform.version_check

            importlib.reload(core.platform.version_check)
            assert core.platform.version_check._detect_astrbot_version() == "4.24.2"


class TestModuleConstants:
    """测试模块级常量。"""

    def test_min_version_is_defined(self) -> None:
        from core.platform.version_check import _MIN_ASTRBOT_VERSION

        assert isinstance(_MIN_ASTRBOT_VERSION, str)
        assert _parse_version_safe(_MIN_ASTRBOT_VERSION) != ()

    def test_current_version_is_str_or_none(self) -> None:
        from core.platform.version_check import _CURRENT_ASTRBOT_VERSION

        assert _CURRENT_ASTRBOT_VERSION is None or isinstance(
            _CURRENT_ASTRBOT_VERSION, str
        )


def _parse_version_safe(v: str) -> tuple:
    from core.platform.version_check import _parse_version

    return _parse_version(v)


# ============================================================================
# core/platform/composition/plugin_initializer.py
# ============================================================================


class TestPluginInitializerConstruction:
    """测试 PluginInitializer.__init__ 与属性默认值。"""

    def test_initial_state_not_initialized(self) -> None:
        from core.platform.composition.plugin_initializer import PluginInitializer

        init = PluginInitializer(
            context=MagicMock(),
            config_manager=MagicMock(),
            data_dir="/tmp/test",
        )
        assert init.is_initialized is False
        assert init.is_failed is False
        assert init.error_message is None

    def test_all_components_initially_none(self) -> None:
        from core.platform.composition.plugin_initializer import PluginInitializer

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
        assert init.memory_quarantine_store is None
        assert init.memory_quality_gate is None
        assert init.conversation_manager is None
        assert init.index_validator is None
        assert init.decay_scheduler is None
        assert init.backfill_scheduler is None

    def test_sub_modules_created_on_init(self) -> None:
        from core.platform.composition.plugin_initializer import PluginInitializer

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
        from core.platform.composition.plugin_initializer import PluginInitializer

        init = PluginInitializer(
            context=MagicMock(),
            config_manager=MagicMock(),
            data_dir="/tmp/test",
        )
        import asyncio

        result = asyncio.run(init.ensure_initialized(timeout=0.5))
        assert result is False

    def test_ensure_initialized_returns_false_when_failed(self) -> None:
        from core.platform.composition.plugin_initializer import PluginInitializer

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
        """衰减调度器为空时，stop_scheduler 应直接返回。"""
        from core.platform.composition.plugin_initializer import PluginInitializer

        init = PluginInitializer(
            context=MagicMock(),
            config_manager=MagicMock(),
            data_dir="/tmp/test",
        )
        import asyncio

        # 不应抛出异常。
        asyncio.run(init.stop_scheduler())

    def test_stop_scheduler_with_active_scheduler(self) -> None:
        from core.platform.composition.plugin_initializer import PluginInitializer

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
        from core.platform.composition.plugin_initializer import PluginInitializer

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

    @pytest.mark.asyncio
    async def test_initialize_reports_provider_wait_start_and_duration(self) -> None:
        """Provider 等待应输出开始、结果、尝试次数和耗时。"""
        from core.platform.composition.plugin_initializer import PluginInitializer

        initializer = PluginInitializer(MagicMock(), MagicMock(), ".")
        initializer._provider_waiter.wait_non_blocking = AsyncMock(
            return_value=(MagicMock(), MagicMock(), True)
        )
        initializer._run_full_init = AsyncMock()

        with patch(
            "core.platform.composition.plugin_initializer.report_debug_event"
        ) as report:
            assert await initializer.initialize() is True

        provider_events = [
            call.kwargs
            for call in report.call_args_list
            if call.args == ("provider_state",)
        ]
        assert [event["status"] for event in provider_events] == [
            "started",
            "completed",
        ]
        assert all(event["stage"] == "provider_wait" for event in provider_events)
        assert provider_events[-1]["attempt_count"] >= 0
        assert provider_events[-1]["duration_ms"] >= 0


class TestComponentFactoryConfig:
    """测试 ComponentFactory 引擎配置构建。"""

    def test_engine_config_includes_data_dir(self, tmp_path) -> None:
        from core.platform.composition.component_factory import ComponentFactory

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

        with (
            patch.object(MemoraPlugin, "_register_official_page_api_if_available"),
            patch.object(
                MemoraPlugin,
                "_create_tracked_task",
                side_effect=lambda coro: coro.close(),
            ),
        ):
            plugin = MemoraPlugin(MagicMock(), astrbot_config)

        assert plugin.astrbot_config is astrbot_config
        assert plugin.config_manager._source_config is astrbot_config

    def test_assigns_unique_instance_id_to_each_plugin_instance(self) -> None:
        MemoraPlugin = _load_memora_plugin_class()

        with (
            patch.object(MemoraPlugin, "_register_official_page_api_if_available"),
            patch.object(
                MemoraPlugin,
                "_create_tracked_task",
                side_effect=lambda coro: coro.close(),
            ),
        ):
            first = MemoraPlugin(MagicMock(), {})
            second = MemoraPlugin(MagicMock(), {})

        assert len(first.instance_id) == 32
        assert len(second.instance_id) == 32
        assert first.instance_id != second.instance_id

    def test_configures_debug_reporting_with_configured_data_dir(self) -> None:
        """调试开关沿用配置值，并传递 AstrBot 数据目录与时区。"""
        MemoraPlugin = _load_memora_plugin_class()
        module = sys.modules[MemoraPlugin.__module__]
        astrbot_config = {"debug": True}
        context = MagicMock()
        context.get_config.return_value = {"timezone": "Asia/Shanghai"}

        with (
            patch.object(MemoraPlugin, "_register_official_page_api_if_available"),
            patch.object(
                MemoraPlugin,
                "_create_tracked_task",
                side_effect=lambda coro: coro.close(),
            ),
            patch.object(module.observability, "set_debug_mode") as set_debug_mode,
        ):
            MemoraPlugin(context, astrbot_config)

        set_debug_mode.assert_called_once_with(
            True,
            data_dir=str(Path(__file__).resolve().parents[1] / ".pytest_memora_data"),
            timezone_name="Asia/Shanghai",
        )


class TestMemoraPluginReloadScheduling:
    """配置应用后的插件重载必须延迟执行且不进入常规任务集合。"""

    @staticmethod
    def _make_plugin(context: MagicMock):
        MemoraPlugin = _load_memora_plugin_class()
        with (
            patch.object(MemoraPlugin, "_register_official_page_api_if_available"),
            patch.object(
                MemoraPlugin,
                "_create_tracked_task",
                side_effect=lambda coro: coro.close(),
            ),
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
    async def test_backup_restore_reload_marks_schedule_failure(self) -> None:
        context = MagicMock()
        reload_called = asyncio.Event()

        async def reload_plugin(_name: str) -> bool:
            reload_called.set()
            return False

        context._star_manager = types.SimpleNamespace(reload=reload_plugin)
        plugin = self._make_plugin(context)
        plugin._backup_manager.mark_reload_scheduled = MagicMock()
        module = sys.modules[plugin.__class__.__module__]

        async def no_delay(_delay: float) -> None:
            return None

        with patch.object(module.asyncio, "sleep", side_effect=no_delay):
            assert plugin.schedule_backup_restore_reload("operation-1234") is True
            await asyncio.wait_for(reload_called.wait(), timeout=1.0)
            await asyncio.sleep(0)

        plugin._backup_manager.mark_reload_scheduled.assert_called_once_with(
            "operation-1234", False
        )

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

        with (
            patch.object(module.asyncio, "sleep", side_effect=delayed_sleep),
            patch.object(module.asyncio, "create_task", side_effect=capture_task),
        ):
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

        with (
            patch.object(module.asyncio, "sleep", side_effect=no_delay),
            patch.object(module.logger, "warning") as warning,
        ):
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

        with (
            patch.object(module.asyncio, "sleep", side_effect=no_delay),
            patch.object(module.logger, "error", side_effect=record_error) as error,
        ):
            assert plugin.schedule_plugin_reload() is True
            await asyncio.wait_for(reload_called.wait(), timeout=1.0)
            await asyncio.wait_for(error_logged.wait(), timeout=1.0)

        assert any("重载" in str(call) for call in error.call_args_list)


class TestMemoraPluginTerminate:
    """测试 MemoraPlugin.terminate 生命周期清理。"""

    @pytest.mark.asyncio
    async def test_terminate_cancels_tracked_background_tasks(self) -> None:
        MemoraPlugin = _load_memora_plugin_class()

        with (
            patch.object(MemoraPlugin, "_register_official_page_api_if_available"),
            patch.object(
                MemoraPlugin,
                "_create_tracked_task",
                side_effect=lambda coro: coro.close(),
            ),
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

        with (
            patch.object(MemoraPlugin, "_register_official_page_api_if_available"),
            patch.object(
                MemoraPlugin,
                "_create_tracked_task",
                side_effect=lambda coro: coro.close(),
            ),
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

        with (
            patch.object(MemoraPlugin, "_register_official_page_api_if_available"),
            patch.object(
                MemoraPlugin,
                "_create_tracked_task",
                side_effect=lambda coro: coro.close(),
            ),
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

    @pytest.mark.asyncio
    async def test_terminate_reports_each_shutdown_step_with_duration(self) -> None:
        """关闭报告应区分各固定步骤，并为执行步骤记录耗时。"""
        MemoraPlugin = _load_memora_plugin_class()
        module = sys.modules[MemoraPlugin.__module__]

        with (
            patch.object(MemoraPlugin, "_register_official_page_api_if_available"),
            patch.object(
                MemoraPlugin,
                "_create_tracked_task",
                side_effect=lambda coro: coro.close(),
            ),
        ):
            plugin = MemoraPlugin(MagicMock(), {})

        plugin.initializer.stop_background_tasks = AsyncMock()
        plugin.initializer.close_memory_evolution_components = AsyncMock()
        plugin.initializer.close_injection_components = AsyncMock()
        plugin.initializer.stop_scheduler = AsyncMock()
        plugin.initializer.close_extension_components = AsyncMock()
        plugin.initializer.conversation_manager = None
        plugin.initializer.memory_engine = None
        plugin.initializer.db = None
        plugin._perf_tracker = MagicMock()
        plugin._perf_tracker.get_perf_data.return_value = {}
        plugin._backfill_scheduler = None

        with (
            patch.object(module.observability, "report_debug_event") as report,
            patch.object(module.observability, "report_debug_exception"),
        ):
            await plugin.terminate()

        step_events = [
            call.kwargs
            for call in report.call_args_list
            if call.args == ("shutdown_step",)
        ]
        completed = {
            event["stage"]: event
            for event in step_events
            if event["status"] == "completed"
        }
        assert {
            "provider_waiter",
            "memory_evolution",
            "injection_components",
            "schedulers",
            "cognitive_components",
        }.issubset(completed)
        assert all(event["duration_ms"] >= 0 for event in completed.values())

    @pytest.mark.asyncio
    async def test_terminate_marks_final_status_degraded_when_a_step_fails(
        self,
    ) -> None:
        """任一清理步骤失败时，总关闭结果不能误报为完整成功。"""
        MemoraPlugin = _load_memora_plugin_class()
        module = sys.modules[MemoraPlugin.__module__]

        with (
            patch.object(MemoraPlugin, "_register_official_page_api_if_available"),
            patch.object(
                MemoraPlugin,
                "_create_tracked_task",
                side_effect=lambda coro: coro.close(),
            ),
        ):
            plugin = MemoraPlugin(MagicMock(), {})

        plugin.initializer.stop_background_tasks = AsyncMock(
            side_effect=RuntimeError("关闭失败")
        )
        plugin.initializer.close_memory_evolution_components = AsyncMock()
        plugin.initializer.close_injection_components = AsyncMock()
        plugin.initializer.stop_scheduler = AsyncMock()
        plugin.initializer.close_extension_components = AsyncMock()
        plugin.initializer.conversation_manager = None
        plugin.initializer.memory_engine = None
        plugin.initializer.db = None
        plugin._perf_tracker = MagicMock()
        plugin._perf_tracker.get_perf_data.return_value = {}
        plugin._backfill_scheduler = None

        with (
            patch.object(module.observability, "report_debug_event") as report,
            patch.object(module.observability, "report_debug_exception"),
        ):
            await plugin.terminate()

        stopped = [
            call.kwargs
            for call in report.call_args_list
            if call.args == ("plugin_stopped",)
        ]
        assert stopped[-1]["status"] == "degraded"
        assert stopped[-1]["reason_code"] == "shutdown_degraded"


class TestMemoraPluginReady:
    """测试 MemoraPlugin._ensure_plugin_ready 生命周期行为。"""

    @pytest.mark.asyncio
    async def test_ensure_plugin_ready_initializes_runtime_components_on_first_call(
        self,
    ) -> None:
        MemoraPlugin = _load_memora_plugin_class()

        with (
            patch.object(MemoraPlugin, "_register_official_page_api_if_available"),
            patch.object(
                MemoraPlugin,
                "_create_tracked_task",
                side_effect=lambda coro: coro.close(),
            ),
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
        plugin.config_manager.get.side_effect = lambda key, default=None: (
            config_values.get(key, default)
        )

        plugin.initializer.ensure_initialized = AsyncMock(return_value=True)
        plugin.initializer._initialization_complete = True
        plugin.initializer.memory_engine = MagicMock()
        plugin.initializer.memory_processor = MagicMock()
        plugin.initializer.conversation_manager = MagicMock()
        plugin.initializer.memory_quality_gate = MagicMock()
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
    async def test_ensure_plugin_ready_registers_agent_tools_on_first_call(
        self,
    ) -> None:
        MemoraPlugin = _load_memora_plugin_class()

        with (
            patch.object(MemoraPlugin, "_register_official_page_api_if_available"),
            patch.object(
                MemoraPlugin,
                "_create_tracked_task",
                side_effect=lambda coro: coro.close(),
            ),
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
        plugin.config_manager.get.side_effect = lambda key, default=None: (
            config_values.get(key, default)
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
    @staticmethod
    def _build_factory_rollback_scenario(
        monkeypatch,
        tmp_path,
        injection_builder,
        *,
        decay_rate=0.1,
        auto_cleanup=False,
        backup_enabled=None,
    ):
        from astrbot.core.provider.provider import Provider

        from core.platform.composition.component_factory import ComponentFactory

        order: list[str] = []
        db = MagicMock()
        db.initialize = AsyncMock()
        db.close = AsyncMock(side_effect=lambda: order.append("db"))
        graph_db = MagicMock()
        graph_db.initialize = AsyncMock()
        graph_db.close = AsyncMock(side_effect=lambda: order.append("graph_db"))
        engine = MagicMock()
        engine.initialize = AsyncMock()
        engine.close = AsyncMock(side_effect=lambda: order.append("memory_engine"))
        engine.text_processor = None
        conversation_store = MagicMock()
        conversation_store.initialize = AsyncMock()
        conversation_store.close = AsyncMock(
            side_effect=lambda: order.append("conversation_store")
        )
        scheduler = MagicMock()
        scheduler.start = AsyncMock()

        async def stop_scheduler() -> None:
            order.append("scheduler")
            raise RuntimeError("scheduler cleanup failed")

        scheduler.stop = AsyncMock(side_effect=stop_scheduler)
        monkeypatch.setattr(
            "core.platform.composition.component_factory.MemoryEngine",
            MagicMock(return_value=engine),
        )
        monkeypatch.setattr(
            "core.platform.composition.component_factory.ConversationStore",
            MagicMock(return_value=conversation_store),
        )
        monkeypatch.setattr(
            "core.platform.composition.component_factory.DecayScheduler",
            MagicMock(return_value=scheduler),
        )
        config = MagicMock()
        config_values = {
            "graph_memory.enabled": True,
            "importance_decay.decay_rate": decay_rate,
            "forgetting_agent.auto_cleanup_enabled": auto_cleanup,
        }
        if backup_enabled is not None:
            config_values["backup_settings.enabled"] = backup_enabled
        config.get.side_effect = lambda key, default=None: config_values.get(
            key, default
        )
        config.session_manager = {}
        factory = ComponentFactory(MagicMock(), config, str(tmp_path))
        factory._build_injection_components = injection_builder
        faiss_checker = MagicMock()
        faiss_checker.check_and_fix_dimension_mismatch = AsyncMock()
        db_setup = MagicMock()
        db_setup.repair_message_counts = AsyncMock()
        db_setup.auto_rebuild_index_if_needed = AsyncMock()
        llm_provider = MagicMock(spec=Provider)
        llm_provider.text_chat = AsyncMock()
        args = (
            MagicMock(),
            llm_provider,
            MagicMock(side_effect=[db, graph_db]),
            faiss_checker,
            db_setup,
        )
        return factory, args, order

    @pytest.mark.asyncio
    async def test_backup_enabled_starts_scheduler_without_decay_or_cleanup(
        self, monkeypatch, tmp_path
    ) -> None:
        async def fail_injection(_db_path):
            raise RuntimeError("injection failed")

        factory, args, order = self._build_factory_rollback_scenario(
            monkeypatch,
            tmp_path,
            fail_injection,
            decay_rate=0,
            auto_cleanup=False,
            backup_enabled=True,
        )

        with pytest.raises(RuntimeError, match="injection failed"):
            await factory.build_all(*args)

        # 只有自动备份开启时，衰减率和自动清理都为零仍应启动并在失败回滚时停止调度器。
        assert order[0] == "scheduler"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cancel_build", [False, True])
    async def test_build_all_rolls_back_owned_components_when_injection_build_fails(
        self, monkeypatch, tmp_path, cancel_build
    ) -> None:
        injection_started = asyncio.Event()

        async def fail_injection(_db_path):
            injection_started.set()
            if cancel_build:
                await asyncio.Future()
            raise RuntimeError("injection failed")

        factory, args, order = self._build_factory_rollback_scenario(
            monkeypatch, tmp_path, fail_injection
        )
        task = asyncio.create_task(factory.build_all(*args))
        await injection_started.wait()
        if cancel_build:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(RuntimeError, match="injection failed"):
                await task

        db_factory = args[2]
        document_provider = db_factory.call_args_list[0].args[2]
        graph_provider = db_factory.call_args_list[1].args[2]
        assert document_provider is graph_provider
        assert document_provider is not args[0]
        assert order == [
            "scheduler",
            "conversation_store",
            "memory_engine",
            "graph_db",
            "db",
        ]

    @pytest.mark.asyncio
    async def test_component_factory_builds_started_injection_recorder(
        self, monkeypatch, tmp_path
    ) -> None:
        from core.platform.composition.component_factory import ComponentFactory

        store = MagicMock()
        store.initialize = AsyncMock()
        store.close = AsyncMock()
        recorder = MagicMock()
        recorder.start = AsyncMock()
        recorder.close = AsyncMock()
        store_type = MagicMock(return_value=store)
        recorder_type = MagicMock(return_value=recorder)
        monkeypatch.setattr(
            "core.platform.composition.component_factory.InjectionDecisionStore",
            store_type,
        )
        monkeypatch.setattr(
            "core.platform.composition.component_factory.InjectionDecisionRecorder",
            recorder_type,
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
        from core.platform.composition.component_factory import ComponentFactory

        order: list[str] = []
        store = MagicMock()
        store.initialize = AsyncMock()
        store.close = AsyncMock(side_effect=lambda: order.append("store"))
        recorder = MagicMock()
        recorder.start = AsyncMock(side_effect=RuntimeError("start failed"))
        recorder.close = AsyncMock(
            side_effect=lambda **_kwargs: order.append("recorder")
        )
        monkeypatch.setattr(
            "core.platform.composition.component_factory.InjectionDecisionStore",
            MagicMock(return_value=store),
        )
        monkeypatch.setattr(
            "core.platform.composition.component_factory.InjectionDecisionRecorder",
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
    async def test_component_factory_preserves_init_error_when_recorder_close_fails(
        self, monkeypatch, tmp_path
    ) -> None:
        from core.platform.composition.component_factory import ComponentFactory

        store = MagicMock()
        store.initialize = AsyncMock()
        store.close = AsyncMock()
        recorder = MagicMock()
        recorder.start = AsyncMock(side_effect=RuntimeError("start failed"))
        recorder.close = AsyncMock(side_effect=RuntimeError("close failed"))
        monkeypatch.setattr(
            "core.platform.composition.component_factory.InjectionDecisionStore",
            MagicMock(return_value=store),
        )
        monkeypatch.setattr(
            "core.platform.composition.component_factory.InjectionDecisionRecorder",
            MagicMock(return_value=recorder),
        )
        config = MagicMock()
        config.get.side_effect = lambda _key, default=None: default
        factory = ComponentFactory(MagicMock(), config, str(tmp_path))

        with pytest.raises(RuntimeError, match="start failed"):
            await factory._build_injection_components(tmp_path / "memora.db")

        recorder.close.assert_awaited_once_with(timeout=5.0)
        store.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_component_factory_closes_store_when_recorder_cleanup_is_cancelled(
        self, monkeypatch, tmp_path
    ) -> None:
        from core.platform.composition.component_factory import ComponentFactory

        store = MagicMock()
        store.initialize = AsyncMock()
        store.close = AsyncMock()
        recorder = MagicMock()
        recorder.start = AsyncMock(side_effect=RuntimeError("start failed"))
        recorder.close = AsyncMock(side_effect=asyncio.CancelledError())
        monkeypatch.setattr(
            "core.platform.composition.component_factory.InjectionDecisionStore",
            MagicMock(return_value=store),
        )
        monkeypatch.setattr(
            "core.platform.composition.component_factory.InjectionDecisionRecorder",
            MagicMock(return_value=recorder),
        )
        config = MagicMock()
        config.get.side_effect = lambda _key, default=None: default
        factory = ComponentFactory(MagicMock(), config, str(tmp_path))

        with pytest.raises(asyncio.CancelledError):
            await factory._build_injection_components(tmp_path / "memora.db")

        recorder.close.assert_awaited_once_with(timeout=5.0)
        store.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_build_all_awaits_and_merges_injection_components(
        self, monkeypatch, tmp_path
    ) -> None:
        """工厂应合并注入组件，并把同一身份运行时挂到会话管理器。"""

        from astrbot.core.provider.provider import Provider

        from core.platform.composition.component_factory import ComponentFactory

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
            "core.platform.composition.component_factory.MemoryEngine",
            MagicMock(return_value=engine),
        )
        conversation_store = MagicMock()
        conversation_store.initialize = AsyncMock()
        monkeypatch.setattr(
            "core.platform.composition.component_factory.ConversationStore",
            MagicMock(return_value=conversation_store),
        )
        faiss_checker = MagicMock()
        faiss_checker.check_and_fix_dimension_mismatch = AsyncMock()
        db_setup = MagicMock()
        db_setup.repair_message_counts = AsyncMock()
        db_setup.auto_rebuild_index_if_needed = AsyncMock()

        llm_provider = MagicMock(spec=Provider)
        llm_provider.text_chat = AsyncMock()
        components = await factory.build_all(
            MagicMock(), llm_provider, db_type, faiss_checker, db_setup
        )

        factory._build_injection_components.assert_awaited_once_with(
            tmp_path / "memora.db"
        )
        assert (
            components["injection_decision_store"]
            is injection_components["injection_decision_store"]
        )
        assert (
            components["injection_decision_recorder"]
            is injection_components["injection_decision_recorder"]
        )
        assert (
            components["conversation_manager"].identity_runtime
            is components["identity_runtime"]
        )
        await asyncio.gather(
            components["memory_evolution_store"].close(),
            components["identity_runtime"].close(),
        )

    @pytest.mark.asyncio
    async def test_plugin_initializer_retains_and_closes_injection_components_once(
        self, tmp_path
    ) -> None:
        from core.platform.composition.plugin_initializer import PluginInitializer

        initializer = PluginInitializer(MagicMock(), MagicMock(), str(tmp_path))
        assert initializer.injection_decision_store is None
        assert initializer.injection_decision_recorder is None
        order: list[str] = []
        recorder = MagicMock()
        recorder.close = AsyncMock(
            side_effect=lambda **_kwargs: order.append("recorder")
        )
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
    async def test_plugin_initializer_preserves_cancelled_recorder_for_retry(
        self, tmp_path
    ) -> None:
        from core.platform.composition.plugin_initializer import PluginInitializer

        initializer = PluginInitializer(MagicMock(), MagicMock(), str(tmp_path))
        close_started = asyncio.Event()

        async def block_recorder_close(**_kwargs) -> None:
            close_started.set()
            await asyncio.Future()

        recorder = MagicMock()
        recorder.close = AsyncMock(side_effect=block_recorder_close)
        store = MagicMock()
        store.close = AsyncMock()
        initializer.injection_decision_recorder = recorder
        initializer.injection_decision_store = store

        close_task = asyncio.create_task(initializer.close_injection_components())
        await close_started.wait()
        close_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await close_task

        store.close.assert_awaited_once()
        assert initializer.injection_decision_recorder is recorder
        assert initializer.injection_decision_store is None

        recorder.close = AsyncMock()
        await initializer.close_injection_components()
        recorder.close.assert_awaited_once_with(timeout=5.0)
        assert initializer.injection_decision_recorder is None

    @pytest.mark.asyncio
    async def test_plugin_initializer_preserves_first_close_error_and_failed_refs(
        self, tmp_path
    ) -> None:
        from core.platform.composition.plugin_initializer import PluginInitializer

        initializer = PluginInitializer(MagicMock(), MagicMock(), str(tmp_path))
        recorder = MagicMock()
        recorder.close = AsyncMock(side_effect=RuntimeError("recorder failed"))
        store = MagicMock()
        store.close = AsyncMock(side_effect=RuntimeError("store failed"))
        initializer.injection_decision_recorder = recorder
        initializer.injection_decision_store = store

        with pytest.raises(RuntimeError, match="recorder failed"):
            await initializer.close_injection_components()

        store.close.assert_awaited_once()
        assert initializer.injection_decision_recorder is recorder
        assert initializer.injection_decision_store is store

        recorder.close = AsyncMock()
        store.close = AsyncMock()
        await initializer.close_injection_components()
        assert initializer.injection_decision_recorder is None
        assert initializer.injection_decision_store is None

    @pytest.mark.asyncio
    async def test_plugin_initializer_clears_only_successfully_closed_reference(
        self, tmp_path
    ) -> None:
        from core.platform.composition.plugin_initializer import PluginInitializer

        initializer = PluginInitializer(MagicMock(), MagicMock(), str(tmp_path))
        recorder = MagicMock()
        recorder.close = AsyncMock()
        store = MagicMock()
        store.close = AsyncMock(side_effect=RuntimeError("store failed"))
        initializer.injection_decision_recorder = recorder
        initializer.injection_decision_store = store

        with pytest.raises(RuntimeError, match="store failed"):
            await initializer.close_injection_components()

        assert initializer.injection_decision_recorder is None
        assert initializer.injection_decision_store is store

    @pytest.mark.asyncio
    async def test_run_full_init_retains_injection_components(self, tmp_path) -> None:
        from core.platform.composition.plugin_initializer import PluginInitializer

        initializer = PluginInitializer(MagicMock(), MagicMock(), str(tmp_path))
        initializer._faiss_checker.load_vec_db_class = MagicMock(
            return_value=MagicMock()
        )
        store = MagicMock()
        recorder = MagicMock()
        memory_processor = MagicMock()
        quarantine_store = MagicMock()
        quality_gate = MagicMock()
        gate_runtime = MagicMock()
        conversation_manager = MagicMock()
        initializer._component_factory.build_all = AsyncMock(
            return_value={
                "db": MagicMock(),
                "graph_db": None,
                "memory_engine": MagicMock(),
                "memory_processor": memory_processor,
                "memory_quarantine_store": quarantine_store,
                "memory_quality_gate": quality_gate,
                "gate_runtime": gate_runtime,
                "conversation_manager": conversation_manager,
                "identity_runtime": types.SimpleNamespace(close=AsyncMock()),
                "index_validator": MagicMock(),
                "decay_scheduler": None,
                "injection_decision_store": store,
                "injection_decision_recorder": recorder,
            }
        )
        initializer._create_prompt_protection_service = MagicMock(
            return_value=MagicMock()
        )
        initializer._initialize_cognitive_components = AsyncMock()

        with patch(
            "core.platform.composition.plugin_initializer.report_debug_event"
        ) as report:
            await initializer._run_full_init()

        assert initializer.injection_decision_store is store
        assert initializer.injection_decision_recorder is recorder
        # 回归防护：发布段必须同时保留 conversation_manager 与 gate_runtime。
        assert initializer.conversation_manager is conversation_manager
        assert initializer.gate_runtime is gate_runtime
        readiness_capabilities = {
            call.kwargs["capability"]
            for call in report.call_args_list
            if call.args == ("plugin_initialized",)
            and call.kwargs.get("stage") == "component_readiness"
        }
        assert {
            "database",
            "memory_engine",
            "memory_processor",
            "memory_quarantine_store",
            "memory_quality_gate",
            "conversation_manager",
            "index_validator",
            "injection_store",
            "injection_recorder",
        }.issubset(readiness_capabilities)

    @pytest.mark.asyncio
    async def test_run_full_init_closes_owned_injection_components_on_error(
        self, tmp_path
    ) -> None:
        from core.platform.composition.plugin_initializer import PluginInitializer
        from core.shared.errors import InitializationError

        initializer = PluginInitializer(MagicMock(), MagicMock(), str(tmp_path))
        initializer._faiss_checker.load_vec_db_class = MagicMock(
            return_value=MagicMock()
        )
        recorder = MagicMock()
        recorder.close = AsyncMock(side_effect=RuntimeError("cleanup failed"))
        store = MagicMock()
        store.close = AsyncMock()
        memory_processor = MagicMock()
        quarantine_store = MagicMock()
        quality_gate = MagicMock()
        initializer._component_factory.build_all = AsyncMock(
            return_value={
                "db": MagicMock(),
                "graph_db": None,
                "memory_engine": MagicMock(),
                "memory_processor": memory_processor,
                "memory_quarantine_store": quarantine_store,
                "memory_quality_gate": quality_gate,
                "gate_runtime": MagicMock(),
                "conversation_manager": MagicMock(),
                "identity_runtime": types.SimpleNamespace(close=AsyncMock()),
                "index_validator": MagicMock(),
                "decay_scheduler": None,
                "injection_decision_store": store,
                "injection_decision_recorder": recorder,
            }
        )
        initializer._create_prompt_protection_service = MagicMock(
            return_value=MagicMock()
        )
        initializer._initialize_cognitive_components = AsyncMock(
            side_effect=ValueError("cognitive failed")
        )

        with pytest.raises(InitializationError, match="cognitive failed"):
            await initializer._run_full_init()

        recorder.close.assert_awaited_once_with(timeout=5.0)
        store.close.assert_awaited_once()
        assert initializer.injection_decision_recorder is recorder
        assert initializer.injection_decision_store is None

    @pytest.mark.asyncio
    async def test_run_full_init_preserves_real_cancellation_during_cleanup(
        self, tmp_path
    ) -> None:
        from core.platform.composition.plugin_initializer import PluginInitializer

        initializer = PluginInitializer(MagicMock(), MagicMock(), str(tmp_path))
        initializer._faiss_checker.load_vec_db_class = MagicMock(
            return_value=MagicMock()
        )
        recorder = MagicMock()
        recorder.close = AsyncMock()
        store = MagicMock()
        store.close = AsyncMock(side_effect=RuntimeError("store cleanup failed"))
        memory_processor = MagicMock()
        quarantine_store = MagicMock()
        quality_gate = MagicMock()
        initializer._component_factory.build_all = AsyncMock(
            return_value={
                "db": MagicMock(),
                "graph_db": None,
                "memory_engine": MagicMock(),
                "memory_processor": memory_processor,
                "memory_quarantine_store": quarantine_store,
                "memory_quality_gate": quality_gate,
                "gate_runtime": MagicMock(),
                "conversation_manager": MagicMock(),
                "identity_runtime": types.SimpleNamespace(close=AsyncMock()),
                "index_validator": MagicMock(),
                "decay_scheduler": None,
                "injection_decision_store": store,
                "injection_decision_recorder": recorder,
            }
        )
        initializer._create_prompt_protection_service = MagicMock(
            return_value=MagicMock()
        )
        cognitive_started = asyncio.Event()

        async def block_cognitive_initialization() -> None:
            cognitive_started.set()
            await asyncio.Future()

        initializer._initialize_cognitive_components = block_cognitive_initialization
        init_task = asyncio.create_task(initializer._run_full_init())
        await cognitive_started.wait()
        init_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await init_task

        recorder.close.assert_awaited_once_with(timeout=5.0)
        store.close.assert_awaited_once()
        assert initializer.injection_decision_recorder is None
        assert initializer.injection_decision_store is store


class TestMemoraInjectionLifecycle:
    @pytest.mark.asyncio
    async def test_terminate_closes_injection_components_without_event_handler(
        self,
    ) -> None:
        MemoraPlugin = _load_memora_plugin_class()
        with (
            patch.object(MemoraPlugin, "_register_official_page_api_if_available"),
            patch.object(
                MemoraPlugin,
                "_create_tracked_task",
                side_effect=lambda coro: coro.close(),
            ),
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
        with (
            patch.object(MemoraPlugin, "_register_official_page_api_if_available"),
            patch.object(
                MemoraPlugin,
                "_create_tracked_task",
                side_effect=lambda coro: coro.close(),
            ),
        ):
            plugin = MemoraPlugin(MagicMock(), {})

        plugin.initializer._initialization_complete = True
        plugin.initializer.memory_engine = MagicMock()
        plugin.initializer.memory_processor = MagicMock()
        plugin.initializer.conversation_manager = MagicMock()
        plugin.initializer.injection_decision_recorder = MagicMock()
        plugin.config_manager.get = MagicMock(
            side_effect=lambda key, default=None: (
                True if key == "agent_tools.enable_recall_tool" else default
            )
        )
        plugin._register_agent_tools_if_needed = MagicMock(
            side_effect=lambda: setattr(plugin, "_llm_tools_registered", True)
        )
        module = sys.modules[MemoraPlugin.__module__]
        event_handler = MagicMock()

        def build_event_handler(**kwargs):
            assert plugin._llm_tools_registered is True
            assert (
                kwargs["injection_recorder"]
                is plugin.initializer.injection_decision_recorder
            )
            assert kwargs["memory_tool_available"] is True
            assert (
                kwargs["memory_quality_gate"] is plugin.initializer.memory_quality_gate
            )
            assert kwargs["identity_runtime"] is plugin.initializer.identity_runtime
            return event_handler

        with patch.object(module, "EventHandler", side_effect=build_event_handler):
            await plugin._ensure_runtime_components()

        plugin._register_agent_tools_if_needed.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_runtime_falls_back_when_memory_tool_registration_fails(
        self,
    ) -> None:
        MemoraPlugin = _load_memora_plugin_class()
        with (
            patch.object(MemoraPlugin, "_register_official_page_api_if_available"),
            patch.object(
                MemoraPlugin,
                "_create_tracked_task",
                side_effect=lambda coro: coro.close(),
            ),
        ):
            plugin = MemoraPlugin(MagicMock(), {})

        plugin.initializer._initialization_complete = True
        plugin.initializer.memory_engine = MagicMock()
        plugin.initializer.memory_processor = MagicMock()
        plugin.initializer.conversation_manager = MagicMock()
        plugin.initializer.memory_quality_gate = MagicMock()
        plugin.initializer.injection_decision_recorder = MagicMock()
        plugin._llm_tools_registered = False
        plugin.config_manager.get = MagicMock(
            side_effect=lambda key, default=None: (
                True if key == "agent_tools.enable_recall_tool" else default
            )
        )
        module = sys.modules[MemoraPlugin.__module__]
        event_handler = MagicMock()
        command_handler = MagicMock()

        with (
            patch.object(
                module,
                "MemorySearchTool",
                side_effect=RuntimeError("registration failed"),
            ) as memory_tool_type,
            patch.object(
                module, "EventHandler", return_value=event_handler
            ) as event_handler_type,
            patch.object(
                module, "CommandHandler", return_value=command_handler
            ) as command_handler_type,
        ):
            first_ready = await plugin._ensure_runtime_components()
            second_ready = await plugin._ensure_runtime_components()

        assert first_ready is True
        assert second_ready is True
        assert plugin._llm_tools_registered is False
        assert plugin.event_handler is event_handler
        assert plugin.command_handler is command_handler
        assert event_handler_type.call_args.kwargs["memory_tool_available"] is False
        assert (
            event_handler_type.call_args.kwargs["memory_quality_gate"]
            is plugin.initializer.memory_quality_gate
        )
        assert (
            event_handler_type.call_args.kwargs["identity_runtime"]
            is plugin.initializer.identity_runtime
        )
        assert (
            command_handler_type.call_args.kwargs["memory_quality_gate"]
            is plugin.initializer.memory_quality_gate
        )
        assert (
            command_handler_type.call_args.kwargs["identity_runtime"]
            is plugin.initializer.identity_runtime
        )
        memory_tool_type.assert_called_once()
        plugin.context.add_llm_tools.assert_not_called()
        command_handler_type.assert_called_once()


class TestMemoryEvolutionLifecycle:
    """验证记忆演化组件的就绪快照与关闭顺序。"""

    @pytest.mark.asyncio
    async def test_close_stops_manager_before_store(self) -> None:
        from core.platform.composition.plugin_initializer import PluginInitializer

        initializer = PluginInitializer(MagicMock(), MagicMock(), ".")
        close_order: list[str] = []
        manager = MagicMock()
        manager.stop = AsyncMock(side_effect=lambda: close_order.append("manager"))
        store = MagicMock()
        store.close = AsyncMock(side_effect=lambda: close_order.append("store"))
        initializer.memory_evolution_manager = manager
        initializer.memory_evolution_store = store

        await initializer.close_memory_evolution_components()

        manager.stop.assert_awaited_once()
        store.close.assert_awaited_once()
        assert close_order == ["manager", "store"]

    def test_readiness_contains_only_evolution_component_booleans(self) -> None:
        from core.platform.composition.plugin_initializer import PluginInitializer

        initializer = PluginInitializer(MagicMock(), MagicMock(), ".")
        snapshot = initializer.get_readiness_snapshot()

        components = snapshot["components_ready"]
        assert components["memory_evolution_store"] is False
        assert components["memory_evolution_manager"] is False
        assert components["memory_quarantine_store"] is False
        assert components["memory_quality_gate"] is False
