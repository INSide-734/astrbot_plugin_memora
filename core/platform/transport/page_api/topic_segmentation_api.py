"""话题分割配置与存量回填管理的 REST API。"""

from __future__ import annotations

from astrbot.api import logger

from .response_utils import error_response, ok_response

_STRATEGY_ALIASES = {
    "a": "strategy_a",
    "b": "strategy_b",
    "c": "strategy_c",
    "d": "strategy_d",
}
_VALID_STRATEGIES = frozenset(
    {"strategy_a", "strategy_b", "strategy_c", "strategy_d", "a_b_hybrid"}
)

_NUMERIC_BOUNDS: dict[str, tuple[float, float]] = {
    "similarity_threshold": (0.0, 1.0),
    "min_cluster_size": (1, 20),
    "max_clusters": (1, 20),
    "topic_shift_threshold": (0.0, 1.0),
    "min_chunk_size": (1, 50),
    "stage1_max_topics": (1, 20),
}


def _validate_and_cast(key: str, value: object) -> object:
    """对话题分割配置值进行基本的类型与范围校验。"""
    if key == "strategy":
        if not isinstance(value, str):
            return None
        normalized = _STRATEGY_ALIASES.get(value, value)
        if normalized not in _VALID_STRATEGIES:
            return None
        return normalized
    if key == "enabled":
        return bool(value)
    if key == "enable_parallel_stage2":
        return bool(value)
    bounds = _NUMERIC_BOUNDS.get(key)
    if bounds is not None:
        if isinstance(value, bool):
            return None  # reject JSON booleans for numeric fields
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        lo, hi = bounds
        if v < lo or v > hi:
            return None
        if key in (
            "min_cluster_size",
            "max_clusters",
            "min_chunk_size",
            "stage1_max_topics",
        ):
            if not v.is_integer():
                return None
            return int(v)
        return v
    return value


