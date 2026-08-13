"""群组列表与路由元数据 mixin。"""

from __future__ import annotations

import inspect
from typing import Any

from astrbot.api import logger

from ....features.observability.infrastructure.debug_reporter import (
    report_debug_exception,
)
from ....shared.number_utils import safe_float


class GroupListApiMixin:
    """承载路由元数据与群组列表接口。"""

    def get_route_metadata(self: Any) -> list[dict[str, Any]]:
        """返回已注册主路由的审计元数据副本。"""
        return [dict(item) for item in self._route_metadata]

    @staticmethod
    def _build_route_metadata(
        path: str,
        handler: Any,
        methods: list[str],
        description: str,
    ) -> dict[str, Any]:
        """根据路由参数构建插件侧审计元数据。"""
        normalized_methods = [str(method).upper() for method in methods]
        risk = GroupListApiMixin._infer_route_risk(path, normalized_methods)
        auth = "admin" if "POST" in normalized_methods else "host"
        return {
            "path": path,
            "handler_name": getattr(handler, "__name__", str(handler)),
            "methods": normalized_methods,
            "description": description,
            "risk": risk,
            "auth": auth,
            "aliases": "别名" in str(description),
            "requires_ready": not (
                path.endswith("/delegation/status")
                or path.endswith("/delegation/provided-services")
                or path.endswith("/config/schema")
                or path.endswith("/config/state")
                or path.endswith("/config/apply")
                or path.endswith("/update/check")
                or path.endswith("/update/status")
            ),
            "write_guard": risk
            in {
                "write",
                "maintenance",
                "destructive",
                "runtime_exec",
            },
        }

    @staticmethod
    def _infer_route_risk(path: str, methods: list[str]) -> str:
        """根据 HTTP 方法与路径特征推断路由风险级别。"""
        if "POST" not in methods:
            return "read"
        lowered = path.lower()
        if "/dashboard/install" in lowered or "/dashboard/build" in lowered:
            return "runtime_exec"
        if any(
            token in lowered
            for token in ("/delete", "batch-delete", "/purge", "/restore", "/reset")
        ):
            return "destructive"
        if any(
            token in lowered
            for token in (
                "/maintenance/",
                "/backup/",
                "/backfill/start",
                "/config/",
                "/quality/reset",
                "/system/",
                "/update/",
            )
        ):
            return "maintenance"
        return "write"

    # ---- 群组列表 ----

    async def get_groups(self: Any) -> dict[str, Any]:
        """汇总所有数据来源中的可用群组列表并返回稳定响应。

        会聚合黑话存储、好感度存储、表达模式存储、社交关系存储
        以及会话数据中的不同群组标识，并在部分来源失败时返回
        按来源划分的可见性元数据。
        """
        groups: dict[str, dict[str, Any]] = {}
        sources: dict[str, dict[str, Any]] = {}

        def add_group(
            group_id: Any, source: str, message_count: int | float = 0
        ) -> None:
            """合并单个来源的群组及其消息数量。"""
            gid = str(group_id or "").strip()
            if not gid:
                return
            if gid not in groups:
                groups[gid] = {
                    "group_id": gid,
                    "source": source,
                    "message_count": int(message_count),
                }
                return
            groups[gid]["message_count"] = max(
                int(groups[gid].get("message_count", 0)),
                int(message_count),
            )

        def set_source_success(source: str, count: int) -> None:
            """记录单个数据来源的成功状态。"""
            sources[source] = {"ok": True, "count": int(count)}

        def set_source_error(source: str, exc: Exception) -> None:
            """记录脱敏后的来源失败状态并上报诊断事件。"""
            logger.warning("[页面接口] 收集 %s 群组列表失败", source)
            report_debug_exception(
                "maintenance_task",
                exc,
                component="page_api",
                stage="maintenance",
                status="failed",
                reason_code="group_source_error",
                task_type="group_listing",
            )
            sources[source] = {
                "ok": False,
                "count": 0,
                "error": exc.__class__.__name__,
            }

        # 1. 来自黑话存储
        try:
            jargon_store = (
                await self._get_jargon_store()
                if hasattr(self, "_get_jargon_store")
                else None
            )
            if jargon_store and hasattr(jargon_store, "list_group_ids"):
                group_ids = await jargon_store.list_group_ids()
                for gid in group_ids:
                    add_group(gid, "jargon", 0)
                set_source_success("jargon", len(group_ids))
            else:
                set_source_success("jargon", 0)
        except Exception as exc:
            set_source_error("jargon", exc)

        # 2. 来自好感度存储
        try:
            affection_store = getattr(self, "_get_affection_store", lambda: None)()
            if affection_store is None:
                plugin = getattr(self, "plugin", None)
                if plugin:
                    for attr in ("_affection_store", "affection_store"):
                        affection_store = getattr(plugin, attr, None)
                        if affection_store:
                            break
            if affection_store and hasattr(affection_store, "list_group_ids"):
                group_ids = await affection_store.list_group_ids()
                for gid in group_ids:
                    add_group(gid, "affection", 0)
                set_source_success("affection", len(group_ids))
            else:
                set_source_success("affection", 0)
        except Exception as exc:
            set_source_error("affection", exc)

        # 3. 来自社交关系存储
        try:
            rel_manager = getattr(self, "_get_relation_manager", lambda: None)()
            if rel_manager and hasattr(rel_manager, "list_group_ids"):
                group_ids = rel_manager.list_group_ids()
                if inspect.isawaitable(group_ids):
                    group_ids = await group_ids
                if not isinstance(group_ids, (list, tuple, set)):
                    group_ids = []
                for gid in group_ids:
                    add_group(gid, "social", 0)
                set_source_success("social", len(group_ids))
            elif rel_manager and hasattr(rel_manager, "list_all"):
                all_rels = rel_manager.list_all()
                if inspect.isawaitable(all_rels):
                    all_rels = await all_rels
                if not isinstance(all_rels, (list, tuple, set)):
                    all_rels = []
                distinct_group_ids: set[str] = set()
                for rel in all_rels or []:
                    gid = str(getattr(rel, "group_id", "") or "").strip()
                    if gid:
                        distinct_group_ids.add(gid)
                    add_group(gid, "social", 0)
                set_source_success("social", len(distinct_group_ids))
            else:
                set_source_success("social", 0)
        except Exception as exc:
            set_source_error("social", exc)

        # 4. 来自会话数据（记忆引擎）
        try:
            engine = self.plugin.initializer.memory_engine
            if engine and hasattr(engine, "stats"):
                stats_data = await engine.stats()
                if not isinstance(stats_data, dict):
                    stats_data = {}
                sessions = stats_data.get("sessions", {})
                if not isinstance(sessions, dict):
                    sessions = {}
                for sid, info in (sessions or {}).items():
                    cnt = info.get("message_count", 0) if isinstance(info, dict) else 0
                    add_group(sid, "session", cnt)
                set_source_success("session", len(sessions or {}))
            else:
                set_source_success("session", 0)
        except Exception as exc:
            set_source_error("session", exc)

        # 5. 兜底：从会话管理器提取
        try:
            conv_mgr = self.plugin.initializer.conversation_manager
            store = getattr(conv_mgr, "store", None) if conv_mgr else None
            if store and hasattr(store, "list_session_origins"):
                origins = await store.list_session_origins()
                valid_origin_count = 0
                for item in origins:
                    if not isinstance(item, dict):
                        continue
                    add_group(
                        item.get("session_id"),
                        "conversation",
                        safe_float(item.get("message_count"), 0.0),
                    )
                    if str(item.get("session_id", "") or "").strip():
                        valid_origin_count += 1
                set_source_success("conversation", valid_origin_count)
            elif conv_mgr and hasattr(conv_mgr, "cache"):
                cache = conv_mgr.cache
                if hasattr(cache, "keys"):
                    keys = list(cache.keys())
                    for key in keys:
                        add_group(key, "conversation", 0)
                    set_source_success("conversation", len(keys))
                else:
                    set_source_success("conversation", 0)
            else:
                set_source_success("conversation", 0)
        except Exception as exc:
            set_source_error("conversation", exc)

        result = list(groups.values())
        return self._ok({"groups": result, "total": len(result), "sources": sources})
