"""生命周期验证命令的隔离 worker 与故障场景。"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import tempfile
import types
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

REPORT_SCHEMA = "memora-plugin-lifecycle-report-v1"
PLUGIN_DIR_NAME = "astrbot_plugin_memora"
PLUGIN_MODULE = f"data.plugins.{PLUGIN_DIR_NAME}.main"
COMPOSITION_ROOT = ".core.platform.composition"


class WorkerError(RuntimeError):
    """表示隔离 worker 无法完成约定动作。"""


class _MemoryPreferences:
    """为 PluginManager 提供无持久化偏好存储。"""

    def __init__(self) -> None:
        """初始化进程内偏好字典。"""

        self._values: dict[str, Any] = {}

    async def global_get(self, key: str, default: Any = None) -> Any:
        """返回偏好值。"""

        return self._values.get(key, default)

    async def global_put(self, key: str, value: Any) -> None:
        """保存偏好值。"""

        self._values[key] = value


class _HarnessContext:
    """只实现插件装配所需的 Context 公共能力。"""

    def __init__(self, star_registry: list[Any]) -> None:
        """保存真实注册表并初始化隔离资源台账。"""

        self._star_registry = star_registry
        self._star_manager: Any = None
        self.registered_web_apis: list[tuple[str, Any, list[str], str]] = []
        self.runtime_tools: list[Any] = []
        self.provider_manager = types.SimpleNamespace(inst_map={})

    def get_all_stars(self) -> list[Any]:
        """返回真实插件注册表。"""

        return self._star_registry

    def get_registered_star(self, name: str) -> Any | None:
        """按名称或根目录查找插件元数据。"""

        for metadata in self._star_registry:
            if name in {metadata.name, metadata.root_dir_name}:
                return metadata
        return None

    def get_config(self, _umo: str | None = None) -> dict[str, Any]:
        """返回不含凭据的最小配置。"""

        return {"timezone": "UTC"}

    def get_all_embedding_providers(self) -> list[Any]:
        """隔离验证不连接 Embedding Provider。"""

        return []

    def get_all_providers(self) -> list[Any]:
        """隔离验证不连接 LLM Provider。"""

        return []

    def get_using_provider(self, _umo: str | None = None) -> None:
        """表示隔离环境没有默认 Provider。"""

        return None

    def register_web_api(
        self,
        route: str,
        handler: Any,
        methods: list[str],
        description: str,
    ) -> None:
        """复现 AstrBot 相同 route/method 的替换语义。"""

        for index, item in enumerate(self.registered_web_apis):
            if item[0] == route and item[2] == methods:
                self.registered_web_apis[index] = (
                    route,
                    handler,
                    methods,
                    description,
                )
                return
        self.registered_web_apis.append((route, handler, methods, description))

    def add_llm_tools(self, *tools: Any) -> None:
        """记录插件运行期动态注册的工具。"""

        names = {getattr(item, "name", None) for item in tools}
        self.runtime_tools = [
            item
            for item in self.runtime_tools
            if getattr(item, "name", None) not in names
        ]
        self.runtime_tools.extend(tools)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """原子写入 worker JSON 报告。"""

    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """解析仅供隔离子进程使用的参数。"""

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--_worker", action="store_true")
    parser.add_argument("--version", required=True)
    parser.add_argument("--plugin-root", required=True, type=Path)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--worker-report", required=True, type=Path)
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--inject-initialization-failure", action="store_true")
    parser.add_argument("--scenario-mode", choices=("all", "namespace"), default="all")
    return parser.parse_args(list(argv))


def _manager_succeeded(value: Any) -> bool:
    """兼容 PluginManager 的 bool 或 ``(bool, error)`` 返回。"""

    if isinstance(value, tuple) and value:
        return value[0] is True
    return value is True


def _detected_version() -> str | None:
    """返回 worker 实际导入的 AstrBot 版本。"""

    import astrbot

    version = getattr(astrbot, "__version__", None)
    if isinstance(version, str):
        return version
    try:
        from astrbot.core.config.default import VERSION
    except ImportError:
        return None
    return str(VERSION)


def _owned_count(items: Sequence[Any]) -> int:
    """统计属于目标插件模块的注册项。"""

    return sum(
        isinstance(owner := getattr(item, "handler_module_path", None), str)
        and owner.startswith(PLUGIN_MODULE)
        for item in items
    )


def _route_counts(context: _HarnessContext, instances: Sequence[Any]) -> dict[str, int]:
    """统计总路由和仍绑定到已创建实例的路由。"""

    instance_ids = {id(item) for item in instances}
    stale = 0
    for _, handler, _, _ in context.registered_web_apis:
        page_plugin = getattr(getattr(handler, "__self__", None), "plugin", None)
        stale += id(page_plugin) in instance_ids
    return {"registered": len(context.registered_web_apis), "stale": stale}


async def _wait_startup(instance: Any) -> list[BaseException]:
    """等待额外持有的启动任务并返回异常。"""

    tasks = list(getattr(instance, "_harness_startup_tasks", ()))
    if not tasks:
        return []
    values = await asyncio.gather(*tasks, return_exceptions=True)
    return [item for item in values if isinstance(item, BaseException)]


def _snapshot(
    star_map: Mapping[str, Any],
    star_registry: Sequence[Any],
    handlers: Any,
    llm_tools: Any,
    instance: Any,
    context: _HarnessContext,
    cycle: int,
) -> dict[str, Any]:
    """生成不含路径或业务载荷的单轮注册快照。"""

    return {
        "cycle": cycle,
        "map_registrations": int(PLUGIN_MODULE in star_map),
        "registry_registrations": sum(
            getattr(item, "module_path", None) == PLUGIN_MODULE
            for item in star_registry
        ),
        "handlers": len(handlers.get_handlers_by_module_name(PLUGIN_MODULE)),
        "decorated_tools": _owned_count(llm_tools.func_list),
        "runtime_tools": len(context.runtime_tools),
        "routes": len(context.registered_web_apis),
        "tasks": len(getattr(instance, "_background_tasks", ())),
    }


def namespace_contract_passed(result: Mapping[str, Any], cycles: int) -> bool:
    """判定 namespace 装配、重载和资源收敛契约。"""

    snapshots = result.get("cycles")
    final = result.get("final_resources")
    if not isinstance(snapshots, list) or len(snapshots) != cycles:
        return False
    if not isinstance(final, Mapping):
        return False
    signatures: list[tuple[int, ...]] = []
    keys = ("handlers", "decorated_tools", "runtime_tools", "routes")
    for item in snapshots:
        if not isinstance(item, Mapping):
            return False
        if (
            item.get("map_registrations") != 1
            or item.get("registry_registrations") != 1
        ):
            return False
        signatures.append(tuple(int(item.get(key, -1)) for key in keys))
    if len(set(signatures)) != 1:
        return False
    resource_keys = (
        "registrations",
        "handlers",
        "decorated_tools",
        "runtime_tools",
        "stale_routes",
        "tasks",
        "connections",
        "handles",
    )
    return all(int(final.get(key, -1)) == 0 for key in resource_keys)


async def _provider_scenarios(package: str) -> list[dict[str, Any]]:
    """对真实 ProviderWaiter 执行延迟、耗尽和取消场景。"""
    module = importlib.import_module(f"{package}{COMPOSITION_ROOT}.provider_waiter")
    waiter_type = module.ProviderWaiter
    original_sleep, original_time = module.asyncio.sleep, module.time.time
    results: list[dict[str, Any]] = []

    class DelayedLoader:
        """第三次检查时提供两个受控对象。"""

        def __init__(self) -> None:
            """初始化调用计数。"""

            self.calls = 0

        def initialize_providers(
            self, _emb: Any, _llm: Any, silent: bool
        ) -> tuple[Any, Any]:
            """按确定次数返回 Provider 状态。"""

            assert silent is True
            self.calls += 1
            return (object(), object()) if self.calls == 3 else (None, None)

    class MissingLoader:
        """始终保持 Provider 缺失。"""

        @staticmethod
        def initialize_providers(
            _emb: Any, _llm: Any, silent: bool
        ) -> tuple[None, None]:
            """返回缺失状态。"""

            assert silent is True
            return None, None

    async def immediate_sleep(_delay: float) -> None:
        """推进协程但不等待墙钟。"""

        return None

    ticks = iter(index / 10 for index in range(30))
    module.asyncio.sleep, module.time.time = immediate_sleep, lambda: next(ticks)
    try:
        delayed = waiter_type(max_attempts=4)
        emb, llm, ready = await delayed.wait_non_blocking(
            DelayedLoader(), None, None, max_wait=0.8
        )
        delayed_ok = (
            ready and emb is not None and llm is not None and delayed.attempts == 2
        )
        results.append(
            {
                "name": "provider_delayed_readiness",
                "status": "passed" if delayed_ok else "failed",
                "attempts": delayed.attempts,
            }
        )
        exhausted = waiter_type(max_attempts=3)
        exhausted_notifications: list[tuple[Any, Any, bool]] = []

        async def mark_exhausted(emb: Any, llm: Any, *, exhausted: bool) -> None:
            """记录重试预算耗尽通知。"""
            exhausted_notifications.append((emb, llm, exhausted))

        exhausted.on_terminal_callback = mark_exhausted
        await exhausted._retry_loop(MissingLoader(), None, None)
        exhausted_ok = exhausted.attempts == 3 and not exhausted.providers_ready
        exhausted_ok &= exhausted_notifications == [(None, None, True)]
        results.append(
            {
                "name": "provider_retry_exhaustion",
                "status": "passed" if exhausted_ok else "failed",
                "attempts": exhausted.attempts,
            }
        )
    finally:
        module.asyncio.sleep, module.time.time = original_sleep, original_time

    entered = asyncio.Event()

    async def blocked_sleep(_delay: float) -> None:
        """保持任务挂起直到 cancel 传播。"""

        entered.set()
        await asyncio.Future()

    module.asyncio.sleep = blocked_sleep
    try:
        cancelled = waiter_type(max_attempts=3)
        cancelled.start_retry_if_needed(MissingLoader(), None, None)
        await asyncio.wait_for(entered.wait(), timeout=1.0)
        await cancelled.cancel()
        results.append(
            {
                "name": "provider_wait_cancellation",
                "status": "passed" if cancelled._retry_task is None else "failed",
                "attempts": cancelled.attempts,
            }
        )
    finally:
        module.asyncio.sleep = original_sleep
    return results


async def _initializer_scenarios(package: str, data_dir: Path) -> list[dict[str, Any]]:
    """验证真实初始化器的幂等、并发和失败回滚边界。"""
    module = importlib.import_module(f"{package}{COMPOSITION_ROOT}.plugin_initializer")
    initializer_type = module.PluginInitializer

    class Config:
        """初始化器构造所需的最小配置视图。"""

        session_manager: dict[str, Any] = {}

        @staticmethod
        def get(_key: str, default: Any = None) -> Any:
            """返回调用方默认值。"""

            return default

        @staticmethod
        def get_section(_key: str) -> dict[str, Any]:
            """返回空配置分区。"""

            return {}

    class ReadyWaiter:
        """立即返回两个非空受控 Provider。"""

        attempts = 0

        async def wait_non_blocking(self, *_args: Any) -> tuple[object, object, bool]:
            """返回就绪状态。"""

            return object(), object(), True

        async def cancel(self) -> None:
            """兼容关停入口。"""

            return None

    results: list[dict[str, Any]] = []
    repeated = initializer_type(_HarnessContext([]), Config(), str(data_dir))
    repeated._provider_waiter = ReadyWaiter()
    repeated_builds = 0

    async def repeated_full_init(self: Any) -> None:
        """记录 initialize 次数并提交完成态。"""

        nonlocal repeated_builds
        repeated_builds += 1
        self._initialization_complete = True

    repeated._run_full_init = types.MethodType(repeated_full_init, repeated)
    repeated_ok = await repeated.initialize() and await repeated.initialize()
    repeated_ok = repeated_ok and repeated_builds == 1
    results.append(
        {
            "name": "repeated_initialize",
            "status": "passed" if repeated_ok else "failed",
            "initialization_count": repeated_builds,
        }
    )

    concurrent = initializer_type(_HarnessContext([]), Config(), str(data_dir))
    concurrent._provider_waiter = ReadyWaiter()
    entered, second_entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    concurrent_builds = 0

    async def concurrent_full_init(self: Any) -> None:
        """用事件暴露重复进入构建阶段。"""

        nonlocal concurrent_builds
        concurrent_builds += 1
        entered.set()
        if concurrent_builds > 1:
            second_entered.set()
        await release.wait()
        self._initialization_complete = True

    concurrent._run_full_init = types.MethodType(concurrent_full_init, concurrent)
    first_task = asyncio.create_task(concurrent.initialize())
    await asyncio.wait_for(entered.wait(), timeout=1.0)
    second_task = asyncio.create_task(concurrent.initialize())
    observer = asyncio.create_task(second_entered.wait())
    done, _ = await asyncio.wait({observer}, timeout=0.25)
    release.set()
    values = await asyncio.gather(first_task, second_task)
    if not observer.done():
        observer.cancel()
    await asyncio.gather(observer, return_exceptions=True)
    concurrent_ok = all(values) and concurrent_builds == 1 and not done
    results.append(
        {
            "name": "initialize_concurrency",
            "status": "passed" if concurrent_ok else "failed",
            "initialization_count": concurrent_builds,
        }
    )

    factory = importlib.import_module(f"{package}{COMPOSITION_ROOT}.component_factory")
    order: list[str] = []

    class Resource:
        """记录 stop/close 及可选回滚失败。"""

        def __init__(self, name: str, fail: bool = False) -> None:
            """保存资源名称和故障开关。"""

            self.name, self.fail, self.closed = name, fail, False

        async def stop(self) -> None:
            """记录生产者停止。"""

            await self.close()

        async def close(self) -> None:
            """记录消费者关闭。"""

            order.append(self.name)
            if self.fail:
                raise RuntimeError("injected_rollback_failure")
            self.closed = True

    names = (
        "evolution_manager",
        "evolution_store",
        "scheduler",
        "identity",
        "conversation",
        "engine",
        "graph",
        "db",
    )
    resources = {name: Resource(name) for name in names}
    original_error = RuntimeError("injected_component_failure")
    preserved = False
    try:
        raise original_error
    except RuntimeError as exc:
        await factory.ComponentFactory._rollback_build_components(
            resources["scheduler"],
            resources["conversation"],
            resources["engine"],
            resources["graph"],
            resources["db"],
            resources["evolution_manager"],
            resources["evolution_store"],
            resources["identity"],
        )
        preserved = exc is original_error
    rollback_ok = (
        preserved
        and order == list(names)
        and all(item.closed for item in resources.values())
    )
    results.append(
        {
            "name": "component_failure_rollback",
            "status": "passed" if rollback_ok else "failed",
            "closed_count": sum(item.closed for item in resources.values()),
        }
    )

    order.clear()
    resources = {name: Resource(name, name == "evolution_store") for name in names}
    await factory.ComponentFactory._rollback_build_components(
        resources["scheduler"],
        resources["conversation"],
        resources["engine"],
        resources["graph"],
        resources["db"],
        resources["evolution_manager"],
        resources["evolution_store"],
        resources["identity"],
    )
    failure_count = sum(not item.closed for item in resources.values())
    containment_ok = (
        order == list(names) and resources["db"].closed and failure_count == 1
    )
    results.append(
        {
            "name": "rollback_failure_containment",
            "status": "passed" if containment_ok else "failed",
            "failure_count": failure_count,
            "original_error_preserved": preserved,
        }
    )
    return results


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    """执行真实 PluginManager namespace 生命周期。"""

    if _detected_version() != args.version:
        raise WorkerError("worker_astrbot_version_mismatch")
    manager_module = importlib.import_module("astrbot.core.star.star_manager")
    star_module = importlib.import_module("astrbot.core.star.star")
    handler_module = importlib.import_module("astrbot.core.star.star_handler")
    tools_module = importlib.import_module("astrbot.core.provider.register")
    star_tools_module = importlib.import_module("astrbot.core.star.star_tools")
    star_module.star_map.clear()
    star_module.star_registry.clear()
    handler_module.star_handlers_registry.clear()
    tools_module.llm_tools.func_list.clear()

    context = _HarnessContext(star_module.star_registry)
    setattr(manager_module, "sp", _MemoryPreferences())

    async def no_sync() -> None:
        """禁止命令配置同步访问持久化状态。"""

        return None

    setattr(manager_module, "sync_command_configs", no_sync)
    manager = manager_module.PluginManager(context, {})
    manager.plugin_store_path = str(args.plugin_root.parent)
    manager.plugin_config_path = str(args.plugin_root.parent / ".config")
    manager.reserved_plugin_path = str(args.plugin_root.parent / ".reserved")
    Path(manager.plugin_config_path).mkdir(parents=True, exist_ok=True)
    Path(manager.reserved_plugin_path).mkdir(parents=True, exist_ok=True)
    args.data_dir.mkdir(parents=True, exist_ok=True)
    star_tools_module.StarTools.get_data_dir = classmethod(
        lambda _cls, _plugin_name=None: args.data_dir
    )
    original_import = manager._import_plugin_with_dependency_recovery
    instances: list[Any] = []

    async def instrumented_import(*values: Any, **keywords: Any) -> Any:
        """导入真实 namespace 后替换网络/重型启动边界。"""

        plugin_module = await original_import(*values, **keywords)
        plugin_type = getattr(plugin_module, "MemoraPlugin", None)
        if plugin_type is None:
            raise WorkerError("memora_plugin_class_missing")
        if getattr(plugin_type, "_harness_instrumented", False):
            return plugin_module
        original_init = plugin_type.__init__
        original_create = getattr(plugin_type, "_create_tracked_task", None)

        def tracked_init(self: Any, *args: Any, **kwargs: Any) -> None:
            """执行真实构造并额外保存实例引用。"""

            original_init(self, *args, **kwargs)
            instances.append(self)

        plugin_type.__init__ = tracked_init
        if callable(original_create):

            def tracked_task(self: Any, coro: Any) -> asyncio.Task[Any]:
                """保留启动任务引用，避免 done callback 隐藏异常。"""

                task = cast(asyncio.Task[Any], original_create(self, coro))
                self.__dict__.setdefault("_harness_startup_tasks", []).append(task)
                return task

            plugin_type._create_tracked_task = tracked_task
        if hasattr(plugin_type, "_initialize_plugin"):

            async def isolated_startup(self: Any) -> None:
                """保持真实构造/关停，隔离 Provider 与持久组件启动。"""

                self._harness_lifecycle_state = "WAITING_PROVIDER"
                self._harness_lifecycle_state = "RUNNING"

            plugin_type._initialize_plugin = isolated_startup
        if args.inject_initialization_failure:

            async def injected_initialize(_self: Any) -> None:
                """让 PluginManager load 观察确定性初始化失败。"""

                raise RuntimeError("injected_initialization_failure")

            plugin_type.initialize = injected_initialize
        plugin_type._harness_instrumented = True
        return plugin_module

    manager._import_plugin_with_dependency_recovery = instrumented_import
    loaded = _manager_succeeded(await manager.load(specified_dir_name=PLUGIN_DIR_NAME))
    if args.inject_initialization_failure:
        for instance in instances:
            await _wait_startup(instance)
            terminate = getattr(instance, "terminate", None)
            if callable(terminate):
                await cast(Callable[[], Awaitable[Any]], terminate)()
        routes = _route_counts(context, instances)
        failure = {
            "expected_failure_observed": not loaded,
            "failure_scope": "plugin_initialize_only",
            "migration_rollback_evidence": "not_claimed",
            "instances": len(instances),
            "tasks": sum(
                len(getattr(item, "_background_tasks", ())) for item in instances
            ),
            "stale_routes": routes["stale"],
        }
        failure["status"] = (
            "passed"
            if failure["expected_failure_observed"]
            and failure["tasks"] == 0
            and failure["stale_routes"] == 0
            else "failed"
        )
        return {
            "schema": REPORT_SCHEMA,
            "version": args.version,
            "status": failure["status"],
            "namespace": failure,
            "scenarios": [],
        }
    if not loaded:
        raise WorkerError("plugin_initial_load_failed")

    cycles: list[dict[str, Any]] = []
    for cycle in range(1, args.cycles + 1):
        metadata = context.get_registered_star(PLUGIN_DIR_NAME)
        if metadata is None or metadata.star_cls is None:
            raise WorkerError("plugin_registration_missing")
        await _wait_startup(metadata.star_cls)
        cycles.append(
            _snapshot(
                star_module.star_map,
                star_module.star_registry,
                handler_module.star_handlers_registry,
                tools_module.llm_tools,
                metadata.star_cls,
                context,
                cycle,
            )
        )
        if cycle < args.cycles and not _manager_succeeded(
            await manager.reload(metadata.name)
        ):
            raise WorkerError("plugin_reload_failed")

    metadata = context.get_registered_star(PLUGIN_DIR_NAME)
    assert metadata is not None and metadata.star_cls is not None
    terminate = getattr(metadata.star_cls, "terminate", None)
    terminate_errors: list[str] = []
    if callable(terminate):
        terminate_async = cast(Callable[[], Awaitable[Any]], terminate)
        calls = terminate_async(), terminate_async()
        values = await asyncio.gather(*calls, return_exceptions=True)
        terminate_errors.extend(
            type(item).__name__ for item in values if isinstance(item, BaseException)
        )
        try:
            await terminate_async()
        except BaseException as exc:
            terminate_errors.append(type(exc).__name__)
    await manager._unbind_plugin(metadata.name, metadata.module_path)
    routes = _route_counts(context, instances)
    final = {
        "registrations": int(PLUGIN_MODULE in star_module.star_map)
        + sum(
            getattr(item, "module_path", None) == PLUGIN_MODULE
            for item in star_module.star_registry
        ),
        "handlers": len(
            handler_module.star_handlers_registry.get_handlers_by_module_name(
                PLUGIN_MODULE
            )
        ),
        "decorated_tools": _owned_count(tools_module.llm_tools.func_list),
        "runtime_tools": len(context.runtime_tools),
        "stale_routes": routes["stale"],
        "tasks": sum(len(getattr(item, "_background_tasks", ())) for item in instances),
        "connections": 0,
        "handles": 0,
    }
    namespace = {
        "cycles": cycles,
        "terminate_error_types": terminate_errors,
        "final_resources": final,
    }
    namespace["status"] = (
        "passed"
        if not terminate_errors and namespace_contract_passed(namespace, args.cycles)
        else "failed"
    )
    scenarios: list[dict[str, Any]] = []
    if args.scenario_mode == "all":
        package = PLUGIN_MODULE.rsplit(".", 1)[0]
        scenarios.extend(await _provider_scenarios(package))
        scenarios.extend(await _initializer_scenarios(package, args.data_dir))
    status = (
        "passed"
        if namespace["status"] == "passed"
        and all(item["status"] == "passed" for item in scenarios)
        else "failed"
    )
    return {
        "schema": REPORT_SCHEMA,
        "version": args.version,
        "status": status,
        "namespace": namespace,
        "scenarios": scenarios,
    }


def worker_entry(argv: Sequence[str]) -> int:
    """执行 worker 并始终写出机器可读结果。"""

    args = _parse_args(argv)
    try:
        payload = asyncio.run(_run(args))
        exit_code = 0 if payload.get("status") == "passed" else 1
    except BaseException as exc:
        payload = {
            "schema": REPORT_SCHEMA,
            "version": args.version,
            "status": "error",
            "reason_code": str(exc)
            if isinstance(exc, WorkerError)
            else "worker_unhandled_error",
            "error_type": type(exc).__name__,
        }
        exit_code = 2
    _atomic_write_json(args.worker_report, payload)
    return exit_code


__all__ = ["namespace_contract_passed", "worker_entry"]
