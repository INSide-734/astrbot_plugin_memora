"""话题目录运行期 dirty 周期收敛调度器。"""

import asyncio
import contextlib
from typing import Any

from astrbot.api import logger

#: 目录收敛间隔配置叶（0 表示禁用），worker 每轮重读以支持热重载。
CONFIG_INTERVAL_KEY = "topic_segmentation.catalog_reconcile_interval_seconds"
#: 配置缺失或非法时回退的收敛间隔（秒）。
DEFAULT_INTERVAL_SECONDS = 60
#: 单轮修复批量上限，与写路径 repair_pending 使用的批大小一致。
REPAIR_BATCH_LIMIT = 25


class TopicCatalogReconcileScheduler:
    """周期消费话题目录 dirty 队列的运行期收敛 worker。

    生命周期与 ``DecayScheduler`` 保持一致：``start()`` 幂等，
    ``stop()`` 取消并等待后台任务；``asyncio.CancelledError`` 只在
    ``stop()`` 边界被吞掉，循环体内始终传播。依赖仅有 catalog_store
    端口与 config_manager（每轮重读配置，支持热重载），不取协调器锁，
    与关停/启动修复路径靠 lease owner CAS 互斥。
    """

    def __init__(self, catalog_store: Any, config_manager: Any) -> None:
        """保存目录端口与配置读取器并初始化生命周期状态。

        参数:
            catalog_store: 提供 ``get_state``/``list_dirty``/
                ``repair_pending`` 的 ``TopicCatalogStore`` 端口。
            config_manager: 提供点号路径 ``get`` 的配置读取器。
        """

        self.catalog_store = catalog_store
        self.config_manager = config_manager
        self._task: asyncio.Task | None = None
        self._running = False

    @property
    def owner_token(self) -> str:
        """返回 repair 领取使用的稳定 owner 标识。"""

        return f"catalog-reconcile-{id(self)}"

    @staticmethod
    def _log_task_exception(task: asyncio.Task) -> None:
        """记录后台任务的普通异常，并忽略已取消任务。"""

        if task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc:
            logger.error("[目录收敛] 后台任务异常", exc_info=exc)

    def _read_interval_seconds(self) -> int:
        """每轮重读收敛间隔配置，读取失败时回退默认值。"""

        try:
            return int(
                self.config_manager.get(CONFIG_INTERVAL_KEY, DEFAULT_INTERVAL_SECONDS)
            )
        except (TypeError, ValueError):
            return DEFAULT_INTERVAL_SECONDS

    async def _reconcile_once(self) -> None:
        """执行单轮收敛：禁用或 catalog 未就绪时跳过本轮。"""

        if self._read_interval_seconds() <= 0:
            return
        state = await self.catalog_store.get_state()
        if state.get("status") != "ready" or not state.get("active_generation"):
            # degraded/backfilling 归启动重建路径处理，worker 不介入。
            return
        dirty = await self.catalog_store.list_dirty(
            states=("pending", "failed"), limit=1
        )
        if not dirty:
            # 排空即停：不做全表 count，只探测一条。
            return
        repaired = await self.catalog_store.repair_pending(
            self.owner_token, limit=REPAIR_BATCH_LIMIT
        )
        if repaired:
            logger.info(
                f"[目录收敛] 本轮修复 {repaired} 条 dirty (owner={self.owner_token})"
            )

    async def _scheduler_loop(self) -> None:
        """按配置间隔循环执行单轮收敛，普通失败记日志后继续。"""

        while self._running:
            interval = self._read_interval_seconds()
            # 禁用（interval <= 0）时保持默认重读周期，热重载后可重新启用。
            await asyncio.sleep(interval if interval > 0 else DEFAULT_INTERVAL_SECONDS)
            if not self._running:
                break
            try:
                await self._reconcile_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "[目录收敛] 单轮修复失败 "
                    f"(reason_code=catalog_reconcile_round_failed, "
                    f"error={type(error).__name__})"
                )

    async def start(self) -> None:
        """幂等启动周期收敛循环任务。"""

        if self._running:
            logger.warning("[目录收敛] 调度器已在运行")
            return

        self._running = True
        self._task = asyncio.create_task(self._scheduler_loop())
        self._task.add_done_callback(self._log_task_exception)
        logger.info(f"[目录收敛] 调度器已启动 (owner={self.owner_token})")

    async def stop(self) -> None:
        """停止并等待后台任务，CancelledError 只在此边界吞掉。"""

        self._running = False

        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

        self._task = None
        logger.info("[目录收敛] 调度器已停止")


__all__ = ["TopicCatalogReconcileScheduler"]
