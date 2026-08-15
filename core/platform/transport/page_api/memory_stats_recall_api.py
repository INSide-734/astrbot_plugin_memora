"""记忆统计与召回测试 Page API。"""

import asyncio
import time

from astrbot.api import logger
from quart import request

from ....shared.memory_status import effective_memory_status


def _coerce_count(value, default: int = 0) -> int:
    """将统计值转为非布尔整数，失败时返回默认值。

    参数:
        value: 待转换的内部统计值。
        default: 转换失败时使用的值。

    返回:
        转换后的整数或默认值。
    """

    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_count_map(value) -> dict:
    """把统计映射的键转为字符串，并安全规范化所有计数。"""
    if not isinstance(value, dict):
        return {}
    return {str(key): _coerce_count(count, 0) for key, count in value.items()}


def _default_importance_distribution() -> dict[str, int]:
    """返回前端要求的十个空重要性区间。"""
    return {f"{i}-{i + 1}": 0 for i in range(0, 10)}


def _coerce_score(value, default=None):
    """将非布尔评分转为浮点数，失败时返回默认值。"""
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_result_list(results):
    """将召回结果转为列表，不可迭代时返回空列表。"""
    try:
        return list(results or [])
    except Exception:
        return []


def _safe_score_breakdown_items(value):
    """读取评分分解项，容器不兼容时返回空列表。"""
    try:
        return list((value or {}).items())
    except Exception:
        return []


def _safe_recent_sessions(value) -> list[dict[str, int | str]]:
    """将 canonical 会话计数映射转换为兼容的最近会话列表。"""
    if not isinstance(value, dict):
        return []
    normalized = [
        {"session_id": str(session_id), "message_count": _coerce_count(count, 0)}
        for session_id, count in value.items()
    ]
    return sorted(normalized, key=lambda item: -item["message_count"])[:10]


