"""Review Page API tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.page_api import PAGE_API_PREFIX, PluginPageApi
from core.review import (
    ReviewAction,
    ReviewItem,
    ReviewReason,
    ReviewSeverity,
)


def _mock_request(**args):
    mock = MagicMock()
    mock.args = args
    mock.get_json = AsyncMock(return_value=None)
    return mock


class FakeMemoryEngine:
    def __init__(self) -> None:
        self.memories = {
            "1": {
                "id": 1,
                "content": "same durable fact",
                "text": "same durable fact",
                "metadata": {"status": "active", "source": "chat"},
                "importance": 0.5,
            },
            "2": {
                "id": 2,
                "content": "same durable fact",
                "text": "same durable fact",
                "metadata": {"status": "active", "source": "chat"},
                "importance": 0.5,
            },
            "3": {
                "id": 3,
                "content": "old maybe useful fact",
                "text": "old maybe useful fact",
                "metadata": {
                    "status": "active",
                    "source": "chat",
                    "last_accessed_days": 365,
                },
                "importance": 0.2,
            },
        }
        self.deleted: list[str] = []
        self.update_calls: list[tuple[str, dict]] = []

    async def list_memories(self, *, limit: int = 200):
        return list(self.memories.values())[:limit]

    async def get_memory(self, memory_id):
        return self.memories.get(str(memory_id))

    async def update_memory(self, memory_id, updates):
        memory = self.memories.get(str(memory_id))
        if memory is None:
            return False
        if "content" in updates:
            memory["content"] = updates["content"]
            memory["text"] = updates["content"]
        if "text" in updates:
            memory["content"] = updates["text"]
            memory["text"] = updates["text"]
        if "metadata" in updates:
            memory.setdefault("metadata", {}).update(updates["metadata"])
        self.update_calls.append((str(memory_id), updates))
        return True

    async def delete_memory(self, memory_id):
        self.deleted.append(str(memory_id))
        return self.memories.pop(str(memory_id), None) is not None


class ReplacementMemoryEngine(FakeMemoryEngine):
    def __init__(self) -> None:
        super().__init__()
        self.next_id = 10
        self.add_calls: list[dict] = []
        self.direct_add_forbidden = False

    async def add_memory(
        self,
        content: str,
        session_id: str | None = None,
        persona_id: str | None = None,
        importance: float = 0.5,
        metadata: dict | None = None,
    ) -> int:
        if self.direct_add_forbidden:
            raise AssertionError("Review API must use update_memory for replacements")
        new_id = self.next_id
        self.next_id += 1
        payload = {
            "id": new_id,
            "content": content,
            "text": content,
            "metadata": dict(metadata or {}),
            "importance": importance,
        }
        if session_id is not None:
            payload["metadata"]["session_id"] = session_id
        if persona_id is not None:
            payload["metadata"]["persona_id"] = persona_id
        payload["metadata"]["importance"] = importance
        self.memories[str(new_id)] = payload
        self.add_calls.append(payload)
        return new_id

    async def update_memory(self, memory_id, updates):
        memory = self.memories.get(str(memory_id))
        if memory is None:
            return False
        self.update_calls.append((str(memory_id), updates))
        if "content" not in updates:
            return await super().update_memory(memory_id, updates)

        new_content = str(updates["content"])
        metadata = dict(memory.get("metadata") or {})
        metadata["previous_id"] = str(memory_id)
        was_forbidden = self.direct_add_forbidden
        self.direct_add_forbidden = False
        try:
            await self.add_memory(
                content=new_content,
                session_id=metadata.get("session_id"),
                persona_id=metadata.get("persona_id"),
                importance=float(
                    memory.get("importance", metadata.get("importance", 0.5))
                ),
                metadata=metadata,
            )
        finally:
            self.direct_add_forbidden = was_forbidden
        await self.delete_memory(memory_id)
        return True


def _api_with_store(tmp_path, engine: FakeMemoryEngine | None = None):
    engine = engine or FakeMemoryEngine()
    plugin = SimpleNamespace(
        initializer=SimpleNamespace(
            memory_engine=engine,
            data_dir=str(tmp_path),
            conversation_manager=None,
            index_validator=None,
        ),
        _ensure_plugin_ready=AsyncMock(return_value=(True, "")),
        context=MagicMock(),
    )
    api = PluginPageApi(plugin)
    return api, engine


@pytest.mark.asyncio
async def test_review_routes_registered(tmp_path) -> None:
    api, _engine = _api_with_store(tmp_path)

    api.register_routes()

    paths = [call[0][0] for call in api.plugin.context.register_web_api.call_args_list]
    assert f"{PAGE_API_PREFIX}/review/items" in paths
    assert f"{PAGE_API_PREFIX}/review/items/detail" in paths
    assert f"{PAGE_API_PREFIX}/review/refresh" in paths
    assert f"{PAGE_API_PREFIX}/review/action" in paths


@pytest.mark.asyncio
async def test_list_and_detail_use_review_store_items(tmp_path) -> None:
    api, _engine = _api_with_store(tmp_path)
    store = await api._get_review_store()
    item = await store.upsert_item(
        ReviewItem(
            item_id="rev-one",
            memory_id="1",
            reasons=[ReviewReason.LOW_CONFIDENCE],
            severity=ReviewSeverity.MEDIUM,
            content_preview="needs review",
        )
    )
    await store.record_action(
        ReviewAction(
            item_id=item["item_id"],
            action="approved",
            payload={"note": "ok"},
        )
    )

    req = _mock_request(status="approved")
    with patch("core.platform.transport.page_api.review_api.request", req):
        listed = await api.list_review_items()
    assert listed["status"] == "ok"
    assert listed["data"]["items"][0]["item_id"] == "rev-one"

    req = _mock_request(review_id="rev-one")
    with patch("core.platform.transport.page_api.review_api.request", req):
        detail = await api.get_review_item_detail()
    assert detail["status"] == "ok"
    assert detail["data"]["item"]["item_id"] == "rev-one"
    assert detail["data"]["actions"][0]["action"] == "approved"


@pytest.mark.asyncio
async def test_refresh_detects_and_upserts_review_items(tmp_path) -> None:
    api, _engine = _api_with_store(tmp_path)

    req = _mock_request(limit="20")
    with patch("core.platform.transport.page_api.review_api.request", req):
        result = await api.refresh_review_items()

    assert result["status"] == "ok"
    assert result["data"]["scanned"] == 3
    assert result["data"]["opened"] >= 1
    assert "unchanged" in result["data"]

    store = await api._get_review_store()
    items = await store.list_items(status="open")
    reasons = {reason for item in items for reason in item["reasons"]}
    assert {"duplicate", "stale"} & reasons


@pytest.mark.asyncio
async def test_approve_closes_review_item_without_mutating_memory(tmp_path) -> None:
    api, engine = _api_with_store(tmp_path)
    store = await api._get_review_store()
    await store.upsert_item(
        ReviewItem(
            item_id="rev-approve",
            memory_id="1",
            reasons=[ReviewReason.DUPLICATE],
            severity=ReviewSeverity.MEDIUM,
        )
    )
    original = engine.memories["1"]["content"]

    req = _mock_request()
    req.get_json = AsyncMock(
        return_value={"review_id": "rev-approve", "action": "approve", "payload": {}}
    )
    with patch("core.platform.transport.page_api.review_api.request", req):
        result = await api.apply_review_action()

    assert result["status"] == "ok"
    assert result["data"]["memory_mutated"] is False
    assert result["data"]["new_status"] == "approved"
    assert engine.memories["1"]["content"] == original


@pytest.mark.asyncio
async def test_archive_mutates_engine_and_records_action(tmp_path) -> None:
    api, engine = _api_with_store(tmp_path)
    store = await api._get_review_store()
    await store.upsert_item(
        ReviewItem(
            item_id="rev-archive",
            memory_id="1",
            reasons=[ReviewReason.STALE],
            severity=ReviewSeverity.LOW,
        )
    )

    req = _mock_request()
    req.get_json = AsyncMock(
        return_value={"review_id": "rev-archive", "action": "archive", "payload": {}}
    )
    with patch("core.platform.transport.page_api.review_api.request", req):
        result = await api.apply_review_action()

    assert result["status"] == "ok"
    assert result["data"]["memory_mutated"] is True
    assert result["data"]["new_status"] == "archived"
    assert engine.memories["1"]["metadata"]["status"] == "archived"
    actions = await store.list_actions("rev-archive")
    assert actions[-1]["action"] == "archived"


@pytest.mark.asyncio
async def test_delete_requires_confirmation_and_then_deletes(tmp_path) -> None:
    api, engine = _api_with_store(tmp_path)
    store = await api._get_review_store()
    await store.upsert_item(
        ReviewItem(
            item_id="rev-delete",
            memory_id="2",
            reasons=[ReviewReason.NOISY],
            severity=ReviewSeverity.LOW,
        )
    )

    req = _mock_request()
    req.get_json = AsyncMock(
        return_value={"review_id": "rev-delete", "action": "delete", "payload": {}}
    )
    with patch("core.platform.transport.page_api.review_api.request", req):
        result = await api.apply_review_action()

    assert result == {"status": "error", "message": "confirmation_required"}
    assert engine.deleted == []

    req = _mock_request()
    req.get_json = AsyncMock(
        return_value={
            "review_id": "rev-delete",
            "action": "delete",
            "confirmed": True,
            "payload": {},
        }
    )
    with patch("core.platform.transport.page_api.review_api.request", req):
        result = await api.apply_review_action()

    assert result["status"] == "ok"
    assert result["data"]["memory_mutated"] is True
    assert result["data"]["new_status"] == "deleted"
    assert engine.deleted == ["2"]
    assert (await store.list_actions("rev-delete"))[-1]["action"] == "deleted"


@pytest.mark.asyncio
async def test_mark_safe_closes_matching_open_reasons_for_same_memory(tmp_path) -> None:
    api, _engine = _api_with_store(tmp_path)
    store = await api._get_review_store()
    await store.upsert_item(
        ReviewItem(
            item_id="rev-safe-one",
            memory_id="1",
            reasons=[ReviewReason.DUPLICATE],
            severity=ReviewSeverity.MEDIUM,
        )
    )
    await store.upsert_item(
        ReviewItem(
            item_id="rev-safe-other",
            memory_id="1",
            reasons=[ReviewReason.STALE],
            severity=ReviewSeverity.LOW,
        )
    )

    req = _mock_request()
    req.get_json = AsyncMock(
        return_value={"review_id": "rev-safe-one", "action": "mark_safe", "payload": {}}
    )
    with patch("core.platform.transport.page_api.review_api.request", req):
        result = await api.apply_review_action()

    assert result["status"] == "ok"
    assert result["data"]["memory_mutated"] is False
    duplicate_items = await store.list_items(status="safe", reason="duplicate")
    assert {item["item_id"] for item in duplicate_items} == {"rev-safe-one"}
    stale_item = await store.get_item("rev-safe-other")
    assert stale_item["status"] == "open"


@pytest.mark.asyncio
async def test_mark_safe_pages_through_open_review_items(tmp_path) -> None:
    api, _engine = _api_with_store(tmp_path)
    store = await api._get_review_store()
    await store.upsert_item(
        ReviewItem(
            item_id="rev-safe-anchor",
            memory_id="1",
            reasons=[ReviewReason.DUPLICATE],
            severity=ReviewSeverity.MEDIUM,
        )
    )
    await store.upsert_item(
        ReviewItem(
            item_id="rev-safe-page-two",
            memory_id="1",
            reasons=[ReviewReason.DUPLICATE],
            severity=ReviewSeverity.MEDIUM,
        )
    )
    pages = [
        [
            {
                "item_id": "rev-safe-anchor",
                "memory_id": "1",
                "reasons": ["duplicate"],
            }
        ],
        [
            {
                "item_id": "rev-safe-page-two",
                "memory_id": "1",
                "reasons": ["duplicate"],
            }
        ],
        [],
    ]
    cursors_seen: list[str | None] = []
    original_list_items = store.list_items

    async def paged_list_items(**kwargs):
        if kwargs.get("status") != "open":
            return await original_list_items(**kwargs)
        cursors_seen.append(kwargs.get("cursor"))
        return pages.pop(0)

    store.list_items = paged_list_items

    req = _mock_request()
    req.get_json = AsyncMock(
        return_value={
            "review_id": "rev-safe-anchor",
            "action": "mark_safe",
            "payload": {},
        }
    )
    with patch("core.platform.transport.page_api.review_api.request", req):
        result = await api.apply_review_action()

    assert result["status"] == "ok"
    assert cursors_seen == [None, "rev-safe-anchor", "rev-safe-page-two"]
    assert await original_list_items(status="safe", reason="duplicate", limit=10)


@pytest.mark.asyncio
async def test_edit_replaces_memory_when_engine_exposes_add_and_delete(
    tmp_path,
) -> None:
    api, engine = _api_with_store(tmp_path, ReplacementMemoryEngine())
    engine.direct_add_forbidden = True
    store = await api._get_review_store()
    await store.upsert_item(
        ReviewItem(
            item_id="rev-edit-replace",
            memory_id="1",
            reasons=[ReviewReason.LOW_CONFIDENCE],
            severity=ReviewSeverity.MEDIUM,
        )
    )

    req = _mock_request()
    req.get_json = AsyncMock(
        return_value={
            "review_id": "rev-edit-replace",
            "action": "edit",
            "payload": {"content": "replacement content"},
        }
    )
    with patch("core.platform.transport.page_api.review_api.request", req):
        result = await api.apply_review_action()

    assert result["status"] == "ok"
    assert result["data"]["memory_id"] == "1"
    assert result["data"]["new_memory_id"] == "10"
    assert "1" not in engine.memories
    assert engine.memories["10"]["content"] == "replacement content"
    assert engine.update_calls == [("1", {"content": "replacement content"})]
    actions = await store.list_actions("rev-edit-replace")
    assert actions[-1]["payload"]["old_memory_id"] == "1"
    assert actions[-1]["payload"]["new_memory_id"] == "10"


@pytest.mark.asyncio
async def test_merge_replaces_target_and_archives_source_with_replacement_engine(
    tmp_path,
) -> None:
    api, engine = _api_with_store(tmp_path, ReplacementMemoryEngine())
    engine.direct_add_forbidden = True
    store = await api._get_review_store()
    await store.upsert_item(
        ReviewItem(
            item_id="rev-merge-replace",
            memory_id="2",
            reasons=[ReviewReason.DUPLICATE],
            severity=ReviewSeverity.MEDIUM,
        )
    )

    req = _mock_request()
    req.get_json = AsyncMock(
        return_value={
            "review_id": "rev-merge-replace",
            "action": "merge",
            "payload": {"target_memory_id": "3"},
        }
    )
    with patch("core.platform.transport.page_api.review_api.request", req):
        result = await api.apply_review_action()

    assert result["status"] == "ok"
    assert result["data"]["memory_id"] == "2"
    assert result["data"]["replacement_target_memory_id"] == "10"
    assert "3" not in engine.memories
    assert (
        engine.memories["10"]["content"] == "old maybe useful fact\nsame durable fact"
    )
    assert engine.memories["2"]["metadata"]["status"] == "archived"
    assert (
        "3",
        {"content": "old maybe useful fact\nsame durable fact"},
    ) in engine.update_calls
    assert ("2", {"metadata": {"status": "archived"}}) in engine.update_calls
    actions = await store.list_actions("rev-merge-replace")
    assert actions[-1]["payload"]["source_memory_id"] == "2"
    assert actions[-1]["payload"]["target_memory_id"] == "3"
    assert actions[-1]["payload"]["replacement_target_memory_id"] == "10"


@pytest.mark.asyncio
async def test_apply_review_action_validation_errors_are_consistent(tmp_path) -> None:
    api, _engine = _api_with_store(tmp_path)

    req = _mock_request()
    req.get_json = AsyncMock(return_value={"action": "approve", "payload": {}})
    with patch("core.platform.transport.page_api.review_api.request", req):
        missing_id = await api.apply_review_action()
    assert missing_id == {"status": "error", "message": "review_id required"}

    req = _mock_request()
    req.get_json = AsyncMock(
        return_value={"review_id": "rev-missing", "action": "bad", "payload": {}}
    )
    with patch("core.platform.transport.page_api.review_api.request", req):
        unsupported = await api.apply_review_action()
    assert unsupported["status"] == "error"
    assert "unsupported review action" in unsupported["message"]

    store = await api._get_review_store()
    await store.upsert_item(
        ReviewItem(
            item_id="rev-bad-edit",
            memory_id="1",
            reasons=[ReviewReason.LOW_CONFIDENCE],
            severity=ReviewSeverity.MEDIUM,
        )
    )
    req = _mock_request()
    req.get_json = AsyncMock(
        return_value={"review_id": "rev-bad-edit", "action": "edit", "payload": {}}
    )
    with patch("core.platform.transport.page_api.review_api.request", req):
        bad_edit = await api.apply_review_action()
    assert bad_edit == {"status": "error", "message": "content required"}


@pytest.mark.asyncio
async def test_edit_and_merge_smoke(tmp_path) -> None:
    api, engine = _api_with_store(tmp_path)
    store = await api._get_review_store()
    await store.upsert_item(
        ReviewItem(
            item_id="rev-edit",
            memory_id="1",
            reasons=[ReviewReason.LOW_CONFIDENCE],
            severity=ReviewSeverity.MEDIUM,
        )
    )
    await store.upsert_item(
        ReviewItem(
            item_id="rev-merge",
            memory_id="2",
            reasons=[ReviewReason.DUPLICATE],
            severity=ReviewSeverity.MEDIUM,
        )
    )

    req = _mock_request()
    req.get_json = AsyncMock(
        return_value={
            "review_id": "rev-edit",
            "action": "edit",
            "payload": {"content": "updated content"},
        }
    )
    with patch("core.platform.transport.page_api.review_api.request", req):
        edit_result = await api.apply_review_action()
    assert edit_result["status"] == "ok"
    assert edit_result["data"]["new_status"] == "edited"
    assert engine.memories["1"]["content"] == "updated content"

    req = _mock_request()
    req.get_json = AsyncMock(
        return_value={
            "review_id": "rev-merge",
            "action": "merge",
            "payload": {"target_memory_id": "1"},
        }
    )
    with patch("core.platform.transport.page_api.review_api.request", req):
        merge_result = await api.apply_review_action()
    assert merge_result["status"] == "ok"
    assert merge_result["data"]["new_status"] == "merged"
    assert engine.memories["2"]["metadata"]["status"] == "archived"
