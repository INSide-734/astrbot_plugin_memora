"""控制台页面的黑话发现、管理与统计接口。"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger
from quart import request

from .response_utils import error_response, ok_response


def _parse_limit(raw_value: Any, *, default: int, maximum: int) -> int:
    """解析正整数 limit 参数，非法时使用合理的默认值回退。"""
    try:
        limit = int(raw_value)
    except (TypeError, ValueError):
        return default
    if limit <= 0:
        return default
    return min(limit, maximum)


def _meaning_to_dict(meaning: Any) -> dict[str, Any]:
    """将 ``JargonMeaning`` 转换为 JSON 响应字典。"""
    return {
        "term": meaning.term,
        "group_id": meaning.group_id,
        "meaning": meaning.meaning,
        "confidence": meaning.confidence,
        "is_jargon": meaning.is_jargon,
        "is_confirmed": meaning.is_confirmed,
        "is_global": meaning.is_global,
        "is_complete": meaning.is_complete,
        "count": meaning.count,
        "last_inference_count": meaning.last_inference_count,
        "created_at": meaning.created_at,
        "updated_at": meaning.updated_at,
    }


def _candidate_to_dict(candidate: Any) -> dict[str, Any]:
    """将 ``JargonCandidate`` 转换为 JSON 响应字典。"""
    return {
        "term": candidate.term,
        "group_id": candidate.group_id,
        "score": candidate.score,
        "frequency": candidate.frequency,
        "unique_users": candidate.unique_users,
        "idf_score": candidate.idf_score,
        "burst_score": candidate.burst_score,
        "concentration_score": candidate.concentration_score,
        "first_seen": candidate.first_seen,
        "context_examples": candidate.context_examples,
    }


def _safe_meaning_to_dict(meaning: Any) -> dict[str, Any] | None:
    try:
        return _meaning_to_dict(meaning)
    except Exception:
        return None


def _safe_candidate_to_dict(candidate: Any) -> dict[str, Any] | None:
    try:
        return _candidate_to_dict(candidate)
    except Exception:
        return None


def _safe_list_items(items: Any) -> list[Any]:
    try:
        return list(items or [])
    except Exception:
        return []


class JargonApiMixin:
    """为 Memora 控制台页面提供黑话相关 REST 端点的混入类。"""

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

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
        from ..jargon.statistical_filter import JargonStatisticalFilter

        jf = JargonStatisticalFilter()
        plugin._jargon_filter = jf
        logger.info("[黑话接口] 已惰性创建黑话统计过滤器实例")
        return jf

    async def _get_jargon_store(self) -> Any | None:
        """惰性解析或创建 ``JargonStore``。

        优先从插件或初始化器上查找现有 store。
        若不存在，则基于插件数据目录惰性创建，并缓存到
        ``plugin._jargon_store``。
        """
        plugin = getattr(self, "plugin", None)
        if plugin is None:
            return None
        for attr_name in ("_jargon_store", "jargon_store"):
            obj = getattr(plugin, attr_name, None)
            if obj is not None:
                return obj
        initializer = getattr(plugin, "initializer", None)
        if initializer is not None:
            obj = getattr(initializer, "jargon_store", None)
            if obj is not None:
                return obj

        # ── 惰性创建并缓存 ──
        from pathlib import Path

        from ..jargon.jargon_store import JargonStore

        data_dir = getattr(plugin, "data_dir", None)
        if data_dir is None:
            initializer = getattr(plugin, "initializer", None)
            if initializer is not None:
                data_dir = getattr(initializer, "data_dir", None)
        if data_dir is None:
            logger.warning("[黑话接口] 无法惰性创建黑话存储：未找到数据目录")
            return None

        db_path = str(Path(data_dir) / "jargon.db")
        store = JargonStore(db_path)
        await store.initialize()
        plugin._jargon_store = store
        logger.info("[黑话接口] 已惰性创建黑话存储实例，路径=%s", db_path)
        return store

    async def _get_jargon_miner(self) -> Any | None:
        """惰性解析或创建 ``JargonMiner``。

        优先从插件或初始化器上查找现有 miner。
        若不存在，则惰性创建一个新实例（依赖 LLM provider、filter 与 store），
        并缓存到 ``plugin._jargon_miner``。
        """
        plugin = getattr(self, "plugin", None)
        if plugin is None:
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
        from ..jargon.jargon_miner import JargonMiner

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
                        "[黑话接口] 从上下文回退获取 LLM 提供器失败: %s",
                        exc,
                        exc_info=True,
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

    # ------------------------------------------------------------------
    # GET /candidates
    # ------------------------------------------------------------------

    async def get_jargon_candidates(self):
        """返回指定群组经统计预过滤后的黑话候选词。

        查询参数:
            group_id (str, 必填): 群组标识。
            limit (int, 可选): 返回上限，默认 20，最大 100。
        """
        jf = self._get_jargon_filter()
        if jf is None:
            return error_response("黑话统计过滤器不可用")

        args = request.args
        group_id, err = self._require_group_id(args)
        if err:
            return err

        try:
            limit = _parse_limit(args.get("limit", 20), default=20, maximum=100)
            candidates = jf.get_candidates(group_id, limit=limit)
            candidates = _safe_list_items(candidates)
            serialized_candidates = [
                item
                for item in (_safe_candidate_to_dict(c) for c in candidates)
                if item is not None
            ]
            return ok_response({
                "candidates": serialized_candidates,
                "total": len(candidates),
                "group_id": group_id,
            })
        except Exception as e:
            logger.error(f"[黑话接口] 获取候选词失败: {e}", exc_info=True)
            return error_response(f"获取黑话候选词失败：{e}")

    # ------------------------------------------------------------------
    # GET /meanings
    # ------------------------------------------------------------------

    async def get_jargon_meanings(self):
        """返回指定群组已确认的黑话释义。

        查询参数:
            group_id (str, 必填): 群组标识。
            confirmed_only (bool, 可选): 是否仅返回已确认条目，
                默认 true。
        """
        store = await self._get_jargon_store()
        if store is None:
            return error_response("黑话存储不可用")

        args = request.args
        group_id, err = self._require_group_id(args)
        if err:
            return err

        try:
            confirmed_only = args.get("confirmed_only", "true").lower() != "false"
            meanings = await store.list_by_group(group_id, confirmed_only=confirmed_only)
            meanings = _safe_list_items(meanings)
            serialized_meanings = [
                item
                for item in (_safe_meaning_to_dict(m) for m in meanings)
                if item is not None
            ]
            return ok_response({
                "meanings": serialized_meanings,
                "total": len(meanings),
                "group_id": group_id,
            })
        except Exception as e:
            logger.error(f"[黑话接口] 获取黑话释义失败: {e}", exc_info=True)
            return error_response(f"获取黑话释义失败：{e}")

    # ------------------------------------------------------------------
    # GET /stats
    # ------------------------------------------------------------------

    async def get_jargon_stats(self):
        """返回指定群组的黑话统计摘要。

        查询参数:
            group_id (str, 必填): 群组标识。
        """
        jf = self._get_jargon_filter()
        if jf is None:
            return error_response("黑话统计过滤器不可用")

        args = request.args
        group_id, err = self._require_group_id(args)
        if err:
            return err

        try:
            stats = jf.get_stats(group_id)
            top_candidates = _safe_list_items(getattr(stats, "top_candidates", []))
            serialized_candidates = [
                item
                for item in (
                    _safe_candidate_to_dict(c) for c in top_candidates
                )
                if item is not None
            ]
            result = {
                "group_id": stats.group_id,
                "total_terms": stats.total_terms,
                "candidate_count": stats.candidate_count,
                "top_candidates": serialized_candidates,
            }

            # 若存储层可用，则补充 store 统计
            store = await self._get_jargon_store()
            if store is not None:
                try:
                    result["store_total"] = await store.count_by_group(group_id)
                    result["store_confirmed"] = await store.count_confirmed(group_id)
                except Exception as exc:
                    logger.debug(
                        "[黑话接口] 为群组=%s 补充存储统计失败: %s",
                        group_id,
                        exc,
                        exc_info=True,
                    )

            return ok_response(result)
        except Exception as e:
            logger.error(f"[黑话接口] 获取黑话统计失败: {e}", exc_info=True)
            return error_response(f"获取黑话统计失败：{e}")

    # ------------------------------------------------------------------
    # POST /confirm
    # ------------------------------------------------------------------

    async def confirm_jargon(self):
        """手动确认或拒绝某个黑话词条。

        请求体（JSON）:
            term (str, 必填): 黑话词条。
            group_id (str, 必填): 群组标识。
            confirmed (bool, 可选): ``True`` 表示确认，``False`` 表示拒绝，
                默认 true。
        """
        store = await self._get_jargon_store()
        if store is None:
            return error_response("黑话存储不可用")

        try:
            body = await request.get_json()
        except Exception as exc:
            logger.debug("[黑话接口] confirm_jargon 的 JSON 请求体无效: %s", exc, exc_info=True)
            return error_response("JSON 请求体无效")

        if not body or not isinstance(body, dict):
            return error_response("请求体必须为 JSON 对象")

        term = (body.get("term", "") or "").strip()
        group_id = (body.get("group_id", "") or "").strip()
        confirmed = bool(body.get("confirmed", True))

        if not term:
            return error_response("缺少必填参数 term")
        if not group_id:
            return error_response("缺少必填参数 group_id")

        try:
            await store.confirm(term, group_id, confirmed=confirmed)
            action = "confirmed" if confirmed else "rejected"
            action_text = "已确认" if confirmed else "已驳回"
            return ok_response({
                "term": term,
                "group_id": group_id,
                "action": action,
                "message": f"词条“{term}”{action_text}",
            })
        except Exception as e:
            logger.error(f"[黑话接口] 确认黑话词条失败: {e}", exc_info=True)
            return error_response(f"确认黑话词条失败：{e}")

    # ------------------------------------------------------------------
    # POST /mine
    # ------------------------------------------------------------------

    async def mine_jargon(self):
        """手动触发指定群组的一轮黑话挖掘。

        请求体（JSON）:
            group_id (str, 必填): 群组标识。
            limit (int, 可选): 最多推断词条数，默认 5，最大 20。
        """
        # 检查功能委托开关：若 self_learning 接管黑话能力，则拒绝执行挖掘
        fd = self._get_feature_delegation()
        if fd is not None and fd.should_delegate_jargon():
            return error_response(
                "黑话挖掘能力已委托给 self_learning 插件；"
                "伴侣插件启用期间，Memora 本地黑话处理会保持关闭。"
            )

        miner = await self._get_jargon_miner()
        if miner is None:
            return error_response("黑话挖掘器不可用")

        try:
            body = await request.get_json()
        except Exception as exc:
            logger.debug("[黑话接口] mine_jargon 的 JSON 请求体无效: %s", exc, exc_info=True)
            return error_response("JSON 请求体无效")

        if not body or not isinstance(body, dict):
            return error_response("请求体必须为 JSON 对象")

        group_id = (body.get("group_id", "") or "").strip()
        if not group_id:
            return error_response("缺少必填参数 group_id")

        try:
            limit = _parse_limit(body.get("limit", 5), default=5, maximum=20)
            results = await miner.run_once(group_id, limit=limit)
            results = _safe_list_items(results)
            serialized_results = [
                item
                for item in (_safe_meaning_to_dict(r) for r in results)
                if item is not None
            ]
            return ok_response({
                "group_id": group_id,
                "inferred_count": len(results),
                "results": serialized_results,
                "message": f"黑话挖掘完成，共推断出 {len(results)} 个词条",
            })
        except Exception as e:
            logger.error(f"[黑话接口] 执行黑话挖掘失败: {e}", exc_info=True)
            return error_response(f"执行黑话挖掘失败：{e}")


__all__ = ["JargonApiMixin"]
