"""控制台 API：知识库的列表、搜索、详情、增删改查与批量操作。"""

from __future__ import annotations

import contextlib
import copy
import math
from functools import wraps
from typing import Any

from astrbot.api import logger
from quart import request

from ..base.list_sorting import parse_sort_query
from ..features.knowledge.domain.models import KnowledgeEntry, KnowledgeType
from ..features.knowledge.infrastructure.knowledge_store import KNOWLEDGE_SORT_COLUMNS
from .response_utils import error_response, ok_response


def _coerce_entry_id(raw_id: Any) -> int:
    """将外部传入的知识条目 ID 转换为整数，同时拒绝 JSON 布尔值。"""
    if isinstance(raw_id, bool):
        raise TypeError("boolean values are not valid knowledge entry ids")
    return int(raw_id)


def _coerce_confidence_value(raw_value: Any) -> float:
    """将外部传入的置信度值转换为浮点数，同时拒绝 JSON 布尔值。"""
    if isinstance(raw_value, bool):
        raise TypeError("boolean values are not valid confidence values")
    return float(raw_value)


def _safe_entry_to_dict(entry: Any) -> dict[str, Any] | None:
    try:
        return entry.to_dict()
    except Exception:
        return None


def _safe_entry_list(entries: Any) -> list[Any]:
    try:
        return list(entries or [])
    except Exception:
        return []


