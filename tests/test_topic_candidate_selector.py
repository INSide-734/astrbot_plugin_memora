"""TopicCandidateSelector 的单元测试。

测试范围：
- 四种模式：off/observe/full/top_k
- 失败降级和 baseline
- BM25 + 补足逻辑
- Token 和数量双预算
- 规范键去重
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.memory.infrastructure.topic_catalog_store import TopicCatalogStore
from core.features.recall.processors.conversation_formatter import (
    ConversationFormatter,
)
from core.features.recall.processors.text_processor import TextProcessor
from core.features.reflection.application.topic_candidate_selector import (
    TopicCandidateSelector,
)
from core.features.reflection.domain.config import CandidateReuseConfig
from core.features.reflection.domain.summary_models import (
    Message,
    SourceWindow,
    TopicCandidateContext,
    TopicCandidateMode,
)
from core.shared.summary_source import source_window_digest


@pytest.fixture
def mock_catalog_store():
    """模拟 TopicCatalogStore（含 scope topic 计数与 ready 状态）。"""
    store = MagicMock(spec=TopicCatalogStore)
    store.list_scope_topics = AsyncMock(return_value=[])
    store.select_full_candidates = AsyncMock(return_value=[])
    store.select_bm25_candidates = AsyncMock(return_value=[])
    store.select_recent_frequent_candidates = AsyncMock(return_value=[])
    # P0-2：规模分桶改从 catalog 聚合取 scope topic 计数
    store.count_scope_topics = AsyncMock(return_value=1)
    # catalog_state 默认 ready，避免走 degraded 降级分支
    mock_db = AsyncMock()
    cursor = AsyncMock()
    cursor.fetchone = AsyncMock(return_value=("ready", 1))
    mock_db.execute = AsyncMock(return_value=cursor)
    store.db_connection = mock_db
    return store


@pytest.fixture
def mock_text_processor():
    """模拟 TextProcessor。"""
    processor = MagicMock(spec=TextProcessor)
    processor.tokenize = MagicMock(return_value=["测试", "话题"])
    # build_topic_fts_query 使用异步 tokenize_async 构造 FTS query
    processor.tokenize_async = AsyncMock(return_value=["测试", "话题"])
    return processor


@pytest.fixture
def mock_conversation_formatter():
    """模拟 ConversationFormatter。"""
    formatter = MagicMock(spec=ConversationFormatter)
    formatter.format_conversation = MagicMock(return_value="测试对话内容")
    return formatter


@pytest.fixture
def selector(mock_catalog_store, mock_text_processor, mock_conversation_formatter):
    """创建 TopicCandidateSelector 实例。"""
    return TopicCandidateSelector(
        catalog_store=mock_catalog_store,
        text_processor=mock_text_processor,
        conversation_formatter=mock_conversation_formatter,
    )


@pytest.fixture
def sample_window():
    """创建示例 SourceWindow。"""
    messages = (
        Message(
            id=1,
            session_id="session1",
            role="user",
            sender_id="user1",
            content="你好",
            timestamp=1.0,
        ),
        Message(
            id=2,
            session_id="session1",
            role="assistant",
            sender_id="bot",
            content="你好！",
            timestamp=2.0,
        ),
    )
    seqs = (1, 2)
    return SourceWindow(
        session_id="session1",
        start_seq=0,
        end_seq=2,
        expected_count=2,
        source_digest=source_window_digest(messages, seqs),
        messages=messages,
        message_seqs=seqs,
    )


@pytest.fixture
def sample_context():
    """创建示例 TopicCandidateContext（含完整 provenance）。"""
    return TopicCandidateContext(
        scope_key="test_scope",
        privacy_level="public",
        chat_type="private",
        resolver_revision="v1",
        scope_reason_code="scope_resolved",
        source_provenance_complete=True,
    )


@pytest.fixture
def default_config():
    """创建默认配置。"""
    return CandidateReuseConfig(
        mode="observe",
        activation_threshold=32,
        fixed_k=8,
        max_full_topics=32,
        max_full_prompt_tokens=256,
        max_query_chars=2000,
        overfetch_factor=3,
        metrics_retention_days=30,
    )


class TestTopicCandidateSelectorModes:
    """测试四种模式的基本行为。"""

    @pytest.mark.asyncio
    async def test_off_mode_returns_baseline(
        self, selector, sample_window, sample_context
    ):
        """off 模式返回 baseline 空候选。"""
        config = CandidateReuseConfig(
            mode="off",
            activation_threshold=32,
            fixed_k=8,
            max_full_topics=32,
            max_full_prompt_tokens=256,
            max_query_chars=2000,
            overfetch_factor=3,
            metrics_retention_days=30,
        )

        result = await selector.select_candidates(sample_window, sample_context, config)

        assert result.labels == ()
        assert result.mode == "off"
        assert result.effective_mode == TopicCandidateMode.OFF
        assert result.reason_code == "mode_off"
        assert result.candidate_count == 0

    @pytest.mark.asyncio
    async def test_scope_unavailable_returns_baseline(
        self, selector, sample_window, default_config
    ):
        """scope 不可用时返回 baseline。"""
        # scope 三元组必须整体缺失，部分缺失会触发 scope_snapshot_incomplete
        bad_context = TopicCandidateContext(
            scope_key="",
            privacy_level=None,
            chat_type=None,
            resolver_revision="",
            scope_reason_code="scope_unavailable",
        )

        result = await selector.select_candidates(
            sample_window, bad_context, default_config
        )

        assert result.labels == ()
        assert result.effective_mode == TopicCandidateMode.OFF
        assert result.reason_code == "scope_unavailable"

    @pytest.mark.asyncio
    async def test_observe_mode_forces_off_effective(
        self,
        selector,
        sample_window,
        sample_context,
        default_config,
        mock_catalog_store,
    ):
        """observe 模式强制 effective_mode 为 OFF。"""
        mock_catalog_store.select_full_candidates.return_value = [
            {
                "display_topic": "测试话题",
                "active_source_count": 5,
                "last_seen_at": 100.0,
            }
        ]

        result = await selector.select_candidates(
            sample_window, sample_context, default_config
        )

        assert result.mode == TopicCandidateMode.OBSERVE
        assert result.effective_mode == TopicCandidateMode.OFF
        assert result.source_provenance_complete is False
        assert "observe_shadow" in result.reason_code

    @pytest.mark.asyncio
    async def test_full_mode_returns_all_within_budget(
        self, selector, sample_window, sample_context, mock_catalog_store
    ):
        """full 模式在预算内返回全部候选。"""
        mock_catalog_store.select_full_candidates.return_value = [
            {"display_topic": "话题1", "active_source_count": 5, "last_seen_at": 100.0},
            {"display_topic": "话题2", "active_source_count": 3, "last_seen_at": 90.0},
        ]

        config = CandidateReuseConfig(
            mode="full",
            activation_threshold=10,
            fixed_k=8,
            max_full_topics=10,
            max_full_prompt_tokens=256,
            max_query_chars=2000,
            overfetch_factor=3,
            metrics_retention_days=30,
        )

        result = await selector.select_candidates(sample_window, sample_context, config)

        assert result.mode == TopicCandidateMode.FULL
        assert result.effective_mode == TopicCandidateMode.FULL
        assert result.labels == ("话题1", "话题2")
        assert result.candidate_count == 2
        assert result.topic_count_bucket == "1-8"
        assert result.source_provenance_complete is True

    @pytest.mark.asyncio
    async def test_top_k_mode_small_scope_uses_full(
        self, selector, sample_window, sample_context, mock_catalog_store
    ):
        """top_k 模式在小规模时使用有界 full。"""
        # 返回少于 activation_threshold 的候选
        mock_catalog_store.select_full_candidates.return_value = [
            {
                "display_topic": f"话题{i}",
                "active_source_count": 5,
                "last_seen_at": 100.0,
            }
            for i in range(5)
        ]

        config = CandidateReuseConfig(
            mode="top_k",
            activation_threshold=10,
            fixed_k=8,
            max_full_topics=32,
            max_full_prompt_tokens=256,
            max_query_chars=2000,
            overfetch_factor=3,
            metrics_retention_days=30,
        )

        result = await selector.select_candidates(sample_window, sample_context, config)

        assert result.effective_mode == TopicCandidateMode.FULL
        assert result.candidate_count == 5


class TestBudgetEnforcement:
    """测试数量和 token 双预算。"""

    @pytest.mark.asyncio
    async def test_count_budget_exceeded_returns_baseline(
        self, selector, sample_window, sample_context, mock_catalog_store
    ):
        """超过数量上限返回 baseline。"""
        mock_catalog_store.select_full_candidates.return_value = [
            {
                "display_topic": f"话题{i}",
                "active_source_count": 5,
                "last_seen_at": 100.0,
            }
            for i in range(50)
        ]

        config = CandidateReuseConfig(
            mode="full",
            activation_threshold=10,
            fixed_k=8,
            max_full_topics=10,
            max_full_prompt_tokens=1000,
            max_query_chars=2000,
            overfetch_factor=3,
            metrics_retention_days=30,
        )

        result = await selector.select_candidates(sample_window, sample_context, config)

        assert result.labels == ()
        assert result.budget_reason == "count_exceeded"
        assert result.reason_code == "count_budget_exceeded"

    @pytest.mark.asyncio
    async def test_token_budget_exceeded_returns_baseline(
        self, selector, sample_window, sample_context, mock_catalog_store
    ):
        """超过 token 上限返回 baseline。"""
        # 创建非常长的 topic 来超过 token 预算
        long_topics = [
            {
                "display_topic": "这是一个非常长的话题名称" * 20,
                "active_source_count": 5,
                "last_seen_at": 100.0,
            }
            for _ in range(5)
        ]
        mock_catalog_store.select_full_candidates.return_value = long_topics

        config = CandidateReuseConfig(
            mode="full",
            activation_threshold=10,
            fixed_k=8,
            max_full_topics=10,
            max_full_prompt_tokens=50,  # 很小的 token 限制
            max_query_chars=2000,
            overfetch_factor=3,
            metrics_retention_days=30,
        )

        result = await selector.select_candidates(sample_window, sample_context, config)

        assert result.labels == ()
        assert result.budget_reason == "token_exceeded"


class TestBM25AndFill:
    """测试 BM25 主排序和近期/高频补足。"""

    @pytest.mark.asyncio
    async def test_bm25_sufficient_no_fill(
        self, selector, sample_window, sample_context, mock_catalog_store
    ):
        """BM25 足够时不使用补足。"""
        # 大规模 scope
        mock_catalog_store.select_full_candidates.return_value = [
            {
                "display_topic": f"话题{i}",
                "active_source_count": 5,
                "last_seen_at": 100.0,
            }
            for i in range(50)
        ]

        # BM25 返回足够的结果
        mock_catalog_store.select_bm25_candidates.return_value = [
            {
                "display_topic": f"相关{i}",
                "active_source_count": 5,
                "last_seen_at": 100.0,
            }
            for i in range(8)
        ]

        config = CandidateReuseConfig(
            mode="top_k",
            activation_threshold=32,
            fixed_k=8,
            max_full_topics=32,
            max_full_prompt_tokens=256,
            max_query_chars=2000,
            overfetch_factor=3,
            metrics_retention_days=30,
        )

        result = await selector.select_candidates(sample_window, sample_context, config)

        assert result.bm25_hit_count == 8
        assert result.recent_fill_count == 0
        assert len(result.labels) == 8

    @pytest.mark.asyncio
    async def test_bm25_insufficient_uses_fill(
        self, selector, sample_window, sample_context, mock_catalog_store
    ):
        """BM25 不足时使用补足。"""
        # 大规模 scope
        mock_catalog_store.select_full_candidates.return_value = [
            {
                "display_topic": f"话题{i}",
                "active_source_count": 5,
                "last_seen_at": 100.0,
            }
            for i in range(50)
        ]

        # BM25 只返回 3 个
        mock_catalog_store.select_bm25_candidates.return_value = [
            {
                "display_topic": f"相关{i}",
                "active_source_count": 5,
                "last_seen_at": 100.0,
            }
            for i in range(3)
        ]

        # 补足返回 5 个
        mock_catalog_store.select_recent_frequent_candidates.return_value = [
            {
                "display_topic": f"补充{i}",
                "active_source_count": 3,
                "last_seen_at": 90.0,
            }
            for i in range(5)
        ]

        config = CandidateReuseConfig(
            mode="top_k",
            activation_threshold=32,
            fixed_k=8,
            max_full_topics=32,
            max_full_prompt_tokens=256,
            max_query_chars=2000,
            overfetch_factor=3,
            metrics_retention_days=30,
        )

        result = await selector.select_candidates(sample_window, sample_context, config)

        assert result.bm25_hit_count == 3
        assert result.recent_fill_count == 5
        assert len(result.labels) == 8

    @pytest.mark.asyncio
    async def test_shortfall_records_reason_when_fill_exhausted(
        self, selector, sample_window, sample_context, mock_catalog_store
    ):
        """补足耗尽仍不足 K 时返回 shortfall reason，不伪装成功。"""
        # 大规模 scope，BM25 与补足合计仍少于 fixed_k
        mock_catalog_store.select_full_candidates.return_value = [
            {
                "display_topic": f"话题{i}",
                "active_source_count": 5,
                "last_seen_at": 100.0,
            }
            for i in range(50)
        ]
        mock_catalog_store.select_bm25_candidates.return_value = [
            {"display_topic": "相关0", "active_source_count": 5, "last_seen_at": 100.0},
            {"display_topic": "相关1", "active_source_count": 5, "last_seen_at": 100.0},
        ]
        # 补足第二批后耗尽（返回空列表终止循环）
        mock_catalog_store.select_recent_frequent_candidates.side_effect = [
            [
                {
                    "display_topic": "补充0",
                    "active_source_count": 3,
                    "last_seen_at": 90.0,
                }
            ],
            [],
        ]

        config = CandidateReuseConfig(
            mode="top_k",
            activation_threshold=32,
            fixed_k=8,
            max_full_topics=32,
            max_full_prompt_tokens=256,
            max_query_chars=2000,
            overfetch_factor=3,
            metrics_retention_days=30,
        )

        result = await selector.select_candidates(sample_window, sample_context, config)

        assert 0 < len(result.labels) < 8
        assert result.reason_code == "candidate_shortfall"
        assert result.effective_mode == TopicCandidateMode.TOP_K


class TestDeduplication:
    """测试规范键去重。"""

    @pytest.mark.asyncio
    async def test_deduplicates_by_nfkc_key(
        self, selector, sample_window, sample_context, mock_catalog_store
    ):
        """按 NFKC 规范键去重。"""
        mock_catalog_store.select_full_candidates.return_value = [
            {
                "display_topic": "Python编程",
                "active_source_count": 5,
                "last_seen_at": 100.0,
            },
            {
                "display_topic": "python编程",
                "active_source_count": 3,
                "last_seen_at": 90.0,
            },
            {
                "display_topic": "  Python编程  ",
                "active_source_count": 2,
                "last_seen_at": 80.0,
            },
        ]

        config = CandidateReuseConfig(
            mode="full",
            activation_threshold=32,
            fixed_k=8,
            max_full_topics=32,
            max_full_prompt_tokens=256,
            max_query_chars=2000,
            overfetch_factor=3,
            metrics_retention_days=30,
        )

        result = await selector.select_candidates(sample_window, sample_context, config)

        # 应该只保留第一个
        assert len(result.labels) == 1
        assert result.labels[0] == "Python编程"


class TestFailureDegradation:
    """测试失败降级。"""

    @pytest.mark.asyncio
    async def test_exception_returns_baseline(
        self,
        selector,
        sample_window,
        sample_context,
        default_config,
        mock_catalog_store,
    ):
        """异常时返回 baseline 并记录。"""
        mock_catalog_store.select_full_candidates.side_effect = RuntimeError("测试异常")

        result = await selector.select_candidates(
            sample_window, sample_context, default_config
        )

        assert result.labels == ()
        assert result.effective_mode == TopicCandidateMode.OFF
        assert result.reason_code == "selector_failed"

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(
        self,
        selector,
        sample_window,
        sample_context,
        default_config,
        mock_catalog_store,
    ):
        """CancelledError 必须传播。"""
        import asyncio

        mock_catalog_store.select_full_candidates.side_effect = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await selector.select_candidates(
                sample_window, sample_context, default_config
            )


class TestTopicCountBucketing:
    """通过公开 selection 字段覆盖 topic 数量桶。"""

    @pytest.mark.asyncio
    async def test_empty_full_result_uses_zero_bucket(
        self, selector, sample_window, sample_context, mock_catalog_store
    ):
        """0 个 topic 映射到 0 桶。"""
        mock_catalog_store.select_full_candidates.return_value = []
        config = CandidateReuseConfig(
            mode="full",
            activation_threshold=32,
            fixed_k=8,
            max_full_topics=32,
            max_full_prompt_tokens=256,
            max_query_chars=2000,
            overfetch_factor=3,
            metrics_retention_days=30,
        )
        result = await selector.select_candidates(sample_window, sample_context, config)
        assert result.topic_count_bucket == "0"

    @pytest.mark.asyncio
    async def test_nine_topics_use_9_to_16_bucket(
        self, selector, sample_window, sample_context, mock_catalog_store
    ):
        """9 个 topic 映射到 9-16 桶。"""
        mock_catalog_store.select_full_candidates.return_value = [
            {
                "display_topic": f"话题{index}",
                "active_source_count": 5,
                "last_seen_at": 100.0,
            }
            for index in range(9)
        ]
        config = CandidateReuseConfig(
            mode="full",
            activation_threshold=32,
            fixed_k=8,
            max_full_topics=32,
            max_full_prompt_tokens=256,
            max_query_chars=2000,
            overfetch_factor=3,
            metrics_retention_days=30,
        )
        result = await selector.select_candidates(sample_window, sample_context, config)
        assert result.topic_count_bucket == "9-16"
        assert result.candidate_count == 9
