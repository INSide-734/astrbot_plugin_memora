"""记忆统计 + 召回测试 API"""

import time

from astrbot.api import logger
from quart import request


def _coerce_count(value, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_count_map(value) -> dict:
    if not isinstance(value, dict):
        return {}
    return {str(key): _coerce_count(count, 0) for key, count in value.items()}


def _default_importance_distribution() -> dict[str, int]:
    return {f"{i}-{i + 1}": 0 for i in range(0, 10)}


def _coerce_score(value, default=None):
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_result_list(results):
    try:
        return list(results or [])
    except Exception:
        return []


def _safe_score_breakdown_items(value):
    try:
        return list((value or {}).items())
    except Exception:
        return []


def _safe_recent_sessions(value) -> list[dict[str, int | str]]:
    if not isinstance(value, dict):
        return []
    normalized = [
        {"session_id": str(session_id), "message_count": _coerce_count(count, 0)}
        for session_id, count in value.items()
    ]
    return sorted(normalized, key=lambda item: -item["message_count"])[:10]


class MemoryStatsRecallApiMixin:
    """混入类：统计信息 / 召回测试"""

    @staticmethod
    def _coerce_limit(raw_value) -> int:
        """将外部传入的数值上限转换为整数，同时拒绝 JSON 布尔值。"""
        if isinstance(raw_value, bool):
            raise TypeError("boolean values are not valid integer limits")
        return int(raw_value)

    async def get_stats(self):
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

            # 扁平化 status_breakdown → 前端期望的独立字段
            breakdown = _normalize_count_map(stats.get("status_breakdown", {}))
            stats.setdefault("active_count", breakdown.get("active", 0))
            stats.setdefault("archived_count", breakdown.get("archived", 0))
            stats.setdefault("deleted_count", breakdown.get("deleted", 0))
            # 确保前端期望字段至少存在默认值
            for key in (
                "active_count",
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
                        raise TypeError("graph entry stats must be a mapping")
                    stats["graph_nodes"] = entry_stats.get("graph_nodes", 0)
                    stats["graph_edges"] = entry_stats.get("graph_edges", 0)
                    stats["graph_entries"] = entry_stats.get("graph_entries", 0)
                except Exception as exc:
                    logger.debug(
                        "[MemoryStatsRecallApi] graph stats unavailable: %s",
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

            session_data = stats.get("sessions", {})
            stats["recent_sessions"] = _safe_recent_sessions(session_data)

            return self._ok(stats)
        except Exception as exc:
            logger.error(f"[PageAPI] 获取统计信息失败: {exc}", exc_info=True)
            return self._error(str(exc))

    async def test_recall(self):
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
                "status": metadata_source.get("status", "active"),
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
