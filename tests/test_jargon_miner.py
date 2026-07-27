"""Tests for Jargon LLM 三步推断引擎。

覆盖：
- JargonMiner: 三步推断、渐进阈值、is_complete 标记
- JargonQueryService: 缓存命中/未命中、check_and_explain
- JargonStore: CRUD 操作
- JargonMeaning: 数据模型构造
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.jargon.jargon_miner import (
    INFERENCE_THRESHOLDS,
    JargonMiner,
    _extract_meaning_str,
    _safe_parse_json,
)
from core.jargon.jargon_query import JargonQueryService, TTLCache
from core.jargon.jargon_store import JargonStore
from core.jargon.models import JargonCandidate, JargonMeaning
from core.jargon.statistical_filter import JargonStatisticalFilter

# ============================================================================
# 数据模型测试
# ============================================================================


class TestJargonMeaning:
    """JargonMeaning 数据模型测试。"""

    def test_default_construction(self) -> None:
        """测试默认构造和使用默认字段值。"""
        jm = JargonMeaning(term="yyds", group_id="group-1")
        assert jm.term == "yyds"
        assert jm.group_id == "group-1"
        assert jm.meaning == ""
        assert jm.confidence == 0.0
        assert jm.is_jargon is False
        assert jm.is_confirmed is False
        assert jm.is_global is False
        assert jm.is_complete is False
        assert jm.count == 0
        assert jm.last_inference_count == 0
        assert jm.context_examples == []
        assert jm.created_at == 0.0
        assert jm.updated_at == 0.0

    def test_full_construction(self) -> None:
        """测试完整字段构造。"""
        now = time.time()
        jm = JargonMeaning(
            term="xswl",
            group_id="group-2",
            meaning="笑死我了",
            confidence=0.85,
            is_jargon=True,
            is_confirmed=True,
            is_global=False,
            is_complete=True,
            count=120,
            last_inference_count=100,
            context_examples=["xswl 哈哈哈哈", "这个真的 xswl"],
            created_at=now - 1000,
            updated_at=now,
        )
        assert jm.term == "xswl"
        assert jm.meaning == "笑死我了"
        assert jm.confidence == 0.85
        assert jm.is_jargon is True
        assert jm.is_complete is True


# ============================================================================
# _safe_parse_json 单元测试
# ============================================================================


class TestSafeParseJson:
    """_safe_parse_json 单元测试。"""

    def test_plain_json_object(self) -> None:
        """测试纯 JSON 对象字符串。"""
        result = _safe_parse_json('{"meaning": "test", "no_info": false}')
        assert result == {"meaning": "test", "no_info": False}

    def test_json_with_markdown_fence(self) -> None:
        """测试 markdown 代码块包裹的 JSON。"""
        text = '```json\n{"meaning": "hello"}\n```'
        result = _safe_parse_json(text)
        assert result == {"meaning": "hello"}

    def test_json_with_leading_text(self) -> None:
        """测试前导文本。"""
        text = 'Here is my analysis: {"meaning": "world"}\nSome trailing text'
        result = _safe_parse_json(text)
        assert result == {"meaning": "world"}

    def test_json_with_nested_braces(self) -> None:
        """测试嵌套大括号。"""
        text = '{"meaning": {"nested": "deep", "count": 5}, "no_info": false}'
        result = _safe_parse_json(text)
        assert result == {
            "meaning": {"nested": "deep", "count": 5},
            "no_info": False,
        }

    def test_empty_input(self) -> None:
        """测试空输入。"""
        assert _safe_parse_json("") is None
        assert _safe_parse_json("   ") is None

    def test_invalid_json(self) -> None:
        """测试无效 JSON。"""
        result = _safe_parse_json("not json at all")
        assert result is None

    def test_boolean_and_null_values(self) -> None:
        """测试布尔值 null 值。"""
        text = '{"no_info": true, "meaning": null}'
        result = _safe_parse_json(text)
        assert result == {"no_info": True, "meaning": None}


# ============================================================================
# _extract_meaning_str 单元测试
# ============================================================================


class TestExtractMeaningStr:
    """_extract_meaning_str 单元测试。"""

    def test_plain_string_meaning(self) -> None:
        """测试普通字符串 meaning。"""
        assert _extract_meaning_str({"meaning": "一种表达"}) == "一种表达"

    def test_dict_meaning_to_json(self) -> None:
        """测试 dict 类型 meaning 转 JSON 字符串。"""
        data = {"meaning": {"detail": "详细含义", "source": "网络用语"}}
        result = _extract_meaning_str(data)
        assert "detail" in result
        assert "网络用语" in result

    def test_list_meaning_to_json(self) -> None:
        """测试 list 类型 meaning 转 JSON 字符串。"""
        data = {"meaning": ["含义1", "含义2"]}
        result = _extract_meaning_str(data)
        parsed = json.loads(result)
        assert parsed == ["含义1", "含义2"]

    def test_none_meaning(self) -> None:
        """测试 None 含义。"""
        assert _extract_meaning_str({"meaning": None}) == ""

    def test_missing_meaning_key(self) -> None:
        """测试缺少 meaning 键。"""
        assert _extract_meaning_str({}) == ""


# ============================================================================
# TTLCache 单元测试
# ============================================================================


class TestTTLCache:
    """TTLCache 单元测试。"""

    def test_set_and_get(self) -> None:
        """测试基本 set/get。"""
        cache = TTLCache(maxsize=10, ttl=60)
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_expired_entry(self) -> None:
        """测试过期条目。"""
        cache = TTLCache(maxsize=10, ttl=0)  # 立即过期
        cache.set("key1", "value1")
        time.sleep(0.02)
        assert cache.get("key1") is None

    def test_cache_miss(self) -> None:
        """测试缓存未命中。"""
        cache = TTLCache(maxsize=10, ttl=60)
        assert cache.get("nonexistent") is None

    def test_maxsize_eviction(self) -> None:
        """测试 maxsize 满时逐出最旧条目。"""
        cache = TTLCache(maxsize=2, ttl=600)
        cache.set("key1", "v1")
        cache.set("key2", "v2")
        cache.set("key3", "v3")  # 应逐出 key1
        assert cache.get("key1") is None
        assert cache.get("key2") == "v2"
        assert cache.get("key3") == "v3"

    def test_overwrite_same_key(self) -> None:
        """测试覆盖同 key。"""
        cache = TTLCache(maxsize=10, ttl=600)
        cache.set("key1", "v1")
        cache.set("key1", "v2")
        assert cache.get("key1") == "v2"

    def test_clear(self) -> None:
        """测试清除所有缓存。"""
        cache = TTLCache(maxsize=10, ttl=600)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None


# ============================================================================
# helpers
# ============================================================================


async def _init_store() -> JargonStore:
    """Create a transient JargonStore backed by a temp file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = JargonStore(path)
    await s.initialize()
    # attach path so caller can clean up later
    s._tmp_path = path  # type: ignore[attr-defined]
    return s


