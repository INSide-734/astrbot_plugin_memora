"""数据库迁移 + 索引重建 + 消息计数修复"""

from astrbot.api import logger


class DatabaseSetup:
    """数据库初始化、索引一致性检查和修复"""

    def __init__(self, config_manager):
        self.config_manager = config_manager

    @staticmethod
    async def auto_rebuild_index_if_needed(index_validator, memory_engine):
        try:
            if not index_validator or not memory_engine:
                return

            status = await index_validator.check_consistency()
            if not status.is_consistent and status.needs_rebuild:
                logger.warning(f"检测到索引不一致: {status.reason}")
                logger.info(
                    f"当前索引计数 - Documents: {status.documents_count}, "
                    f"BM25: {status.bm25_count}, Vector: {status.vector_count}"
                )
                result = await index_validator.rebuild_indexes(memory_engine)
                if result["success"]:
                    logger.info(
                        f"索引自动重建完成: 成功 {result['processed']} 条, 失败 {result['errors']} 条"
                    )
                else:
                    logger.error(f"索引自动重建失败: {result.get('message')}")
            else:
                logger.info(f"索引一致性检查通过: {status.reason}")

        except Exception as e:
            logger.error(f"自动重建索引失败: {e}", exc_info=True)

    @staticmethod
    async def repair_message_counts(conversation_store):
        try:
            logger.info("开始检查并修复 message_count 一致性。")
            fixed_sessions = await conversation_store.sync_message_counts()
            if fixed_sessions:
                logger.info(f"已修复 {len(fixed_sessions)} 个会话的 message_count")
            else:
                logger.debug("所有会话的 message_count 均正确")
        except Exception as e:
            logger.error(f"修复 message_count 失败: {e}", exc_info=True)