class TopicSegmentationApiMixin:
    async def get_topic_segmentation_config(self):
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        c = self.plugin.config_manager
        cfg = {
            "enabled": c.get("topic_segmentation.enabled"),
            "strategy": c.get("topic_segmentation.strategy"),
            "strategy_b": {
                "similarity_threshold": c.get(
                    "topic_segmentation.strategy_b.similarity_threshold"
                ),
                "min_cluster_size": c.get(
                    "topic_segmentation.strategy_b.min_cluster_size"
                ),
                "max_clusters": c.get("topic_segmentation.strategy_b.max_clusters"),
            },
            "strategy_c": {
                "topic_shift_threshold": c.get(
                    "topic_segmentation.strategy_c.topic_shift_threshold"
                ),
                "min_chunk_size": c.get("topic_segmentation.strategy_c.min_chunk_size"),
            },
            "strategy_d": {
                "stage1_max_topics": c.get(
                    "topic_segmentation.strategy_d.stage1_max_topics"
                ),
                "enable_parallel_stage2": c.get(
                    "topic_segmentation.strategy_d.enable_parallel_stage2"
                ),
            },
            "hybrid_fallback_fact_threshold": c.get(
                "topic_segmentation.hybrid_fallback_fact_threshold"
            ),
            "legacy_backfill": {
                "enabled": c.get("topic_segmentation.legacy_backfill.enabled"),
                "batch_size": c.get("topic_segmentation.legacy_backfill.batch_size"),
                "max_backfill_per_run": c.get(
                    "topic_segmentation.legacy_backfill.max_backfill_per_run"
                ),
            },
            "available_strategies": [
                {
                    "key": "a_b_hybrid",
                    "label": "A+B 混合模式",
                    "desc": "LLM 主分割 + 嵌入聚类兜底",
                },
                {
                    "key": "strategy_a",
                    "label": "方案 A — Prompt 工程",
                    "desc": "LLM 直接输出 memories[] 数组",
                },
                {
                    "key": "strategy_b",
                    "label": "方案 B — 嵌入聚类",
                    "desc": "key_facts 嵌入相似度聚类分拆",
                },
                {
                    "key": "strategy_c",
                    "label": "方案 C — 话题预分块",
                    "desc": "LLM 调用前检测话题边界",
                },
                {
                    "key": "strategy_d",
                    "label": "方案 D — 两阶段 LLM",
                    "desc": "先识别话题范围再分别抽取",
                },
            ],
        }
        return ok_response(cfg)

    async def update_topic_segmentation_config(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        try:
            request_getter = getattr(self, "_get_web_request", None)
            request_source = request_getter() if callable(request_getter) else None
            if request_source is None:
                from astrbot.api.web import request as request_source

            invalid_json = object()
            try:
                body = await request_source.json(default=invalid_json)
            except TypeError:
                # 兼容 json() 尚不支持公共代理 default 参数的旧请求适配器。
                body = await request_source.json()
            if body is invalid_json:
                return error_response("invalid JSON body")
        except Exception as exc:
            logger.debug(
                "[TopicSegmentationApi] invalid config JSON body: %s",
                exc,
                exc_info=True,
            )
            return error_response("invalid JSON body")
        if not isinstance(body, dict):
            return error_response("request body must be a JSON object")

        cfg = self.plugin.config_manager
        flat_updates: dict[str, object] = {}

        if "strategy" in body:
            v = _validate_and_cast("strategy", body["strategy"])
            if v is None:
                return error_response(f"invalid strategy: {body['strategy']}")
            flat_updates["topic_segmentation.strategy"] = v

        if "enabled" in body:
            flat_updates["topic_segmentation.enabled"] = bool(body["enabled"])

        for section, fields in {
            "strategy_b": {
                "similarity_threshold": "topic_segmentation.strategy_b.similarity_threshold",
                "min_cluster_size": "topic_segmentation.strategy_b.min_cluster_size",
                "max_clusters": "topic_segmentation.strategy_b.max_clusters",
            },
            "strategy_c": {
                "topic_shift_threshold": "topic_segmentation.strategy_c.topic_shift_threshold",
                "min_chunk_size": "topic_segmentation.strategy_c.min_chunk_size",
            },
            "strategy_d": {
                "stage1_max_topics": "topic_segmentation.strategy_d.stage1_max_topics",
                "enable_parallel_stage2": "topic_segmentation.strategy_d.enable_parallel_stage2",
            },
        }.items():
            if section in body and isinstance(body[section], dict):
                for field, config_path in fields.items():
                    if field in body[section]:
                        v = _validate_and_cast(field, body[section][field])
                        if v is None:
                            return error_response(
                                f"invalid value for {section}.{field}: {body[section][field]}"
                            )
                        flat_updates[config_path] = v

        if not await cfg.update_runtime_config(flat_updates, persist=True):
            return error_response("invalid runtime config update")
        updated = list(flat_updates.keys())

        logger.info("[TopicSegmentationApi] config updated: %s", updated)
        return ok_response(
            {"ok": True, "updated": updated, "message": "配置已更新，下次记忆处理生效"}
        )

    async def start_backfill(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        scheduler = getattr(self.plugin, "_backfill_scheduler", None)
        if scheduler is None:
            return error_response("backfill scheduler not available")
        try:
            if scheduler.is_running:
                return error_response("回填任务已在运行中")
            job_id = await scheduler.start()
            return ok_response({"job_id": job_id, "message": "回填任务已启动"})
        except Exception as e:
            logger.error(
                "[TopicSegmentationApi] backfill start failed: %s", e, exc_info=True
            )
            return error_response(f"启动回填失败: {e}")

    async def get_backfill_status(self):
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        scheduler = getattr(self.plugin, "_backfill_scheduler", None)
        if scheduler is None:
            return ok_response({"status": "unavailable"})
        return ok_response(scheduler.progress)