async def _close_store(s: JargonStore) -> None:
    """Close the store and delete its temp file."""
    path: str | None = getattr(s, "_tmp_path", None)  # type: ignore[attr-defined]
    await s.close()
    if path and os.path.exists(path):
        os.unlink(path)


def _make_meaning(
    term: str,
    group_id: str = "group-test",
    meaning: str = "",
    is_jargon: bool = False,
    is_confirmed: bool = False,
    count: int = 0,
) -> JargonMeaning:
    return JargonMeaning(
        term=term,
        group_id=group_id,
        meaning=meaning,
        is_jargon=is_jargon,
        is_confirmed=is_confirmed,
        count=count,
    )


def _make_candidate(term: str, freq: int = 10) -> JargonCandidate:
    """Create a test JargonCandidate."""
    return JargonCandidate(
        term=term,
        group_id="group-test",
        score=0.75,
        frequency=freq,
        unique_users=2,
        idf_score=0.7,
        burst_score=0.6,
        concentration_score=0.8,
        first_seen=time.time() - 7200,
        context_examples=[f"上下文示例: {term} 相关消息", f"另一条 {term} 消息"],
    )


# ============================================================================
# JargonStore 测试
# ============================================================================


@pytest.mark.asyncio
class TestJargonStore:
    """JargonStore CRUD 操作测试。"""

    async def test_upsert_and_get(self) -> None:
        """测试 upsert + get_by_term。"""
        store = await _init_store()
        try:
            sm = _make_meaning(
                "yyds", meaning="永远的神", is_jargon=True, is_confirmed=True, count=50
            )
            await store.upsert(sm)
            result = await store.get_by_term("yyds", "group-test")
            assert result is not None
            assert result.term == "yyds"
            assert result.meaning == "永远的神"
            assert result.is_jargon is True
        finally:
            await _close_store(store)

    async def test_get_by_term_not_found(self) -> None:
        """测试查询不存在的词。"""
        store = await _init_store()
        try:
            result = await store.get_by_term("nonexistent", "group-test")
            assert result is None
        finally:
            await _close_store(store)

    async def test_upsert_update_existing(self) -> None:
        """测试 upsert 更新已存在条目。"""
        store = await _init_store()
        try:
            sm = _make_meaning(
                "yyds", meaning="永远的神", is_jargon=True, is_confirmed=True, count=50
            )
            await store.upsert(sm)

            sm.meaning = "永远的的神"
            sm.confidence = 0.95
            await store.upsert(sm)

            result = await store.get_by_term("yyds", "group-test")
            assert result is not None
            assert result.meaning == "永远的的神"
            assert result.confidence == 0.95
        finally:
            await _close_store(store)

    async def test_list_by_group(self) -> None:
        """测试按群组列出所有黑话。"""
        store = await _init_store()
        try:
            await store.upsert(
                _make_meaning(
                    "yyds",
                    meaning="永远的神",
                    is_jargon=True,
                    is_confirmed=True,
                    count=50,
                )
            )
            await store.upsert(
                _make_meaning(
                    "xswl",
                    meaning="笑死我了",
                    is_jargon=True,
                    is_confirmed=True,
                    count=30,
                )
            )
            results = await store.list_by_group("group-test", confirmed_only=True)
            assert len(results) == 2
            terms = {r.term for r in results}
            assert terms == {"yyds", "xswl"}
        finally:
            await _close_store(store)

    async def test_list_by_group_empty(self) -> None:
        """测试空群组。"""
        store = await _init_store()
        try:
            results = await store.list_by_group("empty-group", confirmed_only=True)
            assert results == []
        finally:
            await _close_store(store)

    async def test_search_by_keyword(self) -> None:
        """测试模糊关键词搜索。"""
        store = await _init_store()
        try:
            await store.upsert(
                _make_meaning(
                    "yyds",
                    meaning="永远的神",
                    is_jargon=True,
                    is_confirmed=True,
                    count=50,
                )
            )
            await store.upsert(
                _make_meaning(
                    "yyds2",
                    meaning="永远的神 v2",
                    is_jargon=True,
                    is_confirmed=True,
                    count=10,
                )
            )
            await store.upsert(
                _make_meaning(
                    "xswl",
                    meaning="笑死我了",
                    is_jargon=True,
                    is_confirmed=True,
                    count=20,
                )
            )
            results = await store.search("yyds", "group-test")
            assert len(results) >= 1
            terms = {r.term for r in results}
            assert "yyds" in terms or "yyds2" in terms
        finally:
            await _close_store(store)

    async def test_search_no_match(self) -> None:
        """测试搜索无匹配。"""
        store = await _init_store()
        try:
            await store.upsert(
                _make_meaning(
                    "yyds",
                    meaning="永远的神",
                    is_jargon=True,
                    is_confirmed=True,
                    count=50,
                )
            )
            results = await store.search("zzzz", "group-test")
            assert results == []
        finally:
            await _close_store(store)

    async def test_confirm(self) -> None:
        """测试手动确认/取消确认。"""
        store = await _init_store()
        try:
            sm = _make_meaning(
                "yyds", meaning="永远的神", is_jargon=True, is_confirmed=True, count=50
            )
            await store.upsert(sm)

            result = await store.get_by_term("yyds", "group-test")
            assert result is not None
            assert result.is_confirmed is True

            await store.confirm("yyds", "group-test", confirmed=False)
            result = await store.get_by_term("yyds", "group-test")
            assert result is not None
            assert result.is_confirmed is False

            await store.confirm("yyds", "group-test", confirmed=True)
            result = await store.get_by_term("yyds", "group-test")
            assert result is not None
            assert result.is_confirmed is True
        finally:
            await _close_store(store)

    async def test_delete(self) -> None:
        """测试删除黑话条目。"""
        store = await _init_store()
        try:
            sm = _make_meaning(
                "yyds", meaning="永远的神", is_jargon=True, is_confirmed=True, count=50
            )
            await store.upsert(sm)
            result = await store.get_by_term("yyds", "group-test")
            assert result is not None

            await store.delete("yyds", "group-test")
            result = await store.get_by_term("yyds", "group-test")
            assert result is None
        finally:
            await _close_store(store)

    async def test_count_by_group(self) -> None:
        """测试群组计数。"""
        store = await _init_store()
        try:
            await store.upsert(
                _make_meaning(
                    "yyds",
                    meaning="永远的神",
                    is_jargon=True,
                    is_confirmed=True,
                    count=50,
                )
            )
            count = await store.count_by_group("group-test")
            assert count == 1
        finally:
            await _close_store(store)


