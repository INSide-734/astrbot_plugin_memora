"""数据库迁移 + 索引重建 + 消息计数修复"""

from astrbot.api import logger


class DatabaseSetup:
    """数据库初始化、索引一致性检查和修复"""

    def __init__(self, config_manager):
        """保存配置管理器，供启动期维护步骤读取配置。"""

        self.config_manager = config_manager

    @staticmethod
    async def auto_rebuild_index_if_needed(
        index_validator,
        memory_engine,
        rebuild_coordinator=None,
    ):
        """检查索引并在需要时执行统一派生重建。

        ``rebuild_coordinator`` 由组件工厂注入时，重建顺序由协调器固定为
        canonical、FTS/FAISS、graph、relation/projection；未注入时保留旧的
        FTS/FAISS-only 兼容路径，方便独立测试和延迟装配。
        """

        try:
            if not index_validator or not memory_engine:
                return {
                    "success": False,
                    "reason_code": "components_unavailable",
                }

            status = await index_validator.check_consistency()
            if not status.is_consistent and status.needs_rebuild:
                logger.warning(f"检测到索引不一致: {status.reason}")
                logger.info(
                    f"当前索引计数 - Documents: {status.documents_count}, "
                    f"BM25: {status.bm25_count}, Vector: {status.vector_count}"
                )
                if rebuild_coordinator is not None:
                    result = await rebuild_coordinator.rebuild_all()
                else:
                    result = await index_validator.rebuild_indexes(memory_engine)
                if result["success"]:
                    if "processed" in result:
                        logger.info(
                            f"索引自动重建完成: 成功 {result['processed']} 条, "
                            f"失败 {result['errors']} 条"
                        )
                    else:
                        logger.info("统一派生重建完成")
                else:
                    logger.error(
                        "索引自动重建失败: %s",
                        result.get("reason_code") or result.get("message"),
                    )
                return result
            else:
                logger.info(f"索引一致性检查通过: {status.reason}")
                return {
                    "success": True,
                    "skipped": True,
                    "reason_code": "indexes_consistent",
                }

        except Exception:
            logger.error("自动重建索引失败，reason_code=index_rebuild_failed")
            return {
                "success": False,
                "reason_code": "index_rebuild_failed",
            }

    @staticmethod
    async def repair_message_counts(conversation_store):
        """校正会话表中的消息计数，失败时仅记录并继续启动。"""

        try:
            logger.info("开始检查并修复 message_count 一致性。")
            fixed_sessions = await conversation_store.sync_message_counts()
            if fixed_sessions:
                logger.info(f"已修复 {len(fixed_sessions)} 个会话的 message_count")
            else:
                logger.debug("所有会话的 message_count 均正确")
        except Exception as e:
            logger.error(f"修复 message_count 失败: {e}", exc_info=True)
