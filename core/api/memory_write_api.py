"""记忆写入 API"""

import asyncio
import time
from typing import Any

from quart import request

from astrbot.api import logger

from ..utils.number_utils import clamp_float
from .history_tracker import HistoryTracker
from .response_utils import error_response


def _coerce_importance_value(raw_value: Any) -> float:
    """将外部传入的重要性值转换为浮点数，同时拒绝 JSON 布尔值。"""
    if isinstance(raw_value, bool):
        raise TypeError("boolean values are not valid importance values")
    return float(raw_value)


def _is_safe_replacement_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _replacement_error(message: str, code: str) -> dict[str, Any]:
    return error_response(message, code=code)


class _TaskTerminalError(Exception):
    def __init__(self, cause: Exception, caller_cancelled: bool) -> None:
        self.cause = cause
        self.caller_cancelled = caller_cancelled
        super().__init__(type(cause).__name__)


class MemoryWriteApiMixin:
    """混入类：单条记忆更新"""

    @staticmethod
    def _coerce_memory_id(raw_id: Any) -> int:
        """将外部传入的 memory ID 转换为整数，同时拒绝 JSON 布尔值。"""
        if isinstance(raw_id, bool):
            raise TypeError("boolean values are not valid memory ids")
        return int(raw_id)

    async def update_memory(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        memory_engine = ready["memory_engine"]

        payload = await request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return self._error("请求体必须是 JSON 对象")
        try:
            memory_id = MemoryWriteApiMixin._coerce_memory_id(
                payload.get("memory_id")
            )
        except (TypeError, ValueError):
            return self._error("memory_id 必须是整数")

        if "changes" in payload:
            return await self._update_memory_changes(
                memory_engine,
                memory_id,
                payload["changes"],
                str(payload.get("reason", "")).strip(),
            )

        field = str(payload.get("field", "")).strip()
        value = payload.get("value")
        reason = str(payload.get("reason", "")).strip()

        if not field or value is None:
            return self._error("需要指定 field 和 value")

        memory = await self._get_memory_record(memory_id)
        if not memory:
            return self._error("记忆不存在")

        current_metadata = self._normalize_metadata(memory.get("metadata"))

        if field == "content":
            new_content = str(value).strip()
            if not new_content:
                return self._error("记忆内容不能为空")

            session_id = current_metadata.get("session_id")
            persona_id = current_metadata.get("persona_id")
            importance = clamp_float(current_metadata.get("importance"), default=0.5)
            updated_at = time.time()
            update_history = HistoryTracker.append_update_history(
                current_metadata,
                field="content",
                old_value=memory.get("text", ""),
                new_value=new_content,
                reason=reason,
                timestamp=updated_at,
            )

            if reason:
                current_metadata["update_reason"] = reason
            current_metadata["updated_at"] = updated_at
            current_metadata["previous_content"] = str(memory.get("text", ""))[:100]
            current_metadata["update_history"] = update_history

            new_memory_id = None
            try:
                new_memory_id = await memory_engine.add_memory(
                    content=new_content,
                    session_id=session_id,
                    persona_id=persona_id,
                    importance=importance,
                    metadata=current_metadata,
                )
                delete_success = await memory_engine.delete_memory(memory_id)
                if not delete_success:
                    await memory_engine.delete_memory(new_memory_id)
                    return self._error("旧记忆删除失败，已回滚本次内容更新")
            except Exception as exc:
                if new_memory_id is not None:
                    try:
                        await memory_engine.delete_memory(new_memory_id)
                    except Exception:
                        logger.error(
                            f"[PageAPI] 回滚新记忆失败 (new_memory_id={new_memory_id})",
                            exc_info=True,
                        )
                logger.error(f"[PageAPI] 更新记忆内容失败: {exc}", exc_info=True)
                return self._error(str(exc))

            return self._ok(
                {
                    "message": f"记忆内容已更新（ID: {memory_id} → {new_memory_id}）",
                    "old_memory_id": memory_id,
                    "new_memory_id": new_memory_id,
                    "field": field,
                }
            )

        updates: dict[str, Any] = {}
        old_v: Any
        new_v: Any
        if field == "importance":
            try:
                parsed = _coerce_importance_value(value)
            except (TypeError, ValueError):
                return self._error("重要性必须是数字")
            if 0.0 <= parsed <= 1.0:
                normalized = parsed
            elif 0.0 <= parsed <= 10.0:
                normalized = parsed / 10.0
            else:
                return self._error("重要性必须在 0-1 或 0-10 范围内")
            updates["importance"] = normalized
            old_v = self._importance_to_display(current_metadata.get("importance", 0.5))
            new_v = round(normalized * 10.0, 2)
        elif field == "status":
            status_value = str(value).strip()
            if status_value not in {"active", "archived", "deleted"}:
                return self._error("状态必须是 active、archived 或 deleted")
            updates["metadata"] = {"status": status_value}
            old_v = current_metadata.get("status", "active")
            new_v = status_value
        elif field == "type":
            type_value = str(value).strip()
            if not type_value:
                return self._error("类型不能为空")
            updates["metadata"] = {"memory_type": type_value}
            old_v = current_metadata.get("memory_type", "GENERAL")
            new_v = type_value
        else:
            return self._error(f"不支持编辑字段: {field}")

        updated_at = time.time()
        updates.setdefault("metadata", {})
        updates["metadata"]["update_history"] = HistoryTracker.append_update_history(
            current_metadata,
            field=field,
            old_value=old_v,
            new_value=new_v,
            reason=reason,
            timestamp=updated_at,
        )
        updates["metadata"]["updated_at"] = updated_at
        if reason:
            updates["metadata"]["update_reason"] = reason

        try:
            success = await memory_engine.update_memory(memory_id, updates)
        except Exception as exc:
            logger.error(f"[PageAPI] 更新记忆失败: {exc}", exc_info=True)
            return self._error(str(exc))

        if not success:
            return self._error("更新失败")

        return self._ok(
            {
                "message": f"记忆 {memory_id} 的 {field} 已更新",
                "memory_id": memory_id,
                "field": field,
            }
        )

    async def _update_memory_changes(
        self,
        memory_engine,
        memory_id: int,
        changes: Any,
        reason: str,
    ):
        if not isinstance(changes, dict):
            return self._error("changes 必须是 JSON 对象")
        if not changes:
            return self._error("changes 不能为空")

        editable_fields = {"content", "importance", "status", "type"}
        unsupported = sorted(set(changes) - editable_fields)
        if unsupported:
            return self._error(f"不支持编辑字段: {unsupported[0]}")

        memory = await self._get_memory_record(memory_id)
        if not memory:
            return self._error("记忆不存在")

        current_metadata = dict(self._normalize_metadata(memory.get("metadata")))
        final_metadata = dict(current_metadata)
        new_content = str(memory.get("text", ""))
        content_changed = False
        history_items: list[tuple[str, Any, Any]] = []

        for field, value in changes.items():
            if field == "content":
                parsed_content = str(value).strip()
                if not parsed_content:
                    return self._error("记忆内容不能为空")
                new_content = parsed_content
                content_changed = True
                history_items.append((field, memory.get("text", ""), parsed_content))
            elif field == "importance":
                try:
                    parsed_importance = _coerce_importance_value(value)
                except (TypeError, ValueError):
                    return self._error("重要性必须是数字")
                if 0.0 <= parsed_importance <= 1.0:
                    normalized_importance = parsed_importance
                elif 0.0 <= parsed_importance <= 10.0:
                    normalized_importance = parsed_importance / 10.0
                else:
                    return self._error("重要性必须在 0-1 或 0-10 范围内")
                final_metadata["importance"] = normalized_importance
                history_items.append(
                    (
                        field,
                        self._importance_to_display(
                            current_metadata.get("importance", 0.5)
                        ),
                        round(normalized_importance * 10.0, 2),
                    )
                )
            elif field == "status":
                status_value = str(value).strip()
                if status_value not in {"active", "archived", "deleted"}:
                    return self._error("状态必须是 active、archived 或 deleted")
                final_metadata["status"] = status_value
                history_items.append(
                    (field, current_metadata.get("status", "active"), status_value)
                )
            else:
                type_value = str(value).strip()
                if not type_value:
                    return self._error("类型不能为空")
                final_metadata["memory_type"] = type_value
                history_items.append(
                    (field, current_metadata.get("memory_type", "GENERAL"), type_value)
                )

        updated_at = time.time()
        history_metadata = dict(current_metadata)
        for field, old_value, new_value in history_items:
            history_metadata["update_history"] = HistoryTracker.append_update_history(
                history_metadata,
                field=field,
                old_value=old_value,
                new_value=new_value,
                reason=reason,
                timestamp=updated_at,
            )
        final_metadata["update_history"] = history_metadata["update_history"]
        final_metadata["updated_at"] = updated_at
        if reason:
            final_metadata["update_reason"] = reason

        if not content_changed:
            updates: dict[str, Any] = {"metadata": final_metadata}
            if "importance" in changes:
                updates["importance"] = final_metadata["importance"]
            try:
                success = await memory_engine.update_memory(memory_id, updates)
            except Exception as exc:
                logger.error(
                    "[PageAPI] operation=%s memory_id=%s error_class=%s",
                    "update_memory_metadata",
                    memory_id,
                    type(exc).__name__,
                )
                return self._error("更新记忆失败")
            if not success:
                return self._error("更新失败")
            return self._ok(
                {
                    "message": f"记忆 {memory_id} 已更新",
                    "memory_id": memory_id,
                    "field": "changes",
                }
            )

        final_metadata["previous_content"] = str(memory.get("text", ""))[:100]
        add_task = asyncio.create_task(
            memory_engine.add_memory(
                content=new_content,
                session_id=final_metadata.get("session_id"),
                persona_id=final_metadata.get("persona_id"),
                importance=clamp_float(final_metadata.get("importance"), default=0.5),
                metadata=final_metadata,
            )
        )
        try:
            new_memory_id, add_cancelled = await self._await_task_terminal(add_task)
        except _TaskTerminalError as failure:
            logger.error(
                "[PageAPI] operation=add_replacement old_memory_id=%s "
                "new_memory_id=%s cleanup_result=%s error_class=%s",
                memory_id,
                None,
                "not_started",
                type(failure.cause).__name__,
            )
            if failure.caller_cancelled:
                raise asyncio.CancelledError
            return _replacement_error("创建替换记忆失败", "replacement_failed")

        if not _is_safe_replacement_id(new_memory_id):
            if add_cancelled:
                raise asyncio.CancelledError
            return _replacement_error("创建替换记忆失败", "replacement_failed")

        if add_cancelled:
            await self._cleanup_replacement(memory_engine, memory_id, new_memory_id)
            raise asyncio.CancelledError

        old_delete_task = asyncio.create_task(memory_engine.delete_memory(memory_id))
        try:
            delete_success, delete_cancelled = await self._await_task_terminal(
                old_delete_task
            )
        except _TaskTerminalError as failure:
            return await self._replacement_failure_after_cleanup(
                memory_engine,
                memory_id,
                new_memory_id,
                operation="delete_old_memory",
                error_class=type(failure.cause).__name__,
                caller_cancelled=failure.caller_cancelled,
            )

        if delete_success is not True:
            return await self._replacement_failure_after_cleanup(
                memory_engine,
                memory_id,
                new_memory_id,
                operation="delete_old_memory",
                error_class=None,
                caller_cancelled=delete_cancelled,
            )

        if delete_cancelled:
            raise asyncio.CancelledError

        return self._ok(
            {
                "message": f"记忆内容已更新（ID: {memory_id} → {new_memory_id}）",
                "old_memory_id": memory_id,
                "new_memory_id": new_memory_id,
                "field": "changes",
            }
        )

    @staticmethod
    async def _await_task_terminal(task: asyncio.Task) -> tuple[Any, bool]:
        """等待已启动的后端任务结束，记录但不丢失调用方取消信号。"""

        caller_cancelled = False
        while True:
            try:
                return await asyncio.shield(task), caller_cancelled
            except asyncio.CancelledError:
                if task.done():
                    try:
                        return task.result(), caller_cancelled
                    except Exception as exc:
                        raise _TaskTerminalError(exc, caller_cancelled) from exc
                caller_cancelled = True
                current_task = asyncio.current_task()
                if current_task is not None:
                    current_task.uncancel()
            except Exception as exc:
                raise _TaskTerminalError(exc, caller_cancelled) from exc

    async def _cleanup_replacement(
        self,
        memory_engine,
        old_memory_id: int,
        new_memory_id: int,
    ) -> tuple[bool, bool]:
        """删除已创建替换项，并返回（已确认回滚，调用方曾取消）。"""

        if not _is_safe_replacement_id(new_memory_id):
            return False, False
        cleanup_task = asyncio.create_task(memory_engine.delete_memory(new_memory_id))
        try:
            cleanup_result, cleanup_cancelled = await self._await_task_terminal(
                cleanup_task
            )
        except asyncio.CancelledError:
            self._log_cleanup_failure(
                old_memory_id,
                new_memory_id,
                cleanup_result="cancelled",
                error_class="CancelledError",
            )
            return False, False
        except _TaskTerminalError as failure:
            self._log_cleanup_failure(
                old_memory_id,
                new_memory_id,
                cleanup_result="exception",
                error_class=type(failure.cause).__name__,
            )
            return False, failure.caller_cancelled
        if cleanup_result is not True:
            self._log_cleanup_failure(
                old_memory_id,
                new_memory_id,
                cleanup_result=str(cleanup_result),
                error_class=None,
            )
            return False, cleanup_cancelled
        return True, cleanup_cancelled

    async def _replacement_failure_after_cleanup(
        self,
        memory_engine,
        old_memory_id: int,
        new_memory_id: int,
        *,
        operation: str,
        error_class: str | None,
        caller_cancelled: bool,
    ):
        cleanup_success, cleanup_cancelled = await self._cleanup_replacement(
            memory_engine, old_memory_id, new_memory_id
        )
        if error_class is not None:
            logger.error(
                "[PageAPI] operation=%s old_memory_id=%s new_memory_id=%s "
                "cleanup_result=%s error_class=%s",
                operation,
                old_memory_id,
                new_memory_id,
                "succeeded" if cleanup_success else "failed",
                error_class,
            )
        if caller_cancelled or cleanup_cancelled:
            raise asyncio.CancelledError
        if not cleanup_success:
            return _replacement_error(
                "替换回滚失败，请稍后检查记忆状态", "rollback_failed"
            )
        return _replacement_error("替换记忆失败，已回滚", "replacement_failed")

    @staticmethod
    def _log_cleanup_failure(
        old_memory_id: int,
        new_memory_id: int,
        *,
        cleanup_result: str,
        error_class: str | None,
    ) -> None:
        logger.error(
            "[PageAPI] operation=rollback_replacement old_memory_id=%s "
            "new_memory_id=%s cleanup_result=%s error_class=%s",
            old_memory_id,
            new_memory_id,
            cleanup_result,
            error_class,
        )
