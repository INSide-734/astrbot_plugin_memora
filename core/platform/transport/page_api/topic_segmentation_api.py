"""话题分割配置与存量回填管理的 REST API。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from astrbot.api import logger

from ....features.memory.infrastructure.topic_metrics import (
    read_topic_metrics_summary,
)
from ...config.manager import ConfigConflictError
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
            raise ValueError("strategy_must_be_string")
        normalized = _STRATEGY_ALIASES.get(value, value)
        if normalized not in _VALID_STRATEGIES:
            raise ValueError("strategy_invalid")
        return normalized
    if key == "enabled":
        return bool(value)
    if key == "enable_parallel_stage2":
        return bool(value)
    bounds = _NUMERIC_BOUNDS.get(key)
    if bounds is not None:
        if isinstance(value, bool):
            raise ValueError(f"{key}_must_be_number")
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key}_must_be_number") from None
        low, high = bounds
        if not (low <= v <= high):
            raise ValueError(f"{key}_out_of_range")
        if key in {
            "min_cluster_size",
            "max_clusters",
            "min_chunk_size",
            "stage1_max_topics",
        }:
            if not v.is_integer():
                raise ValueError(f"{key}_must_be_integer")
            v = int(v)
        return v
    return value


class TopicSegmentationApiMixin:
    """提供话题配置、回填控制与低敏候选观测。"""

    async def get_topic_segmentation_config(self):
        """返回现有配置和 canonical Store 的低敏状态；保留 ready 错误 envelope。"""
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
            "candidate_reuse": await self._get_candidate_reuse_status(engines),
        }
        return ok_response(cfg)

    async def _get_candidate_reuse_status(
        self, engines: dict[str, Any]
    ) -> dict[str, Any]:
        """只通过 canonical Store 公共端口读取目录与版本化指标聚合。"""
        result: dict[str, Any] = {
            "catalog_status": "unavailable",
            "dirty_count": None,
            "scope_buckets": None,
            "aggregated_metrics": None,
        }
        store = getattr(engines.get("memory_engine"), "topic_catalog_store", None)
        if store is None:
            return result
        try:
            summary = await store.get_catalog_summary()
            for field in ("catalog_status", "dirty_count", "scope_buckets"):
                result[field] = summary[field]
        except Exception:
            logger.warning("[API] 读取话题目录状态失败")
        result["aggregated_metrics"] = await self._query_aggregated_metrics(engines)
        return result

    async def _query_aggregated_metrics(
        self, engines: dict[str, Any]
    ) -> dict[str, float | None] | None:
        """从 canonical catalog 读取 UTC 窗口样本的真实 P95。"""
        try:
            memory_engine = engines.get("memory_engine")
            catalog_store = getattr(memory_engine, "topic_catalog_store", None)
            if catalog_store is None:
                return None
            initializer = getattr(self.plugin, "initializer", None)
            data_dir = getattr(initializer, "data_dir", None)
            if not data_dir:
                db_path = getattr(memory_engine, "db_path", None)
                data_dir = Path(db_path).parent if db_path else None
            if not data_dir:
                return None
            summary = await read_topic_metrics_summary(
                catalog_store,
                data_dir,
                retention_days=7,
                now=time.time(),
            )
            if not isinstance(summary, dict):
                return None
            safe_summary: dict[str, float | None] = {}
            for field in ("p95_latency_ms", "p95_candidates", "p95_tokens"):
                value = summary.get(field)
                if value is None:
                    safe_summary[field] = None
                elif (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and value >= 0
                    and value == value
                    and value != float("inf")
                    and value != float("-inf")
                ):
                    safe_summary[field] = float(value)
                else:
                    return None
            return (
                safe_summary
                if any(value is not None for value in safe_summary.values())
                else None
            )
        except Exception:
            logger.warning("[API] 读取 canonical 候选指标失败")
            return None

    async def update_topic_segmentation_config(self):
        """更新话题分割配置，并以 base_revision 执行原子热重载。"""
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        body = await self._get_web_request().json()
        if not isinstance(body, dict):
            return error_response("配置对象必须是 JSON 对象")

        try:
            base_revision = body.pop("base_revision", None)
            (
                _,
                current_revision,
            ) = await self.plugin.config_manager.get_config_snapshot_async()
            if base_revision is None:
                base_revision = current_revision
            if not isinstance(base_revision, str) or not base_revision.strip():
                return error_response(
                    "base_revision 必须是非空字符串", code="invalid_request"
                )
            changes: dict[str, object] = {}
            for key, value in body.items():
                if key.startswith("topic_segmentation."):
                    short_key = key.replace("topic_segmentation.", "", 1)
                    parts = short_key.split(".")
                    if len(parts) == 1:
                        changes[key] = _validate_and_cast(parts[0], value)
                    elif len(parts) == 2:
                        changes[key] = _validate_and_cast(parts[1], value)
                elif key.startswith("candidate_reuse."):
                    changes[f"topic_segmentation.{key}"] = value
                elif key in ("strategy", "enabled"):
                    changes[f"topic_segmentation.{key}"] = _validate_and_cast(
                        key, value
                    )
                elif key in ("strategy_b", "strategy_c", "strategy_d") and isinstance(
                    value, dict
                ):
                    for field, field_value in value.items():
                        changes[f"topic_segmentation.{key}.{field}"] = (
                            _validate_and_cast(field, field_value)
                        )
            if not changes:
                return error_response("未找到有效的配置字段")
            result = await self.plugin.config_manager.apply_config_changes(
                changes, expected_revision=base_revision, persist=True
            )
            return ok_response(
                {
                    "updated": list(result.changed_paths),
                    "revision": result.revision,
                    "status": "success",
                }
            )
        except ConfigConflictError as exc:
            return error_response(
                str(exc),
                code="config_conflict",
                data={"current_revision": exc.current_revision},
            )
        except Exception as exc:
            logger.error("[API] 配置更新失败: %s", exc, exc_info=True)
            return error_response(f"配置更新失败: {exc}")

    async def start_backfill(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        scheduler = self.plugin._backfill_scheduler
        if scheduler is None:
            return error_response("回填调度器未初始化")
        try:
            if scheduler.is_running:
                return error_response("回填任务已在运行中")
            job_id = await scheduler.start()
            return ok_response({"job_id": job_id, "message": "回填任务已启动"})
        except Exception as e:
            logger.error("[API] 启动回填失败: %s", e, exc_info=True)
            return error_response(f"启动回填失败: {e}")

    async def get_backfill_status(self):
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        scheduler = self.plugin._backfill_scheduler
        if scheduler is None:
            return error_response("回填调度器未初始化")
        return ok_response(scheduler.progress)
