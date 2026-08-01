"""Memory review queue Page API."""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import aiosqlite
from astrbot.api import logger
from quart import request

from ..managers.feedback_signal_manager import record_explicit_correction
from ..models.feedback_signal import FeedbackOutcome
from ..review import ReviewAction, ReviewDetector, ReviewStore
from ..review.models import ReviewStatus
from ..storage.base import apply_perf_pragmas

_ACTION_STATUS = {
    "approve": ReviewStatus.APPROVED.value,
    "edit": ReviewStatus.EDITED.value,
    "merge": ReviewStatus.MERGED.value,
    "archive": ReviewStatus.ARCHIVED.value,
    "delete": ReviewStatus.DELETED.value,
    "mark_safe": ReviewStatus.SAFE.value,
}


class ReviewApiMixin:
    """Mixin: review queue list/detail/refresh/action endpoints."""

    async def _get_review_store(self) -> ReviewStore:
        store = getattr(self, "_review_store", None)
        if store is not None:
            return store

        initializer = getattr(getattr(self, "plugin", None), "initializer", None)
        data_dir = getattr(initializer, "data_dir", None)
        if not data_dir:
            raise RuntimeError("review store data_dir unavailable")

        db_path = Path(data_dir) / "review_queue.sqlite3"
        store = ReviewStore(db_path)
        await store.initialize()
        self._review_store = store
        return store

    async def list_review_items(self):
        try:
            store = await self._get_review_store()
            limit = self._safe_int(request.args.get("limit", 50), 50)
            items = await store.list_items(
                status=self._optional_arg("status"),
                reason=self._optional_arg("reason"),
                severity=self._optional_arg("severity"),
                cursor=self._optional_arg("cursor"),
                limit=min(200, max(1, limit)),
            )
            return self._ok({"items": items, "total": len(items)})
        except Exception as exc:
            logger.error("[ReviewAPI] list failed: %s", exc, exc_info=True)
            return self._error(str(exc))

    async def get_review_item_detail(self):
        try:
            review_id = self._review_id_from_args()
            if not review_id:
                return self._error("review_id required")
            store = await self._get_review_store()
            item = await store.get_item(review_id)
            if item is None:
                return self._error("review item not found")
            actions = await store.list_actions(review_id)
            return self._ok({"item": item, "actions": actions})
        except Exception as exc:
            logger.error("[ReviewAPI] detail failed: %s", exc, exc_info=True)
            return self._error(str(exc))

    async def refresh_review_items(self):
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error
        try:
            limit = min(
                500, max(1, self._safe_int(request.args.get("limit", 200), 200))
            )
            memory_engine = ready["memory_engine"]
            memories = await self._load_review_candidate_memories(memory_engine, limit)
            quality_stats = self._load_review_quality_stats()
            detected = ReviewDetector().detect(
                memories=memories,
                quality_stats=quality_stats,
            )
            store = await self._get_review_store()

            opened = 0
            unchanged = 0
            for item in detected:
                before = await self._find_open_review_item(
                    store,
                    item.memory_id,
                    [str(reason) for reason in item.reasons],
                )
                stored = await store.upsert_item(item)
                if before is None:
                    opened += 1
                elif stored == before:
                    unchanged += 1
                else:
                    unchanged += 1

            return self._ok(
                {
                    "scanned": len(memories),
                    "opened": opened,
                    "unchanged": unchanged,
                }
            )
        except Exception as exc:
            logger.error("[ReviewAPI] refresh failed: %s", exc, exc_info=True)
            return self._error(str(exc))

    async def apply_review_action(self):
        try:
            payload = await request.get_json(silent=True) or {}
            if not isinstance(payload, dict):
                return self._error("请求体必须为 JSON 对象")

            review_id = str(payload.get("review_id") or "").strip()
            action = str(payload.get("action") or "").strip()
            action_payload = payload.get("payload") or {}
            if not isinstance(action_payload, dict):
                return self._error("payload must be an object")
            if not review_id:
                return self._error("review_id required")
            if action not in _ACTION_STATUS:
                return self._error(f"unsupported review action: {action}")
            if action == "delete" and payload.get("confirmed") is not True:
                return self._error("confirmation_required")
        except Exception as exc:
            logger.error(
                "[ReviewAPI] action request parse failed: %s", exc, exc_info=True
            )
            return self._error(str(exc))

        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        ready, error = await self._ensure_plugin_ready()
        if error:
            return error

        store = await self._get_review_store()
        item = await store.get_item(review_id)
        if item is None:
            return self._error("review item not found")

        memory_engine = ready["memory_engine"]
        memory_id = item["memory_id"]
        try:
            mutation = await self._apply_review_memory_mutation(
                memory_engine,
                memory_id,
                action,
                action_payload,
            )
            status = _ACTION_STATUS[action]
            record_payload = {
                "request_payload": action_payload,
                "memory_id": memory_id,
                **mutation["payload"],
            }
            if action == "mark_safe":
                await self._mark_matching_review_items_safe(store, item, record_payload)
            else:
                await store.record_action(
                    ReviewAction(
                        item_id=review_id,
                        action=status,
                        payload=record_payload,
                    )
                )
            self._record_review_feedback(memory_engine, review_id, action)
            return self._ok(
                {
                    "review_id": review_id,
                    "action": action,
                    "memory_mutated": mutation["memory_mutated"],
                    "new_status": status,
                    **mutation.get("response", {}),
                }
            )
        except Exception as exc:
            logger.error("[ReviewAPI] action failed: %s", exc, exc_info=True)
            return self._error(str(exc))

    def _record_review_feedback(
        self,
        memory_engine: Any,
        review_id: str,
        action: str,
    ) -> None:
        """把管理员复核动作作为可信反馈写入隔离管线。"""

        manager = getattr(memory_engine, "feedback_signal_manager", None)
        if manager is None:
            return
        try:
            record_explicit_correction(
                manager,
                decision_key=f"review:{review_id}",
                scope_domain="review",
                outcome=(
                    FeedbackOutcome.POSITIVE
                    if action in {"approve", "mark_safe"}
                    else FeedbackOutcome.NEGATIVE
                ),
            )
        except Exception:
            logger.warning("[ReviewAPI] 反馈记录失败")

    async def _apply_review_memory_mutation(
        self,
        memory_engine: Any,
        memory_id: str,
        action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if action in {"approve", "mark_safe"}:
            return {"memory_mutated": False, "payload": {}}
        if action == "archive":
            await self._archive_memory(memory_engine, memory_id)
            return {
                "memory_mutated": True,
                "payload": {"archived_memory_id": memory_id},
            }
        if action == "delete":
            await self._delete_memory(memory_engine, memory_id)
            return {
                "memory_mutated": True,
                "payload": {"deleted_memory_id": memory_id},
            }
        if action == "edit":
            return await self._edit_memory(memory_engine, memory_id, payload)
        if action == "merge":
            return await self._merge_memory(memory_engine, memory_id, payload)
        raise ValueError(f"unsupported review action: {action}")

    async def _edit_memory(
        self,
        memory_engine: Any,
        memory_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        new_content = str(payload.get("content") or payload.get("text") or "").strip()
        if not new_content:
            raise ValueError("content required")
        before = await self._get_engine_memory(memory_engine, memory_id)
        before_content = self._memory_content(before)

        result = await self._update_memory_content(
            memory_engine, memory_id, new_content
        )
        if result is False:
            raise ValueError("memory update failed")
        replacement = await self._detect_replacement_memory_id(
            memory_engine,
            memory_id,
            new_content,
            result,
        )
        if replacement is not None and str(replacement) != str(memory_id):
            replacement_payload = {
                "old_memory_id": str(memory_id),
                "new_memory_id": str(replacement),
            }
            response_payload = {
                "memory_id": str(memory_id),
                "new_memory_id": str(replacement),
            }
        else:
            replacement_payload = {"old_memory_id": str(memory_id)}
            response_payload = {"memory_id": str(memory_id)}
            if self._content_update_may_replace(memory_engine):
                replacement_payload["replacement_unknown"] = True
                response_payload["replacement_unknown"] = True

        return {
            "memory_mutated": True,
            "payload": {
                "before_content": before_content,
                "after_content": new_content,
                **replacement_payload,
            },
            "response": response_payload,
        }

    async def _merge_memory(
        self,
        memory_engine: Any,
        source_memory_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        target_memory_id = str(
            payload.get("target_memory_id") or payload.get("target_id") or ""
        ).strip()
        if not target_memory_id:
            raise ValueError("target_memory_id required")
        source = await self._get_engine_memory(memory_engine, source_memory_id)
        target = await self._get_engine_memory(memory_engine, target_memory_id)
        if source is None or target is None:
            raise ValueError("source or target memory not found")

        target_content = self._memory_content(target)
        source_content = self._memory_content(source)
        replacement_target_memory_id = None
        if source_content and source_content not in target_content:
            merged_content = f"{target_content}\n{source_content}".strip()
            result = await self._update_memory_content(
                memory_engine,
                target_memory_id,
                merged_content,
            )
            if result is False:
                raise ValueError("target memory update failed")
            replacement_target_memory_id = await self._detect_replacement_memory_id(
                memory_engine,
                target_memory_id,
                merged_content,
                result,
            )
        await self._archive_memory(memory_engine, source_memory_id)
        action_payload = {
            "source_memory_id": source_memory_id,
            "target_memory_id": target_memory_id,
        }
        response_payload = {
            "memory_id": source_memory_id,
            "target_memory_id": target_memory_id,
        }
        if replacement_target_memory_id is not None:
            action_payload["replacement_target_memory_id"] = str(
                replacement_target_memory_id
            )
            response_payload["replacement_target_memory_id"] = str(
                replacement_target_memory_id
            )
        elif (
            source_content
            and source_content not in target_content
            and self._content_update_may_replace(memory_engine)
        ):
            action_payload["replacement_target_unknown"] = True
            response_payload["replacement_target_unknown"] = True
        return {
            "memory_mutated": True,
            "payload": action_payload,
            "response": response_payload,
        }

    async def _update_memory_content(
        self,
        memory_engine: Any,
        memory_id: str,
        new_content: str,
    ) -> Any:
        updater = getattr(memory_engine, "update_memory_content", None)
        if callable(updater):
            result = updater(memory_id, new_content)
            if inspect.isawaitable(result):
                result = await result
            return result
        return await self._update_memory(
            memory_engine, memory_id, {"content": new_content}
        )

    async def _detect_replacement_memory_id(
        self,
        memory_engine: Any,
        old_memory_id: str,
        new_content: str,
        update_result: Any,
    ) -> str | None:
        direct = self._coerce_memory_id_from_update_result(update_result)
        if direct:
            return direct
        from_list = await self._find_replacement_in_engine_lists(
            memory_engine,
            old_memory_id,
            new_content,
        )
        if from_list:
            return from_list
        db_path = getattr(memory_engine, "db_path", None)
        if db_path:
            return await self._find_replacement_in_db(
                db_path, old_memory_id, new_content
            )
        return None

    async def _find_replacement_in_engine_lists(
        self,
        memory_engine: Any,
        old_memory_id: str,
        new_content: str,
    ) -> str | None:
        memories: list[Any] = []
        list_memories = getattr(memory_engine, "list_memories", None)
        if callable(list_memories):
            try:
                result = list_memories(limit=500)
            except TypeError:
                result = list_memories()
            if inspect.isawaitable(result):
                result = await result
            memories = self._extract_memory_sequence(result)
        else:
            values = getattr(memory_engine, "memories", None)
            if isinstance(values, Mapping):
                memories = list(values.values())
            elif isinstance(values, Sequence) and not isinstance(
                values, str | bytes | bytearray
            ):
                memories = list(values)
        return self._find_replacement_in_memory_items(
            memories, old_memory_id, new_content
        )

    async def _find_replacement_in_db(
        self,
        db_path: str,
        old_memory_id: str,
        new_content: str,
    ) -> str | None:
        try:
            async with aiosqlite.connect(db_path) as db:
                await apply_perf_pragmas(db)
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    """
                    SELECT id, text, metadata
                    FROM documents
                    WHERE CASE WHEN json_valid(metadata)
                          THEN json_extract(metadata, '$.previous_id') END = ?
                       OR text = ?
                    ORDER BY id DESC
                    LIMIT 20
                    """,
                    (old_memory_id, new_content),
                )
                rows = await cursor.fetchall()
        except Exception as exc:
            logger.debug("[ReviewAPI] replacement db lookup failed: %s", exc)
            return None
        return self._find_replacement_in_memory_items(rows, old_memory_id, new_content)

    async def _archive_memory(self, memory_engine: Any, memory_id: str) -> None:
        result = await self._update_memory(
            memory_engine,
            memory_id,
            {"metadata": {"status": "archived"}},
        )
        if result is False:
            raise ValueError("memory archive failed")

    async def _delete_memory(self, memory_engine: Any, memory_id: str) -> None:
        deleter = getattr(memory_engine, "delete_memory", None)
        if not callable(deleter):
            raise ValueError("delete_memory unavailable")
        result = deleter(memory_id)
        if inspect.isawaitable(result):
            result = await result
        if result is False:
            raise ValueError("memory delete failed")

    async def _update_memory(
        self,
        memory_engine: Any,
        memory_id: str,
        updates: dict[str, Any],
    ) -> Any:
        updater = getattr(memory_engine, "update_memory", None)
        if not callable(updater):
            raise ValueError("update_memory unavailable")
        result = updater(memory_id, updates)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def _get_engine_memory(self, memory_engine: Any, memory_id: str) -> Any:
        getter = getattr(memory_engine, "get_memory", None)
        if not callable(getter):
            return None
        result = getter(memory_id)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def _mark_matching_review_items_safe(
        self,
        store: ReviewStore,
        item: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        memory_id = str(item["memory_id"])
        reasons = set(item.get("reasons") or [])
        cursor = None
        seen_cursors: set[str] = set()
        while True:
            open_items = await store.list_items(
                status="open",
                limit=200,
                cursor=cursor,
            )
            if not open_items:
                break
            for candidate in open_items:
                if str(candidate.get("memory_id")) != memory_id:
                    continue
                if not reasons.intersection(set(candidate.get("reasons") or [])):
                    continue
                await store.record_action(
                    ReviewAction(
                        item_id=candidate["item_id"],
                        action=ReviewStatus.SAFE.value,
                        payload=payload,
                    )
                )
            next_cursor = str(open_items[-1].get("item_id") or "")
            if not next_cursor or next_cursor in seen_cursors:
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor

    async def _load_review_candidate_memories(
        self,
        memory_engine: Any,
        limit: int,
    ) -> list[dict[str, Any]]:
        list_memories = getattr(memory_engine, "list_memories", None)
        if callable(list_memories):
            try:
                result = list_memories(limit=limit)
            except TypeError:
                result = list_memories()
            if inspect.isawaitable(result):
                result = await result
            memories = self._extract_memory_sequence(result)
            return [self._normalize_review_memory(item) for item in memories[:limit]]

        db_path = getattr(memory_engine, "db_path", None)
        if not db_path:
            raise RuntimeError("memory listing unavailable")
        return await self._load_review_memories_from_db(db_path, limit)

    async def _load_review_memories_from_db(
        self,
        db_path: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        async with aiosqlite.connect(db_path) as db:
            await apply_perf_pragmas(db)
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT id, doc_id, text, metadata, created_at, updated_at
                FROM documents
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
        memories = []
        for row in rows:
            metadata = self._normalize_metadata(row["metadata"])
            memories.append(
                {
                    "id": row["id"],
                    "doc_id": row["doc_id"],
                    "content": row["text"],
                    "text": row["text"],
                    "metadata": metadata,
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "importance": metadata.get("importance", 0),
                }
            )
        return memories

    def _load_review_quality_stats(self) -> dict[str, Any]:
        scorer = None
        get_scorer = getattr(self, "_get_existing_quality_scorer", None)
        if callable(get_scorer):
            scorer = get_scorer()
        if scorer is not None and hasattr(scorer, "get_stats"):
            try:
                stats = scorer.get_stats()
                if isinstance(stats, dict):
                    return stats
            except Exception as exc:
                logger.debug("[ReviewAPI] quality scorer unavailable: %s", exc)
        build_summary = getattr(self, "_build_quality_summary", None)
        if callable(build_summary):
            try:
                summary = build_summary()
                if isinstance(summary, dict):
                    return summary
            except Exception:
                return {}
        return {}

    async def _find_open_review_item(
        self,
        store: ReviewStore,
        memory_id: str,
        reasons: Sequence[str],
    ) -> dict[str, Any] | None:
        open_items = await store.list_items(status="open", limit=200)
        reason_set = set(reasons)
        for item in open_items:
            if str(item.get("memory_id")) != str(memory_id):
                continue
            if reason_set.intersection(set(item.get("reasons") or [])):
                return item
        return None

    @staticmethod
    def _extract_memory_sequence(result: Any) -> list[Any]:
        if isinstance(result, Mapping):
            result = result.get("items") or result.get("memories") or []
        if isinstance(result, Sequence) and not isinstance(
            result, str | bytes | bytearray
        ):
            return list(result)
        return []

    def _normalize_review_memory(self, memory: Any) -> dict[str, Any]:
        if isinstance(memory, Mapping):
            item = dict(memory)
        else:
            item = {
                "id": getattr(memory, "id", getattr(memory, "memory_id", None)),
                "memory_id": getattr(memory, "memory_id", None),
                "content": getattr(memory, "content", getattr(memory, "text", "")),
                "text": getattr(memory, "text", getattr(memory, "content", "")),
                "metadata": getattr(memory, "metadata", {}),
                "importance": getattr(memory, "importance", 0),
            }
        metadata = item.get("metadata")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        content = item.get("content") or item.get("text") or item.get("summary") or ""
        return {
            **item,
            "id": item.get("id") or item.get("memory_id"),
            "memory_id": item.get("memory_id") or item.get("id"),
            "content": str(content),
            "metadata": metadata,
            "importance": item.get("importance", metadata.get("importance", 0)),
        }

    @staticmethod
    def _memory_content(memory: Any) -> str:
        if isinstance(memory, Mapping):
            return str(memory.get("content") or memory.get("text") or "")
        return str(getattr(memory, "content", getattr(memory, "text", "")) or "")

    @staticmethod
    def _memory_id(memory: Any) -> str | None:
        if isinstance(memory, Mapping):
            raw = memory.get("id") or memory.get("memory_id")
        else:
            raw = getattr(memory, "id", getattr(memory, "memory_id", None))
        return None if raw is None else str(raw)

    @staticmethod
    def _memory_metadata(memory: Any) -> dict[str, Any] | None:
        metadata = (
            memory.get("metadata")
            if isinstance(memory, Mapping)
            else getattr(memory, "metadata", None)
        )
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                return None
        return dict(metadata) if isinstance(metadata, Mapping) else None

    @staticmethod
    def _memory_importance(memory: Any, metadata: Mapping[str, Any]) -> float:
        raw = (
            memory.get("importance")
            if isinstance(memory, Mapping)
            else getattr(memory, "importance", None)
        )
        if raw is None:
            raw = metadata.get("importance", 0.5)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0.5

    @staticmethod
    def _coerce_memory_id_from_add_result(result: Any) -> str | None:
        if isinstance(result, Mapping):
            result = result.get("id") or result.get("memory_id")
        if result is None or result is False:
            return None
        return str(result)

    @staticmethod
    def _coerce_memory_id_from_update_result(result: Any) -> str | None:
        if isinstance(result, Mapping):
            raw = (
                result.get("new_memory_id")
                or result.get("replacement_memory_id")
                or result.get("memory_id")
                or result.get("id")
            )
            return None if raw is None else str(raw)
        if isinstance(result, int) and not isinstance(result, bool):
            return str(result)
        if isinstance(result, str) and result.strip():
            return result.strip()
        return None

    def _find_replacement_in_memory_items(
        self,
        memories: Sequence[Any],
        old_memory_id: str,
        new_content: str,
    ) -> str | None:
        content_match: str | None = None
        for memory in memories:
            candidate_id = self._memory_id(memory)
            if not candidate_id or candidate_id == str(old_memory_id):
                continue
            metadata = self._memory_metadata(memory) or {}
            previous_id = metadata.get("previous_id")
            if previous_id is not None and str(previous_id) == str(old_memory_id):
                return candidate_id
            if content_match is None and self._memory_content(memory) == new_content:
                content_match = candidate_id
        return content_match

    @staticmethod
    def _content_update_may_replace(memory_engine: Any) -> bool:
        return callable(getattr(memory_engine, "update_memory", None)) and callable(
            getattr(memory_engine, "delete_memory", None)
        )

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            if isinstance(value, bool):
                return default
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _optional_arg(name: str) -> str | None:
        value = str(request.args.get(name, "") or "").strip()
        return value or None

    @staticmethod
    def _review_id_from_args() -> str:
        return str(
            request.args.get("review_id") or request.args.get("id") or ""
        ).strip()


__all__ = ["ReviewApiMixin"]