def _safe_total(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _entry_response_or_error(entry: Any):
    payload = _safe_entry_to_dict(entry)
    if payload is None:
        return error_response("entry serialization failed: 条目序列化失败")
    return ok_response(payload)


def _json_object_payload_or_error(payload: Any):
    if isinstance(payload, dict):
        return payload, None
    return None, error_response("请求体必须为 JSON 对象")


def _stable_api_errors(operation: str):
    def decorate(handler):
        @wraps(handler)
        async def wrapped(*args, **kwargs):
            try:
                return await handler(*args, **kwargs)
            except Exception as exc:
                logger.error(
                    "[知识 API] operation=%s id=unavailable error_class=%s",
                    operation,
                    type(exc).__name__,
                )
                return error_response("知识库操作失败", code="internal_error")

        return wrapped

    return decorate


def _normalize_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    try:
        return [str(item).strip() for item in value if str(item).strip()]
    except TypeError:
        text = str(value).strip()
        return [text] if text else []


def _changes_validation_error(field_errors: dict[str, str]):
    return error_response(
        "知识条目更新数据无效", code="validation_error", field_errors=field_errors
    )


def _sort_query_error(exc: ValueError):
    message = str(exc)
    field = "sort_order" if message == "sort_order must be asc or desc" else "sort_by"
    return error_response(
        message,
        code="invalid_query",
        field_errors={field: message},
    )


def _knowledge_changes_candidate(entry: KnowledgeEntry, changes: Any):
    if not isinstance(changes, dict):
        return None, _changes_validation_error({"changes": "必须是对象"})
    if not changes:
        return None, _changes_validation_error({"changes": "不能为空"})
    editable_fields = {"title", "content", "category", "confidence", "tags"}
    unsupported = sorted(set(changes) - editable_fields)
    if unsupported:
        return None, _changes_validation_error(
            {field: "字段不可写" for field in unsupported}
        )

    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for field, value in changes.items():
        if field in {"title", "content"}:
            if not isinstance(value, str):
                errors[field] = "必须为字符串"
            elif not value.strip():
                errors[field] = "不能为空"
            else:
                values[field] = value.strip()
        elif field == "category":
            if not isinstance(value, str):
                errors[field] = "必须为字符串"
            else:
                try:
                    values[field] = KnowledgeType(value.strip().lower())
                except ValueError:
                    errors[field] = "不支持的分类"
        elif field == "confidence":
            if isinstance(value, bool):
                errors[field] = "必须为数字"
            else:
                try:
                    confidence = float(value)
                except (TypeError, ValueError):
                    errors[field] = "必须为数字"
                else:
                    if not math.isfinite(confidence):
                        errors[field] = "必须为有限数字"
                    elif not 0.0 <= confidence <= 1.0:
                        errors[field] = "必须在 0 到 1 之间"
                    else:
                        values[field] = confidence
        else:
            if isinstance(value, str):
                values[field] = [
                    item.strip() for item in value.split(",") if item.strip()
                ]
            elif isinstance(value, list):
                normalized_tags: list[str] = []
                for index, tag in enumerate(value):
                    if not isinstance(tag, str):
                        errors[f"tags.{index}"] = "必须为字符串"
                    elif tag.strip():
                        normalized_tags.append(tag.strip())
                values[field] = normalized_tags
            else:
                errors[field] = "必须为字符串或字符串数组"
    if errors:
        return None, _changes_validation_error(errors)

    candidate = copy.copy(entry)
    for field, value in values.items():
        setattr(candidate, field, value)
    return candidate, None


class KnowledgeApiMixin:
    async def list_knowledge(self):
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        manager = getattr(engines["memory_engine"], "knowledge_manager", None)
        if not manager:
            return ok_response({"entries": [], "total": 0})
        args = request.args
        try:
            limit = int(args.get("limit", 50))
            offset = int(args.get("offset", 0))
        except (TypeError, ValueError):
            return error_response("limit 和 offset 必须为整数")
        try:
            sort = parse_sort_query(
                args,
                allowed=KNOWLEDGE_SORT_COLUMNS,
                default_by="updated_at",
                default_order="desc",
            )
        except ValueError as exc:
            return _sort_query_error(exc)
        category = str(args.get("category", ""))
        entries, total = await manager.list_entries(
            limit=limit,
            offset=offset,
            category=category,
            sort=sort,
        )
        entries = _safe_entry_list(entries)
        total = _safe_total(total, 0)
        serialized_entries = [
            item
            for item in (_safe_entry_to_dict(e) for e in entries)
            if item is not None
        ]
        return ok_response(
            {
                "entries": serialized_entries,
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        )

    async def search_knowledge(self):
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        args = request.args
        query = str(args.get("query", ""))
        if not query:
            return error_response("缺少查询参数 query")
        manager = getattr(engines["memory_engine"], "knowledge_manager", None)
        if not manager:
            return ok_response({"entries": [], "total": 0})
        try:
            limit = int(args.get("limit", 20))
        except (TypeError, ValueError):
            return error_response("limit 必须为整数")
        try:
            sort = parse_sort_query(
                args,
                allowed=KNOWLEDGE_SORT_COLUMNS,
                default_by="updated_at",
                default_order="desc",
            )
        except ValueError as exc:
            return _sort_query_error(exc)
        category = str(args.get("category", ""))
        entries, total = await manager.search(
            query=query,
            limit=limit,
            category=category,
            sort=sort,
        )
        entries = _safe_entry_list(entries)
        total = _safe_total(total, 0)
        serialized_entries = [
            item
            for item in (_safe_entry_to_dict(e) for e in entries)
            if item is not None
        ]
        return ok_response({"entries": serialized_entries, "total": total})

    @_stable_api_errors("read_detail")
    async def get_knowledge_detail(self):
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        manager = getattr(engines["memory_engine"], "knowledge_manager", None)
        if not manager:
            return error_response("知识库功能未启用")
        args = request.args
        try:
            entry_id = _coerce_entry_id(args.get("entry_id", 0))
        except (TypeError, ValueError):
            return error_response("entry_id must be an integer: entry_id 必须为整数")
        if not entry_id:
            return error_response("缺少必填参数 entry_id")
        entry = await manager.get_entry(entry_id)
        if entry is None:
            return error_response("not found: 条目不存在")
        return _entry_response_or_error(entry)

    @_stable_api_errors("create")
    async def create_knowledge_entry(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        manager = getattr(engines["memory_engine"], "knowledge_manager", None)
        if not manager:
            return error_response("知识库功能未启用")
        payload = await request.get_json(silent=True) or {}
        payload, error = _json_object_payload_or_error(payload)
        if error:
            return error
        title = str(payload.get("title", "")).strip()
        content = str(payload.get("content", "")).strip()
        if not title or not content:
            return error_response("title 和 content 为必填项")
        category_raw = str(payload.get("category", "fact")).strip().lower()
        try:
            category = KnowledgeType(category_raw)
        except ValueError:
            category = KnowledgeType.FACT
        try:
            confidence = _coerce_confidence_value(payload.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        tags = _normalize_tags(payload.get("tags", []))
        entry = KnowledgeEntry(
            title=title,
            content=content,
            category=category,
            confidence=confidence,
            tags=tags,
        )
        entry_id = await manager.add_entry(entry)
        if entry_id is None:
            return error_response("创建知识条目失败")
        return ok_response({"entry_id": entry_id})

    @_stable_api_errors("update")
    async def update_knowledge_entry(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        manager = getattr(engines["memory_engine"], "knowledge_manager", None)
        if not manager:
            return error_response("知识库功能未启用")
        payload = await request.get_json(silent=True) or {}
        payload, error = _json_object_payload_or_error(payload)
        if error:
            return error
        try:
            entry_id = _coerce_entry_id(payload.get("entry_id", 0))
        except (TypeError, ValueError):
            return error_response("entry_id must be an integer: entry_id 必须为整数")
        if not entry_id:
            return error_response("缺少必填参数 entry_id")
        entry = await manager.get_entry(entry_id)
        if entry is None:
            return error_response("not found: 条目不存在")
        if "changes" in payload:
            candidate, candidate_error = _knowledge_changes_candidate(
                entry, payload["changes"]
            )
            if candidate_error:
                return candidate_error
            updated = await manager.update_entry(candidate)
            if not updated:
                return error_response("not found: 条目不存在")
            return ok_response({"entry_id": entry_id})
        # field/value 模式（前端兼容）：{entry_id, field: "title"|"content"|..., value}
        field = str(payload.get("field", "")).strip()
        if field and "value" in payload:
            value = payload["value"]
            if field == "title":
                entry.title = str(value)
            elif field == "content":
                entry.content = str(value)
            elif field == "category":
                with contextlib.suppress(ValueError):
                    entry.category = KnowledgeType(str(value).strip().lower())
            elif field == "confidence":
                with contextlib.suppress(TypeError, ValueError):
                    entry.confidence = _coerce_confidence_value(value)
            elif field == "tags":
                entry.tags = list(
                    value if isinstance(value, list) else str(value).split(",")
                )
            else:
                return error_response(f"不支持的字段: {field}")
        else:
            # 全对象模式（原有兼容）
            if "title" in payload:
                entry.title = str(payload["title"]).strip()
            if "content" in payload:
                entry.content = str(payload["content"]).strip()
            if "category" in payload:
                with contextlib.suppress(ValueError):
                    entry.category = KnowledgeType(
                        str(payload["category"]).strip().lower()
                    )
            if "confidence" in payload:
                with contextlib.suppress(TypeError, ValueError):
                    entry.confidence = _coerce_confidence_value(payload["confidence"])
            if "tags" in payload:
                entry.tags = _normalize_tags(payload["tags"])
        if not entry.title or not entry.content:
            return error_response("title 和 content 为必填项")
        updated = await manager.update_entry(entry)
        if not updated:
            return error_response("not found: 条目不存在")
        return ok_response({"entry_id": entry_id})

    @_stable_api_errors("delete")
    async def delete_knowledge_entry(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        manager = getattr(engines["memory_engine"], "knowledge_manager", None)
        if not manager:
            return error_response("知识库功能未启用")
        payload = await request.get_json(silent=True) or {}
        payload, error = _json_object_payload_or_error(payload)
        if error:
            return error
        try:
            entry_id = _coerce_entry_id(payload.get("entry_id", 0))
        except (TypeError, ValueError):
            return error_response("entry_id must be an integer: entry_id 必须为整数")
        if not entry_id:
            return error_response("缺少必填参数 entry_id")
        deleted = await manager.delete_entry(entry_id)
        return ok_response({"deleted": deleted})

    async def batch_knowledge(self):
        """POST /knowledge/batch {entry_ids, action: "delete"} — 统一批量入口"""
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        payload = await request.get_json(silent=True) or {}
        payload, error = _json_object_payload_or_error(payload)
        if error:
            return error
        entry_ids = payload.get("entry_ids", [])
        action = str(payload.get("action", "")).strip()
        if not isinstance(entry_ids, list) or not entry_ids:
            return error_response("需要提供知识条目 ID 列表")
        if action == "delete":
            return await self._batch_delete_knowledge_impl(entry_ids)
        return error_response(f"不支持的操作: {action}")

    async def _batch_delete_knowledge_impl(self, entry_ids: list):
        """内部方法：批量删除知识条目（不读取 request body）"""
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        manager = getattr(engines["memory_engine"], "knowledge_manager", None)
        if not manager:
            return error_response("知识库功能未启用")
        deleted_count = 0
        failed_count = 0
        failed_ids: list[Any] = []
        for raw_id in entry_ids:
            try:
                eid = _coerce_entry_id(raw_id)
                if await manager.delete_entry(eid):
                    deleted_count += 1
                else:
                    failed_count += 1
                    failed_ids.append(raw_id)
            except Exception as e:
                logger.debug(f"批量删除知识条目失败 (raw_id={raw_id}): {e}")
                failed_count += 1
                failed_ids.append(raw_id)
        return ok_response(
            {
                "deleted_count": deleted_count,
                "failed_count": failed_count,
                "total": len(entry_ids),
                "failed_ids": failed_ids,
            }
        )

    async def batch_delete_knowledge(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        manager = getattr(engines["memory_engine"], "knowledge_manager", None)
        if not manager:
            return error_response("知识库功能未启用")
        payload = await request.get_json(silent=True) or {}
        payload, error = _json_object_payload_or_error(payload)
        if error:
            return error
        entry_ids = payload.get("entry_ids", [])
        if not isinstance(entry_ids, list) or not entry_ids:
            return error_response("需要提供知识条目 ID 列表")
        deleted_count = 0
        failed_count = 0
        failed_ids: list[Any] = []
        for raw_id in entry_ids:
            try:
                eid = _coerce_entry_id(raw_id)
                if await manager.delete_entry(eid):
                    deleted_count += 1
                else:
                    failed_count += 1
                    failed_ids.append(raw_id)
            except Exception as e:
                logger.debug(f"批量删除知识条目失败 (raw_id={raw_id}): {e}")
                failed_count += 1
                failed_ids.append(raw_id)
        return ok_response(
            {
                "deleted_count": deleted_count,
                "failed_count": failed_count,
                "total": len(entry_ids),
                "failed_ids": failed_ids,
            }
        )

    async def batch_update_knowledge(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        manager = getattr(engines["memory_engine"], "knowledge_manager", None)
        if not manager:
            return error_response("知识库功能未启用")
        payload = await request.get_json(silent=True) or {}
        payload, error = _json_object_payload_or_error(payload)
        if error:
            return error
        entry_ids = payload.get("entry_ids", [])
        field = str(payload.get("field", "")).strip()
        value = payload.get("value")
        if not isinstance(entry_ids, list) or not entry_ids:
            return error_response("需要提供知识条目 ID 列表")
        if not field or value is None:
            return error_response("需要指定 field 和 value")
        if field not in ("category",):
            return error_response(f"批量更新不支持字段: {field}")
        updated_count = 0
        failed_ids: list[Any] = []
        for raw_id in entry_ids:
            try:
                eid = _coerce_entry_id(raw_id)
            except (TypeError, ValueError):
                failed_ids.append(raw_id)
                continue
            try:
                entry = await manager.get_entry(eid)
                if entry is None:
                    failed_ids.append(raw_id)
                    continue
                if field == "category":
                    try:
                        entry.category = KnowledgeType(str(value).strip().lower())
                    except ValueError:
                        failed_ids.append(raw_id)
                        continue
                if not await manager.update_entry(entry):
                    failed_ids.append(raw_id)
                    continue
                updated_count += 1
            except Exception as e:
                logger.debug(f"批量更新知识条目失败 (raw_id={raw_id}): {e}")
                failed_ids.append(raw_id)
        return ok_response(
            {
                "updated_count": updated_count,
                "failed_count": len(failed_ids),
                "total": len(entry_ids),
                "failed_ids": failed_ids,
            }
        )


__all__ = ["KnowledgeApiMixin"]
