"""验证黑话推断的渐进调度与并发去重。"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.jargon.jargon_miner import JargonMiner
from core.jargon.jargon_store import JargonStore
from core.jargon.models import JargonCandidate


def _make_candidate(frequency: int) -> JargonCandidate:
    """构造指定出现频次的确定性黑话候选。

    参数:
        frequency: 候选词在群内累计出现的次数。

    返回:
        用于调度行为测试的候选词。
    """
    return JargonCandidate(
        term="yyds",
        group_id="group-test",
        score=0.75,
        frequency=frequency,
        unique_users=2,
        idf_score=0.7,
        burst_score=0.6,
        concentration_score=0.8,
        first_seen=time.time() - 60,
        context_examples=["这个游戏 yyds 太好玩了"],
    )


def _make_available_llm() -> MagicMock:
    """构造满足可用性检测条件、不会访问真实模型的 LLM 替身。"""
    llm = MagicMock()
    llm.get_current_llm_provider.return_value = object()
    return llm


@pytest.mark.asyncio
async def test_run_once_only_retries_after_next_threshold(tmp_path) -> None:
    """同一频次不重复推断，达到下一渐进阈值后才重新推断。"""
    store = JargonStore(str(tmp_path / "jargon.db"))
    await store.initialize()
    try:
        candidate = _make_candidate(3)
        stats = MagicMock()
        stats.get_candidates.return_value = [candidate]
        miner = JargonMiner(_make_available_llm(), stats, store)
        first_meaning = miner._build_meaning(
            candidate, "永远的神", is_jargon=True, confidence=0.8
        )
        candidate.frequency = 6
        second_meaning = miner._build_meaning(
            candidate, "永远的神", is_jargon=True, confidence=0.8
        )
        candidate.frequency = 3
        miner.infer_meaning = AsyncMock(side_effect=[first_meaning, second_meaning])

        assert await miner.run_once("group-test", limit=1) == [first_meaning]
        assert await miner.run_once("group-test", limit=1) == []
        miner.infer_meaning.assert_awaited_once()

        candidate.frequency = 6
        assert await miner.run_once("group-test", limit=1) == [second_meaning]
        assert miner.infer_meaning.await_count == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_infer_and_store_skips_an_inflight_candidate() -> None:
    """同一群词的未完成推断只能由一个任务执行。"""
    candidate = _make_candidate(3)
    first_lookup_started = asyncio.Event()
    allow_first_lookup = asyncio.Event()
    lookup_count = 0

    async def get_by_term(_term: str, _group_id: str):
        """阻塞首个读取，使第二个任务可在首个任务执行期间进入。"""
        nonlocal lookup_count
        lookup_count += 1
        if lookup_count == 1:
            first_lookup_started.set()
            await allow_first_lookup.wait()
        return None

    store = MagicMock()
    store.get_by_term = AsyncMock(side_effect=get_by_term)
    store.upsert = AsyncMock()
    miner = JargonMiner(_make_available_llm(), MagicMock(), store)
    meaning = miner._build_meaning(
        candidate, "永远的神", is_jargon=True, confidence=0.8
    )
    miner.infer_meaning = AsyncMock(return_value=meaning)

    first_task = asyncio.create_task(miner._infer_and_store(candidate))
    await first_lookup_started.wait()
    try:
        assert await miner._infer_and_store(candidate) is None
        assert lookup_count == 1
    finally:
        allow_first_lookup.set()
        first_result = await first_task

    assert first_result == meaning
    miner.infer_meaning.assert_awaited_once_with(candidate)
