"""自主学习配置提交后的插件 reload 宿主回调测试。"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.platform.transport.page_api.learning_api import _schedule_learning_reload

_CANDIDATE_ID = "candidate_plugin_reload01"
_OPERATION_ID = "operation_plugin_reload01"
_LEARNING_PATHS = (
    "graph_memory.document_route_weight",
    "graph_memory.graph_route_weight",
)


def _load_memora_plugin_class() -> type:
    """在 AstrBot 测试替身完成后隔离加载插件主类。"""

    root = Path(__file__).resolve().parents[1]
    package_name = "memora_reload_testpkg"
    package = types.ModuleType(package_name)
    package.__path__ = [str(root)]
    sys.modules[package_name] = package
    star_module = sys.modules["astrbot.api.star"]
    star_module.Star = type(
        "TestStar",
        (object,),
        {"__init__": lambda self, context=None: None},
    )
    star_module.StarTools = types.SimpleNamespace(
        get_data_dir=lambda: root / ".pytest_memora_data"
    )
    star_module.register = lambda *args, **kwargs: lambda cls: cls

    event_module = sys.modules["astrbot.api.event"]
    for decorator_name in (
        "platform_adapter_type",
        "on_llm_request",
        "on_llm_response",
        "after_message_sent",
    ):
        getattr(event_module.filter, decorator_name).side_effect = (
            lambda *args, **kwargs: lambda function: function
        )

    class _CommandGroup:
        """提供命令组装饰器所需的最小协议。"""

        def __call__(self, function):
            """把命令组装饰目标原样返回为组对象。"""

            return self

        def command(self, *args, **kwargs):
            """返回不改变目标函数的命令装饰器。"""

            return lambda function: function

    event_module.filter.command_group.side_effect = lambda *args, **kwargs: (
        _CommandGroup()
    )
    filter_module = types.ModuleType("astrbot.api.event.filter")
    filter_module.PermissionType = types.SimpleNamespace(ADMIN="admin")
    filter_module.permission_type = lambda *args, **kwargs: lambda function: function
    sys.modules["astrbot.api.event.filter"] = filter_module

    spec = importlib.util.spec_from_file_location(
        f"{package_name}.main",
        root / "main.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.MemoraPlugin


def _plugin(reload_plugin, manager: object):
    """构造只包含 reload 生命周期依赖的插件实例。"""

    plugin_class = _load_memora_plugin_class()
    plugin = object.__new__(plugin_class)
    plugin.context = types.SimpleNamespace(
        _star_manager=types.SimpleNamespace(reload=reload_plugin)
    )
    plugin.initializer = types.SimpleNamespace(
        memory_engine=types.SimpleNamespace(auto_learning=manager)
    )
    plugin._backup_manager = MagicMock()
    plugin._terminating = False
    return plugin


async def _run_scheduled_task(plugin: object) -> asyncio.Task:
    """捕获并等待插件创建的非跟踪 reload task。"""

    module = sys.modules[plugin.__class__.__module__]
    created_tasks: list[asyncio.Task] = []
    create_task = asyncio.create_task
    real_sleep = asyncio.sleep

    def capture_task(coroutine) -> asyncio.Task:
        """记录真实 task，避免测试依赖未观察的后台执行。"""

        task = create_task(coroutine)
        created_tasks.append(task)
        return task

    async def no_delay(_delay: float) -> None:
        """移除生产延迟但保留一次异步调度点。"""

        await real_sleep(0)

    with (
        patch.object(module.asyncio, "sleep", side_effect=no_delay),
        patch.object(module.asyncio, "create_task", side_effect=capture_task),
    ):
        assert plugin.schedule_learning_reload(_OPERATION_ID) is True
        assert len(created_tasks) == 1
        await created_tasks[0]
    return created_tasks[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("host_result", [False, (), (False, "rejected")])
async def test_learning_reload_marks_running_before_host_failure(
    host_result: object,
) -> None:
    """宿主布尔或元组失败前先持久化 running，随后收口为 failed。"""

    events: list[str] = []

    async def update_operation(
        operation_id: str,
        *,
        state: str,
        reason_code: str,
    ) -> dict[str, str]:
        """记录 manager reload 回调的先后顺序。"""

        assert operation_id == _OPERATION_ID
        events.append(f"{state}:{reason_code}")
        return {"operation_id": operation_id, "state": state}

    async def reload_plugin(name: str) -> object:
        """模拟 AstrBot 已执行但明确返回失败。"""

        assert name == "astrbot_plugin_memora"
        events.append("host_reload")
        return host_result

    manager = types.SimpleNamespace(
        update_reload_operation=AsyncMock(side_effect=update_operation)
    )
    plugin = _plugin(reload_plugin, manager)

    await _run_scheduled_task(plugin)

    assert events == [
        "running:reload_started",
        "host_reload",
        "failed:host_reload_failed",
    ]


@pytest.mark.asyncio
async def test_learning_reload_host_exception_is_persisted_and_propagated() -> None:
    """宿主异常先收口 failed，再由既有 task 回调观察异常。"""

    events: list[str] = []

    async def update_operation(
        operation_id: str,
        *,
        state: str,
        reason_code: str,
    ) -> dict[str, str]:
        """记录 manager reload 回调的先后顺序。"""

        assert operation_id == _OPERATION_ID
        events.append(f"{state}:{reason_code}")
        return {"operation_id": operation_id, "state": state}

    async def reload_plugin(name: str) -> bool:
        """模拟 AstrBot reload 抛出异常。"""

        assert name == "astrbot_plugin_memora"
        events.append("host_reload")
        raise RuntimeError("host reload failed")

    manager = types.SimpleNamespace(
        update_reload_operation=AsyncMock(side_effect=update_operation)
    )
    plugin = _plugin(reload_plugin, manager)

    with pytest.raises(RuntimeError, match="host reload failed"):
        await _run_scheduled_task(plugin)

    assert events == [
        "running:reload_started",
        "host_reload",
        "failed:host_reload_failed",
    ]


@pytest.mark.asyncio
async def test_learning_reload_is_recorded_before_task_is_scheduled() -> None:
    """queued 未持久化完成前，不得创建可能抢跑的 reload task。"""

    record_started = asyncio.Event()
    release_record = asyncio.Event()
    schedule_called = False

    async def record_reload_operation(**kwargs: object) -> dict[str, object]:
        """阻塞 queued 持久化，暴露调度顺序。"""

        assert kwargs["state"] == "queued"
        record_started.set()
        await release_record.wait()
        return {"operation_id": _OPERATION_ID, "state": "queued"}

    def schedule_learning_reload(operation_id: str) -> bool:
        """记录同步调度调用，并确认 operation ID 未变化。"""

        nonlocal schedule_called
        assert operation_id == _OPERATION_ID
        schedule_called = True
        return True

    manager = types.SimpleNamespace(
        record_reload_operation=AsyncMock(side_effect=record_reload_operation)
    )
    api = types.SimpleNamespace(
        plugin=types.SimpleNamespace(
            schedule_learning_reload=schedule_learning_reload,
        )
    )
    task = asyncio.create_task(
        _schedule_learning_reload(
            api,
            manager,
            action="publish",
            candidate_id=_CANDIDATE_ID,
            result={
                "operation_id": _OPERATION_ID,
                "applied_revision": "config-revision-2",
            },
            changed_paths=_LEARNING_PATHS,
        )
    )

    await asyncio.wait_for(record_started.wait(), timeout=1.0)
    assert schedule_called is False
    release_record.set()

    assert await task == "queued"
    assert schedule_called is True


@pytest.mark.asyncio
async def test_learning_reload_record_failure_does_not_schedule_task() -> None:
    """queued 保存失败时必须保守返回，且不得创建 reload task。"""

    scheduler = MagicMock(return_value=True)
    manager = types.SimpleNamespace(
        record_reload_operation=AsyncMock(side_effect=RuntimeError("save failed"))
    )
    api = types.SimpleNamespace(
        plugin=types.SimpleNamespace(schedule_learning_reload=scheduler)
    )

    state = await _schedule_learning_reload(
        api,
        manager,
        action="publish",
        candidate_id=_CANDIDATE_ID,
        result={
            "operation_id": _OPERATION_ID,
            "applied_revision": "config-revision-2",
        },
        changed_paths=_LEARNING_PATHS,
    )

    assert state == "restart_required"
    scheduler.assert_not_called()


@pytest.mark.asyncio
async def test_learning_reload_schedule_exception_requires_restart() -> None:
    """同步调度异常必须把 queued operation 收口为 restart_required。"""

    def raise_schedule_error(_operation_id: str) -> bool:
        """模拟插件同步调度入口抛出普通异常。"""

        raise RuntimeError("schedule failed")

    manager = types.SimpleNamespace(
        record_reload_operation=AsyncMock(
            return_value={"operation_id": _OPERATION_ID, "state": "queued"}
        ),
        update_reload_operation=AsyncMock(
            return_value={
                "operation_id": _OPERATION_ID,
                "state": "restart_required",
            }
        ),
    )
    api = types.SimpleNamespace(
        plugin=types.SimpleNamespace(schedule_learning_reload=raise_schedule_error)
    )

    state = await _schedule_learning_reload(
        api,
        manager,
        action="publish",
        candidate_id=_CANDIDATE_ID,
        result={
            "operation_id": _OPERATION_ID,
            "applied_revision": "config-revision-2",
        },
        changed_paths=_LEARNING_PATHS,
    )

    assert state == "restart_required"
    manager.update_reload_operation.assert_awaited_once_with(
        _OPERATION_ID,
        state="restart_required",
        reason_code="reload_not_queued",
    )


@pytest.mark.asyncio
async def test_learning_reload_schedule_cancellation_persists_then_propagates() -> None:
    """同步调度取消先收口 operation，再传播 CancelledError。"""

    def cancel_schedule(_operation_id: str) -> bool:
        """模拟插件同步调度入口收到任务取消。"""

        raise asyncio.CancelledError

    manager = types.SimpleNamespace(
        record_reload_operation=AsyncMock(
            return_value={"operation_id": _OPERATION_ID, "state": "queued"}
        ),
        update_reload_operation=AsyncMock(
            return_value={
                "operation_id": _OPERATION_ID,
                "state": "restart_required",
            }
        ),
    )
    api = types.SimpleNamespace(
        plugin=types.SimpleNamespace(schedule_learning_reload=cancel_schedule)
    )

    with pytest.raises(asyncio.CancelledError):
        await _schedule_learning_reload(
            api,
            manager,
            action="publish",
            candidate_id=_CANDIDATE_ID,
            result={
                "operation_id": _OPERATION_ID,
                "applied_revision": "config-revision-2",
            },
            changed_paths=_LEARNING_PATHS,
        )

    manager.update_reload_operation.assert_awaited_once_with(
        _OPERATION_ID,
        state="restart_required",
        reason_code="reload_not_queued",
    )


@pytest.mark.asyncio
async def test_learning_reload_termination_requires_restart_without_host_call() -> None:
    """延迟期间进入关停时必须保守要求重启且不调用宿主。"""

    async def reload_plugin(_name: str) -> bool:
        """关停分支不得调用的宿主替身。"""

        raise AssertionError("host reload must not run while terminating")

    manager = types.SimpleNamespace(update_reload_operation=AsyncMock())
    manager.update_reload_operation.return_value = {
        "operation_id": _OPERATION_ID,
        "state": "restart_required",
    }
    plugin = _plugin(reload_plugin, manager)
    plugin._terminating = True

    await _run_scheduled_task(plugin)

    manager.update_reload_operation.assert_awaited_once_with(
        _OPERATION_ID,
        state="restart_required",
        reason_code="plugin_terminating",
    )


@pytest.mark.asyncio
async def test_learning_reload_not_executed_requires_restart() -> None:
    """running 状态无法落盘时不得继续调用宿主重载。"""

    async def reload_plugin(_name: str) -> bool:
        """状态未持久化分支不得调用的宿主替身。"""

        raise AssertionError("host reload must not run without durable state")

    manager = types.SimpleNamespace(update_reload_operation=AsyncMock())
    manager.update_reload_operation.side_effect = [
        None,
        {"operation_id": _OPERATION_ID, "state": "restart_required"},
    ]
    plugin = _plugin(reload_plugin, manager)

    await _run_scheduled_task(plugin)

    assert manager.update_reload_operation.await_args_list[1].kwargs == {
        "state": "restart_required",
        "reason_code": "reload_not_executed",
    }


@pytest.mark.asyncio
async def test_learning_reload_cancel_propagates_after_restart_callback() -> None:
    """取消必须先持久化 restart_required，再保持 task 的取消语义。"""

    sleep_started = asyncio.Event()
    never_release = asyncio.Event()

    async def reload_plugin(_name: str) -> bool:
        """取消发生在延迟期，因此宿主不得被调用。"""

        raise AssertionError("host reload must not run after cancellation")

    manager = types.SimpleNamespace(update_reload_operation=AsyncMock())
    manager.update_reload_operation.return_value = {
        "operation_id": _OPERATION_ID,
        "state": "restart_required",
    }
    plugin = _plugin(reload_plugin, manager)
    module = sys.modules[plugin.__class__.__module__]
    tasks: list[asyncio.Task] = []
    create_task = asyncio.create_task

    async def blocked_sleep(_delay: float) -> None:
        """把 task 固定在可控的取消点。"""

        sleep_started.set()
        await never_release.wait()

    def capture_task(coroutine) -> asyncio.Task:
        """捕获插件创建的 reload task。"""

        task = create_task(coroutine)
        tasks.append(task)
        return task

    with (
        patch.object(module.asyncio, "sleep", side_effect=blocked_sleep),
        patch.object(module.asyncio, "create_task", side_effect=capture_task),
    ):
        assert plugin.schedule_learning_reload(_OPERATION_ID) is True
        await asyncio.wait_for(sleep_started.wait(), timeout=1.0)
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]

    manager.update_reload_operation.assert_awaited_once_with(
        _OPERATION_ID,
        state="restart_required",
        reason_code="reload_cancelled",
    )
