"""SemanticCompressor 的纯聚类、摘要与构造门测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.managers.semantic_compressor import (
    SemanticCompressor,
    _cluster_sources,
    _eligible_source,
    _partition_sources,
    _synthesize_abstract,
    _topic_similarity,
)
from core.models.memory_evolution import MemorySourceRef

UTC = timezone.utc
NOW = datetime(2026, 8, 1, tzinfo=UTC)


def _source(
    memory_id: int,
    *,
    topics: tuple[str, ...] = ("python", "ai"),
    scope: str = "private:user-a",
    privacy: str = "shared",
    age_days: int = 90,
    content: str | None = None,
) -> MemorySourceRef:
    """构造压缩器纯函数使用的 canonical 来源。"""

    occurred_at = NOW - timedelta(days=age_days)
    return MemorySourceRef(
        memory_id=memory_id,
        revision_token=f"rev-{memory_id}",
        scope_key=scope,
        privacy_level=privacy,
        occurred_at=occurred_at,
        content=content or f"正文 {memory_id}",
        ingested_at=occurred_at,
        topic_keys=topics,
    )


def test_constructor_clamps_age_and_similarity() -> None:
    """构造参数应钳制到受支持的最小年龄和相似度范围。"""

    low = SemanticCompressor(age_days=10, similarity_threshold=0.2)
    high = SemanticCompressor(age_days=120, similarity_threshold=1.5)

    assert low._age_days == 30.0
    assert low._sim_threshold == 0.7
    assert high._age_days == 120.0
    assert high._sim_threshold == 0.98


@pytest.mark.asyncio
async def test_unwired_compressor_is_safe_noop() -> None:
    """未装配 Store 或 proposal 写入器时不得产生任何 canonical mutation。"""

    result = await SemanticCompressor().compress_old_memories(now=NOW)

    assert result == {
        "candidate_groups": 0,
        "projections_applied": 0,
        "failed_groups": 0,
        "canonical_mutations": 0,
    }


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        (frozenset(), frozenset({"a"}), 0.0),
        (frozenset({"a"}), frozenset({"a"}), 1.0),
        (frozenset({"a", "b"}), frozenset({"b", "c"}), 1 / 3),
    ],
)
def test_topic_similarity_uses_jaccard(
    first: frozenset[str],
    second: frozenset[str],
    expected: float,
) -> None:
    """topic 相似度应使用交集除以并集。"""

    assert _topic_similarity(first, second) == pytest.approx(expected)


def test_eligible_source_uses_ingested_age_content_topics_and_role() -> None:
    """年龄、正文、topic 与合法 role 应共同决定候选资格。"""

    cutoff = NOW - timedelta(days=60)
    assert _eligible_source(_source(17), cutoff) is True
    assert _eligible_source(_source(18, age_days=10), cutoff) is False
    assert _eligible_source(_source(19, topics=()), cutoff) is False
    empty = _source(20, content="正文")
    object.__setattr__(empty, "content", "")
    assert _eligible_source(empty, cutoff) is False


def test_partition_sources_keeps_scope_privacy_role_and_subject_separate() -> None:
    """分区键必须包含 scope、privacy、source role 和机密主体。"""

    primary = _source(17)
    supporting = _source(18)
    object.__setattr__(supporting, "source_role", "supporting")
    partitions = _partition_sources(
        [
            primary,
            supporting,
            _source(19, scope="private:user-b"),
            _source(20, privacy="confidential"),
        ]
    )

    assert set(partitions) == {
        ("private:user-a", "shared", "primary", None),
        ("private:user-a", "shared", "supporting", None),
        ("private:user-b", "shared", "primary", None),
        ("private:user-a", "confidential", "primary", None),
    }


def test_cluster_sources_is_stable_casefolded_and_threshold_driven() -> None:
    """聚类应按 ID 稳定排序、忽略大小写并消费配置阈值。"""

    sources = [
        _source(19, topics=("unrelated",)),
        _source(18, topics=("python", "AI", "memory")),
        _source(17, topics=("Python", "ai")),
    ]

    clusters = _cluster_sources(sources, threshold=0.6)

    assert [[item.memory_id for item in cluster] for cluster in clusters] == [[17, 18]]
    assert _cluster_sources(sources, threshold=0.8) == []


def test_synthesize_abstract_is_deterministic_and_bounded() -> None:
    """摘要只吸收有限短补充，并限制模型可见长度。"""

    base = "主记忆" * 120
    summary = _synthesize_abstract(
        [base, "短补充", "第二补充", "不得进入摘要的第三补充"]
    )

    assert summary.startswith("主记忆")
    assert "短补充" in summary
    assert "第二补充" in summary
    assert "第三补充" not in summary
    assert len(summary) <= 600


def test_synthesize_abstract_handles_empty_input() -> None:
    """空白来源不能形成 Projection 摘要。"""

    assert _synthesize_abstract([]) == ""
    assert _synthesize_abstract(["", "   "]) == ""
