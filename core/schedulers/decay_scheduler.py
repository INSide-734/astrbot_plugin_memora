"""
记忆重要性衰减调度器
每日自动对记忆重要性进行衰减处理，并定期备份数据库
"""

import asyncio
import contextlib
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from astrbot.api import logger

if TYPE_CHECKING:
    from ..managers.backup_manager import BackupManager
    from ..managers.memory_engine import MemoryEngine


class DecayScheduler:
    """
    记忆重要性衰减调度器

    功能：
    1. 每日凌晨自动执行衰减
    2. 启动时检查并补偿错过的衰减
    3. 防止同一天重复执行
    4. 定期自动备份数据库
    """

    def __init__(
        self,
        memory_engine: "MemoryEngine",
        decay_rate: float,
        data_dir: str,
        check_hour: int = 0,
        check_minute: int = 5,
        backup_manager: "BackupManager | None" = None,
        backup_enabled: bool = True,
        backup_keep_days: int = 7,
    ):
        """
        初始化衰减调度器

        参数:
            memory_engine: 记忆引擎实例
            decay_rate: 每日衰减率 (0-1)
            data_dir: 数据目录，用于存储状态文件
            check_hour: 每日执行时间（小时）
            check_minute: 每日执行时间（分钟）
            backup_manager: 备份管理器（用于每日备份）
            backup_enabled: 是否启用每日自动备份
            backup_keep_days: 备份保留天数，超期自动删除
        """
        self.memory_engine = memory_engine
        self.decay_rate = decay_rate
        self.data_dir = Path(data_dir)
        self.check_hour = check_hour
        self.check_minute = check_minute
        self.backup_manager = backup_manager
        self.backup_enabled = backup_enabled
        self.backup_keep_days = backup_keep_days

        self._state_file = self.data_dir / "decay_state.json"
        self._task: asyncio.Task | None = None
        self._startup_task: asyncio.Task | None = None
        self._running = False
        self.last_backup_result: dict[str, object] = {
            "status": "idle",
            "reason_code": None,
        }
        self.last_backup_prune: dict[str, object] = {
            "removed": [],
            "skipped": [],
            "failed": [],
        }

    @staticmethod
    def _log_task_exception(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc:
            logger.error("[衰减调度] 后台任务异常", exc_info=exc)

    async def _load_state(self) -> dict:
        """加载状态文件"""
        if not self._state_file.exists():
            return {}
        try:
            try:
                import aiofiles
            except ImportError:
                content = await asyncio.to_thread(
                    self._state_file.read_text,
                    encoding="utf-8",
                )
            else:
                async with aiofiles.open(self._state_file, encoding="utf-8") as f:
                    content = await f.read()
            return json.loads(content)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[衰减调度] 加载状态文件失败: {e}")
            return {}

    async def _save_state(self, state: dict) -> None:
        """保存状态文件"""
        tmp_file: Path | None = None
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            content = json.dumps(state, ensure_ascii=False)
            tmp_file = self._state_file.with_name(f"{self._state_file.name}.tmp")
            try:
                import aiofiles
            except ImportError:
                await asyncio.to_thread(
                    tmp_file.write_text,
                    content,
                    encoding="utf-8",
                )
            else:
                async with aiofiles.open(tmp_file, "w", encoding="utf-8") as f:
                    await f.write(content)
            await asyncio.to_thread(tmp_file.replace, self._state_file)
        except OSError as e:
            with contextlib.suppress(OSError, AttributeError):
                if tmp_file is not None:
                    tmp_file.unlink()
            logger.error(f"[衰减调度] 保存状态文件失败: {e}")
            logger.error(f"[衰减调度] 保存状态文件失败: {e}")

    async def _get_last_decay_date(self) -> str | None:
        """获取上次衰减日期 (格式: YYYY-MM-DD)"""
        state = await self._load_state()
        return state.get("last_decay_date")

    async def _set_last_decay_date(self, date_str: str) -> None:
        """设置上次衰减日期"""
        state = await self._load_state()
        state["last_decay_date"] = date_str
        state["last_decay_timestamp"] = time.time()
        await self._save_state(state)

    @staticmethod
    def _get_today_str() -> str:
        """获取今天日期字符串"""
        return datetime.now().strftime("%Y-%m-%d")

    async def _calculate_missed_days(self) -> int:
        """计算错过的衰减天数"""
        last_date_str = await self._get_last_decay_date()
        if not last_date_str:
            return 0

        try:
            last_date = datetime.strptime(last_date_str, "%Y-%m-%d").date()
            today = datetime.now().date()
            delta = (today - last_date).days
            return max(0, delta - 1)
        except ValueError:
            return 0

    async def _execute_decay(self, days: int = 1) -> bool:
        """
        执行衰减操作

        Args:
            days: 衰减天数（用于补偿错过的天数）

        返回:
            是否执行成功
        """
        try:
            if self.decay_rate > 0:
                affected = await self.memory_engine.apply_daily_decay(
                    self.decay_rate, days
                )
                logger.info(
                    f"[衰减调度] 衰减完成，影响 {affected} 条记忆，衰减天数: {days}"
                )
            else:
                logger.info("[衰减调度] 衰减率为0，跳过衰减")

            # 每日衰减后可选执行一次旧记忆清理（三阶段分层遗忘）
            if self.memory_engine.config.get("auto_cleanup_enabled", True):
                try:
                    cleanup_days = self.memory_engine.config.get(
                        "cleanup_days_threshold", 30
                    )
                    cleanup_importance = self.memory_engine.config.get(
                        "cleanup_importance_threshold", 0.3
                    )
                    processed = await self.memory_engine.cleanup_old_memories(
                        days_threshold=cleanup_days,
                        importance_threshold=cleanup_importance,
                    )
                    logger.info(f"[衰减调度] 分层遗忘完成，处理 {processed} 条记忆")
                except Exception as cleanup_err:
                    logger.error(
                        f"[衰减调度] 自动清理失败: {cleanup_err}", exc_info=True
                    )

            # 梦境整合：衰减后巩固高重要性记忆的关联网络
            try:
                consolidate_result = await self.memory_engine.consolidate_memories()
                paired = consolidate_result.get("paired", 0)
                if paired:
                    logger.info(f"[衰减调度] 梦境整合完成，巩固 {paired} 对记忆")
            except Exception as cons_err:
                logger.warning(f"[衰减调度] 梦境整合异常: {cons_err}", exc_info=True)

            await self._set_last_decay_date(self._get_today_str())

            # 每日执行备份
            if self.backup_enabled and self.backup_manager:
                await self._run_backup()

            try:
                maintenance_result = await self.memory_engine.maintain_storage()
                if maintenance_result.get("success"):
                    reclaimed = int(maintenance_result.get("bytes_reclaimed", 0))
                    logger.info(
                        f"[衰减调度] 存储维护完成，释放 {reclaimed / 1024 / 1024:.2f} MB"
                    )
                else:
                    logger.warning(
                        f"[衰减调度] 存储维护失败: {maintenance_result.get('error')}"
                    )
            except Exception as maintenance_err:
                logger.warning(
                    f"[衰减调度] 存储维护异常: {maintenance_err}",
                    exc_info=True,
                )

            await self._run_optional_maintenance()

            return True
        except Exception as e:
            logger.error(f"[衰减调度] 执行衰减失败: {e}", exc_info=True)
            return False

    async def _check_and_execute(self) -> None:
        """检查并执行衰减（启动时调用）"""
        today_str = self._get_today_str()
        last_date_str = await self._get_last_decay_date()

        if last_date_str == today_str:
            logger.debug("[衰减调度] 今日已执行过衰减，跳过")
            return

        missed_days = await self._calculate_missed_days()
        total_days = missed_days + 1

        if missed_days > 0:
            logger.info(f"[衰减调度] 检测到错过 {missed_days} 天衰减，执行补偿")

        await self._execute_decay(total_days)

    async def _run_optional_maintenance(self) -> None:
        """执行子维护任务（独立 try/except，单点失败不影响其他）"""
        engine = self.memory_engine

        # 用户画像标签衰减
        try:
            profile_mgr = getattr(engine, "profile_manager", None)
            if profile_mgr is not None:
                decay_all = getattr(profile_mgr, "decay_and_clean_all", None)
                has_decay_all = hasattr(
                    type(profile_mgr), "decay_and_clean_all"
                ) or "decay_and_clean_all" in vars(profile_mgr)
                if decay_all is not None and has_decay_all:
                    result = await decay_all()
                    removed = int(result.get("removed", 0))
                    failed = int(result.get("failed", 0))
                    if removed or failed:
                        logger.info(
                            f"[衰减调度] 画像衰减完成: removed={removed}, failed={failed}"
                        )
                else:
                    logger.warning(
                        "[衰减调度] 画像管理器缺少 decay_and_clean_all，已跳过"
                    )
        except Exception as e:
            logger.warning(f"[衰减调度] 画像衰减异常: {e}")

        # 知识库过期清理
        try:
            knowledge_mgr = getattr(engine, "knowledge_manager", None)
            if knowledge_mgr is not None:
                removed = await knowledge_mgr.cleanup_expired()
                if removed:
                    logger.info(f"[衰减调度] 知识库清理: {removed} 条")
        except Exception as e:
            logger.warning(f"[衰减调度] 知识库清理异常: {e}")

        # 自主学习参数优化
        try:
            auto_learning = getattr(engine, "auto_learning", None)
            if auto_learning is not None:
                await auto_learning.optimize()
        except Exception as e:
            logger.warning(f"[衰减调度] 自主学习优化异常: {e}")

        # 笔记版本清理
        try:
            note_mgr = getattr(engine, "note_manager", None)
            if note_mgr is not None:
                max_versions = int(engine.config.get("notes.max_versions", 20))
                await note_mgr.prune_versions(max_versions)
        except Exception as e:
            logger.warning(f"[衰减调度] 笔记版本清理异常: {e}")

        # 前瞻记忆：扫描未来 24 小时内的 PLANNED 原子并缓存待注入
        try:
            atom_store = getattr(engine, "atom_store", None)
            if atom_store is not None:
                upcoming = await atom_store.query_upcoming_planned(lookahead_sec=86400)
                if upcoming:
                    engine._pending_proactive = upcoming
                    logger.info(f"[衰减调度] 前瞻记忆: {len(upcoming)} 条待注入")
        except Exception as e:
            logger.warning(f"[衰减调度] 前瞻记忆扫描异常: {e}")

    async def _run_backup(self) -> None:
        """执行定时备份并委托管理器清理过期备份。"""
        if not self.backup_manager:
            return
        try:
            result = await self.backup_manager.create_backup(kind="scheduled")
        except Exception as exc:
            self.last_backup_result = {
                "status": "failed",
                "reason_code": "backup_create_failed",
            }
            logger.error(
                "[衰减调度] 定时备份失败 error_class=%s",
                type(exc).__name__,
            )
            return

        if not result:
            self.last_backup_result = {
                "status": "failed",
                "reason_code": "backup_create_failed",
            }
            logger.warning("[衰减调度] 定时备份失败")
            return

        backup_name = result.get("name") if isinstance(result, dict) else None
        self.last_backup_result = {
            "status": "succeeded",
            "name": str(backup_name) if backup_name else None,
        }
        logger.info("[衰减调度] 定时备份完成")
        try:
            prune_result = self.backup_manager.prune_backups(
                keep_days=self.backup_keep_days
            )
            self.last_backup_prune = (
                prune_result if isinstance(prune_result, dict) else {"removed": []}
            )
        except Exception:
            self.last_backup_prune = {
                "removed": [],
                "skipped": [],
                "failed": [{"reason_code": "backup_prune_failed"}],
            }
            logger.warning("[衰减调度] 定时备份清理失败")

    async def _cleanup_old_backups(self) -> None:
        """兼容旧调用，转交 BackupManager 的保留策略。"""
        if not self.backup_manager:
            return
        try:
            result = self.backup_manager.prune_backups(keep_days=self.backup_keep_days)
            if isinstance(result, dict):
                self.last_backup_prune = result
        except Exception:
            logger.warning("[衰减调度] 定时备份清理失败")

    def _seconds_until_next_run(self) -> float:
        """计算距离下次执行的秒数"""
        now = datetime.now()
        target = now.replace(
            hour=self.check_hour,
            minute=self.check_minute,
            second=0,
            microsecond=0,
        )

        if now >= target:
            target += timedelta(days=1)

        return (target - now).total_seconds()

    async def _scheduler_loop(self) -> None:
        """调度器主循环"""
        while self._running:
            try:
                wait_seconds = self._seconds_until_next_run()
                logger.debug(f"[衰减调度] 下次执行在 {wait_seconds / 3600:.1f} 小时后")

                await asyncio.sleep(wait_seconds)

                if not self._running:
                    break

                await self._execute_decay(1)

            except asyncio.CancelledError:
                logger.info("[衰减调度] 调度器被取消")
                break
            except Exception as e:
                logger.error(f"[衰减调度] 循环异常: {e}", exc_info=True)
                await asyncio.sleep(3600)

    async def start(self) -> None:
        """启动调度器"""
        if self._running:
            logger.warning("[衰减调度] 调度器已在运行")
            return

        self._running = True

        self._startup_task = asyncio.create_task(self._check_and_execute())
        self._startup_task.add_done_callback(self._log_task_exception)

        self._task = asyncio.create_task(self._scheduler_loop())
        self._task.add_done_callback(self._log_task_exception)
        logger.info(
            f"[衰减调度] 调度器已启动 (衰减率: {self.decay_rate}, "
            f"执行时间: {self.check_hour:02d}:{self.check_minute:02d})"
        )

    async def stop(self) -> None:
        """停止调度器"""
        self._running = False

        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

        if self._startup_task and not self._startup_task.done():
            self._startup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._startup_task

        self._task = None
        self._startup_task = None
        logger.info("[衰减调度] 调度器已停止")
