"""记忆批量操作 API"""

from typing import Any

from quart import request

from astrbot.api import logger


def _coerce_importance_value(raw_value: Any) -> float:
    """将外部传入的重要性值转换为浮点数，同时拒绝 JSON 布尔值。"""
    if isinstance(raw_value, bool):
        raise TypeError("boolean values are not valid importance values")
    return float(raw_value)


class MemoryBatchApiMixin:
    """Mixin: 批量删除 / 批量更新 / 统一批量操作"""

    @staticmethod
    def _json_object_payload_or_error(payload: Any):
        if isinstance(payload, dict):
            return payload, None
        return None, "请求体必须为 JSON 对象"

    @staticmethod
    def _coerce_memory_id(raw_id: Any) -> int:
        """将外部传入的 memory ID 转换为整数，同时拒绝 JSON 布尔值。"""
        if isinstance(raw_id, bool):
            raise TypeError("boolean values are not valid memory ids")
        return int(raw_id)

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_list(value: Any) -> list[Any]:
        try:
            return list(value or [])
        except Exception:
            return []

    @staticmethod
    def _normalize_delete_result(result: Any) -> dict[str, Any]:
        if isinstance(result, dict):
            deleted_count = MemoryBatchApiMixin._safe_int(
                result.get("deleted_count", 0)
            )
            failed_ids = MemoryBatchApiMixin._safe_list(
                result.get("failed_ids", [])
            )
            not_found_ids = MemoryBatchApiMixin._safe_list(
                result.get("not_found_ids", [])
            )
            return {
                "deleted_count": deleted_count,
                "failed_ids": failed_ids,
                "not_found_ids": not_found_ids,
                "errors": MemoryBatchApiMixin._safe_list(result.get("errors", [])),
            }
        return {
            "deleted_count": MemoryBatchApiMixin._safe_int(result or 0),
            "failed_ids": [],
            "not_found_ids": [],
            "errors": [],
        }

    async def _delete_valid_memory_ids(self, memory_engine, valid_ids: list[int]) -> dict:
        if not valid_ids:
            return self._normalize_delete_result(0)
        detailed_delete = getattr(memory_engine, "batch_delete_memories_detailed", None)
        has_real_detailed_delete = hasattr(
            type(memory_engine), "batch_delete_memories_detailed"
        ) or "batch_delete_memories_detailed" in vars(memory_engine)
        if detailed_delete is not None and has_real_detailed_delete:
            return self._normalize_delete_result(await detailed_delete(valid_ids))
        return self._normalize_delete_result(
            await memory_engine.batch_delete_memories(valid_ids)
        )

    async def batch_memories(self):
        """POST /memories/batch {memory_ids, action: "delete"|"archive"} — 统一批量入口"""
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        payload = await request.get_json(silent=True) or {}
        payload, error = MemoryBatchApiMixin._json_object_payload_or_error(payload)
        if error:
            return self._error(error)
        memory_ids = payload.get("memory_ids", [])
        action = str(payload.get("action", "")).strip()
        if not isinstance(memory_ids, list) or not memory_ids:
            return self._error("需要提供记忆 ID 列表")
        if action == "delete":
            return await self._batch_delete_memories_impl(memory_ids)
        if action == "archive":
            return await self._batch_update_memories_impl(
                memory_ids, "status", "archived"
            )
        return self._error(f"不支持的操作: {action}")

    async def _batch_delete_memories_impl(self, memory_ids: list):
        """内部方法：批量删除记忆（不读取 request body）"""
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        memory_engine = ready["memory_engine"]
        failed_ids: list[Any] = []
        valid_ids: list[int] = []
        for raw_id in memory_ids:
            try:
                valid_ids.append(self._coerce_memory_id(raw_id))
            except (ValueError, TypeError) as e:
                logger.debug(f"批量删除 ID 转换失败 (raw_id={raw_id}): {e}")
                failed_ids.append(raw_id)
        delete_result = await self._delete_valid_memory_ids(memory_engine, valid_ids)
        failed_ids.extend(delete_result["failed_ids"])
        not_found_ids = delete_result["not_found_ids"]
        return self._ok(
            {
                "deleted_count": delete_result["deleted_count"],
                "failed_count": len(failed_ids) + len(not_found_ids),
                "total": len(memory_ids),
                "failed_ids": failed_ids,
                "not_found_ids": not_found_ids,
                "errors": delete_result["errors"],
            }
        )

    async def _batch_update_memories_impl(
        self, memory_ids: list, field: str, value: Any
    ):
        """内部方法：批量更新记忆（不读取 request body）"""
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        memory_engine = ready["memory_engine"]
        updated_count = 0
        failed_ids: list[Any] = []
        for raw_id in memory_ids:
            try:
                memory_id = self._coerce_memory_id(raw_id)
            except (TypeError, ValueError):
                failed_ids.append(raw_id)
                continue
            try:
                updates: dict[str, Any] = {}
                if field == "status":
                    status_value = str(value).strip()
                    if status_value not in {"active", "archived", "deleted"}:
                        failed_ids.append(raw_id)
                        continue
                    updates["metadata"] = {"status": status_value}
                elif field == "importance":
                    try:
                        parsed = _coerce_importance_value(value)
                    except (TypeError, ValueError):
                        failed_ids.append(raw_id)
                        continue
                    updates["importance"] = (
                        parsed / 10.0 if 0.0 <= parsed <= 10.0 else parsed
                    )
                elif field == "type":
                    type_value = str(value).strip()
                    if not type_value:
                        failed_ids.append(raw_id)
                        continue
                    updates["metadata"] = {"memory_type": type_value}
                success = await memory_engine.update_memory(memory_id, updates)
                if success:
                    updated_count += 1
                else:
                    failed_ids.append(raw_id)
            except Exception as e:
                logger.debug(f"批量更新记忆失败 (raw_id={raw_id}): {e}")
                failed_ids.append(raw_id)
        return self._ok(
            {
                "updated_count": updated_count,
                "failed_count": len(failed_ids),
                "total": len(memory_ids),
                "failed_ids": failed_ids,
            }
        )

    async def batch_delete_memories(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        memory_engine = ready["memory_engine"]

        payload = await request.get_json(silent=True) or {}
        payload, error = MemoryBatchApiMixin._json_object_payload_or_error(payload)
        if error:
            return self._error(error)
        memory_ids = payload.get("memory_ids", [])
        if not isinstance(memory_ids, list) or not memory_ids:
            return self._error("需要提供记忆 ID 列表")

        failed_ids: list[Any] = []

        valid_ids: list[int] = []
        for raw_id in memory_ids:
            try:
                valid_ids.append(self._coerce_memory_id(raw_id))
            except (ValueError, TypeError) as e:
                logger.debug(f"批量删除 ID 转换失败 (raw_id={raw_id}): {e}")
                failed_ids.append(raw_id)

        delete_result = await self._delete_valid_memory_ids(memory_engine, valid_ids)
        failed_ids.extend(delete_result["failed_ids"])
        not_found_ids = delete_result["not_found_ids"]

        return self._ok(
            {
                "deleted_count": delete_result["deleted_count"],
                "failed_count": len(failed_ids) + len(not_found_ids),
                "total": len(memory_ids),
                "failed_ids": failed_ids,
                "not_found_ids": not_found_ids,
                "errors": delete_result["errors"],
            }
        )

    async def batch_update_memories(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        memory_engine = ready["memory_engine"]

        payload = await request.get_json(silent=True) or {}
        payload, error = MemoryBatchApiMixin._json_object_payload_or_error(payload)
        if error:
            return self._error(error)
        memory_ids = payload.get("memory_ids", [])
        field = str(payload.get("field", "")).strip()
        value = payload.get("value")

        if not isinstance(memory_ids, list) or not memory_ids:
            return self._error("需要提供记忆 ID 列表")
        if not field or value is None:
            return self._error("需要指定 field 和 value")
        if field not in ("status", "importance", "type"):
            return self._error(f"批量更新不支持字段: {field}")

        updated_count = 0
        failed_ids: list[Any] = []

        for raw_id in memory_ids:
            try:
                memory_id = self._coerce_memory_id(raw_id)
            except (TypeError, ValueError):
                failed_ids.append(raw_id)
                continue

            try:
                updates: dict[str, Any] = {}
                if field == "status":
                    status_value = str(value).strip()
                    if status_value not in {"active", "archived", "deleted"}:
                        failed_ids.append(raw_id)
                        continue
                    updates["metadata"] = {"status": status_value}
                elif field == "importance":
                    try:
                        parsed = _coerce_importance_value(value)
                    except (TypeError, ValueError):
                        failed_ids.append(raw_id)
                        continue
                    if 0.0 <= parsed <= 1.0:
                        updates["importance"] = parsed
                    elif 0.0 <= parsed <= 10.0:
                        updates["importance"] = parsed / 10.0
                    else:
                        failed_ids.append(raw_id)
                        continue
                elif field == "type":
                    type_value = str(value).strip()
                    if not type_value:
                        failed_ids.append(raw_id)
                        continue
                    updates["metadata"] = {"memory_type": type_value}

                success = await memory_engine.update_memory(memory_id, updates)
                if success:
                    updated_count += 1
                else:
                    failed_ids.append(raw_id)
            except Exception as e:
                logger.debug(f"批量更新记忆失败 (raw_id={raw_id}): {e}")
                failed_ids.append(raw_id)

        return self._ok(
            {
                "updated_count": updated_count,
                "failed_count": len(failed_ids),
                "total": len(memory_ids),
                "failed_ids": failed_ids,
            }
        )