# ============================================================================
# JargonMiner 三步推断测试
# ============================================================================


@pytest.mark.asyncio
class TestJargonMiner:
    """JargonMiner 三步推断引擎测试。"""

    @staticmethod
    def _make_mock_llm() -> MagicMock:
        """Create a mock LLM client."""
        llm = MagicMock()
        llm.call_llm_with_retry = AsyncMock()
        llm.get_current_llm_provider = MagicMock(return_value=True)
        return llm

    @staticmethod
    def _make_mock_stats() -> "JargonStatisticalFilter":
        """Create a JargonStatisticalFilter with pre-populated data."""
        from core.jargon.statistical_filter import JargonStatisticalFilter

        sf = JargonStatisticalFilter()
        for _ in range(10):
            sf.update("这个游戏 yyds 太好玩了", "group-test", "user1")
            sf.update("yyds 就是他没错了", "group-test", "user2")
        for _ in range(5):
            sf.update("xswl 哈哈", "group-test", "user1")
        return sf

    # -- 三步推断测试 --

    async def test_step1_no_info_abandons(self) -> None:
        """测试 Step 1 返回 no_info=true → 放弃推断。"""
        mock_llm = self._make_mock_llm()
        mock_llm.call_llm_with_retry.return_value = json.dumps(
            {"no_info": True, "meaning": ""}
        )
        mock_stats = self._make_mock_stats()
        store = await _init_store()
        try:
            miner = JargonMiner(mock_llm, mock_stats, store)
            candidate = _make_candidate("yyds", freq=10)
            result = await miner.infer_meaning(candidate)
            assert result is None
            mock_llm.call_llm_with_retry.assert_called_once()
        finally:
            await _close_store(store)

    async def test_step1_empty_meaning_abandons(self) -> None:
        """测试 Step 1 meaning 为空 → 放弃推断。"""
        mock_llm = self._make_mock_llm()
        mock_llm.call_llm_with_retry.return_value = json.dumps(
            {"no_info": False, "meaning": ""}
        )
        mock_stats = self._make_mock_stats()
        store = await _init_store()
        try:
            miner = JargonMiner(mock_llm, mock_stats, store)
            candidate = _make_candidate("yyds", freq=10)
            result = await miner.infer_meaning(candidate)
            assert result is None
        finally:
            await _close_store(store)

    async def test_different_meanings_is_jargon(self) -> None:
        """测试不同含义 → is_jargon = True。"""
        mock_llm = self._make_mock_llm()
        responses = [
            json.dumps({"meaning": "永远的神，表示极度崇拜或赞扬", "no_info": False}),
            json.dumps({"meaning": "英文字母缩写 yyds，无明显含义"}),
            json.dumps({"is_similar": False, "reason": "上下文含义与词面含义完全不同"}),
        ]
        mock_llm.call_llm_with_retry.side_effect = responses
        mock_stats = self._make_mock_stats()
        store = await _init_store()
        try:
            miner = JargonMiner(mock_llm, mock_stats, store)
            candidate = _make_candidate("yyds", freq=10)
            result = await miner.infer_meaning(candidate)
            assert result is not None
            assert result.is_jargon is True
            assert "永远的神" in result.meaning
            assert result.confidence > 0
        finally:
            await _close_store(store)

    async def test_same_meanings_not_jargon(self) -> None:
        """测试相同含义 → is_jargon = False。"""
        mock_llm = self._make_mock_llm()
        responses = [
            json.dumps({"meaning": "表示同意或确认", "no_info": False}),
            json.dumps({"meaning": "是的，表示同意或确认"}),
            json.dumps({"is_similar": True, "reason": "两个推断都理解为同意/确认"}),
        ]
        mock_llm.call_llm_with_retry.side_effect = responses
        mock_stats = self._make_mock_stats()
        store = await _init_store()
        try:
            miner = JargonMiner(mock_llm, mock_stats, store)
            candidate = _make_candidate("okok", freq=10)
            result = await miner.infer_meaning(candidate)
            assert result is not None
            assert result.is_jargon is False
        finally:
            await _close_store(store)

    async def test_step2_fails_graceful_degradation(self) -> None:
        """测试 Step 2 失败 → 优雅降级，保守判定为 jargon。"""
        mock_llm = self._make_mock_llm()
        mock_llm.call_llm_with_retry.side_effect = [
            json.dumps({"meaning": "永远的神", "no_info": False}),
            None,
        ]
        mock_stats = self._make_mock_stats()
        store = await _init_store()
        try:
            miner = JargonMiner(mock_llm, mock_stats, store)
            candidate = _make_candidate("yyds", freq=10)
            result = await miner.infer_meaning(candidate)
            assert result is not None
            assert result.is_jargon is True
            assert result.confidence == 0.3
        finally:
            await _close_store(store)

    async def test_step3_fails_conservative_jargon(self) -> None:
        """测试 Step 3 失败 → 保守判定为 jargon。"""
        mock_llm = self._make_mock_llm()
        mock_llm.call_llm_with_retry.side_effect = [
            json.dumps({"meaning": "永远的神", "no_info": False}),
            json.dumps({"meaning": "英文字母缩写"}),
            None,
        ]
        mock_stats = self._make_mock_stats()
        store = await _init_store()
        try:
            miner = JargonMiner(mock_llm, mock_stats, store)
            candidate = _make_candidate("yyds", freq=10)
            result = await miner.infer_meaning(candidate)
            assert result is not None
            assert result.is_jargon is True
            assert result.confidence == 0.5
        finally:
            await _close_store(store)

    # -- 渐进阈值测试 --

    @pytest.mark.parametrize(
        "frequency,should_trigger",
        [
            (2, False),
            (3, True),
            (5, True),
            (6, True),
            (9, True),
            (10, True),
            (20, True),
            (40, True),
            (60, True),
            (100, True),
            (101, True),
        ],
    )
    async def test_progressive_thresholds(
        self, frequency: int, should_trigger: bool
    ) -> None:
        """测试渐进阈值触发机制。"""
        mock_llm = self._make_mock_llm()
        mock_stats = self._make_mock_stats()
        # _should_infer 不涉及 I/O，可以用 uninitialized store
        miner = JargonMiner(mock_llm, mock_stats, MagicMock())
        candidate = _make_candidate("test_term", freq=frequency)
        assert miner._should_infer(candidate) == should_trigger

    async def test_is_complete_when_count_exceeds_max(self) -> None:
        """测试 count >= 最大阈值时标记 is_complete。"""
        mock_llm = self._make_mock_llm()
        mock_stats = self._make_mock_stats()
        miner = JargonMiner(mock_llm, mock_stats, MagicMock())
        candidate = _make_candidate("test_term", freq=100)
        meaning = miner._build_meaning(
            candidate, meaning="测试含义", is_jargon=True, confidence=0.8
        )
        assert meaning.is_complete is True

    async def test_is_complete_when_count_above_max(self) -> None:
        """测试 count > 100 也标记 is_complete。"""
        mock_llm = self._make_mock_llm()
        mock_stats = self._make_mock_stats()
        miner = JargonMiner(mock_llm, mock_stats, MagicMock())
        candidate = _make_candidate("test_term", freq=150)
        meaning = miner._build_meaning(
            candidate, meaning="test", is_jargon=True, confidence=0.8
        )
        assert meaning.is_complete is True

    # -- LLM 不可用测试 --

    async def test_llm_unavailable_run_once(self) -> None:
        """测试 LLM 不可用时 run_once 优雅降级。"""
        mock_llm = MagicMock()
        mock_llm.call_llm_with_retry = AsyncMock()
        mock_llm.get_current_llm_provider = MagicMock(return_value=None)
        mock_stats = self._make_mock_stats()
        store = await _init_store()
        try:
            miner = JargonMiner(mock_llm, mock_stats, store)
            results = await miner.run_once("group-test", limit=5)
            assert results == []
        finally:
            await _close_store(store)

    async def test_llm_unavailable_infer_meaning(self) -> None:
        """测试 LLM 不可用时 infer_meaning 返回 None。"""
        mock_llm = MagicMock()
        mock_llm.call_llm_with_retry = AsyncMock()
        mock_llm.get_current_llm_provider = MagicMock(return_value=None)
        mock_stats = self._make_mock_stats()
        store = await _init_store()
        try:
            miner = JargonMiner(mock_llm, mock_stats, store)
            candidate = _make_candidate("yyds", freq=10)
            result = await miner.infer_meaning(candidate)
            assert result is None
        finally:
            await _close_store(store)

    # -- 集成: run_once 流程 --

    async def test_run_once_with_eligible_candidates(self) -> None:
        """测试 run_once 完整流程。"""
        mock_llm = self._make_mock_llm()
        mock_llm.call_llm_with_retry.side_effect = [
            json.dumps({"meaning": "永远的神", "no_info": False}),
            json.dumps({"meaning": "英文字母缩写"}),
            json.dumps({"is_similar": False, "reason": "完全不同"}),
        ]
        mock_stats = self._make_mock_stats()
        store = await _init_store()
        try:
            miner = JargonMiner(mock_llm, mock_stats, store)
            results = await miner.run_once("group-test", limit=2)
            assert isinstance(results, list)
        finally:
            await _close_store(store)

    async def test_run_once_times_out_slow_candidate(self) -> None:
        """测试单个候选推断超时不会卡住整轮任务。"""
        mock_llm = self._make_mock_llm()
        mock_stats = MagicMock()
        mock_stats.get_candidates.return_value = [_make_candidate("slow", freq=10)]
        store = await _init_store()
        try:
            miner = JargonMiner(mock_llm, mock_stats, store, inference_timeout=0.01)

            async def _slow_infer(_candidate):
                await asyncio.sleep(1)
                return None

            miner._infer_and_store = _slow_infer
            results = await miner.run_once("group-test", limit=1)

            assert results == []
        finally:
            await _close_store(store)

    async def test_run_once_keeps_successful_results_when_one_task_fails(self) -> None:
        """测试单个候选任务异常不会丢弃其他成功结果。"""
        mock_llm = self._make_mock_llm()
        candidate_ok = _make_candidate("ok", freq=10)
        candidate_bad = _make_candidate("bad", freq=10)
        mock_stats = MagicMock()
        mock_stats.get_candidates.return_value = [candidate_ok, candidate_bad]
        store = await _init_store()
        try:
            miner = JargonMiner(mock_llm, mock_stats, store, inference_timeout=1.0)
            meaning = _make_meaning("ok", meaning="正常结果", is_jargon=True)

            async def _mixed_infer(candidate):
                if candidate.term == "bad":
                    raise RuntimeError("boom")
                return meaning

            miner._infer_and_store = _mixed_infer
            results = await miner.run_once("group-test", limit=2)

            assert results == [meaning]
        finally:
            await _close_store(store)

    # -- 已完成的候选跳过 --

    async def test_infer_and_store_skips_completed(self) -> None:
        """测试已完成的黑话跳过重新推断。"""
        mock_llm = self._make_mock_llm()
        mock_stats = self._make_mock_stats()
        store = await _init_store()
        try:
            completed = _make_meaning(
                "yyds", meaning="永远的神", is_jargon=True, is_confirmed=True, count=110
            )
            completed.is_complete = True
            completed.confidence = 0.9
            completed.last_inference_count = 100
            await store.upsert(completed)

            miner = JargonMiner(mock_llm, mock_stats, store)
            candidate = _make_candidate("yyds", freq=110)
            result = await miner._infer_and_store(candidate)
            assert result is not None
            assert result.is_complete is True
            mock_llm.call_llm_with_retry.assert_not_called()
        finally:
            await _close_store(store)


