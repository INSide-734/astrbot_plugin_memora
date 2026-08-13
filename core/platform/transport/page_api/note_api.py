"""控制台笔记 API，提供增删改查、搜索、版本历史与批量操作。"""

from __future__ import annotations

import copy
from functools import wraps
from typing import Any

from astrbot.api import logger
from quart import request

from ....features.notes.domain.models import Note, NoteStatus
from .response_utils import error_response, ok_response


def _get_note_manager_or_store(memory_engine):
    manager = getattr(memory_engine, "note_manager", None)
    store = getattr(memory_engine, "note_store", None)
    if manager is not None and "note_manager" not in vars(memory_engine):
        manager = None
    if store is not None and "note_store" not in vars(memory_engine):
        store = None
    return manager, store


def _store_supports_soft_delete(store) -> bool:
    return store is not None and hasattr(type(store), "soft_delete")


def _coerce_note_id(raw_id: Any) -> int:
    """将外部传入的 note_id 转换为整数，同时拒绝 JSON 布尔值。"""
    if isinstance(raw_id, bool):
        raise TypeError("布尔值不是合法的 note_id")
    return int(raw_id)


def _safe_note_to_dict(note: Any) -> dict[str, Any] | None:
    try:
        return note.to_dict()
    except Exception:
        return None


def _safe_note_version_to_dict(version: Any) -> dict[str, Any] | None:
    try:
        return {
            "version": version.version,
            "content": version.content,
            "created_at": version.created_at,
        }
    except Exception:
        return None


def _note_response_or_error(note: Any, versions: list[Any]):
    note_payload = _safe_note_to_dict(note)
    if note_payload is None:
        return error_response("note serialization failed: 笔记序列化失败")
    versions = _safe_note_version_list(versions)
    return ok_response(
        {
            "note": note_payload,
            "versions": [
                item
                for item in (_safe_note_version_to_dict(v) for v in versions)
                if item is not None
            ],
        }
    )


def _safe_note_version_value(note: Any) -> int | None:
    try:
        return note.version
    except Exception:
        return None


def _safe_note_version_list(versions: Any) -> list[Any]:
    try:
        return list(versions or [])
    except Exception:
        return []


