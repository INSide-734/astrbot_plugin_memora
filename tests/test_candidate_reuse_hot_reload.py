"""验证 candidate_reuse 配置热重载和 catalog degraded 降级保护。

测试覆盖：
1. Worker 配置快照固定，新 Worker 使用最新配置
2. catalog degraded 时强制降级到 observe
3. 配置保存后热重载生效
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.reflection.application.summary_worker import SummaryWorker
from core.features.reflection.application.topic_candidate_selector import (
    TopicCandidateSelector,
)
from core.features.reflection.application.topic_candidate_selector_helper import (
    select_with_fallback,
)
from core.features.reflection.domain.config import CandidateReuseConfig
from core.features.reflection.domain.summary_models import (
    ClaimedJob,
    SourceWindow,
    SummaryJob,
    TopicCandidateContext,
)
from core.shared.contracts.conversation import Message
from core.shared.summary_source import source_window_digest


@pytest.fixture
def mock_config_manager():
    """模拟 ConfigManager（真实快照形状：topic_segmentation.candidate_reuse 嵌套）。"""
    manager = MagicMock()
    manager.get_config_snapshot = MagicMock(
        return_value=(
            {
                "topic_segmentation": {
                    "candidate_reuse": {
                        "mode": "top_k",
                        "activation_threshold": 10,
                        "fixed_k": 5,
                        "max_full_topics": 10,
                        "max_full_prompt_tokens": 500,
                    }
                }
            },
            "rev-1",
        )
    )
    return manager


@pytest.fixture
def mock_catalog_store():
    """模拟 TopicCatalogStore。"""
    store = MagicMock()
    store.db_connection = MagicMock()
    store.select_full_candidates = AsyncMock(return_value=[])
    store.select_bm25_candidates = AsyncMock(return_value=[])
    store.select_recent_frequent_candidates = AsyncMock(return_value=[])
    # P0-2：规模分桶从 catalog 聚合读取 scope topic 计数
    store.count_scope_topics = AsyncMock(return_value=1)
    return store


@pytest.fixture
def selector(mock_catalog_store):
    """创建 TopicCandidateSelector 实例。"""
    text_processor = MagicMock()
    text_processor.tokenize = MagicMock(return_value=["测试"])
    formatter = MagicMock()
    formatter.format_conversation = MagicMock(return_value="测试对话")
    return TopicCandidateSelector(
        catalog_store=mock_catalog_store,
        text_processor=text_processor,
        conversation_formatter=formatter,
    )


@pytest.fixture
def sample_window():
    """创建示例 SourceWindow。"""
    messages = (
        Message(
            id=1,
            session_id="s1",
            role="user",
            sender_id="u1",
            content="测试",
            timestamp=1.0,
        ),
    )
    return SourceWindow(
        session_id="s1",
        start_seq=0,
        end_seq=1,
        expected_count=1,
        source_digest=source_window_digest(messages, (1,)),
        messages=messages,
        message_seqs=(1,),
    )


@pytest.fixture
def sample_context():
    """创建示例 TopicCandidateContext。"""
    return TopicCandidateContext(
        scope_key="scope1",
        privacy_level="public",
        chat_type="private",
        resolver_revision="v1",
        scope_reason_code="scope_resolved",
        source_provenance_complete=True,
    )


@pytest.mark.asyncio
async def test_worker_config_hot_reload(mock_config_manager):
    """Worker 每次读取配置时都获取最新快照，支持热重载。"""
    worker = SummaryWorker(
        job_store=MagicMock(),
        processor=MagicMock(),
        quality_gate=None,
        memory_engine=MagicMock(),
        batch_preparer=MagicMock(),
        candidate_selector=MagicMock(),
        config_manager=mock_config_manager,
    )

    # 读取初始配置
    # 模拟配置热重载（保持真实嵌套快照形状）
    mock_config_manager.get_config_snapshot.return_value = (
        {
            "topic_segmentation": {
                "candidate_reuse": {
                    "mode": "observe",
                    "activation_threshold": 15,
                    "fixed_k": 3,
                    "max_full_topics": 20,
                    "max_full_prompt_tokens": 300,
                }
            }
        },
        "rev-2",
    )

    # Worker 再次读取配置时应获取到更新后的值
    config2 = worker._get_candidate_reuse_config()
    assert config2.mode == "observe"
    assert config2.activation_threshold == 15
    assert config2.fixed_k == 3


@pytest.mark.asyncio
async def test_catalog_degraded_forces_observe(
    selector, sample_window, sample_context, mock_catalog_store
):
    """catalog 状态不为 ready 时强制降级到 observe，记录 reason_code。"""
    # 模拟 catalog 状态为 degraded
    mock_db = AsyncMock()
    cursor = AsyncMock()
    cursor.fetchone = AsyncMock(return_value=("degraded",))
    mock_db.execute = AsyncMock(return_value=cursor)
    mock_catalog_store.db_connection = mock_db

    config = CandidateReuseConfig(
        mode="top_k",
        activation_threshold=10,
        fixed_k=5,
        max_full_topics=10,
        max_full_prompt_tokens=500,
    )

    result = await selector.select_candidates(sample_window, sample_context, config)

    # 验证强制降级到 observe
    assert result.mode == "observe"
    assert result.effective_mode == "off"
    assert "catalog_degraded" in result.reason_code
    assert result.candidate_count == 0


@pytest.mark.asyncio
async def test_catalog_backfilling_forces_observe(
    selector, sample_window, sample_context, mock_catalog_store
):
    """catalog 状态为 backfilling 时强制降级到 observe。"""
    mock_db = AsyncMock()
    cursor = AsyncMock()
    cursor.fetchone = AsyncMock(return_value=("backfilling",))
    mock_db.execute = AsyncMock(return_value=cursor)
    mock_catalog_store.db_connection = mock_db

    config = CandidateReuseConfig(
        mode="top_k",
        activation_threshold=10,
        fixed_k=5,
        max_full_topics=10,
        max_full_prompt_tokens=500,
    )

    result = await selector.select_candidates(sample_window, sample_context, config)

    assert result.mode == "observe"
    assert "catalog_backfilling" in result.reason_code


@pytest.mark.asyncio
async def test_catalog_ready_allows_full_mode(
    selector, sample_window, sample_context, mock_catalog_store
):
    """catalog 状态为 ready 时允许 top_k 模式正常执行。"""
    mock_db = AsyncMock()
    cursor = AsyncMock()
    cursor.fetchone = AsyncMock(return_value=("ready",))
    mock_db.execute = AsyncMock(return_value=cursor)
    mock_catalog_store.db_connection = mock_db

    # 返回一些候选
    mock_catalog_store.select_full_candidates.return_value = [
        {
            "display_topic": "测试话题",
            "active_source_count": 5,
            "last_seen_at": 100.0,
        }
    ]

    config = CandidateReuseConfig(
        mode="top_k",
        activation_threshold=10,
        fixed_k=5,
        max_full_topics=10,
        max_full_prompt_tokens=500,
    )

    result = await selector.select_candidates(sample_window, sample_context, config)

    # 验证 top_k 模式正常执行
    assert result.mode == "top_k"
    assert result.candidate_count > 0
    assert "catalog_degraded" not in result.reason_code
    assert "catalog_backfilling" not in result.reason_code


@pytest.mark.asyncio
async def test_catalog_unknown_forces_observe(
    selector, sample_window, sample_context, mock_catalog_store
):
    """catalog 状态无法读取时强制降级到 observe。"""
    mock_db = AsyncMock()
    cursor = AsyncMock()
    cursor.fetchone = AsyncMock(return_value=None)
    mock_db.execute = AsyncMock(return_value=cursor)
    mock_catalog_store.db_connection = mock_db

    config = CandidateReuseConfig(
        mode="top_k",
        activation_threshold=10,
        fixed_k=5,
        max_full_topics=10,
        max_full_prompt_tokens=500,
    )

    result = await selector.select_candidates(sample_window, sample_context, config)

    assert result.mode == "observe"
    assert "catalog_empty" in result.reason_code


@pytest.mark.asyncio
async def test_worker_without_config_manager_defaults_to_observe():
    """Worker 没有 config_manager 时降级为默认 observe 配置。"""
    worker = SummaryWorker(
        job_store=MagicMock(),
        processor=MagicMock(),
        quality_gate=None,
        memory_engine=MagicMock(),
        batch_preparer=MagicMock(),
        candidate_selector=MagicMock(),
        config_manager=None,  # 没有 config_manager
    )

    config = worker._get_candidate_reuse_config()

    assert config.mode == "observe"
    assert config.activation_threshold == 32


@pytest.mark.asyncio
async def test_worker_config_manager_exception_defaults_to_observe(
    mock_config_manager,
):
    """config_manager 读取失败时降级为默认 observe 配置。"""
    mock_config_manager.get_config_snapshot.side_effect = RuntimeError("读取失败")

    worker = SummaryWorker(
        job_store=MagicMock(),
        processor=MagicMock(),
        quality_gate=None,
        memory_engine=MagicMock(),
        batch_preparer=MagicMock(),
        candidate_selector=MagicMock(),
        config_manager=mock_config_manager,
    )

    config = worker._get_candidate_reuse_config()

    assert config.mode == "observe"


@pytest.mark.asyncio
async def test_config_reaches_selector_and_returns_candidates(
    mock_config_manager, mock_catalog_store
):
    """P0-1/P0-2 集成链路：嵌套配置 → worker 读取 → selector 执行 → 候选非空。"""
    text_processor = MagicMock()
    formatter = MagicMock()
    selector = TopicCandidateSelector(
        catalog_store=mock_catalog_store,
        text_processor=text_processor,
        conversation_formatter=formatter,
    )
    worker = SummaryWorker(
        job_store=MagicMock(),
        processor=MagicMock(),
        quality_gate=None,
        memory_engine=MagicMock(),
        batch_preparer=MagicMock(),
        candidate_selector=selector,
        config_manager=mock_config_manager,
    )

    # catalog ready + 小规模 scope（5 个 topic，低于 activation_threshold=10）
    mock_db = AsyncMock()
    cursor = AsyncMock()
    cursor.fetchone = AsyncMock(return_value=("ready",))
    mock_db.execute = AsyncMock(return_value=cursor)
    mock_catalog_store.db_connection = mock_db
    mock_catalog_store.count_scope_topics = AsyncMock(return_value=5)
    mock_catalog_store.select_full_candidates = AsyncMock(
        return_value=[
            {
                "display_topic": f"话题{i}",
                "active_source_count": 5,
                "last_seen_at": 100.0,
            }
            for i in range(3)
        ]
    )

    # 配置可达：嵌套快照中的 top_k 真实生效（P0-1）
    config = worker._get_candidate_reuse_config()
    assert config.mode == "top_k"

    messages = (
        Message(
            id=1,
            session_id="s1",
            role="user",
            sender_id="u1",
            content="测试",
            timestamp=1.0,
        ),
    )
    seqs = (1,)
    window = SourceWindow(
        session_id="s1",
        start_seq=0,
        end_seq=1,
        expected_count=1,
        source_digest=source_window_digest(messages, seqs),
        messages=messages,
        message_seqs=seqs,
    )
    job = SummaryJob(
        job_id="job-1",
        session_id="s1",
        session_epoch=1,
        start_seq=0,
        end_seq=1,
        expected_count=1,
        source_digest=window.source_digest,
        scope_key="scope1",
        privacy_level="public",
        chat_type="private",
        resolver_revision="v1",
        scope_reason_code="scope_resolved",
        scope_provenance_complete=True,
    )
    claim = ClaimedJob(
        job=job,
        claim_token="token-1",
        scheduler_id="sched-1",
        lease_until=1.0,
        worker_generation=1,
    )

    # selector 执行且候选非空：scope_snapshot 提供完整 provenance（P1-1），
    # 规模分桶来自 catalog 计数而非 window.topic_count（P0-2）
    selection = await select_with_fallback(selector, window, claim, config)
    assert selection.reason_code == "full_success"
    assert len(selection.labels) == 3
    assert selection.effective_mode.value == "full"
    assert selection.source_provenance_complete is True
