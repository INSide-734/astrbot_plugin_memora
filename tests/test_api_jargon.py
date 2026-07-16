"""core/api/jargon_api.py — JargonApiMixin 测试。

Validates endpoint responses, parameter validation, and error handling.
Uses unittest.mock to mock jargon filter, store, miner, and quart.request.
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.api.jargon_api import JargonApiMixin
from core.api.response_utils import error_response
from core.base.entity_editing import (
    EditConflictError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
)
from core.jargon.jargon_admin_service import JargonAdminService
from core.jargon.jargon_query import JargonQueryService
from core.jargon.jargon_store import JargonStore
from core.page_api import PluginPageApi


def _make_mock_request(**args):
    """Create a mock quart.request with args dict and async get_json."""
    mock = MagicMock()
    mock.args = args
    mock.get_json = AsyncMock(return_value=None)
    return mock


def _make_jargon_candidate(term="破防", group_id="g1", score=0.72):
    """Create a mock JargonCandidate."""
    from core.jargon.models import JargonCandidate

    return JargonCandidate(
        term=term,
        group_id=group_id,
        score=score,
        frequency=12,
        unique_users=3,
        idf_score=1.5,
        burst_score=2.1,
        concentration_score=0.33,
        first_seen=1700000000.0,
        context_examples=["我今天真的破防了", "破防了破防了"],
    )


def _make_jargon_meaning(term="破防", group_id="g1", confirmed=True):
    """Create a mock JargonMeaning."""
    from core.jargon.models import JargonMeaning

    return JargonMeaning(
        term=term,
        group_id=group_id,
        meaning="心理防线被突破，情绪失控",
        confidence=0.85,
        is_jargon=True,
        is_confirmed=confirmed,
        is_global=False,
        is_complete=True,
        count=120,
        last_inference_count=100,
        context_examples=["我今天真的破防了"],
        created_at=1700000000.0,
        updated_at=1700000001.0,
    )


def _make_stub(*, has_filter=True, has_store=True, has_miner=False,
               candidates=None, meanings=None, store_count=5, store_confirmed=3):
    """Create a JargonApiMixin stub with mocked dependencies."""

    class Stub:
        get_jargon_candidates = JargonApiMixin.get_jargon_candidates
        get_jargon_meanings = JargonApiMixin.get_jargon_meanings
        get_jargon_stats = JargonApiMixin.get_jargon_stats
        confirm_jargon = JargonApiMixin.confirm_jargon
        mine_jargon = JargonApiMixin.mine_jargon
        _get_jargon_filter = JargonApiMixin._get_jargon_filter
        _get_jargon_resolution_lock = JargonApiMixin._get_jargon_resolution_lock
        _is_closed_jargon_store = staticmethod(
            JargonApiMixin._is_closed_jargon_store
        )
        _find_open_jargon_store = JargonApiMixin._find_open_jargon_store
        _close_unpublished_jargon_store = staticmethod(
            JargonApiMixin._close_unpublished_jargon_store
        )
        _get_jargon_store_locked = JargonApiMixin._get_jargon_store_locked
        _get_jargon_store = JargonApiMixin._get_jargon_store
        _get_current_jargon_query_service = (
            JargonApiMixin._get_current_jargon_query_service
        )
        _invalidate_current_jargon_query = (
            JargonApiMixin._invalidate_current_jargon_query
        )
        _get_jargon_admin_service = JargonApiMixin._get_jargon_admin_service
        _get_jargon_miner = JargonApiMixin._get_jargon_miner
        _get_feature_delegation = JargonApiMixin._get_feature_delegation
        _require_group_id = staticmethod(JargonApiMixin._require_group_id)

    stub = Stub()
    stub.plugin = None  # default: no plugin (test will set if needed)

    if has_filter or has_store or has_miner:
        stub.plugin = SimpleNamespace(
            initializer=None,
            data_dir=None,
            feature_delegation=None,
        )

    if has_filter:
        jf = MagicMock()
        jf.get_candidates = MagicMock(return_value=candidates or [])
        jf.get_stats = MagicMock(return_value=MagicMock(
            group_id="g1", total_terms=50, candidate_count=8,
            top_candidates=candidates or [],
        ))
        stub.plugin._jargon_filter = jf

    if has_store:
        store = MagicMock()
        store.list_by_group = AsyncMock(return_value=meanings or [])
        store.get_by_term = AsyncMock(
            return_value=(meanings or [_make_jargon_meaning()])[0]
        )
        store.update_if_revision = AsyncMock(
            return_value=(meanings or [_make_jargon_meaning()])[0]
        )
        store.confirm = AsyncMock(return_value=None)
        store.count_by_group = AsyncMock(return_value=store_count)
        store.count_confirmed = AsyncMock(return_value=store_confirmed)
        stub.plugin._jargon_store = store

    if has_miner:
        miner = MagicMock()
        miner.run_once = AsyncMock(return_value=[])
        stub.plugin._jargon_miner = miner

    return stub


# ---------------------------------------------------------------------------
# Jargon candidates
# ---------------------------------------------------------------------------


class TestJargonCandidates:
    @pytest.mark.asyncio
    async def test_requires_group_id(self) -> None:
        stub = _make_stub()
        mock_req = _make_mock_request()
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_candidates()
        assert result["status"] == "error"
        assert "group_id" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_returns_candidates(self) -> None:
        cands = [_make_jargon_candidate("破防", "g1"), _make_jargon_candidate("躺平", "g1")]
        stub = _make_stub(candidates=cands)
        mock_req = _make_mock_request(group_id="g1", limit="10")
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_candidates()
        assert result["status"] == "ok"
        assert len(result["data"]["candidates"]) == 2
        assert result["data"]["candidates"][0]["term"] == "破防"
        assert "revision" not in result["data"]["candidates"][0]

    @pytest.mark.asyncio
    async def test_no_filter_returns_error(self) -> None:
        stub = _make_stub(has_filter=False, has_store=False)
        mock_req = _make_mock_request(group_id="g1")
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_candidates()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_negative_limit_falls_back_to_default_window(self) -> None:
        cands = [_make_jargon_candidate("破防", "g1"), _make_jargon_candidate("躺平", "g1")]
        stub = _make_stub(candidates=cands)
        mock_req = _make_mock_request(group_id="g1", limit="-1")
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_candidates()
        assert result["status"] == "ok"
        stub.plugin._jargon_filter.get_candidates.assert_called_once_with("g1", limit=20)
        assert len(result["data"]["candidates"]) == 2

    @pytest.mark.asyncio
    async def test_skips_malformed_candidate_items(self) -> None:
        broken = MagicMock()
        type(broken).term = property(lambda self: (_ for _ in ()).throw(RuntimeError("broken candidate")))
        cands = [_make_jargon_candidate("破防", "g1"), broken]
        stub = _make_stub(candidates=cands)
        mock_req = _make_mock_request(group_id="g1", limit="10")
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_candidates()
        assert result["status"] == "ok"
        assert result["data"]["total"] == 1
        assert [item["term"] for item in result["data"]["candidates"]] == ["破防"]
        assert "revision" not in result["data"]["candidates"][0]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure_source", ["filter_resolution", "read"])
    async def test_outer_failure_is_fully_redacted(
        self,
        failure_source: str,
    ) -> None:
        path_secret = "C:/secret/candidate-store-4f87.db"
        group_secret = "candidate-group-secret-5a98"
        exception_secret = "candidate-exception-secret-6ba9"
        stub = _make_stub()
        failure = RuntimeError(path_secret + group_secret + exception_secret)
        if failure_source == "filter_resolution":
            stub._get_jargon_filter = MagicMock(side_effect=failure)
        else:
            stub.plugin._jargon_filter.get_candidates = MagicMock(
                side_effect=failure
            )
        mock_req = _make_mock_request(group_id=group_secret, limit="10")

        with (
            patch("core.api.jargon_api.request", mock_req),
            patch("core.api.jargon_api.logger") as logged,
        ):
            result = await stub.get_jargon_candidates()

        rendered = json.dumps(result, ensure_ascii=False) + str(logged.method_calls)
        assert result["code"] == "internal_error"
        assert "exc_info" not in rendered
        logged.error.assert_called_once_with(
            "[黑话接口] operation=%s error_class=%s",
            "list_candidates",
            "RuntimeError",
        )
        for secret in (path_secret, group_secret, exception_secret):
            assert secret not in rendered

    @pytest.mark.asyncio
    async def test_tolerates_malformed_candidate_container(self) -> None:
        class BrokenCandidates:
            def __iter__(self):
                raise RuntimeError("broken candidates")

            def __len__(self):
                raise RuntimeError("broken candidate length")

            def __bool__(self):
                return True

        stub = _make_stub(candidates=[])
        stub.plugin._jargon_filter.get_candidates = MagicMock(
            return_value=BrokenCandidates()
        )
        mock_req = _make_mock_request(group_id="g1", limit="10")
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_candidates()
        assert result["status"] == "ok"
        assert result["data"]["candidates"] == []
        assert result["data"]["total"] == 0


# ---------------------------------------------------------------------------
# Jargon meanings
# ---------------------------------------------------------------------------


class TestJargonMeanings:
    @pytest.mark.asyncio
    async def test_requires_group_id(self) -> None:
        stub = _make_stub()
        mock_req = _make_mock_request()
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_meanings()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_returns_meanings(self) -> None:
        meanings = [_make_jargon_meaning("破防", "g1"), _make_jargon_meaning("躺平", "g1")]
        stub = _make_stub(meanings=meanings)
        service = MagicMock()
        service.revision_for = MagicMock(side_effect=lambda item: f"rev-{item.term}")
        stub._get_jargon_admin_service = AsyncMock(return_value=service)
        mock_req = _make_mock_request(group_id="g1")
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_meanings()
        assert result["status"] == "ok"
        assert len(result["data"]["meanings"]) == 2
        assert result["data"]["meanings"][0]["context_examples"] == [
            "我今天真的破防了"
        ]
        assert [item["revision"] for item in result["data"]["meanings"]] == [
            "rev-破防",
            "rev-躺平",
        ]
        assert service.revision_for.call_args_list[0].args == (meanings[0],)

    @pytest.mark.asyncio
    async def test_no_store_returns_error(self) -> None:
        stub = _make_stub(has_store=False)
        mock_req = _make_mock_request(group_id="g1")
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_meanings()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_skips_malformed_meaning_items(self) -> None:
        broken = MagicMock()
        type(broken).term = property(lambda self: (_ for _ in ()).throw(RuntimeError("broken meaning")))
        good = _make_jargon_meaning("破防", "g1")
        revision_failure = _make_jargon_meaning("躺平", "g1")
        meanings = [good, broken, revision_failure]
        stub = _make_stub(meanings=meanings)
        service = SimpleNamespace(
            revision_for=lambda item: (
                "rev-good"
                if item is good
                else (_ for _ in ()).throw(RuntimeError("revision failed"))
            )
        )
        stub._get_jargon_admin_service = AsyncMock(return_value=service)
        mock_req = _make_mock_request(group_id="g1")
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_meanings()
        assert result["status"] == "ok"
        assert result["data"]["total"] == 1
        assert result["data"]["meanings"] == [
            {
                "term": "破防",
                "group_id": "g1",
                "meaning": "心理防线被突破，情绪失控",
                "confidence": 0.85,
                "is_jargon": True,
                "is_confirmed": True,
                "is_global": False,
                "is_complete": True,
                "count": 120,
                "last_inference_count": 100,
                "context_examples": ["我今天真的破防了"],
                "created_at": 1700000000.0,
                "updated_at": 1700000001.0,
                "revision": "rev-good",
            }
        ]

    @pytest.mark.asyncio
    async def test_meanings_store_failure_is_fully_redacted(self) -> None:
        secret = "C:/secret/jargon-meaning-path-4bd19.db"
        store = SimpleNamespace(
            list_by_group=AsyncMock(
                side_effect=RuntimeError("store failed at " + secret)
            )
        )
        api = JargonApiMixin()
        api.plugin = SimpleNamespace()
        api._get_jargon_admin_service = AsyncMock(
            return_value=SimpleNamespace(revision_for=lambda item: "rev")
        )
        api._get_jargon_store = AsyncMock(return_value=store)
        mock_req = _make_mock_request(group_id="group-secret-995c")

        with (
            patch("core.api.jargon_api.request", mock_req),
            patch("core.api.jargon_api.logger") as logged,
        ):
            result = await api.get_jargon_meanings()

        rendered = json.dumps(result, ensure_ascii=False) + str(logged.method_calls)
        assert result["code"] == "internal_error"
        assert secret not in rendered
        assert "group-secret-995c" not in rendered
        assert "exc_info" not in rendered
        logged.error.assert_called_once_with(
            "[黑话接口] operation=%s error_class=%s",
            "list_meanings",
            "RuntimeError",
        )


# ---------------------------------------------------------------------------
# Jargon admin service resolution
# ---------------------------------------------------------------------------


class TestJargonAdminServiceResolution:
    def test_resolution_lock_is_created_synchronously_once(self) -> None:
        api = JargonApiMixin()
        api.plugin = SimpleNamespace()

        first = api._get_jargon_resolution_lock()
        second = api._get_jargon_resolution_lock()

        assert isinstance(first, asyncio.Lock)
        assert second is first
        assert api.plugin._jargon_resolution_lock is first

    @pytest.mark.asyncio
    async def test_concurrent_first_resolution_creates_one_store_and_service(
        self,
        tmp_path,
    ) -> None:
        initialize_started = asyncio.Event()
        release_initialize = asyncio.Event()
        second_resolution_started = asyncio.Event()
        created_stores = []
        initialize_calls = 0

        class GatedStore:
            def __init__(self, db_path: str) -> None:
                self.db_path = db_path
                self.connection = None
                self._initialized = False
                self._write_lock = asyncio.Lock()
                created_stores.append(self)

            async def initialize(self) -> None:
                nonlocal initialize_calls
                initialize_calls += 1
                initialize_started.set()
                await release_initialize.wait()
                self.connection = object()
                self._initialized = True

            async def close(self) -> None:
                self.connection = None

        plugin = SimpleNamespace(
            data_dir=tmp_path,
            initializer=SimpleNamespace(jargon_query_service=None),
        )
        api = JargonApiMixin()
        api.plugin = plugin

        async def resolve_second():
            second_resolution_started.set()
            return await api._get_jargon_admin_service()

        with patch("core.jargon.jargon_store.JargonStore", GatedStore):
            first_task = asyncio.create_task(api._get_jargon_admin_service())
            await initialize_started.wait()
            second_task = asyncio.create_task(resolve_second())
            await second_resolution_started.wait()
            observed_initialize_calls = initialize_calls
            release_initialize.set()
            first, second = await asyncio.gather(first_task, second_task)

        try:
            assert observed_initialize_calls == 1
            assert initialize_calls == 1
            assert len(created_stores) == 1
            assert first is second
            assert first._store is second._store is created_stores[0]
        finally:
            for store in created_stores:
                await store.close()

    @pytest.mark.asyncio
    async def test_failed_unpublished_store_is_closed_before_original_error(
        self,
        tmp_path,
    ) -> None:
        failure = RuntimeError("initialize-secret-ordinary-7cb0")
        created_stores = []

        class FailingStore:
            def __init__(self, db_path: str) -> None:
                self.db_path = db_path
                self.resource = None
                self.close_calls = 0
                created_stores.append(self)

            async def initialize(self) -> None:
                self.resource = object()
                raise failure

            async def close(self) -> None:
                self.close_calls += 1
                self.resource = None

        plugin = SimpleNamespace(
            data_dir=tmp_path,
            initializer=SimpleNamespace(jargon_query_service=None),
        )
        api = JargonApiMixin()
        api.plugin = plugin

        with patch("core.jargon.jargon_store.JargonStore", FailingStore):
            with pytest.raises(RuntimeError) as caught:
                await api._get_jargon_store()

        store = created_stores[0]
        assert caught.value is failure
        assert store.close_calls == 1
        assert store.resource is None
        assert not hasattr(plugin, "_jargon_store")

    @pytest.mark.asyncio
    async def test_cancelled_unpublished_store_finishes_cleanup_before_propagating(
        self,
        tmp_path,
    ) -> None:
        initialize_started = asyncio.Event()
        close_started = asyncio.Event()
        release_close = asyncio.Event()
        created_stores = []

        class CancelledStore:
            def __init__(self, db_path: str) -> None:
                self.db_path = db_path
                self.resource = None
                self.close_calls = 0
                created_stores.append(self)

            async def initialize(self) -> None:
                self.resource = object()
                initialize_started.set()
                await asyncio.Event().wait()

            async def close(self) -> None:
                self.close_calls += 1
                close_started.set()
                await release_close.wait()
                self.resource = None

        plugin = SimpleNamespace(
            data_dir=tmp_path,
            initializer=SimpleNamespace(jargon_query_service=None),
        )
        api = JargonApiMixin()
        api.plugin = plugin
        close_waiter = asyncio.create_task(close_started.wait())
        with patch("core.jargon.jargon_store.JargonStore", CancelledStore):
            resolver = asyncio.create_task(api._get_jargon_store())
            await initialize_started.wait()
            resolver.cancel()
            done, _ = await asyncio.wait(
                {resolver, close_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )
            try:
                assert close_waiter in done
                resolver.cancel()
                release_close.set()
                with pytest.raises(asyncio.CancelledError):
                    await resolver
            finally:
                release_close.set()
                if not close_waiter.done():
                    close_waiter.cancel()
                await asyncio.gather(resolver, close_waiter, return_exceptions=True)

        store = created_stores[0]
        assert store.close_calls == 1
        assert store.resource is None
        assert not hasattr(plugin, "_jargon_store")

    @pytest.mark.asyncio
    async def test_cleanup_failure_never_replaces_initialization_failure(
        self,
        tmp_path,
    ) -> None:
        initialize_failure = RuntimeError("original-initialize-secret-8dc1")
        cleanup_failure = RuntimeError("cleanup-secret-9ed2")
        created_stores = []

        class CleanupFailingStore:
            def __init__(self, db_path: str) -> None:
                self.db_path = db_path
                self.close_calls = 0
                created_stores.append(self)

            async def initialize(self) -> None:
                raise initialize_failure

            async def close(self) -> None:
                self.close_calls += 1
                raise cleanup_failure

        api = JargonApiMixin()
        api.plugin = SimpleNamespace(
            data_dir=tmp_path,
            initializer=SimpleNamespace(jargon_query_service=None),
        )
        with patch("core.jargon.jargon_store.JargonStore", CleanupFailingStore):
            with pytest.raises(RuntimeError) as caught:
                await api._get_jargon_store()

        assert caught.value is initialize_failure
        assert created_stores[0].close_calls == 1

    @pytest.mark.asyncio
    async def test_retry_after_failure_publishes_one_fresh_store_and_service(
        self,
        tmp_path,
    ) -> None:
        failure = RuntimeError("retry-initialize-secret-af03")
        created_stores = []
        initialize_calls = 0

        class RetryStore:
            def __init__(self, db_path: str) -> None:
                self.db_path = db_path
                self.connection = None
                self._initialized = False
                self._write_lock = asyncio.Lock()
                self.close_calls = 0
                created_stores.append(self)

            async def initialize(self) -> None:
                nonlocal initialize_calls
                initialize_calls += 1
                self.connection = object()
                if initialize_calls == 1:
                    raise failure
                self._initialized = True

            async def close(self) -> None:
                self.close_calls += 1
                self.connection = None

        plugin = SimpleNamespace(
            data_dir=tmp_path,
            initializer=SimpleNamespace(jargon_query_service=None),
        )
        api = JargonApiMixin()
        api.plugin = plugin
        resolution_lock = api._get_jargon_resolution_lock()
        with patch("core.jargon.jargon_store.JargonStore", RetryStore):
            first = await api._get_jargon_admin_service()
            second = await api._get_jargon_admin_service()
            third = await api._get_jargon_admin_service()

        try:
            assert first is None
            assert second is third is plugin._jargon_admin_service
            assert second._store is plugin._jargon_store is created_stores[1]
            assert initialize_calls == 2
            assert len(created_stores) == 2
            assert plugin._jargon_resolution_lock is resolution_lock
            assert created_stores[0].close_calls == 1
            assert created_stores[0].connection is None
            assert created_stores[1].close_calls == 0
        finally:
            await created_stores[1].close()

    @pytest.mark.asyncio
    async def test_existing_store_is_returned_without_ownership_cleanup(self) -> None:
        existing = SimpleNamespace(close=AsyncMock())
        api = JargonApiMixin()
        api.plugin = SimpleNamespace(
            initializer=SimpleNamespace(jargon_store=existing)
        )

        resolved = await api._get_jargon_store()

        assert resolved is existing
        existing.close.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reuses_one_service_store_and_real_query_invalidator(
        self,
        tmp_db_path: str,
    ) -> None:
        store = JargonStore(tmp_db_path)
        await store.initialize()
        try:
            query = JargonQueryService(store)
            plugin = SimpleNamespace(
                initializer=SimpleNamespace(
                    jargon_store=store,
                    jargon_query_service=query,
                )
            )
            api = JargonApiMixin()
            api.plugin = plugin

            first = await api._get_jargon_admin_service()
            second = await api._get_jargon_admin_service()

            assert first is second
            assert first._store is store
            assert first._invalidate_group.__self__ is api
            assert plugin._jargon_admin_service is first
            query._cache.set("group:g1", ["stale"])
            first._invalidate_group("g1")
            assert query._cache.get("group:g1") is None
        finally:
            await store.close()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("query_lifecycle", ["late", "replacement"])
    async def test_cached_service_invalidates_current_query_service(
        self,
        tmp_db_path: str,
        query_lifecycle: str,
    ) -> None:
        store = JargonStore(tmp_db_path)
        await store.initialize()
        initial_query = (
            JargonQueryService(store) if query_lifecycle == "replacement" else None
        )
        initializer = SimpleNamespace(
            jargon_store=store,
            jargon_query_service=initial_query,
        )
        api = JargonApiMixin()
        api.plugin = SimpleNamespace(initializer=initializer)
        try:
            service = await api._get_jargon_admin_service()
            created = await service.create(
                term="灰度",
                group_id="g1",
                meaning="old meaning",
                confidence=0.9,
            )

            current_query = JargonQueryService(store)
            cached = await current_query.get_group_jargon("g1")
            assert cached[0]["meaning"] == "old meaning"
            initializer.jargon_query_service = current_query

            same_service = await api._get_jargon_admin_service()
            assert same_service is service
            await same_service.update(
                term="灰度",
                group_id="g1",
                changes={"meaning": "new meaning"},
                expected_revision=service.revision_for(created),
            )

            refreshed = await current_query.get_group_jargon("g1")
            assert refreshed[0]["meaning"] == "new meaning"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_service_uses_the_same_lazily_created_store(
        self,
        tmp_path,
    ) -> None:
        plugin = SimpleNamespace(
            data_dir=tmp_path,
            initializer=SimpleNamespace(jargon_query_service=None),
        )
        api = JargonApiMixin()
        api.plugin = plugin

        service = await api._get_jargon_admin_service()
        store = await api._get_jargon_store()
        try:
            assert service is not None
            assert service._store is store
            assert plugin._jargon_store is store
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_closed_cached_store_and_service_are_replaced(
        self,
        tmp_path,
    ) -> None:
        plugin = SimpleNamespace(
            data_dir=tmp_path,
            initializer=SimpleNamespace(jargon_query_service=None),
        )
        api = JargonApiMixin()
        api.plugin = plugin
        first_service = await api._get_jargon_admin_service()
        first_store = first_service._store
        original_db_path = first_store.db_path
        replacement_store = None
        try:
            await first_store.close()
            assert first_store.connection is None
            assert first_store._initialized is True
            plugin.data_dir = None

            second_service = await api._get_jargon_admin_service()
            replacement_store = second_service._store

            assert second_service is not first_service
            assert replacement_store is not first_store
            assert replacement_store.db_path == original_db_path
            assert replacement_store.connection is not None
            assert replacement_store._initialized is True
            assert plugin._jargon_store is replacement_store
            assert plugin._jargon_admin_service is second_service
        finally:
            if replacement_store is not None and replacement_store.connection is not None:
                await replacement_store.close()

    @pytest.mark.asyncio
    async def test_store_initialization_failure_is_redacted(
        self,
    ) -> None:
        secret = "resolver-secret-1d145"
        api = JargonApiMixin()
        api.plugin = SimpleNamespace(initializer=None)
        api._get_jargon_store_locked = AsyncMock(
            side_effect=RuntimeError("initialization failed " + secret)
        )

        with patch("core.api.jargon_api.logger.error") as logged:
            service = await api._get_jargon_admin_service()

        assert service is None
        rendered_log = str(logged.call_args_list)
        assert "resolve_service" in rendered_log
        assert "RuntimeError" in rendered_log
        assert secret not in rendered_log


# ---------------------------------------------------------------------------
# Jargon CRUD and safe batch handlers
# ---------------------------------------------------------------------------


def _make_admin_api(service) -> JargonApiMixin:
    api = JargonApiMixin()
    api.plugin = SimpleNamespace()
    api._maintenance_write_guard = MagicMock(return_value=None)
    api._get_jargon_admin_service = AsyncMock(return_value=service)
    return api


def _request_json(payload) -> MagicMock:
    mock = _make_mock_request()
    mock.get_json = AsyncMock(return_value=payload)
    return mock


class TestJargonCrud:
    @pytest.mark.asyncio
    async def test_real_page_api_create_update_delete_flow(
        self,
        tmp_db_path: str,
    ) -> None:
        store = JargonStore(tmp_db_path)
        await store.initialize()
        initializer = SimpleNamespace(
            jargon_store=store,
            jargon_query_service=JargonQueryService(store),
        )
        api = PluginPageApi(SimpleNamespace(initializer=initializer))
        api._maintenance_write_guard = MagicMock(return_value=None)
        try:
            create_payload = {
                "term": "灰度",
                "group_id": "g1",
                "meaning": "Gradual rollout",
                "confidence": 0.9,
            }
            with patch("core.api.jargon_api.request", _request_json(create_payload)):
                created_response = await api.create_jargon()
            assert created_response["status"] == "ok"
            created = await store.get_by_term("灰度", "g1")
            service = await api._get_jargon_admin_service()
            assert created is not None
            assert service._store is store
            assert service._store._write_lock is store._write_lock
            assert created_response["data"]["revision"] == service.revision_for(created)

            update_payload = {
                "identity": {"term": "灰度", "group_id": "g1"},
                "changes": {"meaning": "Updated rollout"},
                "expected_revision": created_response["data"]["revision"],
            }
            with patch("core.api.jargon_api.request", _request_json(update_payload)):
                updated_response = await api.update_jargon()
            updated = await store.get_by_term("灰度", "g1")
            assert updated_response["status"] == "ok"
            assert updated is not None and updated.meaning == "Updated rollout"
            assert updated_response["data"]["revision"] == service.revision_for(updated)

            delete_payload = {
                "identity": {"term": "灰度", "group_id": "g1"},
                "expected_revision": updated_response["data"]["revision"],
            }
            with patch("core.api.jargon_api.request", _request_json(delete_payload)):
                deleted_response = await api.delete_jargon()
            assert deleted_response["status"] == "ok"
            assert await store.get_by_term("灰度", "g1") is None
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_create_returns_complete_entity_revision_and_manual_defaults(self) -> None:
        created = _make_jargon_meaning("灰度", "g1")
        created.meaning = "Gradual rollout"
        created.confidence = 0.9
        created.count = 0
        created.last_inference_count = 0
        created.context_examples = []
        service = MagicMock()
        service.create = AsyncMock(return_value=created)
        service.revision_for = MagicMock(return_value="rev-jargon")
        api = _make_admin_api(service)
        payload = {
            "term": "灰度",
            "group_id": "g1",
            "meaning": "Gradual rollout",
            "confidence": 0.9,
        }

        with (
            patch("core.api.jargon_api.request", _request_json(payload)),
            patch("core.api.jargon_api.logger.info") as audit,
        ):
            result = await api.create_jargon()

        assert result["status"] == "ok"
        assert result["data"]["entity"] == {
            "term": "灰度",
            "group_id": "g1",
            "meaning": "Gradual rollout",
            "confidence": 0.9,
            "is_jargon": True,
            "is_confirmed": True,
            "is_global": False,
            "is_complete": True,
            "count": 0,
            "last_inference_count": 0,
            "context_examples": [],
            "created_at": 1700000000.0,
            "updated_at": 1700000001.0,
        }
        assert result["data"]["revision"] == "rev-jargon"
        service.create.assert_awaited_once_with(**payload)
        service.revision_for.assert_called_once_with(created)
        rendered_audit = repr(audit.call_args_list)
        assert "action=%s entity=jargon identity=%s result=%s error_code=%s" in rendered_audit
        assert "'success', 'none'" in rendered_audit
        assert "Gradual rollout" not in rendered_audit

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "extra_field",
        ["count", "context_examples", "is_complete", "created_at", "unknown"],
    )
    async def test_create_rejects_unknown_and_read_only_fields(
        self,
        extra_field: str,
    ) -> None:
        service = MagicMock()
        service.create = AsyncMock()
        api = _make_admin_api(service)
        payload = {
            "term": "灰度",
            "group_id": "g1",
            "meaning": "Gradual rollout",
            "confidence": 0.9,
            extra_field: "forbidden",
        }

        with patch("core.api.jargon_api.request", _request_json(payload)):
            result = await api.create_jargon()

        assert result["code"] == "validation_error"
        assert result["field_errors"] == {extra_field: "字段不可写"}
        api._get_jargon_admin_service.assert_not_awaited()
        service.create.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "field",
        [
            "term",
            "group_id",
            "count",
            "last_inference_count",
            "context_examples",
            "is_complete",
            "created_at",
            "updated_at",
            "unknown",
        ],
    )
    async def test_update_rejects_immutable_read_only_and_unknown_changes(
        self,
        field: str,
    ) -> None:
        service = MagicMock()
        service.update = AsyncMock()
        api = _make_admin_api(service)
        payload = {
            "identity": {"term": "灰度", "group_id": "g1"},
            "changes": {field: "forbidden"},
            "expected_revision": "rev-1",
        }

        with patch("core.api.jargon_api.request", _request_json(payload)):
            result = await api.update_jargon()

        assert result["code"] == "validation_error"
        assert result["field_errors"] == {field: "字段不可写"}
        api._get_jargon_admin_service.assert_not_awaited()
        service.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_returns_service_revision_for_full_persisted_meaning(self) -> None:
        updated = _make_jargon_meaning("灰度", "g1")
        service = MagicMock()
        service.update = AsyncMock(return_value=updated)
        service.revision_for = MagicMock(return_value="rev-updated")
        api = _make_admin_api(service)
        payload = {
            "identity": {"term": "灰度", "group_id": "g1"},
            "changes": {"meaning": "updated"},
            "expected_revision": "rev-old",
        }

        with patch("core.api.jargon_api.request", _request_json(payload)):
            result = await api.update_jargon()

        assert result["data"]["revision"] == "rev-updated"
        service.update.assert_awaited_once_with(
            term="灰度",
            group_id="g1",
            changes={"meaning": "updated"},
            expected_revision="rev-old",
        )
        service.revision_for.assert_called_once_with(updated)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("handler_name", ["update_jargon", "delete_jargon"])
    async def test_update_and_delete_require_non_empty_revision(
        self,
        handler_name: str,
    ) -> None:
        service = MagicMock()
        service.update = AsyncMock()
        service.delete = AsyncMock()
        api = _make_admin_api(service)
        payload = {
            "identity": {"term": "灰度", "group_id": "g1"},
            "expected_revision": "   ",
        }
        if handler_name == "update_jargon":
            payload["changes"] = {"meaning": "updated"}

        with patch("core.api.jargon_api.request", _request_json(payload)):
            result = await getattr(api, handler_name)()

        assert result["code"] == "validation_error"
        assert "expected_revision" in result["field_errors"]
        api._get_jargon_admin_service.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("handler_name", ["update_jargon", "delete_jargon"])
    async def test_stale_update_and_delete_return_current_entity_and_revision(
        self,
        handler_name: str,
    ) -> None:
        current = {
            "term": "灰度",
            "group_id": "g1",
            "meaning": "server value",
            "context_examples": ["server context"],
        }
        conflict = EditConflictError(current, "rev-current")
        service = MagicMock()
        service.update = AsyncMock(side_effect=conflict)
        service.delete = AsyncMock(side_effect=conflict)
        api = _make_admin_api(service)
        payload = {
            "identity": {"term": "灰度", "group_id": "g1"},
            "expected_revision": "rev-stale",
        }
        if handler_name == "update_jargon":
            payload["changes"] = {"meaning": "local value"}

        with patch("core.api.jargon_api.request", _request_json(payload)):
            result = await getattr(api, handler_name)()

        assert result["code"] == "edit_conflict"
        assert result["data"] == {
            "current_entity": current,
            "current_revision": "rev-current",
        }

    @pytest.mark.asyncio
    async def test_mutation_audit_covers_early_validation_without_payload_leak(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO)
        service = MagicMock()
        service.create = AsyncMock()
        api = _make_admin_api(service)
        secret = "jargon-payload-secret-c9e2"
        payload = {
            "term": "灰度",
            "group_id": "g1",
            "meaning": "Gradual rollout",
            "confidence": 0.9,
            "unknown": secret,
        }

        with patch("core.api.jargon_api.request", _request_json(payload)) as request_mock:
            result = await api.create_jargon()

        audits = [
            record.getMessage()
            for record in caplog.records
            if "[黑话 AUDIT]" in record.getMessage()
        ]
        assert result["code"] == "validation_error"
        assert len(audits) == 1
        assert "action=create" in audits[0]
        assert "entity=jargon" in audits[0]
        assert "identity=unavailable" in audits[0]
        assert "result=failure" in audits[0]
        assert "error_code=validation_error" in audits[0]
        assert secret not in caplog.text
        request_mock.get_json.assert_awaited_once_with(silent=True)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("failure", "expected_code", "exception_secret"),
        [
            (
                EntityNotFoundError("jargon-domain-secret-2b60"),
                "not_found",
                "jargon-domain-secret-2b60",
            ),
            (
                RuntimeError("jargon-generic-secret-77fe"),
                "internal_error",
                "jargon-generic-secret-77fe",
            ),
        ],
    )
    async def test_mutation_audit_mapper_failure_is_single_and_redacted(
        self,
        caplog: pytest.LogCaptureFixture,
        failure: Exception,
        expected_code: str,
        exception_secret: str,
    ) -> None:
        caplog.set_level(logging.INFO)
        service = MagicMock()
        service.create = AsyncMock(side_effect=failure)
        api = _make_admin_api(service)
        payload_secret = "jargon-payload-secret-184a"
        payload = {
            "term": "灰度",
            "group_id": "g1",
            "meaning": payload_secret,
            "confidence": 0.9,
        }

        with patch("core.api.jargon_api.request", _request_json(payload)):
            result = await api.create_jargon()

        audits = [
            record.getMessage()
            for record in caplog.records
            if "[黑话 AUDIT]" in record.getMessage()
        ]
        assert result["code"] == expected_code
        assert len(audits) == 1
        assert "action=create" in audits[0]
        assert "entity=jargon" in audits[0]
        assert "identity=unavailable" in audits[0]
        assert "result=failure" in audits[0]
        assert f"error_code={expected_code}" in audits[0]
        rendered = caplog.text + repr(result)
        assert payload_secret not in rendered
        assert exception_secret not in rendered

    @pytest.mark.asyncio
    async def test_mutation_audit_preserves_cancellation(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO)
        service = MagicMock()
        service.create = AsyncMock(side_effect=asyncio.CancelledError())
        api = _make_admin_api(service)
        payload = {
            "term": "灰度",
            "group_id": "g1",
            "meaning": "Gradual rollout",
            "confidence": 0.9,
        }

        with (
            patch("core.api.jargon_api.request", _request_json(payload)),
            pytest.raises(asyncio.CancelledError),
        ):
            await api.create_jargon()

        assert not [
            record
            for record in caplog.records
            if "[黑话 AUDIT]" in record.getMessage()
        ]

    @pytest.mark.asyncio
    async def test_duplicate_and_not_found_errors_use_stable_codes(self) -> None:
        duplicate = MagicMock()
        duplicate.create = AsyncMock(side_effect=EntityAlreadyExistsError("secret"))
        create_api = _make_admin_api(duplicate)
        create_payload = {
            "term": "灰度",
            "group_id": "g1",
            "meaning": "meaning",
            "confidence": 0.9,
        }
        with patch("core.api.jargon_api.request", _request_json(create_payload)):
            duplicate_result = await create_api.create_jargon()

        missing = MagicMock()
        missing.update = AsyncMock(side_effect=EntityNotFoundError("secret"))
        update_api = _make_admin_api(missing)
        update_payload = {
            "identity": {"term": "灰度", "group_id": "g1"},
            "changes": {"meaning": "updated"},
            "expected_revision": "rev-1",
        }
        with patch("core.api.jargon_api.request", _request_json(update_payload)):
            missing_result = await update_api.update_jargon()

        assert duplicate_result["code"] == "already_exists"
        assert missing_result["code"] == "not_found"
        assert "secret" not in json.dumps(
            [duplicate_result, missing_result], ensure_ascii=False
        )

    @pytest.mark.asyncio
    async def test_delete_returns_deleted_identity(self) -> None:
        service = MagicMock()
        service.delete = AsyncMock(return_value=True)
        api = _make_admin_api(service)
        payload = {
            "identity": {"term": "灰度", "group_id": "g1"},
            "expected_revision": "rev-1",
        }

        with patch("core.api.jargon_api.request", _request_json(payload)):
            result = await api.delete_jargon()

        assert result["data"] == {
            "deleted": True,
            "identity": {"term": "灰度", "group_id": "g1"},
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "handler_name",
        ["create_jargon", "update_jargon", "delete_jargon", "batch_jargon"],
    )
    async def test_mutators_run_maintenance_guard_before_json_or_service(
        self,
        handler_name: str,
    ) -> None:
        guard_response = error_response("maintenance", code="maintenance_mode")
        api = JargonApiMixin()
        api.plugin = SimpleNamespace()
        api._maintenance_write_guard = MagicMock(return_value=guard_response)
        api._get_jargon_admin_service = AsyncMock(
            side_effect=AssertionError("service must not resolve")
        )
        mock_request = _make_mock_request()
        mock_request.get_json = AsyncMock(
            side_effect=AssertionError("JSON must not be parsed")
        )

        with patch("core.api.jargon_api.request", mock_request):
            result = await getattr(api, handler_name)()

        assert result is guard_response
        api._maintenance_write_guard.assert_called_once_with()
        mock_request.get_json.assert_not_awaited()
        api._get_jargon_admin_service.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_component_unavailable_is_stable(self) -> None:
        api = _make_admin_api(None)
        payload = {
            "term": "灰度",
            "group_id": "g1",
            "meaning": "meaning",
            "confidence": 0.9,
        }

        with patch("core.api.jargon_api.request", _request_json(payload)):
            result = await api.create_jargon()

        assert result["code"] == "component_unavailable"
        assert result["message"] == "黑话管理服务不可用"

    @pytest.mark.asyncio
    async def test_unexpected_exception_response_and_log_are_redacted(self) -> None:
        meaning_secret = "meaning-secret-b5a19"
        context_secret = "context-secret-7a22b"
        exception_secret = "exception-secret-9c0df"
        exposed = _make_jargon_meaning("term-secret-d810c", "group-secret-e349d")
        exposed.meaning = meaning_secret
        exposed.context_examples = [context_secret]
        service = MagicMock()
        service.create = AsyncMock(return_value=exposed)
        service.revision_for = MagicMock(
            side_effect=RuntimeError(exception_secret)
        )
        api = _make_admin_api(service)
        payload = {
            "term": "term-secret-d810c",
            "group_id": "group-secret-e349d",
            "meaning": meaning_secret,
            "confidence": 0.9,
        }

        with (
            patch("core.api.jargon_api.request", _request_json(payload)),
            patch("core.api.jargon_api.logger.error") as logged,
        ):
            result = await api.create_jargon()

        rendered = json.dumps(result, ensure_ascii=False) + str(logged.call_args_list)
        assert result["code"] == "internal_error"
        logged.assert_called_once_with(
            "[黑话接口] operation=%s error_class=%s",
            "create",
            "RuntimeError",
        )
        for secret in (
            payload["term"],
            payload["group_id"],
            meaning_secret,
            context_secret,
            exception_secret,
        ):
            assert secret not in rendered


class TestJargonBatch:
    @pytest.mark.asyncio
    async def test_batch_delegates_and_preserves_ordered_partial_results(self) -> None:
        batch_result = {
            "total": 3,
            "succeeded_count": 1,
            "failed_count": 2,
            "succeeded_ids": [{"term": "third", "group_id": "g1"}],
            "failures": [
                {
                    "identity": {"term": "first", "group_id": "g1"},
                    "code": "edit_conflict",
                    "message": "操作失败",
                    "current_entity": {"term": "first", "group_id": "g1"},
                    "current_revision": "rev-current",
                },
                {
                    "identity": {"item_index": 1},
                    "code": "validation_error",
                    "message": "操作失败",
                    "field_errors": {"identity.term": "不能为空"},
                },
            ],
        }
        service = MagicMock()
        service.batch = AsyncMock(return_value=batch_result)
        api = _make_admin_api(service)
        items = [
            {
                "identity": {"term": "first", "group_id": "g1"},
                "expected_revision": "stale",
            },
            {
                "identity": {"term": "", "group_id": "g1"},
                "expected_revision": "rev-2",
            },
            {
                "identity": {"term": "third", "group_id": "g1"},
                "expected_revision": "rev-3",
            },
        ]

        with patch(
            "core.api.jargon_api.request",
            _request_json({"action": "delete", "items": items}),
        ):
            result = await api.batch_jargon()

        assert result["data"] == batch_result
        assert [item["identity"] for item in result["data"]["failures"]] == [
            {"term": "first", "group_id": "g1"},
            {"item_index": 1},
        ]
        service.batch.assert_awaited_once_with(action="delete", items=items)
        json.dumps(result, ensure_ascii=False, allow_nan=False)

    @pytest.mark.asyncio
    async def test_batch_rejects_arbitrary_replacement_via_domain_dispatcher(self) -> None:
        service = JargonAdminService(MagicMock())
        api = _make_admin_api(service)
        payload = {
            "action": "update",
            "items": [
                {
                    "identity": {"term": "灰度", "group_id": "g1"},
                    "expected_revision": "rev-1",
                    "changes": {"meaning": "bulk replacement"},
                }
            ],
        }

        with patch("core.api.jargon_api.request", _request_json(payload)):
            result = await api.batch_jargon()

        assert result["code"] == "validation_error"
        assert result["field_errors"] == {"action": "不支持的批量操作"}


# ---------------------------------------------------------------------------
# Jargon stats
# ---------------------------------------------------------------------------


class TestJargonStats:
    @pytest.mark.asyncio
    async def test_returns_stats_with_store_counts(self) -> None:
        cands = [_make_jargon_candidate("破防", "g1")]
        stub = _make_stub(candidates=cands, store_count=5, store_confirmed=3)
        mock_req = _make_mock_request(group_id="g1")
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_stats()
        assert result["status"] == "ok"
        assert result["data"]["store_total"] == 5
        assert result["data"]["store_confirmed"] == 3

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure_source", ["filter_resolution", "read"])
    async def test_outer_failure_is_fully_redacted(
        self,
        failure_source: str,
    ) -> None:
        path_secret = "C:/secret/jargon-stats-bf14.db"
        group_secret = "stats-group-secret-c025"
        exception_secret = "stats-exception-secret-d136"
        stub = _make_stub()
        failure = RuntimeError(path_secret + group_secret + exception_secret)
        if failure_source == "filter_resolution":
            stub._get_jargon_filter = MagicMock(side_effect=failure)
        else:
            stub.plugin._jargon_filter.get_stats = MagicMock(side_effect=failure)
        mock_req = _make_mock_request(group_id=group_secret)

        with (
            patch("core.api.jargon_api.request", mock_req),
            patch("core.api.jargon_api.logger") as logged,
        ):
            result = await stub.get_jargon_stats()

        rendered = json.dumps(result, ensure_ascii=False) + str(logged.method_calls)
        assert result["code"] == "internal_error"
        assert "exc_info" not in rendered
        logged.error.assert_called_once_with(
            "[黑话接口] operation=%s error_class=%s",
            "read_stats",
            "RuntimeError",
        )
        for secret in (path_secret, group_secret, exception_secret):
            assert secret not in rendered

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure_point", ["total", "confirmed"])
    async def test_store_supplement_failure_is_redacted_best_effort(
        self,
        failure_point: str,
    ) -> None:
        path_secret = "C:/secret/jargon-supplement-e247.db"
        group_secret = "supplement-group-secret-f358"
        exception_secret = "supplement-exception-secret-0469"
        stub = _make_stub()
        stub.plugin._jargon_filter.get_stats = MagicMock(
            return_value=SimpleNamespace(
                group_id=group_secret,
                total_terms=7,
                candidate_count=2,
                top_candidates=[],
            )
        )
        store = stub.plugin._jargon_store
        failure = RuntimeError(path_secret + group_secret + exception_secret)
        if failure_point == "total":
            store.count_by_group = AsyncMock(side_effect=failure)
        else:
            store.count_by_group = AsyncMock(return_value=7)
            store.count_confirmed = AsyncMock(side_effect=failure)
        mock_req = _make_mock_request(group_id=group_secret)

        with (
            patch("core.api.jargon_api.request", mock_req),
            patch("core.api.jargon_api.logger") as logged,
        ):
            result = await stub.get_jargon_stats()

        assert result["status"] == "ok"
        assert result["data"]["group_id"] == group_secret
        assert "store_total" not in result["data"]
        assert "store_confirmed" not in result["data"]
        rendered_log = str(logged.method_calls)
        assert "exc_info" not in rendered_log
        logged.debug.assert_called_once_with(
            "[黑话接口] operation=%s error_class=%s",
            "stats_store_supplement",
            "RuntimeError",
        )
        for secret in (path_secret, group_secret, exception_secret):
            assert secret not in rendered_log

    @pytest.mark.asyncio
    @pytest.mark.parametrize("read_path", ["candidates", "stats", "supplement"])
    async def test_read_boundaries_do_not_swallow_cancellation(
        self,
        read_path: str,
    ) -> None:
        stub = _make_stub()
        if read_path == "candidates":
            stub.plugin._jargon_filter.get_candidates = MagicMock(
                side_effect=asyncio.CancelledError()
            )
            handler = stub.get_jargon_candidates
        else:
            stub.plugin._jargon_filter.get_stats = MagicMock(
                return_value=SimpleNamespace(
                    group_id="g1",
                    total_terms=1,
                    candidate_count=0,
                    top_candidates=[],
                )
            )
            if read_path == "stats":
                stub.plugin._jargon_filter.get_stats = MagicMock(
                    side_effect=asyncio.CancelledError()
                )
            else:
                stub.plugin._jargon_store.count_by_group = AsyncMock(
                    side_effect=asyncio.CancelledError()
                )
            handler = stub.get_jargon_stats

        with patch("core.api.jargon_api.request", _make_mock_request(group_id="g1")):
            with pytest.raises(asyncio.CancelledError):
                await handler()

    @pytest.mark.asyncio
    async def test_requires_group_id(self) -> None:
        stub = _make_stub()
        mock_req = _make_mock_request()
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_stats()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_stats_skips_malformed_top_candidates(self) -> None:
        broken = MagicMock()
        type(broken).term = property(lambda self: (_ for _ in ()).throw(RuntimeError("broken top candidate")))
        cands = [_make_jargon_candidate("破防", "g1"), broken, _make_jargon_candidate("躺平", "g1")]
        stub = _make_stub(candidates=cands, store_count=5, store_confirmed=3)
        mock_req = _make_mock_request(group_id="g1")
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_stats()
        assert result["status"] == "ok"
        assert [item["term"] for item in result["data"]["top_candidates"]] == ["破防", "躺平"]

    @pytest.mark.asyncio
    async def test_stats_tolerates_malformed_top_candidate_container(self) -> None:
        class BrokenCandidates:
            def __iter__(self):
                raise RuntimeError("broken stats candidates")

            def __bool__(self):
                return True

        broken_stats = MagicMock()
        broken_stats.group_id = "g1"
        broken_stats.total_terms = 50
        broken_stats.candidate_count = 8
        broken_stats.top_candidates = BrokenCandidates()

        stub = _make_stub(candidates=[], store_count=5, store_confirmed=3)
        stub.plugin._jargon_filter.get_stats = MagicMock(return_value=broken_stats)
        mock_req = _make_mock_request(group_id="g1")
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_stats()
        assert result["status"] == "ok"
        assert result["data"]["top_candidates"] == []
        assert result["data"]["candidate_count"] == 8

    @pytest.mark.asyncio
    async def test_stats_returns_error_for_malformed_stats_payload(self) -> None:
        broken_stats = MagicMock()
        type(broken_stats).group_id = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("broken stats"))
        )
        broken_stats.top_candidates = [_make_jargon_candidate("破防", "g1")]
        stub = _make_stub(candidates=[], store_count=5, store_confirmed=3)
        stub.plugin._jargon_filter.get_stats = MagicMock(return_value=broken_stats)
        mock_req = _make_mock_request(group_id="g1")
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.get_jargon_stats()
        assert result["status"] == "error"
        assert result["code"] == "internal_error"
        assert result["message"] == "黑话操作失败"


# ---------------------------------------------------------------------------
# Jargon confirm
# ---------------------------------------------------------------------------


class TestJargonConfirm:
    @pytest.mark.asyncio
    async def test_maintenance_guard_runs_before_store_or_json(self) -> None:
        guard_response = error_response("maintenance", code="maintenance_mode")
        api = JargonApiMixin()
        api.plugin = SimpleNamespace()
        api._maintenance_write_guard = MagicMock(return_value=guard_response)
        api._get_jargon_store = AsyncMock(
            side_effect=AssertionError("store resolution must not run")
        )
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(
            side_effect=AssertionError("JSON parsing must not run")
        )

        with patch("core.api.jargon_api.request", mock_req):
            result = await api.confirm_jargon()

        assert result is guard_response
        api._maintenance_write_guard.assert_called_once_with()
        api._get_jargon_store.assert_not_awaited()
        mock_req.get_json.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_confirm_success(self) -> None:
        stub = _make_stub()
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={
            "term": "破防", "group_id": "g1", "confirmed": True,
        })
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.confirm_jargon()
        assert result["status"] == "ok"
        assert result["data"]["action"] == "confirmed"

    @pytest.mark.asyncio
    async def test_reject_success(self) -> None:
        stub = _make_stub()
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={
            "term": "破防", "group_id": "g1", "confirmed": False,
        })
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.confirm_jargon()
        assert result["status"] == "ok"
        assert result["data"]["action"] == "rejected"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("confirmed", [1, 0, "true", None, [], {}])
    async def test_confirm_rejects_non_boolean_without_mutation(self, confirmed) -> None:
        stub = _make_stub()
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={
            "term": "破防", "group_id": "g1", "confirmed": confirmed,
        })
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.confirm_jargon()
        assert result["code"] == "validation_error"
        stub.plugin._jargon_store.confirm.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_term_returns_error(self) -> None:
        stub = _make_stub()
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={
            "group_id": "g1", "confirmed": True,
        })
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.confirm_jargon()
        assert result["status"] == "error"
        assert "term" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_invalid_json_returns_error(self) -> None:
        stub = _make_stub()
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(side_effect=ValueError("bad json"))
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.confirm_jargon()
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Jargon mine
# ---------------------------------------------------------------------------


class TestJargonMine:
    @pytest.mark.asyncio
    async def test_maintenance_guard_runs_before_delegation_miner_or_json(self) -> None:
        guard_response = error_response("maintenance", code="maintenance_mode")
        api = JargonApiMixin()
        api.plugin = SimpleNamespace()
        api._maintenance_write_guard = MagicMock(return_value=guard_response)
        api._get_feature_delegation = MagicMock(
            side_effect=AssertionError("delegation must not run")
        )
        api._get_jargon_miner = AsyncMock(
            side_effect=AssertionError("miner resolution must not run")
        )
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(
            side_effect=AssertionError("JSON parsing must not run")
        )

        with patch("core.api.jargon_api.request", mock_req):
            result = await api.mine_jargon()

        assert result is guard_response
        api._maintenance_write_guard.assert_called_once_with()
        api._get_feature_delegation.assert_not_called()
        api._get_jargon_miner.assert_not_awaited()
        mock_req.get_json.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mine_requires_group_id(self) -> None:
        stub = _make_stub(has_miner=True, has_store=True)
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={"limit": 5})
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.mine_jargon()
        assert result["status"] == "error"
        assert "group_id" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_mine_success(self) -> None:
        meanings = [_make_jargon_meaning("躺平", "g1")]
        stub = _make_stub(has_miner=True)
        stub.plugin._jargon_miner.run_once = AsyncMock(return_value=meanings)
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={
            "group_id": "g1", "limit": 5,
        })
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.mine_jargon()
        assert result["status"] == "ok"
        assert result["data"]["inferred_count"] == 1

    @pytest.mark.asyncio
    async def test_no_miner_returns_error(self) -> None:
        stub = _make_stub(has_miner=False)
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={"group_id": "g1"})
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.mine_jargon()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_negative_limit_falls_back_to_default_window(self) -> None:
        meanings = [_make_jargon_meaning("躺平", "g1")]
        stub = _make_stub(has_miner=True)
        stub.plugin._jargon_miner.run_once = AsyncMock(return_value=meanings)
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={
            "group_id": "g1", "limit": -3,
        })
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.mine_jargon()
        assert result["status"] == "ok"
        stub.plugin._jargon_miner.run_once.assert_awaited_once_with("g1", limit=5)
        assert result["data"]["inferred_count"] == 1

    @pytest.mark.asyncio
    async def test_mine_skips_malformed_result_items(self) -> None:
        broken = MagicMock()
        type(broken).term = property(lambda self: (_ for _ in ()).throw(RuntimeError("broken mined meaning")))
        meanings = [_make_jargon_meaning("躺平", "g1"), broken, _make_jargon_meaning("破防", "g1")]
        stub = _make_stub(has_miner=True)
        stub.plugin._jargon_miner.run_once = AsyncMock(return_value=meanings)
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={
            "group_id": "g1", "limit": 5,
        })
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.mine_jargon()
        assert result["status"] == "ok"
        assert result["data"]["inferred_count"] == 3
        assert [item["term"] for item in result["data"]["results"]] == ["躺平", "破防"]

    @pytest.mark.asyncio
    async def test_mine_tolerates_malformed_result_container(self) -> None:
        class BrokenResults:
            def __iter__(self):
                raise RuntimeError("broken mined results")

            def __len__(self):
                raise RuntimeError("broken mined result length")

            def __bool__(self):
                return True

        stub = _make_stub(has_miner=True)
        stub.plugin._jargon_miner.run_once = AsyncMock(return_value=BrokenResults())
        mock_req = _make_mock_request()
        mock_req.get_json = AsyncMock(return_value={
            "group_id": "g1", "limit": 5,
        })
        with patch("core.api.jargon_api.request", mock_req):
            result = await stub.mine_jargon()
        assert result["status"] == "ok"
        assert result["data"]["inferred_count"] == 0
        assert result["data"]["results"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("handler_name", ["confirm_jargon", "mine_jargon"])
async def test_legacy_invalid_json_failure_is_redacted(handler_name: str) -> None:
    secret = "invalid-json-secret-path-72fb"
    api = JargonApiMixin()
    api.plugin = SimpleNamespace()
    api._maintenance_write_guard = MagicMock(return_value=None)
    api._get_feature_delegation = MagicMock(return_value=None)
    api._get_jargon_store = AsyncMock(return_value=SimpleNamespace())
    api._get_jargon_miner = AsyncMock(return_value=SimpleNamespace())
    mock_req = _make_mock_request()
    mock_req.get_json = AsyncMock(side_effect=ValueError("invalid " + secret))

    with (
        patch("core.api.jargon_api.request", mock_req),
        patch("core.api.jargon_api.logger") as logged,
    ):
        result = await getattr(api, handler_name)()

    rendered = json.dumps(result, ensure_ascii=False) + str(logged.method_calls)
    assert result["status"] == "error"
    assert result["message"] == "JSON 请求体无效"
    assert secret not in rendered
    assert "exc_info" not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_point",
    ["confirm_resolve", "confirm_execute", "mine_resolve", "mine_execute"],
)
async def test_legacy_component_and_execution_failures_are_redacted(
    failure_point: str,
) -> None:
    term_secret = "term-secret-18a4"
    group_secret = "group-secret-29b5"
    meaning_secret = "meaning-secret-3ac6"
    context_secret = "context-secret-4bd7"
    exception_secret = "exception-secret-5ce8"
    failure = RuntimeError(
        exception_secret + meaning_secret + context_secret + group_secret + term_secret
    )
    store = SimpleNamespace(confirm=AsyncMock(return_value=None))
    miner = SimpleNamespace(run_once=AsyncMock(return_value=[]))
    api = JargonApiMixin()
    api.plugin = SimpleNamespace()
    api._maintenance_write_guard = MagicMock(return_value=None)
    api._get_feature_delegation = MagicMock(return_value=None)
    api._get_jargon_store = AsyncMock(return_value=store)
    api._get_jargon_miner = AsyncMock(return_value=miner)
    if failure_point == "confirm_resolve":
        api._get_jargon_store = AsyncMock(side_effect=failure)
    elif failure_point == "confirm_execute":
        store.confirm = AsyncMock(side_effect=failure)
    elif failure_point == "mine_resolve":
        api._get_jargon_miner = AsyncMock(side_effect=failure)
    else:
        miner.run_once = AsyncMock(side_effect=failure)

    is_confirm = failure_point.startswith("confirm")
    payload = (
        {"term": term_secret, "group_id": group_secret, "confirmed": True}
        if is_confirm
        else {"group_id": group_secret, "limit": 5}
    )
    with (
        patch("core.api.jargon_api.request", _request_json(payload)),
        patch("core.api.jargon_api.logger") as logged,
    ):
        result = await getattr(
            api, "confirm_jargon" if is_confirm else "mine_jargon"
        )()

    rendered = json.dumps(result, ensure_ascii=False) + str(logged.method_calls)
    assert result["code"] == "internal_error"
    assert "RuntimeError" in rendered
    assert "exc_info" not in rendered
    for secret in (
        term_secret,
        group_secret,
        meaning_secret,
        context_secret,
        exception_secret,
    ):
        assert secret not in rendered


@pytest.mark.asyncio
async def test_miner_provider_fallback_failure_is_redacted() -> None:
    secret = "provider-secret-path-9f17"
    api = JargonApiMixin()
    api.plugin = SimpleNamespace(
        initializer=SimpleNamespace(
            jargon_filter=object(),
            jargon_store=object(),
            jargon_miner=None,
            llm_provider=None,
        ),
        context=SimpleNamespace(
            get_using_provider=MagicMock(
                side_effect=RuntimeError("provider failed " + secret)
            )
        ),
        feature_delegation=None,
    )
    api._maintenance_write_guard = MagicMock(return_value=None)

    with patch("core.api.jargon_api.logger") as logged:
        result = await api.mine_jargon()

    rendered = json.dumps(result, ensure_ascii=False) + str(logged.method_calls)
    assert result["status"] == "error"
    assert result["message"] == "黑话挖掘器不可用"
    assert secret not in rendered
    assert "exc_info" not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize("handler_name", ["confirm_jargon", "mine_jargon"])
async def test_legacy_execution_does_not_swallow_cancellation(
    handler_name: str,
) -> None:
    api = JargonApiMixin()
    api.plugin = SimpleNamespace()
    api._maintenance_write_guard = MagicMock(return_value=None)
    api._get_feature_delegation = MagicMock(return_value=None)
    api._get_jargon_store = AsyncMock(
        return_value=SimpleNamespace(
            confirm=AsyncMock(side_effect=asyncio.CancelledError())
        )
    )
    api._get_jargon_miner = AsyncMock(
        return_value=SimpleNamespace(
            run_once=AsyncMock(side_effect=asyncio.CancelledError())
        )
    )
    payload = (
        {"term": "灰度", "group_id": "g1", "confirmed": True}
        if handler_name == "confirm_jargon"
        else {"group_id": "g1", "limit": 5}
    )

    with patch("core.api.jargon_api.request", _request_json(payload)):
        with pytest.raises(asyncio.CancelledError):
            await getattr(api, handler_name)()
