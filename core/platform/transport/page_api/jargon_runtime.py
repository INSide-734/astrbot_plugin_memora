"""黑话 Page API 的运行时组件解析辅助。"""

from __future__ import annotations

import asyncio
from typing import Any

from ...config.feature_config import is_jargon_discovery_enabled
from .response_utils import error_response


class _DynamicLogger:
    """把日志调用动态转发到原 API 模块，保留模块替换点。"""

    @staticmethod
    def _logger():
        """返回当前绑定到原 API 模块的 logger。"""
        from . import jargon_api

        return jargon_api.logger

    def debug(self, *args: Any, **kwargs: Any) -> None:
        """转发 debug 日志。"""
        self._logger().debug(*args, **kwargs)

    def info(self, *args: Any, **kwargs: Any) -> None:
        """转发 info 日志。"""
        self._logger().info(*args, **kwargs)

    def warning(self, *args: Any, **kwargs: Any) -> None:
        """转发 warning 日志。"""
        self._logger().warning(*args, **kwargs)

    def error(self, *args: Any, **kwargs: Any) -> None:
        """转发 error 日志。"""
        self._logger().error(*args, **kwargs)


logger = _DynamicLogger()


class JargonRuntimeMixin:
    """解析黑话过滤器、存储、服务和挖掘器，不承载 HTTP 端点。"""

    def _get_feature_delegation(self) -> Any | None:
        """从插件属性中解析 ``FeatureDelegation`` 实例。"""
        plugin = getattr(self, "plugin", None)
        if plugin is None:
            return None
        fd = getattr(plugin, "feature_delegation", None)
        if fd is not None:
            return fd
        initializer = getattr(plugin, "initializer", None)
        if initializer is not None:
            return getattr(initializer, "feature_delegation", None)
        return None

    def _get_jargon_filter(self) -> Any | None:
        """从插件属性中惰性解析 ``JargonStatisticalFilter``。"""
        plugin = getattr(self, "plugin", None)
        if plugin is None:
            return None
        for attr_name in ("_jargon_filter", "jargon_filter"):
            obj = getattr(plugin, attr_name, None)
            if obj is not None:
                return obj
        initializer = getattr(plugin, "initializer", None)
        if initializer is not None:
            obj = getattr(initializer, "jargon_filter", None)
            if obj is not None:
                return obj
        # 惰性创建并缓存
        from ....features.cognition.jargon.statistical_filter import (
            JargonStatisticalFilter,
        )

        jf = JargonStatisticalFilter()
        plugin._jargon_filter = jf
        logger.info("[黑话接口] 已惰性创建黑话统计过滤器实例")
        return jf

    def _get_jargon_resolution_lock(self) -> asyncio.Lock:
        """同步创建 plugin-scoped 解析锁；检查和赋值之间没有 suspension。"""

        plugin = self.plugin
        lock = getattr(plugin, "_jargon_resolution_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            plugin._jargon_resolution_lock = lock
        return lock

    @staticmethod
    def _is_closed_jargon_store(store: Any) -> bool:
        """仅识别已初始化后关闭的真实 ``JargonStore`` 实例。"""

        from ....features.cognition.jargon.jargon_store import JargonStore

        return (
            isinstance(store, JargonStore)
            and getattr(store, "_initialized", False) is True
            and store.connection is None
        )

    def _find_open_jargon_store(self, plugin: Any) -> tuple[Any | None, str | None]:
        """查找可用 store，并保留已关闭真实 store 的数据库路径。"""

        closed_db_path = None
        initializer = getattr(plugin, "initializer", None)
        candidates = (
            getattr(plugin, "_jargon_store", None),
            getattr(plugin, "jargon_store", None),
            getattr(initializer, "jargon_store", None)
            if initializer is not None
            else None,
        )
        for store in candidates:
            if store is None:
                continue
            if self._is_closed_jargon_store(store):
                closed_db_path = getattr(store, "db_path", closed_db_path)
                continue
            return store, closed_db_path
        return None, closed_db_path

    @staticmethod
    async def _close_unpublished_jargon_store(store: Any) -> None:
        """将 cleanup 跑到确定结束，并隐藏 cleanup 自身的失败。"""

        try:
            close_task = asyncio.ensure_future(store.close())
        except BaseException:
            return
        while not close_task.done():
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                continue
        try:
            close_task.result()
        except BaseException:
            pass

    async def _get_jargon_store_locked(self, plugin: Any) -> Any | None:
        """在调用方已经持有解析锁时解析或初始化 store。"""

        store, closed_db_path = self._find_open_jargon_store(plugin)
        if store is not None:
            return store

        from pathlib import Path

        from ....features.cognition.jargon.jargon_store import JargonStore

        data_dir = getattr(plugin, "data_dir", None)
        initializer = getattr(plugin, "initializer", None)
        if data_dir is None and initializer is not None:
            data_dir = getattr(initializer, "data_dir", None)
        if data_dir is not None:
            db_path = str(Path(data_dir) / "jargon.db")
        elif closed_db_path is not None:
            db_path = closed_db_path
        else:
            logger.warning("[黑话接口] 无法惰性创建黑话存储：未找到数据目录")
            return None

        store = JargonStore(db_path)
        try:
            await store.initialize()
        except BaseException:
            await self._close_unpublished_jargon_store(store)
            raise
        plugin._jargon_store = store
        logger.info("[黑话接口] 已惰性创建黑话存储实例")
        return store

    async def _get_jargon_store(self) -> Any | None:
        """并发安全地解析或创建唯一的 plugin-scoped ``JargonStore``。"""

        plugin = getattr(self, "plugin", None)
        if plugin is None:
            return None
        store, _ = self._find_open_jargon_store(plugin)
        if store is not None:
            return store
        lock = self._get_jargon_resolution_lock()
        async with lock:
            return await self._get_jargon_store_locked(plugin)

    def _get_current_jargon_query_service(self) -> Any | None:
        """同步读取当前 query service，不缓存可被替换的 bound method。"""

        plugin = getattr(self, "plugin", None)
        if plugin is None:
            return None
        query_service = (
            getattr(plugin, "_jargon_query", None)
            or getattr(plugin, "jargon_query", None)
            or getattr(plugin, "jargon_query_service", None)
        )
        initializer = getattr(plugin, "initializer", None)
        if query_service is None and initializer is not None:
            query_service = (
                getattr(initializer, "_jargon_query", None)
                or getattr(initializer, "jargon_query", None)
                or getattr(initializer, "jargon_query_service", None)
            )
        return query_service

    def _invalidate_current_jargon_query(self, group_id: str) -> None:
        """在每次提交后发现并失效当前 query service。"""

        query_service = self._get_current_jargon_query_service()
        invalidator = getattr(query_service, "invalidate_group", None)
        if callable(invalidator):
            invalidator(group_id)

    async def _get_jargon_admin_service(self) -> Any | None:
        """解析并在插件上缓存唯一的 ``JargonAdminService``。"""

        plugin = getattr(self, "plugin", None)
        if plugin is None:
            logger.warning("[黑话接口] operation=resolve_service unavailable=plugin")
            return None
        cached = getattr(plugin, "_jargon_admin_service", None)
        if cached is not None and not self._is_closed_jargon_store(
            getattr(cached, "_store", None)
        ):
            return cached

        lock = self._get_jargon_resolution_lock()
        try:
            async with lock:
                cached = getattr(plugin, "_jargon_admin_service", None)
                if cached is not None and not self._is_closed_jargon_store(
                    getattr(cached, "_store", None)
                ):
                    return cached
                store = await self._get_jargon_store_locked(plugin)
                if store is None:
                    logger.warning(
                        "[黑话接口] operation=resolve_service unavailable=store"
                    )
                    return None

                from ....features.cognition.jargon.jargon_admin_service import (
                    JargonAdminService,
                )

                service = JargonAdminService(
                    store,
                    self._invalidate_current_jargon_query,
                )
                plugin._jargon_admin_service = service
                return service
        except Exception as exc:
            logger.error(
                "[黑话接口] operation=resolve_service error_class=%s",
                type(exc).__name__,
            )
            return None

    async def _get_jargon_miner(self) -> Any | None:
        """惰性解析或创建 ``JargonMiner``。

        优先从插件或初始化器上查找现有 miner。
        若不存在，则惰性创建一个新实例（依赖 LLM provider、filter 与 store），
        并缓存到 ``plugin._jargon_miner``。

        ``jargon.enabled`` 关闭时直接返回 ``None``，避免页面 API 绕过
        初始化器重新创建自动发现组件。
        """
        plugin = getattr(self, "plugin", None)
        if plugin is None:
            return None
        if not is_jargon_discovery_enabled(getattr(plugin, "config_manager", None)):
            logger.info("[黑话接口] 黑话自动发现功能已禁用")
            return None
        for attr_name in ("_jargon_miner", "jargon_miner"):
            obj = getattr(plugin, attr_name, None)
            if obj is not None:
                return obj
        initializer = getattr(plugin, "initializer", None)
        if initializer is not None:
            obj = getattr(initializer, "jargon_miner", None)
            if obj is not None:
                return obj

        # ── 惰性创建并缓存 ──
        from ....features.cognition.jargon.jargon_miner import JargonMiner

        # 解析依赖项
        jf = self._get_jargon_filter()
        if jf is None:
            logger.warning("[黑话接口] 无法惰性创建黑话挖掘器：过滤器不可用")
            return None

        store = await self._get_jargon_store()
        if store is None:
            logger.warning("[黑话接口] 无法惰性创建黑话挖掘器：存储不可用")
            return None

        # 从初始化器解析 LLM 客户端
        llm_client = None
        if initializer is not None:
            llm_client = getattr(initializer, "llm_provider", None)
        if llm_client is None:
            # 回退：尝试从插件上下文获取
            ctx = getattr(plugin, "context", None)
            if ctx is not None and hasattr(ctx, "get_using_provider"):
                try:
                    llm_client = ctx.get_using_provider()
                except Exception as exc:
                    logger.debug(
                        "[黑话接口] operation=%s error_class=%s",
                        "resolve_miner_provider",
                        type(exc).__name__,
                    )
        if llm_client is None:
            logger.warning("[黑话接口] 无法惰性创建黑话挖掘器：LLM 提供器不可用")
            return None

        miner = JargonMiner(llm_client, jf, store)
        plugin._jargon_miner = miner
        logger.info("[黑话接口] 已惰性创建黑话挖掘器实例")
        return miner

    @staticmethod
    def _require_group_id(args: Any) -> tuple[str | None, dict | None]:
        """提取并校验 ``group_id`` 查询参数。

        返回:
            成功时返回 ``(group_id, None)``，失败时返回
            ``(None, error_response)``。
        """
        group_id = (args.get("group_id", "") or "").strip()
        if not group_id:
            return None, error_response("缺少必填参数 group_id")
        return group_id, None

    plugin: Any