class MemoryStatsRecallApiMixin:
    """提供统计信息与召回测试端点的 Page API 混入类。"""

    @staticmethod
    def _coerce_limit(raw_value) -> int:
        """将外部上限转为整数，同时拒绝 JSON 布尔值。

        参数:
            raw_value: 请求载荷中的原始上限。

        返回:
            转换后的整数。

        异常:
            TypeError: 原始值为布尔值。
            ValueError: 原始值不能转换为整数。
        """
        if isinstance(raw_value, bool):
            raise TypeError("布尔值不能作为整数上限")
        return int(raw_value)

    async def get_stats(self):
        """返回 canonical 统计，并用 ConversationStore 的真实会话覆盖会话面板。

        返回:
            Page API 成功或失败响应；会话管理器普通失败时保留 canonical 聚合。

        异常:
            asyncio.CancelledError: 会话读取或下游统计被取消时继续向上传播。
        """
        try:
            ready, error = await self._ensure_plugin_ready()
        except Exception as exc:
            logger.error(f"[PageAPI] 插件就绪检查失败: {exc}", exc_info=True)
            return self._error(f"插件就绪检查失败: {exc}")
        if error:
            return error
        memory_engine = ready["memory_engine"]

        try:
            stats = await memory_engine.get_statistics()
            if not isinstance(stats, dict):
                stats = {}

            breakdown = _normalize_count_map(stats.get("status_breakdown", {}))
            stats.setdefault("active_count", breakdown.get("active", 0))
            stats.setdefault("dormant_count", breakdown.get("dormant", 0))
            stats.setdefault("archived_count", breakdown.get("archived", 0))
            stats.setdefault("deleted_count", breakdown.get("deleted", 0))
            # 确保前端期望字段至少存在默认值
            for key in (
                "active_count",
                "dormant_count",
                "archived_count",
                "deleted_count",
                "graph_nodes",
                "graph_edges",
                "atom_count",
            ):
                stats[key] = _coerce_count(stats.get(key, 0), 0)

            graph_store = self._get_graph_store(memory_engine)
            if graph_store is not None:
                try:
                    entry_stats = await graph_store.get_memory_entry_stats()
                    if not isinstance(entry_stats, dict):
                        raise TypeError("图记忆条目统计必须是映射")
                    stats["graph_nodes"] = entry_stats.get("graph_nodes", 0)
                    stats["graph_edges"] = entry_stats.get("graph_edges", 0)
                    stats["graph_entries"] = entry_stats.get("graph_entries", 0)
                except Exception as exc:
                    logger.debug(
                        "[MemoryStatsRecallApi] 图记忆统计不可用: %s",
                        exc,
                        exc_info=True,
                    )
                    stats["graph_nodes"] = stats["graph_edges"] = stats[
                        "graph_entries"
                    ] = 0
            else:
                stats["graph_nodes"] = stats["graph_edges"] = stats["graph_entries"] = 0

            atom_store = getattr(memory_engine, "atom_store", None)
            stats["atom_count"] = 0
            stats["atom_breakdown"] = {}
            if atom_store is not None:
                try:
                    stats["atom_count"] = _coerce_count(
                        await atom_store.count_atoms(), 0
                    )
                except Exception as e:
                    logger.debug(f"获取记忆原子统计失败: {e}")
                try:
                    stats["atom_breakdown"] = _normalize_count_map(
                        await atom_store.count_by_type()
                    )
                except Exception as e:
                    logger.debug(f"获取记忆原子分类统计失败: {e}")

            importance_distribution = _normalize_count_map(
                stats.get("importance_distribution", {})
            )
            if importance_distribution:
                stats["importance_distribution"] = {
                    **_default_importance_distribution(),
                    **importance_distribution,
                }
            else:
                stats["importance_distribution"] = _default_importance_distribution()

            canonical_sessions = _normalize_count_map(stats.get("sessions", {}))
            stats["sessions"] = canonical_sessions
            stats["recent_sessions"] = _safe_recent_sessions(canonical_sessions)

            conversation_manager = ready.get("conversation_manager")
            if conversation_manager is not None:
                try:
                    live_sessions = await conversation_manager.get_recent_sessions(
                        limit=10
                    )
                    live_session_map: dict[str, int] = {}
                    live_recent_sessions: list[dict[str, int | str]] = []
                    for session in live_sessions:
                        session_id = getattr(session, "session_id", None)
                        if not isinstance(session_id, str) or not session_id.strip():
                            raise TypeError("真实会话缺少有效的会话 ID")
                        message_count = _coerce_count(
                            getattr(session, "message_count", 0), 0
                        )
                        live_session_map[session_id] = message_count
                        live_recent_sessions.append(
                            {
                                "session_id": session_id,
                                "message_count": message_count,
                            }
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.debug(
                        "[MemoryStatsRecallApi] 真实会话统计不可用，回退 canonical 聚合: %s",
                        type(exc).__name__,
                    )
                else:
                    stats["sessions"] = live_session_map
                    stats["recent_sessions"] = live_recent_sessions

            return self._ok(stats)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"[PageAPI] 获取统计信息失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def test_recall(self):
        """执行一次管理员召回测试并返回前端兼容的评分结果。

        返回:
            参数错误、运行失败或包含安全召回结果的 Page API 响应。
        """
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        memory_engine = ready["memory_engine"]

        payload = await request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return self._error("请求体必须为 JSON 对象")
        query_text = str(payload.get("query", "")).strip()
        if not query_text:
            return self._error("查询内容不能为空")

        try:
            k = min(
                50,
                max(1, MemoryStatsRecallApiMixin._coerce_limit(payload.get("k", 5))),
            )
        except (TypeError, ValueError):
            return self._error("k 必须是整数")

        session_id = payload.get("session_id")

        try:
            start_time = time.time()
            results = await memory_engine.search_memories(
                query=query_text,
                k=k,
                session_id=session_id,
                persona_id=None,
            )
            elapsed_time = (time.time() - start_time) * 1000
        except Exception as exc:
            logger.error(f"[PageAPI] 召回测试失败: {exc}", exc_info=True)
            return self._error(str(exc))

        results = _safe_result_list(results)
        formatted_results = []
        for result in results:
            try:
                doc_id = int(getattr(result, "doc_id", None))
            except (TypeError, ValueError):
                continue
            score = _coerce_score(getattr(result, "final_score", None), None)
            metadata_source = getattr(result, "metadata", None)
            if score is None or not isinstance(metadata_source, dict):
                continue
            score_breakdown = {
                key: round(float(value), 6)
                for key, value in _safe_score_breakdown_items(
                    getattr(result, "score_breakdown", None)
                )
                if isinstance(value, (int, float))
            }
            metadata = {
                "session_id": metadata_source.get("session_id"),
                "persona_id": metadata_source.get("persona_id"),
                "importance": metadata_source.get("importance", 0.5),
                "memory_type": metadata_source.get("memory_type", "GENERAL"),
                "status": effective_memory_status(metadata_source),
                "create_time": metadata_source.get("create_time"),
            }
            metadata.update(score_breakdown)
            formatted_results.append(
                {
                    # 前端兼容字段（RecallPage 直接读取这些键）
                    "id": doc_id,
                    "score": round(score, 4),
                    "type": metadata_source.get("memory_type", "GENERAL"),
                    "importance": metadata_source.get("importance", 0.5),
                    "created_at": metadata_source.get("create_time"),
                    "summary": metadata_source.get("canonical_summary")
                    or result.content,
                    # 分数分解（前端扁平读取）
                    "doc_kw_score": score_breakdown.get("doc_kw"),
                    "doc_vec_score": score_breakdown.get("doc_vec"),
                    "graph_kw_score": score_breakdown.get("graph_kw"),
                    "graph_vec_score": score_breakdown.get("graph_vec"),
                    # 后端完整字段
                    "memory_id": doc_id,
                    "content": getattr(result, "content", ""),
                    "similarity_score": round(score, 4),
                    "score_percentage": round(score * 100, 2),
                    "metadata": metadata,
                    "score_breakdown": score_breakdown,
                }
            )

        return self._ok(
            {
                "results": formatted_results,
                "total": len(formatted_results),
                "query": query_text,
                "k": k,
                "session_id_filter": session_id,
                "elapsed_time_ms": round(elapsed_time, 2),
            }
        )
