"""对话连续性生产装配、写后标记、召回与持久化闭环测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.features.recall.application import continuity as recall_continuity
from core.features.reflection.application import continuity as reflection_continuity
from core.handlers import continuity_hooks as legacy_continuity
from core.handlers.recall_handler import RecallHandler
from core.handlers.reflection_handler import ReflectionHandler
from core.managers.continuity_tracker import ContinuityTracker
from core.managers.memory_engine import MemoryEngine
from core.managers.memory_engine_lifecycle import (
    MemoryEngineLifecycleMixin,
    _build_continuity_tracker,
)
from core.review.memory_quality_gate import MemoryGateResult

record_continuity_topics = reflection_continuity.record_continuity_topics
resolve_continuity_session = reflection_continuity.resolve_continuity_session


def _recall_handler(tracker: object | None) -> RecallHandler:
    """构造只启用连续性上下文的最小召回处理器。"""

    handler = object.__new__(RecallHandler)
    handler._memory_engine = SimpleNamespace(continuity_tracker=tracker)
    handler._jargon_query_service = None
    handler._expression_learner = None
    handler._affection_manager = None
    return handler


def test_legacy_continuity_hooks_reuse_feature_application_objects() -> None:
    """旧 handlers 路径只能恒等导出两个 feature 的连续性服务。"""

    assert legacy_continuity.__all__ == [
        "build_continuity_context",
        "record_continuity_topics",
        "resolve_continuity_session",
    ]
    assert (
        legacy_continuity.build_continuity_context
        is recall_continuity.build_continuity_context
    )
    assert (
        legacy_continuity.record_continuity_topics
        is reflection_continuity.record_continuity_topics
    )
    assert (
        legacy_continuity.resolve_continuity_session
        is reflection_continuity.resolve_continuity_session
    )


def test_lifecycle_builder_uses_runtime_config_and_restores(tmp_path: Path) -> None:
    """真实 Tracker 应使用 data_dir、TTL 和上限，并恢复未过期状态。"""

    seeded = ContinuityTracker(data_dir=str(tmp_path))
    seeded.mark_topics(
        "private:user-a",
        ["西湖划船", "周末计划", "天气", "交通", "餐厅", "住宿"],
    )
    seeded.save_state()

    tracker = _build_continuity_tracker(
        {
            "continuity_tracking.enabled": True,
            "continuity_tracking.topic_ttl_days": 11,
            "continuity_tracking.max_pending_topics": 4,
            "data_dir": str(tmp_path),
        },
        str(tmp_path / "memora.db"),
    )

    assert isinstance(tracker, ContinuityTracker)
    assert tracker._topic_ttl_sec == 11 * 86400
    assert tracker._max_topics == 4
    assert len(tracker.get_pending_topics("private:user-a", max_return=10)) == 4
    assert tracker.get_continuity_context("private:user-a") is not None


def test_lifecycle_builder_disabled_does_not_create_tracker(tmp_path: Path) -> None:
    """关闭配置时不得创建、读取或写入连续性状态。"""

    assert (
        _build_continuity_tracker(
            {
                "continuity_tracking.enabled": False,
                "data_dir": str(tmp_path),
            },
            str(tmp_path / "memora.db"),
        )
        is None
    )
    assert not (tmp_path / "continuity_state.json").exists()


@pytest.mark.asyncio
async def test_lifecycle_close_calls_sync_continuity_save() -> None:
    """关闭引擎时应同步保存连续性状态，不把同步接口错误 await。"""

    tracker = MagicMock()
    host = SimpleNamespace(
        atom_lifecycle_manager=None,
        continuity_tracker=tracker,
        auto_learning=None,
        anomaly_detector=None,
        _pending_tasks=set(),
        db_connection=None,
        graph_vector_db=None,
    )

    await MemoryEngineLifecycleMixin.close(host)

    tracker.save_state.assert_called_once_with()


@pytest.mark.asyncio
async def test_memory_engine_starts_and_closes_real_continuity_tracker(
    tmp_path: Path,
) -> None:
    """真实 MemoryEngine 启停应装配 Tracker 并持久化状态，无构造类型错误。"""

    engine = MemoryEngine(
        db_path=str(tmp_path / "memora.db"),
        faiss_db=MagicMock(),
        config={
            "graph_memory_enabled": False,
            "recall_engine.stopwords_path": "",
            "write_reliability.repair_enabled": False,
            "user_profile.enabled": False,
            "auto_learning.enabled": False,
            "knowledge_base.enabled": False,
            "notes.enabled": False,
            "reranker.enabled": False,
            "export.enabled": False,
            "continuity_tracking.enabled": True,
            "continuity_tracking.topic_ttl_days": 2,
            "continuity_tracking.max_pending_topics": 3,
            "data_dir": str(tmp_path),
        },
    )
    engine._schema.create_tables = AsyncMock()
    with patch("core.managers.memory_engine_lifecycle.BM25Retriever") as bm25_cls:
        bm25_cls.return_value.initialize = AsyncMock()
        await engine.initialize()

    assert isinstance(engine.continuity_tracker, ContinuityTracker)
    assert engine.continuity_tracker._topic_ttl_sec == 2 * 86400
    engine.continuity_tracker.mark_topics("private:user-a", ["西湖划船"])

    await engine.close()

    assert (tmp_path / "continuity_state.json").exists()


def test_reflection_records_only_normalized_canonical_topics() -> None:
    """canonical 写后钩子应只标记结构化 topic，并保留记忆重要性。"""

    tracker = MagicMock()

    record_continuity_topics(
        SimpleNamespace(continuity_tracker=tracker),
        "private:user-a",
        {
            "importance": 0.8,
            "metadata": {"topics": [" 西湖 ", "", 7, "划船"]},
        },
    )

    tracker.mark_topics.assert_called_once_with(
        "private:user-a",
        ["西湖", "划船"],
        importance=0.8,
    )


def test_reflection_ignores_missing_or_malformed_topics() -> None:
    """无 Tracker 或 topics 结构非法时写后钩子应安全跳过。"""

    tracker = MagicMock()
    record_continuity_topics(
        SimpleNamespace(continuity_tracker=tracker),
        "private:user-a",
        {"metadata": {"topics": "不是列表"}},
    )
    record_continuity_topics(
        SimpleNamespace(continuity_tracker=None),
        "private:user-a",
        {"metadata": {"topics": ["西湖"]}},
    )

    tracker.mark_topics.assert_not_called()


def test_reflection_resolves_session_after_successful_window() -> None:
    """反思窗口完成后应通知 Tracker 保留该 session 的待续话题。"""

    tracker = MagicMock()

    resolve_continuity_session(
        SimpleNamespace(continuity_tracker=tracker),
        "private:user-a",
    )

    tracker.resolve_session.assert_called_once_with("private:user-a")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gate_action", "expected_marks"),
    [("allow", 1), ("quarantined", 0)],
)
async def test_reflection_pipeline_marks_only_canonical_topics(
    gate_action: str,
    expected_marks: int,
) -> None:
    """反思链只在质量门允许且 canonical 写成功后标记 topic。"""

    conversation_manager = MagicMock()
    conversation_manager.get_session_metadata = AsyncMock(return_value=0)
    conversation_manager.update_session_metadata = AsyncMock()
    conversation_manager.update_session_metadata_fields = AsyncMock(return_value=True)
    processor = MagicMock()
    processor.process_conversation = AsyncMock(
        return_value=[
            {
                "content": "可信候选" if gate_action == "allow" else "隔离候选",
                "importance": 0.8,
                "metadata": {
                    "summary_quality": "high" if gate_action == "allow" else "low",
                    "topics": ["西湖", "划船"],
                },
                "atoms": [],
            }
        ]
    )
    tracker = MagicMock()
    engine = MagicMock()
    engine.continuity_tracker = tracker
    engine.add_memory = AsyncMock(return_value=11)
    quality_gate = MagicMock()
    quality_gate.route_candidate = AsyncMock(
        return_value=MemoryGateResult(action=gate_action)
    )
    handler = ReflectionHandler(
        context=MagicMock(),
        config_manager=MagicMock(),
        memory_engine=engine,
        memory_processor=processor,
        conversation_manager=conversation_manager,
        enforce_limit_cb=MagicMock(),
        memory_quality_gate=quality_gate,
    )
    handler._prepare_message_batches = AsyncMock(
        return_value=[[MagicMock(group_id=None)]]
    )

    await handler._storage_task(
        session_id="private:user-a",
        history_messages=[MagicMock(group_id=None), MagicMock(group_id=None)],
        persona_id=None,
        start_index=0,
        end_index=2,
    )

    assert tracker.mark_topics.call_count == expected_marks
    if gate_action == "allow":
        tracker.mark_topics.assert_called_once_with(
            "private:user-a",
            ["西湖", "划船"],
            importance=0.8,
        )
        engine.add_memory.assert_awaited_once()
    else:
        engine.add_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_recall_builds_temporary_continuity_context_for_same_session() -> None:
    """召回应读取同 session 待续话题并标记本次恢复，不跨 session。"""

    real_tracker = ContinuityTracker()
    real_tracker.mark_topics("private:user-a", ["西湖划船"])
    tracker = MagicMock(wraps=real_tracker)
    handler = _recall_handler(tracker)

    context = await handler._build_cognitive_context(
        text="继续",
        group_id="private:user-a",
        persona_id="default",
    )
    other_context = await handler._build_cognitive_context(
        text="继续",
        group_id="private:user-b",
        persona_id="default",
    )

    assert "[连续性提示]" in context
    assert "西湖划船" in context
    assert other_context == ""


@pytest.mark.asyncio
async def test_recall_continuity_failure_degrades_to_empty_context() -> None:
    """连续性读取普通失败不得破坏召回主链。"""

    tracker = MagicMock()
    tracker.get_continuity_context.side_effect = RuntimeError("状态损坏")

    context = await _recall_handler(tracker)._build_cognitive_context(
        text="继续",
        group_id="private:user-a",
        persona_id="default",
    )

    assert context == ""


@pytest.mark.asyncio
async def test_recall_disabled_continuity_does_not_inject_context() -> None:
    """关闭连续性配置后，召回链不得生成任何连续性上下文。"""

    context = await _recall_handler(None)._build_cognitive_context(
        text="继续",
        group_id="private:user-a",
        persona_id="default",
    )

    assert context == ""
