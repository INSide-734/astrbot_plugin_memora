"""测试隐私门禁：拒绝无 source_provenance_complete 的候选。"""

from __future__ import annotations

import pytest

from core.features.recall.processors.memory_processor_candidate_mixin import (
    MemoryProcessorCandidateMixin,
)
from core.features.reflection.domain.summary_models import (
    TopicCandidateMode,
    TopicCandidateSelection,
)


@pytest.mark.asyncio
async def test_privacy_gate_rejects_incomplete_provenance() -> None:
    """隐私门禁：拒绝 source_provenance_complete=False 的候选。"""
    base_prompt = "这是基础 prompt"

    # 模拟有标签但 provenance 不完整的选择结果
    incomplete_selection = TopicCandidateSelection(
        labels=("测试标签1", "测试标签2"),
        source_provenance_complete=False,  # 不完整
        mode=TopicCandidateMode.TOP_K,
        effective_mode=TopicCandidateMode.TOP_K,
        catalog_status="ready",
        candidate_count=2,
    )

    mixin = MemoryProcessorCandidateMixin()
    result = mixin._inject_topic_candidates(base_prompt, incomplete_selection)

    # 必须降级为 baseline
    assert result == base_prompt
    # 不能包含标签
    assert "测试标签1" not in result
    assert "测试标签2" not in result


@pytest.mark.asyncio
async def test_privacy_gate_accepts_complete_provenance() -> None:
    """隐私门禁：接受 source_provenance_complete=True 的候选。"""
    base_prompt = "这是基础 prompt"

    complete_selection = TopicCandidateSelection(
        labels=("测试标签1", "测试标签2"),
        source_provenance_complete=True,  # 完整
        mode=TopicCandidateMode.TOP_K,
        effective_mode=TopicCandidateMode.TOP_K,
        catalog_status="ready",
        candidate_count=2,
    )

    mixin = MemoryProcessorCandidateMixin()
    result = mixin._inject_topic_candidates(base_prompt, complete_selection)

    # 必须包含候选
    assert result != base_prompt
    assert base_prompt in result
    assert "测试标签1" in result
    assert "测试标签2" in result


@pytest.mark.asyncio
async def test_privacy_gate_rejects_none_provenance() -> None:
    """隐私门禁：拒绝 source_provenance_complete=None 的候选。"""
    base_prompt = "这是基础 prompt"

    # 模拟 provenance 缺失的选择结果
    none_selection = TopicCandidateSelection(
        labels=("测试标签1", "测试标签2"),
        source_provenance_complete=None,  # 缺失
        mode=TopicCandidateMode.TOP_K,
        effective_mode=TopicCandidateMode.TOP_K,
        catalog_status="ready",
        candidate_count=2,
    )

    mixin = MemoryProcessorCandidateMixin()
    result = mixin._inject_topic_candidates(base_prompt, none_selection)

    # 必须降级为 baseline
    assert result == base_prompt
    assert "测试标签1" not in result
    assert "测试标签2" not in result