# ============================================================================
# JargonQueryService 测试
# ============================================================================


@pytest.mark.asyncio
class TestJargonQueryService:
    """JargonQueryService 测试。"""

    @staticmethod
    async def _populated_store() -> JargonStore:
        """Build a store with 3 pre-inserted jargon terms."""
        store = await _init_store()
        await store.upsert(
            _make_meaning(
                "yyds",
                group_id="group-1",
                meaning="永远的神",
                is_jargon=True,
                is_confirmed=True,
                count=50,
            )
        )
        await store.upsert(
            _make_meaning(
                "xswl",
                group_id="group-1",
                meaning="笑死我了",
                is_jargon=True,
                is_confirmed=True,
                count=30,
            )
        )
        await store.upsert(
            _make_meaning(
                "nbcs",
                group_id="group-1",
                meaning="nobody cares",
                is_jargon=True,
                is_confirmed=True,
                count=15,
            )
        )
        return store

    async def test_query_cache_hit(self) -> None:
        """测试查询缓存命中。"""
        store = await self._populated_store()
        try:
            svc = JargonQueryService(store)
            results1 = await svc.query("yyds", "group-1")
            results2 = await svc.query("yyds", "group-1")
            assert len(results1) == len(results2)
        finally:
            await _close_store(store)

    async def test_query_cache_miss(self) -> None:
        """测试查询缓存未命中（不同关键词）。"""
        store = await self._populated_store()
        try:
            svc = JargonQueryService(store)
            results = await svc.query("zzzz", "group-1")
            assert results == []
        finally:
            await _close_store(store)

    async def test_get_group_jargon(self) -> None:
        """测试获取群组所有黑话。"""
        store = await self._populated_store()
        try:
            svc = JargonQueryService(store)
            results = await svc.get_group_jargon("group-1")
            assert len(results) == 3
        finally:
            await _close_store(store)

    async def test_get_group_jargon_empty(self) -> None:
        """测试空群组。"""
        store = await self._populated_store()
        try:
            svc = JargonQueryService(store)
            results = await svc.get_group_jargon("nonexistent-group")
            assert results == []
        finally:
            await _close_store(store)

    async def test_check_and_explain_with_jargon(self) -> None:
        """测试 check_and_explain 检测到黑话。"""
        store = await self._populated_store()
        try:
            svc = JargonQueryService(store)
            result = await svc.check_and_explain(
                "今天这个比赛 yyds 太厉害了", "group-1"
            )
            assert result is not None
            assert "yyds" in result
            assert "永远的神" in result
        finally:
            await _close_store(store)

    async def test_check_and_explain_no_jargon(self) -> None:
        """测试 check_and_explain 未检测到黑话。"""
        store = await self._populated_store()
        try:
            svc = JargonQueryService(store)
            result = await svc.check_and_explain("今天天气真好", "group-1")
            assert result is None
        finally:
            await _close_store(store)

    async def test_check_and_explain_empty_text(self) -> None:
        """测试空文本。"""
        store = await self._populated_store()
        try:
            svc = JargonQueryService(store)
            result = await svc.check_and_explain("", "group-1")
            assert result is None
        finally:
            await _close_store(store)

    async def test_ascii_jargon_word_boundary(self) -> None:
        """测试 ASCII 黑话的 word-boundary 匹配。"""
        store = await self._populated_store()
        try:
            svc = JargonQueryService(store)
            result = await svc.check_and_explain("nbcs 这个功能太垃圾了", "group-1")
            assert result is not None
            assert "nbcs" in result
        finally:
            await _close_store(store)

    async def test_invalidate_cache(self) -> None:
        """测试缓存失效。"""
        store = await self._populated_store()
        try:
            svc = JargonQueryService(store)
            await svc.query("yyds", "group-1")
            await svc.invalidate_cache("group-1")
            assert len(svc._cache) == 0
        finally:
            await _close_store(store)

    # -- _match_jargon_in_text 测试 --

    async def test_match_chinese_jargon(self) -> None:
        """测试匹配中文黑话。"""
        entries = [
            JargonMeaning(term="测试词", group_id="g1", meaning="test", is_jargon=True)
        ]
        matched = JargonQueryService._match_jargon_in_text(
            "这是测试词的上下文", entries
        )
        assert len(matched) == 1
        assert matched[0].term == "测试词"

    async def test_match_ascii_jargon_word_boundary(self) -> None:
        """测试 ASCII 黑话 word-boundary 匹配。"""
        entries = [
            JargonMeaning(
                term="btw", group_id="g1", meaning="by the way", is_jargon=True
            )
        ]
        matched = JargonQueryService._match_jargon_in_text(
            "btw I forgot to tell you", entries
        )
        assert len(matched) == 1

        matched2 = JargonQueryService._match_jargon_in_text(
            "something between us", entries
        )
        assert len(matched2) == 0

    async def test_match_multiple_jargon(self) -> None:
        """测试匹配多个黑话。"""
        entries = [
            JargonMeaning(
                term="yyds", group_id="g1", meaning="永远的神", is_jargon=True
            ),
            JargonMeaning(
                term="xswl", group_id="g1", meaning="笑死我了", is_jargon=True
            ),
        ]
        matched = JargonQueryService._match_jargon_in_text("yyds 你说的 xswl", entries)
        assert len(matched) == 2


# ============================================================================
# INFERENCE_THRESHOLDS 常量测试
# ============================================================================


class TestInferenceThresholds:
    """INFERENCE_THRESHOLDS 常量测试。"""

    def test_thresholds_are_sorted(self) -> None:
        """测试阈值列表递增。"""
        assert INFERENCE_THRESHOLDS == sorted(INFERENCE_THRESHOLDS)

    def test_first_threshold_is_3(self) -> None:
        """测试最小阈值为 3。"""
        assert INFERENCE_THRESHOLDS[0] == 3

    def test_last_threshold_is_100(self) -> None:
        """测试最大阈值为 100（完成标记）。"""
        assert INFERENCE_THRESHOLDS[-1] == 100
