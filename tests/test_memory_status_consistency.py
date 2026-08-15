"""记忆生命周期状态在存储、召回、页面与派生链路中的一致性回归测试。"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from core.features.knowledge.application.knowledge_proposal_pipeline import (
    _eligible_memory,
)
from core.features.memory.application.lifecycle_operations import (
    LifecycleOperationsMixin,
)
from core.features.memory.application.retrieval_optimizer import RetrievalOptimizer
from core.features.memory.application.stats_operations import StatsOperationsMixin
from core.features.retrieval.rrf_fusion import HybridResult
from core.platform.transport.page_api.evaluation_api import EvaluationApiMixin
from core.platform.transport.page_api.memory_batch_api import MemoryBatchApiMixin
from core.platform.transport.page_api.memory_read_api import MemoryReadApiMixin
from core.platform.transport.page_api.memory_stats_recall_api import (
    MemoryStatsRecallApiMixin,
)
from core.platform.transport.page_api.memory_write_api import MemoryWriteApiMixin
from core.shared.memory_status import (
    effective_memory_status,
    is_memory_recallable,
    set_memory_status,
)
from core.shared.sql import MEMORY_STATUS_SQL


def _result(memory_id: int, metadata: dict[str, object]) -> HybridResult:
    """构造带指定生命周期 metadata 的召回结果。"""

    return HybridResult(
        doc_id=memory_id,
        final_score=0.9,
        rrf_score=0.9,
        bm25_score=None,
        vector_score=None,
        content="状态一致性测试记忆",
        metadata=metadata,
    )


def _memory_record(metadata: dict[str, object]) -> dict[str, object]:
    """构造满足自动知识质量门的 canonical memory 读取结果。"""

    return {
        "id": 1,
        "text": "状态一致性测试记忆",
        "metadata": {
            "importance": 0.8,
            "confidence": 0.9,
            "stability": 0.9,
            "scope_key": "session:test",
            "privacy_level": "shared",
            **metadata,
        },
    }


def test_effective_memory_status_prefers_lifecycle_field_and_preserves_legacy() -> None:
    """生命周期字段应覆盖冲突旧字段，缺失时仍读取旧字段。"""

    assert (
        effective_memory_status({"memory_status": "dormant", "status": "active"})
        == "dormant"
    )
    assert effective_memory_status({"status": "archived"}) == "archived"
    assert (
        effective_memory_status({"memory_status": "", "status": "deleted"}) == "deleted"
    )
    assert effective_memory_status({}) == "active"
    assert effective_memory_status({"status": " CURRENT "}) == "active"
    assert (
        effective_memory_status({"memory_status": "stable", "status": "dormant"})
        == "active"
    )
    assert (
        effective_memory_status({"memory_status": "vendor", "status": "archived"})
        == "archived"
    )
    assert effective_memory_status({"memory_status": "vendor"}) == "unknown"
    assert not is_memory_recallable({"memory_status": "vendor"})


def test_set_memory_status_keeps_lifecycle_and_legacy_fields_in_sync() -> None:
    """状态写入必须同时更新生命周期字段与兼容字段。"""

    metadata = {"memory_status": "active", "status": "active"}

    set_memory_status(metadata, "dormant", status_changed_at=123.0)

    assert metadata == {
        "memory_status": "dormant",
        "status": "dormant",
        "status_changed_at": 123.0,
    }


@pytest.mark.asyncio
async def test_sql_status_expression_matches_python_normalization(
    tmp_path: Path,
) -> None:
    """SQLite 状态筛选必须与 Python 的类型、空白和大小写规则一致。"""
    rows = [
        (1, {"memory_status": " DORMANT ", "status": "active"}),
        (2, {"memory_status": 42, "status": " ARCHIVED "}),
        (3, {"memory_status": "   ", "status": " Deleted "}),
        (4, {"status": "\tSTABLE\n"}),
        (5, {"status": " CURRENT "}),
        (6, {"status": "\tDÖRMANT\n"}),
        (7, {"memory_status": "vendor", "status": "archived"}),
        (8, {"memory_status": "vendor"}),
        (9, None),
    ]
    db_path = tmp_path / "memory-status.db"
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "CREATE TABLE documents (id INTEGER PRIMARY KEY, metadata TEXT)"
        )
        await db.executemany(
            "INSERT INTO documents (id, metadata) VALUES (?, ?)",
            [
                (
                    memory_id,
                    json.dumps(metadata) if metadata is not None else "not-json",
                )
                for memory_id, metadata in rows
            ],
        )
        cursor = await db.execute(
            f"SELECT id, ({MEMORY_STATUS_SQL}) AS status FROM documents ORDER BY id"
        )
        statuses = {row[0]: row[1] for row in await cursor.fetchall()}

    assert statuses == {
        memory_id: effective_memory_status(metadata) for memory_id, metadata in rows
    }


@pytest.mark.asyncio
async def test_sql_status_expression_uses_last_duplicate_json_key(
    tmp_path: Path,
) -> None:
    """SQLite 状态筛选必须匹配 Python 对重复 JSON 键保留最后值的语义。"""
    rows = [
        (1, '{"memory_status":"dormant","memory_status":"active"}'),
        (
            2,
            '{"memory_status":"active","memory_status":"dormant","status":"active"}',
        ),
        (
            3,
            '{"memory_status":42,"memory_status":"vendor","status":"dormant","status":"archived"}',
        ),
    ]
    db_path = tmp_path / "memory-status-duplicate-keys.db"
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "CREATE TABLE documents (id INTEGER PRIMARY KEY, metadata TEXT)"
        )
        await db.executemany(
            "INSERT INTO documents (id, metadata) VALUES (?, ?)",
            rows,
        )
        cursor = await db.execute(
            f"SELECT id, ({MEMORY_STATUS_SQL}) AS status FROM documents ORDER BY id"
        )
        statuses = {row[0]: row[1] for row in await cursor.fetchall()}

    assert statuses == {
        memory_id: effective_memory_status(json.loads(metadata))
        for memory_id, metadata in rows
    }


class _LifecycleHost(LifecycleOperationsMixin):
    """为分层遗忘状态写入提供最小可观测宿主。"""

    def __init__(self) -> None:
        """创建使用异步数据库替身的宿主。"""

        self._db = MagicMock()
        self._db.commit = AsyncMock()
        self._invalidate_cache: Callable[[], None] | None = None


@pytest.mark.asyncio
async def test_lifecycle_transition_writes_both_status_fields() -> None:
    """分层遗忘进入休眠时，页面与检索读取的状态字段必须同时更新。"""

    host = _LifecycleHost()
    cache = RetrievalOptimizer(config={})
    cache_key = cache.cache_key("状态迁移", 1, None, None)
    cache.set_cached(cache_key, [_result(17, {"status": "active"})])
    host._invalidate_cache = cache.invalidate_cache
    cursor = AsyncMock()
    cursor.fetchone.return_value = (json.dumps({"status": "active"}),)
    host._db.execute = AsyncMock(side_effect=[cursor, None])

    assert cache.get_cached(cache_key) is not None
    updated = await host._batch_update_status([17], "dormant", 123.0)

    assert updated == 1
    payload = json.loads(host._db.execute.await_args_list[1].args[1][0])
    assert payload["memory_status"] == "dormant"
    assert payload["status"] == "dormant"
    assert payload["status_changed_at"] == 123.0
    assert host._db.execute.await_args_list[1].args[1][1] == 123.0
    assert cache.get_cached(cache_key) is None


class _StatsHost(StatsOperationsMixin):
    """为统计状态聚合提供最小可观测宿主。"""

    def __init__(self, documents: list[dict[str, object]]) -> None:
        """构造返回固定 canonical 文档集的存储替身。"""

        self._faiss_db = MagicMock()
        self._faiss_db.document_storage.count_documents = AsyncMock(
            return_value=len(documents)
        )
        self._faiss_db.document_storage.get_documents = AsyncMock(
            return_value=documents
        )
        self._graph_store = None


@pytest.mark.asyncio
async def test_statistics_prioritizes_lifecycle_status() -> None:
    """统计必须将已休眠记忆从活跃计数中移除，并保留旧字段兼容性。"""

    host = _StatsHost(
        [
            {
                "id": 1,
                "text": "休眠记忆",
                "metadata": {
                    "memory_status": "dormant",
                    "status": "active",
                    "importance": 0.5,
                },
            },
            {
                "id": 2,
                "text": "旧归档记忆",
                "metadata": {"status": "archived", "importance": 0.5},
            },
        ]
    )

    stats = await host.get_statistics()

    assert stats["status_breakdown"] == {
        "active": 0,
        "dormant": 1,
        "archived": 1,
        "deleted": 0,
        "unknown": 0,
    }


@pytest.mark.asyncio
async def test_retrieval_filters_lifecycle_and_legacy_nonactive_statuses() -> None:
    """召回应同时过滤新旧字段标记的休眠、归档与删除记忆。"""

    optimizer = RetrievalOptimizer(config={})
    results = await optimizer.apply_boosts(
        [
            _result(1, {"memory_status": "dormant", "status": "active"}),
            _result(2, {"status": "archived"}),
            _result(3, {"status": "deleted"}),
            _result(4, {"memory_status": "active", "status": "archived"}),
            _result(5, {"memory_status": "vendor"}),
        ],
        None,
    )

    assert [result.doc_id for result in results] == [4]


@pytest.mark.asyncio
async def test_chain_expansion_excludes_nonrecallable_graph_and_topic_candidates() -> (
    None
):
    """多跳图边和话题扩展都不得重新引入休眠或归档候选。"""
    seed = _result(1, {"memory_status": "active", "topics": ["咖啡"]})

    async def search_topics(*_args: object, **_kwargs: object) -> list[HybridResult]:
        """返回休眠的话题关联候选。"""
        return [_result(3, {"status": "dormant"})]

    async def expand_graph(*_args: object, **_kwargs: object) -> list[HybridResult]:
        """返回不携带 canonical 生命周期字段的派生图候选。"""
        return [_result(2, {})]

    async def get_memory(memory_id: int) -> dict[str, object] | None:
        """为图候选返回已归档的 canonical metadata。"""
        if memory_id == 2:
            return {"metadata": {"status": "archived"}}
        return {"metadata": {"status": "active"}}

    optimizer = RetrievalOptimizer(
        config={}, search_memories_cb=search_topics, get_memory_cb=get_memory
    )
    optimizer._expand_via_graph_edges = expand_graph

    results = await optimizer.chain_expand_multi_hop(
        [seed], k=3, session_id=None, persona_id=None, max_hops=1
    )

    assert [result.doc_id for result in results] == [1]


def test_trace_preserves_archived_effective_status() -> None:
    """Trace 脱敏必须保留归档记忆的有效生命周期状态。"""
    from core.features.retrieval.trace_privacy import sanitize_trace_payload

    trace = sanitize_trace_payload(
        {
            "trace_id": "trace-archived",
            "results": [{"metadata": {"memory_status": "archived"}}],
        }
    )

    assert trace["results"][0]["metadata"] == {"status": "archived"}


def test_knowledge_eligibility_prioritizes_lifecycle_status() -> None:
    """自动知识不得从休眠来源抽取，但必须兼容旧归档字段。"""

    assert not _eligible_memory(
        _memory_record({"memory_status": "dormant", "status": "active"})
    )
    assert not _eligible_memory(_memory_record({"status": "archived"}))
    assert _eligible_memory(
        _memory_record({"memory_status": "active", "status": "archived"})
    )


class _ReadApiStub:
    """暴露列表与详情行为的最小 Page API 宿主。"""

    list_memories = MemoryReadApiMixin.list_memories
    get_memory_detail = MemoryReadApiMixin.get_memory_detail

    def __init__(self, db_path: str) -> None:
        """保存测试用 canonical 数据库路径。"""

        self._db_path = db_path

    def _ok(self, data: object) -> dict[str, object]:
        """返回测试使用的成功 envelope。"""

        return {"status": "ok", "data": data}

    def _error(self, message: str) -> dict[str, object]:
        """返回测试使用的失败 envelope。"""

        return {"status": "error", "message": message}

    async def _ensure_plugin_ready(self):
        """返回指向测试数据库的最小引擎。"""

        return {"memory_engine": SimpleNamespace(db_path=self._db_path)}, None

    def _normalize_metadata(self, metadata: object) -> dict[str, object]:
        """将 JSON metadata 解析为字典。"""

        if isinstance(metadata, str):
            parsed = json.loads(metadata)
            return parsed if isinstance(parsed, dict) else {}
        if isinstance(metadata, dict):
            return {str(key): value for key, value in metadata.items()}
        return {}

    async def _get_memory_record(self, memory_id: int) -> dict[str, object] | None:
        """从测试数据库读取一条 canonical 记忆。"""

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, doc_id, text, metadata, created_at, updated_at "
                "FROM documents WHERE id = ?",
                (memory_id,),
            )
            row = await cursor.fetchone()
        return dict(row) if row else None

    @staticmethod
    def _get_graph_store(_engine: object) -> None:
        """测试不装配图存储。"""

        return None


async def _seed_documents(db_path: Path) -> None:
    """写入冲突新旧状态和仅旧字段的 canonical 记录。"""

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "CREATE TABLE documents ("
            "id INTEGER PRIMARY KEY, doc_id TEXT, text TEXT, metadata TEXT, "
            "created_at TEXT, updated_at TEXT)"
        )
        await db.executemany(
            "INSERT INTO documents "
            "(id, doc_id, text, metadata, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    1,
                    "doc-1",
                    "休眠记忆",
                    json.dumps(
                        {
                            "memory_status": "dormant",
                            "status": "active",
                            "create_time": 2,
                        }
                    ),
                    "2026-08-15",
                    "2026-08-15",
                ),
                (
                    2,
                    "doc-2",
                    "旧归档记忆",
                    json.dumps({"status": "archived", "create_time": 1}),
                    "2026-08-14",
                    "2026-08-14",
                ),
                (
                    3,
                    "doc-3",
                    "旧活跃别名记忆",
                    json.dumps({"status": "current", "create_time": 3}),
                    "2026-08-16",
                    "2026-08-16",
                ),
            ],
        )
        await db.commit()


@pytest.mark.asyncio
async def test_memory_read_api_returns_and_filters_effective_lifecycle_status(
    tmp_path: Path,
) -> None:
    """列表和详情必须展示有效生命周期状态，状态筛选也必须使用同一规则。"""

    db_path = tmp_path / "memora.db"
    await _seed_documents(db_path)
    api = _ReadApiStub(str(db_path))

    list_request = MagicMock()
    list_request.args = {"status": "dormant"}
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            "core.platform.transport.page_api.memory_read_api.request", list_request
        )
        listed = await api.list_memories()

    assert listed["data"]["total"] == 1
    assert listed["data"]["items"][0]["status"] == "dormant"

    active_request = MagicMock()
    active_request.args = {"status": "active"}
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            "core.platform.transport.page_api.memory_read_api.request", active_request
        )
        active_listed = await api.list_memories()

    assert active_listed["data"]["total"] == 1
    assert active_listed["data"]["items"][0]["status"] == "active"

    detail_request = MagicMock()
    detail_request.args = {"memory_id": "1"}
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            "core.platform.transport.page_api.memory_read_api.request", detail_request
        )
        detail = await api.get_memory_detail()

    assert detail["data"]["status"] == "dormant"


@pytest.mark.asyncio
async def test_current_memory_evaluation_cases_exclude_nonactive_lifecycle_statuses(
    tmp_path: Path,
) -> None:
    """内置评测只应从有效状态为 active 的 canonical 记忆生成自检用例。"""

    db_path = tmp_path / "memora.db"
    await _seed_documents(db_path)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO documents "
            "(id, doc_id, text, metadata, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                4,
                "doc-4",
                "可评测记忆",
                json.dumps({"memory_status": "active", "status": "archived"}),
                "2026-08-15",
                "2026-08-15",
            ),
        )
        await db.execute(
            "INSERT INTO documents "
            "(id, doc_id, text, metadata, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                5,
                "doc-5",
                "兼容活跃记忆",
                json.dumps({"status": "stable"}),
                "2026-08-16",
                "2026-08-16",
            ),
        )
        await db.commit()

    cases = await EvaluationApiMixin._load_current_memory_cases(
        SimpleNamespace(db_path=str(db_path))
    )

    assert [case.relevant_doc_ids for case in cases] == [{"5"}, {"4"}, {"3"}]


class _StatusWriteApiStub:
    """提供单字段状态更新所需依赖的最小 Page API 宿主。"""

    update_memory = MemoryWriteApiMixin.update_memory
    _update_memory_changes = MemoryWriteApiMixin._update_memory_changes

    def __init__(self) -> None:
        """构造可观测的 canonical 引擎替身。"""
        self.engine = MagicMock()
        self.engine.update_memory = AsyncMock(return_value=True)

    def _ok(self, data: object) -> dict[str, object]:
        """返回测试成功 envelope。"""
        return {"status": "ok", "data": data}

    def _error(self, message: str) -> dict[str, object]:
        """返回测试错误 envelope。"""
        return {"status": "error", "message": message}

    async def _ensure_plugin_ready(self):
        """返回可更新的 canonical 引擎。"""
        return {"memory_engine": self.engine}, None

    async def _get_memory_record(self, _memory_id: int) -> dict[str, object]:
        """返回带旧状态字段的现有 canonical memory。"""
        return {"text": "状态测试", "metadata": {"status": "archived"}}

    @staticmethod
    def _normalize_metadata(metadata: object) -> dict[str, object]:
        """将测试 metadata 规范为映射。"""
        return dict(metadata) if isinstance(metadata, dict) else {}

    @staticmethod
    def _importance_to_display(value: object) -> object:
        """保持本用例不涉及的重要性显示值。"""
        return value


@pytest.mark.asyncio
async def test_memory_write_api_synchronizes_lifecycle_and_legacy_status() -> None:
    """单字段编辑状态必须同步 lifecycle 与兼容字段。"""
    api = _StatusWriteApiStub()
    request_payload = MagicMock()
    request_payload.get_json = AsyncMock(
        return_value={"memory_id": 1, "field": "status", "value": "active"}
    )
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            "core.platform.transport.page_api.memory_write_api.request", request_payload
        )
        patcher.setattr(
            "core.platform.transport.page_api.memory_write_api.time.time",
            lambda: 345.0,
        )
        response = await api.update_memory()

    assert response["status"] == "ok"
    metadata = api.engine.update_memory.await_args.args[1]["metadata"]
    assert metadata["memory_status"] == "active"
    assert metadata["status"] == "active"
    assert metadata["status_changed_at"] == 345.0


@pytest.mark.asyncio
async def test_memory_write_api_full_form_sets_shared_status_transition_time() -> None:
    """全表单状态编辑应把生命周期时间戳与统一更新时间对齐。"""
    api = _StatusWriteApiStub()
    request_payload = MagicMock()
    request_payload.get_json = AsyncMock(
        return_value={"memory_id": 1, "changes": {"status": "dormant"}}
    )
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            "core.platform.transport.page_api.memory_write_api.request", request_payload
        )
        patcher.setattr(
            "core.platform.transport.page_api.memory_write_api.time.time",
            lambda: 456.0,
        )
        response = await api.update_memory()

    assert response["status"] == "ok"
    metadata = api.engine.update_memory.await_args.args[1]["metadata"]
    assert metadata["memory_status"] == "dormant"
    assert metadata["status_changed_at"] == 456.0
    assert metadata["updated_at"] == 456.0


class _BatchStatusApiStub:
    """提供批量状态更新所需依赖的最小 Page API 宿主。"""

    batch_update_memories = MemoryBatchApiMixin.batch_update_memories

    def __init__(self) -> None:
        """构造可观测的 canonical 引擎替身。"""
        self.engine = MagicMock()
        self.engine.update_memory = AsyncMock(return_value=True)

    def _ok(self, data: object) -> dict[str, object]:
        """返回测试成功 envelope。"""
        return {"status": "ok", "data": data}

    def _error(self, message: str) -> dict[str, object]:
        """返回测试错误 envelope。"""
        return {"status": "error", "message": message}

    async def _ensure_plugin_ready(self):
        """返回可更新的 canonical 引擎。"""
        return {"memory_engine": self.engine}, None

    @staticmethod
    def _coerce_memory_id(raw_id: object) -> int:
        """复用批量接口的 ID 规范化。"""
        from core.platform.transport.page_api.memory_batch_api import (
            MemoryBatchApiMixin,
        )

        return MemoryBatchApiMixin._coerce_memory_id(raw_id)


@pytest.mark.asyncio
async def test_batch_status_update_sets_shared_transition_time() -> None:
    """批量状态编辑必须为每条成功写入保留同一转换时间。"""
    api = _BatchStatusApiStub()
    request_payload = MagicMock()
    request_payload.get_json = AsyncMock(
        return_value={"memory_ids": [1, 2], "field": "status", "value": "archived"}
    )
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            "core.platform.transport.page_api.memory_batch_api.request", request_payload
        )
        patcher.setattr(
            "core.platform.transport.page_api.memory_batch_api.time.time",
            lambda: 789.0,
        )
        response = await api.batch_update_memories()

    assert response["data"]["updated_count"] == 2
    for call in api.engine.update_memory.await_args_list:
        metadata = call.args[1]["metadata"]
        assert metadata["status"] == "archived"
        assert metadata["status_changed_at"] == 789.0


class _RecallStatusApiStub:
    """提供管理员召回测试所需依赖的最小 Page API 宿主。"""

    test_recall = MemoryStatsRecallApiMixin.test_recall

    def _ok(self, data: object) -> dict[str, object]:
        """返回测试成功 envelope。"""
        return {"status": "ok", "data": data}

    def _error(self, message: str) -> dict[str, object]:
        """返回测试错误 envelope。"""
        return {"status": "error", "message": message}

    async def _ensure_plugin_ready(self):
        """返回携带冲突生命周期字段的固定召回结果。"""

        class Result:
            """构造响应序列化所需的最小召回结果。"""

            doc_id = 1
            content = "休眠记忆"
            final_score = 0.9
            metadata = {"memory_status": "dormant", "status": "active"}
            score_breakdown = {}

        engine = MagicMock()
        engine.search_memories = AsyncMock(return_value=[Result()])
        return {"memory_engine": engine}, None


@pytest.mark.asyncio
async def test_recall_api_returns_effective_lifecycle_status() -> None:
    """管理员召回响应必须优先暴露 memory_status，而非冲突兼容字段。"""
    api = _RecallStatusApiStub()
    request_payload = MagicMock()
    request_payload.get_json = AsyncMock(return_value={"query": "休眠"})
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            "core.platform.transport.page_api.memory_stats_recall_api.request",
            request_payload,
        )
        response = await api.test_recall()

    assert response["data"]["results"][0]["metadata"]["status"] == "dormant"
