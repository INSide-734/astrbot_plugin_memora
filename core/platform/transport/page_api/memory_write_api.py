"""记忆写入 API"""

import asyncio
import time
from typing import Any

from astrbot.api import logger
from quart import request

from ....shared.memory_status import effective_memory_status, set_memory_status
from .history_tracker import HistoryTracker
from .response_utils import error_response


def _coerce_importance_value(raw_value: Any) -> float:
    """将外部传入的重要性值转换为浮点数，同时拒绝 JSON 布尔值。"""
    if isinstance(raw_value, bool):
        raise TypeError("boolean values are not valid importance values")
    return float(raw_value)


def _required_string(raw_value: Any, *, message: str) -> str:
    if not isinstance(raw_value, str):
        raise TypeError(message)
    value = raw_value.strip()
    if not value:
        raise ValueError(message)
    return value


def _is_safe_replacement_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


# 正文派生表示：页面编辑不提供，随旧 metadata 传入会被引擎当成“对本次新正文的
# 显式声明”而拒绝（fact_evidence_mismatch），因此内容替换前必须剔除。
_BODY_DERIVED_METADATA_KEYS = frozenset(
    {"key_facts", "fact_source_evidence", "canonical_summary"}
)


def _replacement_error(message: str, code: str) -> dict[str, Any]:
    return error_response(message, code=code)


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
            memory_id = MemoryWriteApiMixin._coerce_memory_id(payload.get("memory_id"))
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

        try:
            memory = await self._get_memory_record(memory_id)
        except Exception as exc:
            logger.error(
                "[PageAPI] operation=read_memory_for_update memory_id=%s error_class=%s",
                memory_id,
                type(exc).__name__,
            )
            return error_response("读取记忆失败", code="internal_error")
        if not memory:
            return self._error("记忆不存在")

        current_metadata = self._normalize_metadata(memory.get("metadata"))

        if field == "content":
            try:
                new_content = _required_string(
                    value, message="记忆内容必须是非空字符串"
                )
            except (TypeError, ValueError):
                return self._error("记忆内容必须是非空字符串")

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

            # 新建/删除/补偿与崩溃收敛由引擎写账本负责，API 只回读替换后的 owner。
            new_memory_id, failure = await self._apply_content_replacement(
                memory_engine,
                memory_id,
                {"content": new_content, "metadata": current_metadata},
            )
            if failure is not None:
                return failure

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
        updated_at = time.time()
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
            if not isinstance(value, str):
                return self._error("状态必须是字符串")
            status_value = value.strip()
            if status_value not in {"active", "dormant", "archived", "deleted"}:
                return self._error("状态必须是 active、dormant、archived 或 deleted")
            updates["metadata"] = {}
            set_memory_status(
                updates["metadata"],
                status_value,
                status_changed_at=updated_at,
            )
            old_v = effective_memory_status(current_metadata)
            new_v = status_value
        elif field == "type":
            try:
                type_value = _required_string(value, message="类型必须是非空字符串")
            except (TypeError, ValueError):
                return self._error("类型必须是非空字符串")
            updates["metadata"] = {"memory_type": type_value}
            old_v = current_metadata.get("memory_type", "GENERAL")
            new_v = type_value
        else:
            return self._error(f"不支持编辑字段: {field}")

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
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "[PageAPI] operation=legacy_update memory_id=%s error_class=%s",
                memory_id,
                type(exc).__name__,
            )
            return self._error("更新记忆失败")

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

        try:
            memory = await self._get_memory_record(memory_id)
        except Exception as exc:
            logger.error(
                "[PageAPI] operation=read_memory_for_update memory_id=%s error_class=%s",
                memory_id,
                type(exc).__name__,
            )
            return error_response("读取记忆失败", code="internal_error")
        if not memory:
            return self._error("记忆不存在")

        current_metadata = dict(self._normalize_metadata(memory.get("metadata")))
        final_metadata = dict(current_metadata)
        new_content = str(memory.get("text", ""))
        content_changed = False
        history_items: list[tuple[str, Any, Any]] = []
        updated_at = time.time()

        for field, value in changes.items():
            if field == "content":
                try:
                    parsed_content = _required_string(
                        value, message="记忆内容必须是非空字符串"
                    )
                except (TypeError, ValueError):
                    return self._error("记忆内容必须是非空字符串")
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
                if not isinstance(value, str):
                    return self._error("状态必须是字符串")
                status_value = value.strip()
                if status_value not in {"active", "dormant", "archived", "deleted"}:
                    return self._error(
                        "状态必须是 active、dormant、archived 或 deleted"
                    )
                set_memory_status(
                    final_metadata,
                    status_value,
                    status_changed_at=updated_at,
                )
                history_items.append(
                    (field, effective_memory_status(current_metadata), status_value)
                )
            else:
                try:
                    type_value = _required_string(value, message="类型必须是非空字符串")
                except (TypeError, ValueError):
                    return self._error("类型必须是非空字符串")
                final_metadata["memory_type"] = type_value
                history_items.append(
                    (field, current_metadata.get("memory_type", "GENERAL"), type_value)
                )

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
        updates: dict[str, Any] = {
            "content": new_content,
            "metadata": final_metadata,
        }
        if "importance" in changes:
            updates["importance"] = final_metadata["importance"]
        # 替换的创建/删除/补偿由引擎写账本负责，API 只回读替换后的 owner。
        new_memory_id, failure = await self._apply_content_replacement(
            memory_engine,
            memory_id,
            updates,
        )
        if failure is not None:
            return failure

        return self._ok(
            {
                "message": f"记忆内容已更新（ID: {memory_id} → {new_memory_id}）",
                "old_memory_id": memory_id,
                "new_memory_id": new_memory_id,
                "field": "changes",
            }
        )

    @staticmethod
    def _content_replacement_updates(updates: dict[str, Any]) -> dict[str, Any]:
        """构造内容替换入参：剔除旧正文派生的事实表示。

        ``key_facts``/``fact_source_evidence``/``canonical_summary`` 是正文的派生
        表示，页面编辑不提供它们；若随旧 metadata 一并传给引擎，会被当成“对本次
        新正文的显式声明”而按 ``fact_evidence_mismatch`` 拒绝，或用旧事实覆盖新
        正文的表示。这里复制后移除，交由引擎按新正文清除旧值并同步摘要；调用方
        传入的字典不被原地修改。
        """

        replacement = dict(updates)
        metadata = replacement.get("metadata")
        if isinstance(metadata, dict):
            replacement["metadata"] = {
                key: value
                for key, value in metadata.items()
                if key not in _BODY_DERIVED_METADATA_KEYS
            }
        return replacement

    async def _apply_content_replacement(
        self,
        memory_engine: Any,
        memory_id: int,
        updates: dict[str, Any],
    ) -> tuple[int | None, dict[str, Any] | None]:
        """经引擎替换 canonical 正文，并回读替换后的新 owner ID。

        替换的创建、旧行删除、补偿回滚与崩溃收敛全部由
        ``MemoryEngine.update_memory`` 及其写账本负责；页面只负责在成功后用
        ``find_replacement_memory_id`` 回读唯一新 owner，并在无法确认时
        fail-closed（不自行 add/delete，避免制造第二条 canonical）。

        返回 ``(new_memory_id, None)`` 表示替换成功；``(None, error_response)``
        表示失败，错误码沿用既有页面语义（``replacement_failed`` /
        ``repair_required`` / 通用内部错误）。
        """

        try:
            success = await memory_engine.update_memory(
                memory_id,
                self._content_replacement_updates(updates),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "[PageAPI] operation=content_update memory_id=%s error_class=%s",
                memory_id,
                type(exc).__name__,
            )
            return None, self._error("更新记忆失败")

        if not success:
            return None, _replacement_error("替换记忆失败", "replacement_failed")

        try:
            replacement = await memory_engine.find_replacement_memory_id(memory_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "[PageAPI] operation=content_replace_lookup memory_id=%s "
                "error_class=%s",
                memory_id,
                type(exc).__name__,
            )
            return None, _replacement_error(
                "记忆替换状态待修复，请稍后检查", "repair_required"
            )

        if not _is_safe_replacement_id(replacement):
            # 正文已提交但账本无法确认唯一新 owner：不回退自建替换，交由修复收敛。
            return None, _replacement_error(
                "记忆替换状态待修复，请稍后检查", "repair_required"
            )
        return int(replacement), None