def _safe_total(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _json_object_payload_or_error(payload: Any):
    if isinstance(payload, dict):
        return payload, None
    return None, error_response("请求体必须是 JSON 对象")


def _stable_api_errors(operation: str):
    def decorate(handler):
        @wraps(handler)
        async def wrapped(*args, **kwargs):
            try:
                return await handler(*args, **kwargs)
            except Exception as exc:
                logger.error(
                    "[笔记 API] operation=%s id=unavailable error_class=%s",
                    operation,
                    type(exc).__name__,
                )
                return error_response("笔记操作失败", code="internal_error")

        return wrapped

    return decorate


def _note_changes_validation_error(field_errors: dict[str, str]):
    return error_response(
        "笔记更新数据无效", code="validation_error", field_errors=field_errors
    )


def _note_changes_candidate(note: Note, changes: Any):
    if not isinstance(changes, dict):
        return None, _note_changes_validation_error({"changes": "必须是对象"})
    if not changes:
        return None, _note_changes_validation_error({"changes": "不能为空"})
    editable_fields = {"title", "content", "tags", "status"}
    unsupported = sorted(set(changes) - editable_fields)
    if unsupported:
        return None, _note_changes_validation_error(
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
        elif field == "tags":
            if not isinstance(value, list):
                errors[field] = "必须为字符串数组"
                continue
            normalized_tags: list[str] = []
            for index, tag in enumerate(value):
                if not isinstance(tag, str):
                    errors[f"tags.{index}"] = "必须为字符串"
                    continue
                normalized = tag.strip()
                if normalized and normalized not in normalized_tags:
                    normalized_tags.append(normalized)
            values[field] = normalized_tags
        else:
            if not isinstance(value, str):
                errors[field] = "必须为字符串"
            else:
                try:
                    values[field] = NoteStatus(value.strip())
                except ValueError:
                    errors[field] = "不支持的状态"
    if errors:
        return None, _note_changes_validation_error(errors)

    candidate = copy.copy(note)
    for field, value in values.items():
        setattr(candidate, field, value)
    return candidate, None


class NoteApiMixin:
    async def list_notes(self):
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        manager, store = _get_note_manager_or_store(engines["memory_engine"])
        backend = manager or store
        if not backend:
            return ok_response({"notes": [], "total": 0})
        args = request.args
        try:
            limit = int(args.get("limit", 50))
            offset = int(args.get("offset", 0))
        except (TypeError, ValueError):
            return error_response("limit 和 offset 必须为整数")
        status = str(args.get("status", ""))
        notes, total = await backend.list_notes(
            limit=limit, offset=offset, status=status
        )
        serialized_notes = [
            item for item in (_safe_note_to_dict(n) for n in notes) if item is not None
        ]
        return ok_response(
            {
                "notes": serialized_notes,
                "total": _safe_total(total),
                "limit": limit,
                "offset": offset,
            }
        )

    async def search_notes(self):
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        manager, store = _get_note_manager_or_store(engines["memory_engine"])
        backend = manager or store
        if not backend:
            return ok_response({"notes": [], "total": 0})
        args = request.args
        query = str(args.get("query", ""))
        if not query:
            return error_response("缺少查询参数 query")
        try:
            limit = int(args.get("limit", 20))
        except (TypeError, ValueError):
            return error_response("limit 必须为整数")
        notes, total = await backend.search(query=query, limit=limit)
        serialized_notes = [
            item for item in (_safe_note_to_dict(n) for n in notes) if item is not None
        ]
        return ok_response({"notes": serialized_notes, "total": _safe_total(total)})

    @_stable_api_errors("read_detail")
    async def get_note_detail(self):
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        manager, store = _get_note_manager_or_store(engines["memory_engine"])
        backend = manager or store
        if not backend:
            return error_response("笔记功能未启用")
        args = request.args
        try:
            note_id = _coerce_note_id(args.get("note_id", 0))
        except (TypeError, ValueError):
            return error_response("note_id must be an integer: note_id 必须为整数")
        if not note_id:
            return error_response("缺少必填参数 note_id")
        note = await backend.get_note(note_id) if manager else await store.get(note_id)
        if note is None:
            return error_response("not found: 笔记不存在")
        versions = (
            await backend.get_versions(note_id)
            if manager
            else await store.get_versions(note_id)
        )
        return _note_response_or_error(note, versions)

    @_stable_api_errors("create")
    async def create_note(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        manager, store = _get_note_manager_or_store(engines["memory_engine"])
        backend = manager or store
        if not backend:
            return error_response("笔记功能未启用")
        payload = await request.get_json(silent=True) or {}
        payload, error = _json_object_payload_or_error(payload)
        if error:
            return error
        note = Note(
            title=str(payload.get("title", "")),
            content=str(payload.get("content", "")),
            tags=list(payload.get("tags", []) or []),
            user_id=str(payload.get("user_id", "")),
        )
        if not note.title or not note.content:
            return error_response("title 和 content 为必填项")
        if manager:
            note_id = await manager.create_note(
                note.title, note.content, tags=note.tags, user_id=note.user_id
            )
        else:
            note_id = await store.create(note)
        return ok_response({"note_id": note_id})

    @_stable_api_errors("update")
    async def update_note(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        manager, store = _get_note_manager_or_store(engines["memory_engine"])
        backend = manager or store
        if not backend:
            return error_response("笔记功能未启用")
        payload = await request.get_json(silent=True) or {}
        payload, error = _json_object_payload_or_error(payload)
        if error:
            return error
        try:
            note_id = _coerce_note_id(payload.get("note_id", 0))
        except (TypeError, ValueError):
            return error_response("note_id must be an integer: note_id 必须为整数")
        if not note_id:
            return error_response("缺少必填参数 note_id")
        note = await backend.get_note(note_id) if manager else await store.get(note_id)
        if note is None:
            return error_response("not found: 笔记不存在")
        if "changes" in payload:
            candidate, candidate_error = _note_changes_candidate(
                note, payload["changes"]
            )
            if candidate_error:
                return candidate_error
            if manager:
                note = await manager.update_note(
                    note_id,
                    title=candidate.title,
                    content=candidate.content,
                    tags=candidate.tags,
                    status=candidate.status.value,
                )
                if note is None:
                    return error_response("not found: 笔记不存在")
            else:
                await store.update(candidate)
                note = candidate
            version = _safe_note_version_value(note)
            if version is None:
                return error_response(
                    "note version serialization failed: 笔记版本序列化失败"
                )
            return ok_response({"note_id": note_id, "version": version})
        # field/value 模式（前端兼容）：{note_id, field: "title"|"content"|"tags"|"status", value}
        field = str(payload.get("field", "")).strip()
        if field and "value" in payload:
            value = payload["value"]
            if field == "title":
                note.title = str(value)
            elif field == "content":
                note.content = str(value)
            elif field == "tags":
                note.tags = list(
                    value if isinstance(value, list) else str(value).split(",")
                )
            elif field == "status":
                note.status = NoteStatus(str(value).strip())
            else:
                return error_response(f"不支持的字段: {field}")
        else:
            # 全对象模式（原有兼容）
            if "title" in payload:
                note.title = str(payload["title"])
            if "content" in payload:
                note.content = str(payload["content"])
            if "tags" in payload:
                note.tags = list(payload["tags"] or [])
            if "status" in payload:
                note.status = NoteStatus(str(payload["status"]))
        if manager:
            note = await manager.update_note(
                note_id,
                title=note.title,
                content=note.content,
                tags=note.tags,
                status=note.status.value,
            )
            if note is None:
                return error_response("not found: 笔记不存在")
        else:
            await store.update(note)
        version = _safe_note_version_value(note)
        if version is None:
            return error_response(
                "note version serialization failed: 笔记版本序列化失败"
            )
        return ok_response({"note_id": note_id, "version": version})

    async def archive_note(self):
        """POST /notes/archive {note_id}：将笔记状态设为 archived。"""
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        manager, store = _get_note_manager_or_store(engines["memory_engine"])
        backend = manager or store
        if not backend:
            return error_response("笔记功能未启用")
        payload = await request.get_json(silent=True) or {}
        payload, error = _json_object_payload_or_error(payload)
        if error:
            return error
        try:
            note_id = _coerce_note_id(payload.get("note_id", 0))
        except (TypeError, ValueError):
            return error_response("note_id must be an integer: note_id 必须为整数")
        if not note_id:
            return error_response("缺少必填参数 note_id")
        note = await backend.get_note(note_id) if manager else await store.get(note_id)
        if note is None:
            return error_response("not found: 笔记不存在")
        note.status = NoteStatus.ARCHIVED
        if manager:
            note = await manager.update_note(note_id, status=NoteStatus.ARCHIVED.value)
            if note is None:
                return error_response("not found: 笔记不存在")
        else:
            await store.update(note)
        version = _safe_note_version_value(note)
        if version is None:
            return error_response(
                "note version serialization failed: 笔记版本序列化失败"
            )
        return ok_response(
            {"note_id": note_id, "status": "archived", "version": version}
        )

    async def batch_notes(self):
        """POST /notes/batch {note_ids, action}：统一批量操作。"""
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        payload = await request.get_json(silent=True) or {}
        payload, error = _json_object_payload_or_error(payload)
        if error:
            return error
        note_ids = payload.get("note_ids", [])
        action = str(payload.get("action", "")).strip()
        if not isinstance(note_ids, list) or not note_ids:
            return error_response("需要提供笔记 ID 列表")
        if action == "delete":
            return await self._batch_delete_notes_impl(note_ids)
        if action == "archive":
            engines, err = await self._ensure_plugin_ready()
            if err:
                return err
            manager, store = _get_note_manager_or_store(engines["memory_engine"])
            backend = manager or store
            if not backend:
                return error_response("笔记功能未启用")
            archived_count = 0
            failed_ids: list[Any] = []
            for raw_id in note_ids:
                try:
                    nid = _coerce_note_id(raw_id)
                    note = (
                        await backend.get_note(nid) if manager else await store.get(nid)
                    )
                    if note is None:
                        failed_ids.append(raw_id)
                        continue
                    note.status = NoteStatus.ARCHIVED
                    if manager:
                        updated = await manager.update_note(
                            nid, status=NoteStatus.ARCHIVED.value
                        )
                        if updated is None:
                            failed_ids.append(raw_id)
                            continue
                    else:
                        await store.update(note)
                    archived_count += 1
                except Exception as e:
                    logger.debug(f"批量归档笔记失败 (raw_id={raw_id}): {e}")
                    failed_ids.append(raw_id)
            return ok_response(
                {
                    "archived_count": archived_count,
                    "failed_count": len(failed_ids),
                    "total": len(note_ids),
                    "failed_ids": failed_ids,
                    "action": "archive",
                }
            )
        return error_response(f"不支持的操作: {action}")

    @_stable_api_errors("delete")
    async def delete_note(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        manager, store = _get_note_manager_or_store(engines["memory_engine"])
        backend = manager or store
        if not backend:
            return error_response("笔记功能未启用")
        payload = await request.get_json(silent=True) or {}
        payload, error = _json_object_payload_or_error(payload)
        if error:
            return error
        try:
            note_id = _coerce_note_id(payload.get("note_id", 0))
        except (TypeError, ValueError):
            return error_response("note_id must be an integer: note_id 必须为整数")
        if not note_id:
            return error_response("缺少必填参数 note_id")
        if manager:
            deleted = await manager.delete_note(note_id)
        elif _store_supports_soft_delete(store):
            deleted = await store.soft_delete(note_id)
        else:
            deleted = await store.delete(note_id)
        return ok_response({"deleted": deleted})

    @_stable_api_errors("read_versions")
    async def get_note_versions(self):
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        manager, store = _get_note_manager_or_store(engines["memory_engine"])
        backend = manager or store
        if not backend:
            return error_response("笔记功能未启用")
        args = request.args
        try:
            note_id = _coerce_note_id(args.get("note_id", 0))
        except (TypeError, ValueError):
            return error_response("note_id must be an integer: note_id 必须为整数")
        if not note_id:
            return error_response("缺少必填参数 note_id")
        versions = (
            await backend.get_versions(note_id)
            if manager
            else await store.get_versions(note_id)
        )
        versions = _safe_note_version_list(versions)
        return ok_response(
            {
                "versions": [
                    item
                    for item in (_safe_note_version_to_dict(v) for v in versions)
                    if item is not None
                ]
            }
        )

    async def _batch_delete_notes_impl(self, note_ids: list):
        """内部方法：批量删除笔记（由调用者直接传入 note_ids）。"""
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        manager, store = _get_note_manager_or_store(engines["memory_engine"])
        backend = manager or store
        if not backend:
            return error_response("笔记功能未启用")
        deleted_count = 0
        failed_count = 0
        failed_ids: list[Any] = []
        for raw_id in note_ids:
            try:
                nid = _coerce_note_id(raw_id)
                if manager:
                    deleted = await manager.delete_note(nid)
                elif _store_supports_soft_delete(store):
                    deleted = await store.soft_delete(nid)
                else:
                    deleted = await store.delete(nid)
                if deleted:
                    deleted_count += 1
                else:
                    failed_count += 1
                    failed_ids.append(raw_id)
            except Exception as e:
                logger.debug(f"批量删除笔记失败 (raw_id={raw_id}): {e}")
                failed_count += 1
                failed_ids.append(raw_id)
        return ok_response(
            {
                "deleted_count": deleted_count,
                "failed_count": failed_count,
                "total": len(note_ids),
                "failed_ids": failed_ids,
            }
        )

    async def batch_delete_notes(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        manager, store = _get_note_manager_or_store(engines["memory_engine"])
        backend = manager or store
        if not backend:
            return error_response("笔记功能未启用")
        payload = await request.get_json(silent=True) or {}
        payload, error = _json_object_payload_or_error(payload)
        if error:
            return error
        note_ids = payload.get("note_ids", [])
        if not isinstance(note_ids, list) or not note_ids:
            return error_response("需要提供笔记 ID 列表")
        deleted_count = 0
        failed_count = 0
        failed_ids: list[Any] = []
        for raw_id in note_ids:
            try:
                nid = _coerce_note_id(raw_id)
                if manager:
                    deleted = await manager.delete_note(nid)
                elif _store_supports_soft_delete(store):
                    deleted = await store.soft_delete(nid)
                else:
                    deleted = await store.delete(nid)
                if deleted:
                    deleted_count += 1
                else:
                    failed_count += 1
                    failed_ids.append(raw_id)
            except Exception as e:
                logger.debug(f"批量删除笔记失败 (raw_id={raw_id}): {e}")
                failed_count += 1
                failed_ids.append(raw_id)
        return ok_response(
            {
                "deleted_count": deleted_count,
                "failed_count": failed_count,
                "total": len(note_ids),
                "failed_ids": failed_ids,
            }
        )

    async def batch_update_notes(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        manager, store = _get_note_manager_or_store(engines["memory_engine"])
        backend = manager or store
        if not backend:
            return error_response("笔记功能未启用")
        payload = await request.get_json(silent=True) or {}
        payload, error = _json_object_payload_or_error(payload)
        if error:
            return error
        note_ids = payload.get("note_ids", [])
        field = str(payload.get("field", "")).strip()
        value = payload.get("value")
        if not isinstance(note_ids, list) or not note_ids:
            return error_response("需要提供笔记 ID 列表")
        if not field or value is None:
            return error_response("需要指定 field 和 value")
        if field not in ("status",):
            return error_response(f"批量更新不支持字段: {field}")
        updated_count = 0
        failed_ids: list[Any] = []
        for raw_id in note_ids:
            try:
                nid = _coerce_note_id(raw_id)
            except (TypeError, ValueError):
                failed_ids.append(raw_id)
                continue
            try:
                note = await backend.get_note(nid) if manager else await store.get(nid)
                if note is None:
                    failed_ids.append(raw_id)
                    continue
                if field == "status":
                    status_value = str(value).strip()
                    try:
                        note.status = NoteStatus(status_value)
                    except ValueError:
                        failed_ids.append(raw_id)
                        continue
                if manager:
                    updated = await manager.update_note(
                        nid,
                        title=note.title,
                        content=note.content,
                        tags=note.tags,
                        status=note.status.value,
                    )
                    if updated is None:
                        failed_ids.append(raw_id)
                        continue
                else:
                    await store.update(note)
                updated_count += 1
            except Exception as e:
                logger.debug(f"批量更新笔记失败 (raw_id={raw_id}): {e}")
                failed_ids.append(raw_id)
        return ok_response(
            {
                "updated_count": updated_count,
                "failed_count": len(failed_ids),
                "total": len(note_ids),
                "failed_ids": failed_ids,
            }
        )


__all__ = ["NoteApiMixin"]
