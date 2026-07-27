"""表达式模式学习子系统测试。

覆盖：
- 从消息序列中提取对话对
- 短消息过滤
- System message filtering ([, http, @ prefixes)
- Duplicate pattern weight accumulation
- Three-dimensional scope isolation
- Capacity eviction (lowest-weight deletion)
- Quadratic decay calculation
- get_patterns_for_injection ordering
- Store CRUD operations
"""

from __future__ import annotations

import time

import pytest

from core.base.list_sorting import SortQuery
from core.expression.models import ExpressionPattern, GroupState, PatternScope
from core.expression.pattern_learner import ExpressionPatternLearner
from core.expression.pattern_store import (
    EXPRESSION_SORT_COLUMNS,
    ExpressionPatternStore,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _new_store(db_path: str) -> ExpressionPatternStore:
    s = ExpressionPatternStore(db_path)
    await s.initialize()
    return s


def _make_learner(store: ExpressionPatternStore, **kwargs) -> ExpressionPatternLearner:
    return ExpressionPatternLearner(store, **kwargs)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestExpressionPattern:
    def test_creation_defaults(self):
        p = ExpressionPattern(
            situation="你好",
            expression="你好呀",
            group_id="g1",
            persona_id="default",
        )
        assert p.situation == "你好"
        assert p.expression == "你好呀"
        assert p.weight == 1.0
        assert p.usage_count == 0
        assert p.user_id is None

    def test_creation_with_user(self):
        p = ExpressionPattern(
            situation="你好",
            expression="你好呀",
            group_id="g1",
            persona_id="custom",
            user_id="u42",
        )
        assert p.user_id == "u42"
        assert p.persona_id == "custom"


class TestPatternScope:
    def test_to_key_group_level(self):
        scope = PatternScope(group_id="g1", persona_id="default")
        assert scope.to_key() == "g1:default:group-level"

    def test_to_key_user_level(self):
        scope = PatternScope(group_id="g1", persona_id="p1", user_id="u1")
        assert scope.to_key() == "g1:p1:u1"

    def test_frozen(self):
        scope = PatternScope(group_id="g1", persona_id="default")
        with pytest.raises(Exception):
            scope.group_id = "g2"  # type: ignore[misc]


class TestGroupState:
    def test_defaults(self):
        state = GroupState(group_id="g1")
        assert state.group_id == "g1"
        assert state.message_buffer == []
        assert state.last_learning_at == 0.0
        assert state.message_count_since_last_learn == 0


# ---------------------------------------------------------------------------
# Dialog pair extraction
# ---------------------------------------------------------------------------


_SAMPLE_MESSAGES = [
    {"sender_id": "user_1", "content": "今天天气真好啊", "timestamp": 1000.0},
    {"sender_id": "bot", "content": "是呀，阳光明媚适合出去走走", "timestamp": 1001.0},
    {"sender_id": "user_2", "content": "有人知道附近好吃的吗", "timestamp": 1002.0},
    {
        "sender_id": "bot",
        "content": "推荐转角那家面馆，牛肉面很好吃",
        "timestamp": 1003.0,
    },
    {"sender_id": "user_1", "content": "我也想吃面了", "timestamp": 1004.0},
    {
        "sender_id": "bot",
        "content": "那一起去吧，我知道一家特别好的",
        "timestamp": 1005.0,
    },
]


class TestDialogPairExtraction:
    @pytest.mark.asyncio
    async def test_basic_extraction(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)
        result = await learner.process_messages(_SAMPLE_MESSAGES, "g1")
        assert len(result) == 3

        assert result[0].situation == "今天天气真好啊"
        assert result[0].expression == "是呀，阳光明媚适合出去走走"
        assert result[1].situation == "有人知道附近好吃的吗"
        assert result[1].expression == "推荐转角那家面馆，牛肉面很好吃"
        assert result[2].situation == "我也想吃面了"
        assert result[2].expression == "那一起去吧，我知道一家特别好的"

    @pytest.mark.asyncio
    async def test_no_bot_messages(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)
        messages = [
            {"sender_id": "user_1", "content": "hello"},
            {"sender_id": "user_2", "content": "hi"},
        ]
        result = await learner.process_messages(messages, "g1")
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_bot_first(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)
        messages = [
            {"sender_id": "bot", "content": "你好"},
            {"sender_id": "user_1", "content": "你好呀"},
        ]
        result = await learner.process_messages(messages, "g1")
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_empty_content_filtered(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)
        messages = [
            {"sender_id": "user_1", "content": ""},
            {"sender_id": "bot", "content": "something"},
            {"sender_id": "user_2", "content": "valid message here"},
            {"sender_id": "bot", "content": ""},
        ]
        result = await learner.process_messages(messages, "g1")
        assert len(result) == 0


class TestMessageFiltering:
    @pytest.mark.asyncio
    async def test_short_user_message_filtered(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)
        messages = [
            {"sender_id": "user_1", "content": "ab"},
            {"sender_id": "bot", "content": "this is a valid reply"},
        ]
        result = await learner.process_messages(messages, "g1")
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_short_bot_reply_filtered(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)
        messages = [
            {"sender_id": "user_1", "content": "valid user message"},
            {"sender_id": "bot", "content": "ok"},
        ]
        result = await learner.process_messages(messages, "g1")
        assert len(result) == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "content",
        [
            "[图片]",
            "[表情]",
            "http://example.com",
            "https://link.to/something",
            "@someone hello",
        ],
    )
    async def test_system_prefix_filtered(self, tmp_db_path, content):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)
        messages = [
            {"sender_id": "user_1", "content": content},
            {"sender_id": "bot", "content": "a valid bot reply here"},
        ]
        result = await learner.process_messages(messages, "g1")
        assert len(result) == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "content",
        [
            "[图片]",
            "[表情]",
            "http://example.com",
        ],
    )
    async def test_system_bot_reply_filtered(self, tmp_db_path, content):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)
        messages = [
            {"sender_id": "user_1", "content": "valid user message"},
            {"sender_id": "bot", "content": content},
        ]
        result = await learner.process_messages(messages, "g1")
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_custom_bot_id(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store, bot_id="my_bot")
        messages = [
            {"sender_id": "user_1", "content": "hello there"},
            {"sender_id": "my_bot", "content": "hi human"},
            {"sender_id": "user_2", "content": "another message"},
            {"sender_id": "bot", "content": "this is default bot"},
        ]
        result = await learner.process_messages(messages, "g1")
        assert len(result) == 1
        assert result[0].situation == "hello there"


# ---------------------------------------------------------------------------
# Duplicate pattern handling
# ---------------------------------------------------------------------------


class TestDuplicatePatterns:
    @pytest.mark.asyncio
    async def test_weight_accumulation(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)
        messages = [
            {"sender_id": "user_1", "content": "今天好开心"},
            {"sender_id": "bot", "content": "天天开心呀"},
            {"sender_id": "user_2", "content": "今天好开心"},
            {"sender_id": "bot", "content": "天天开心呀"},
        ]
        result = await learner.process_messages(messages, "g1")
        assert len(result) == 2
        scope = PatternScope(group_id="g1", persona_id="default")
        patterns = await store.get_by_scope(scope)
        assert len(patterns) == 1
        assert patterns[0].weight == pytest.approx(2.0, rel=0.01)

    @pytest.mark.asyncio
    async def test_different_situations_create_separate(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)
        messages = [
            {"sender_id": "user_1", "content": "今天天气真好啊"},
            {"sender_id": "bot", "content": "是呀阳光明媚"},
            {"sender_id": "user_2", "content": "明天会下雨吗"},
            {"sender_id": "bot", "content": "预报说明天下雨"},
        ]
        await learner.process_messages(messages, "g1")
        scope = PatternScope(group_id="g1", persona_id="default")
        patterns = await store.get_by_scope(scope)
        assert len(patterns) == 2


# ---------------------------------------------------------------------------
# Scope isolation
# ---------------------------------------------------------------------------


class TestScopeIsolation:
    @pytest.mark.asyncio
    async def test_different_groups_isolated(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)
        mg1 = [
            {"sender_id": "user_1", "content": "hello g1"},
            {"sender_id": "bot", "content": "hi from g1"},
        ]
        mg2 = [
            {"sender_id": "user_1", "content": "hello g2"},
            {"sender_id": "bot", "content": "hi from g2"},
        ]
        await learner.process_messages(mg1, "g1")
        await learner.process_messages(mg2, "g2")

        p1 = await store.get_by_scope(PatternScope(group_id="g1", persona_id="default"))
        p2 = await store.get_by_scope(PatternScope(group_id="g2", persona_id="default"))
        assert len(p1) == 1
        assert len(p2) == 1
        assert p1[0].group_id == "g1"
        assert p2[0].group_id == "g2"

    @pytest.mark.asyncio
    async def test_different_personas_isolated(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)
        messages = [
            {"sender_id": "user_1", "content": "hello"},
            {"sender_id": "bot", "content": "hi there"},
        ]
        await learner.process_messages(messages, "g1", persona_id="p1")
        await learner.process_messages(messages, "g1", persona_id="p2")

        assert len(await store.get_by_scope(PatternScope("g1", "p1"))) == 1
        assert len(await store.get_by_scope(PatternScope("g1", "p2"))) == 1

    @pytest.mark.asyncio
    async def test_different_users_isolated(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)
        messages = [
            {"sender_id": "user_1", "content": "hello"},
            {"sender_id": "bot", "content": "hi there"},
        ]
        await learner.process_messages(messages, "g1", user_id="u1")
        await learner.process_messages(messages, "g1", user_id="u2")

        assert len(await store.get_by_scope(PatternScope("g1", "default", "u1"))) == 1
        assert len(await store.get_by_scope(PatternScope("g1", "default", "u2"))) == 1

    @pytest.mark.asyncio
    async def test_group_vs_user_scopes_independent(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)
        messages = [
            {"sender_id": "user_1", "content": "hi group"},
            {"sender_id": "bot", "content": "hello group"},
        ]
        await learner.process_messages(messages, "g1")  # group-level
        await learner.process_messages(messages, "g1", user_id="u1")  # user-level

        assert len(await store.get_by_scope(PatternScope("g1", "default"))) == 1
        assert len(await store.get_by_scope(PatternScope("g1", "default", "u1"))) == 1


# ---------------------------------------------------------------------------
# Capacity eviction
# ---------------------------------------------------------------------------


class TestCapacityEviction:
    @pytest.mark.asyncio
    async def test_eviction_triggers_at_max(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store, max_patterns_per_scope=5)

        for i in range(8):
            p = ExpressionPattern(
                situation=f"situation_{i}",
                expression=f"expression_{i}",
                group_id="g1",
                persona_id="default",
                weight=float(i + 1),
            )
            await store.upsert(p)

        messages = [
            {"sender_id": "user_1", "content": "trigger eviction"},
            {"sender_id": "bot", "content": "evicted now"},
        ]
        await learner.process_messages(messages, "g1")

        scope = PatternScope(group_id="g1", persona_id="default")
        remaining = await store.get_by_scope(scope)
        assert len(remaining) <= 5
        situations = {p.situation for p in remaining}
        assert "situation_0" not in situations

    @pytest.mark.asyncio
    async def test_no_eviction_under_cap(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store, max_patterns_per_scope=100)
        messages = [
            {"sender_id": "user_1", "content": "hello"},
            {"sender_id": "bot", "content": "hi there"},
        ]
        await learner.process_messages(messages, "g1")
        scope = PatternScope(group_id="g1", persona_id="default")
        assert len(await store.get_by_scope(scope)) == 1


# ---------------------------------------------------------------------------
# Quadratic decay
# ---------------------------------------------------------------------------


class TestDecay:
    @staticmethod
    def _make_learner_for_decay():
        """Create a bare learner with only _decay_days set for unit testing."""
        learner = ExpressionPatternLearner.__new__(ExpressionPatternLearner)
        learner._decay_days = 15
        return learner

    def test_day_zero_no_decay(self):
        learner = self._make_learner_for_decay()
        assert learner._calculate_decay_factor(0.0) == 0.0
        assert learner._calculate_decay_factor(-1.0) == 0.0

    def test_day_seven_partial_decay(self):
        learner = self._make_learner_for_decay()
        factor = learner._calculate_decay_factor(7.0)
        expected = (7.0 / 15.0) ** 2
        assert factor == pytest.approx(expected, rel=0.01)

    def test_day_fifteen_full_decay(self):
        learner = self._make_learner_for_decay()
        assert learner._calculate_decay_factor(15.0) == pytest.approx(1.0, rel=0.01)

    def test_beyond_window_capped(self):
        learner = self._make_learner_for_decay()
        assert learner._calculate_decay_factor(30.0) == 1.0
        assert learner._calculate_decay_factor(100.0) == 1.0

    def test_weight_decay_formula(self):
        learner = self._make_learner_for_decay()
        original = 10.0
        days = 10.0
        factor = learner._calculate_decay_factor(days)
        decayed = original * (1.0 - factor)
        expected_decay = (10.0 / 15.0) ** 2
        assert factor == pytest.approx(expected_decay, rel=0.01)
        assert decayed == pytest.approx(10.0 * (1 - expected_decay), rel=0.01)

    @pytest.mark.asyncio
    async def test_decay_applied_in_process(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store, max_patterns_per_scope=100)

        old_time = time.time() - (8 * 86400)  # 8 days ago
        p = ExpressionPattern(
            situation="old situation",
            expression="old expression",
            group_id="g1",
            persona_id="default",
            weight=5.0,
            created_at=old_time,
            last_used_at=old_time,
            decayed_at=old_time,
        )
        await store.upsert(p)

        messages = [
            {"sender_id": "user_1", "content": "new message here"},
            {"sender_id": "bot", "content": "brand new reply content"},
        ]
        await learner.process_messages(messages, "g1")

        scope = PatternScope(group_id="g1", persona_id="default")
        patterns = await store.get_by_scope(scope)
        old_patterns = [p for p in patterns if p.situation == "old situation"]
        if old_patterns:
            assert old_patterns[0].weight < 5.0


# ---------------------------------------------------------------------------
# get_patterns_for_injection
# ---------------------------------------------------------------------------


class TestGetPatternsForInjection:
    @pytest.mark.asyncio
    async def test_returns_highest_weight_first(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)

        for w in [1.0, 5.0, 3.0, 10.0, 2.0]:
            p = ExpressionPattern(
                situation=f"w{w}",
                expression=f"expr_{w}",
                group_id="g1",
                persona_id="default",
                weight=w,
            )
            await store.upsert(p)

        result = await learner.get_patterns_for_injection("g1", limit=3)
        assert len(result) == 3
        assert result[0].weight >= result[1].weight >= result[2].weight

    @pytest.mark.asyncio
    async def test_scope_isolation_in_injection(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)

        p_g1 = ExpressionPattern(
            situation="g1 situation",
            expression="g1 expr",
            group_id="g1",
            persona_id="default",
            weight=10.0,
        )
        p_g2 = ExpressionPattern(
            situation="g2 situation",
            expression="g2 expr",
            group_id="g2",
            persona_id="default",
            weight=5.0,
        )
        await store.upsert(p_g1)
        await store.upsert(p_g2)

        result_g1 = await learner.get_patterns_for_injection("g1")
        assert len(result_g1) == 1
        assert result_g1[0].group_id == "g1"

    @pytest.mark.asyncio
    async def test_empty_when_no_patterns(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)
        result = await learner.get_patterns_for_injection("nonexistent")
        assert result == []

    @pytest.mark.asyncio
    async def test_format_prompt(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)

        p = ExpressionPattern(
            situation="user said hello",
            expression="bot replied hi",
            group_id="g1",
            persona_id="default",
            weight=1.0,
        )
        await store.upsert(p)

        prompt = await learner.format_patterns_for_prompt("g1", limit=1)
        assert "user said hello" in prompt
        assert "bot replied hi" in prompt
        assert "[学习到的表达习惯]" in prompt

    @pytest.mark.asyncio
    async def test_format_prompt_empty(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)
        prompt = await learner.format_patterns_for_prompt("nonexistent")
        assert prompt == ""


# ---------------------------------------------------------------------------
# Store CRUD
# ---------------------------------------------------------------------------


class TestStoreCRUD:
    @pytest.mark.asyncio
    async def test_initialize_creates_table(self, tmp_db_path):
        import aiosqlite

        s = ExpressionPatternStore(tmp_db_path)
        await s.initialize()

        db = await aiosqlite.connect(tmp_db_path)
        try:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='expression_patterns'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "expression_patterns"
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_connect_uses_shared_foreign_key_pragma(self, tmp_db_path):
        s = ExpressionPatternStore(tmp_db_path)

        async with s._connect() as db:
            cursor = await db.execute("PRAGMA foreign_keys")
            row = await cursor.fetchone()
            assert row[0] == 1

    @pytest.mark.asyncio
    async def test_upsert_creates_new(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        p = ExpressionPattern(
            situation="hello",
            expression="hi",
            group_id="g1",
            persona_id="default",
        )
        result = await store.upsert(p)
        assert result.pattern_id > 0
        assert result.weight == 1.0

    @pytest.mark.asyncio
    async def test_upsert_updates_existing(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        p = ExpressionPattern(
            situation="hello",
            expression="hi",
            group_id="g1",
            persona_id="default",
            weight=1.0,
        )
        r1 = await store.upsert(p)
        r2 = await store.upsert(p)
        assert r1.pattern_id == r2.pattern_id
        assert r2.weight == pytest.approx(2.0)

    @pytest.mark.asyncio
    async def test_count_by_scope(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        for i in range(3):
            p = ExpressionPattern(
                situation=f"s{i}",
                expression=f"e{i}",
                group_id="g1",
                persona_id="default",
            )
            await store.upsert(p)
        count = await store.count_by_scope(PatternScope("g1", "default"))
        assert count == 3

    @pytest.mark.asyncio
    async def test_delete_below_weight(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        for w in [0.5, 1.0, 2.0, 3.0]:
            p = ExpressionPattern(
                situation=f"w{w}",
                expression=f"e{w}",
                group_id="g1",
                persona_id="default",
                weight=w,
            )
            await store.upsert(p)
        scope = PatternScope(group_id="g1", persona_id="default")
        deleted = await store.delete_below_weight(scope, 1.0)
        assert deleted >= 1
        remaining = await store.get_by_scope(scope)
        assert all(p.weight > 1.0 for p in remaining)

    @pytest.mark.asyncio
    async def test_delete_lowest_weight(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        for w in [1.0, 2.0, 3.0, 4.0, 5.0]:
            p = ExpressionPattern(
                situation=f"w{w}",
                expression=f"e{w}",
                group_id="g1",
                persona_id="default",
                weight=w,
            )
            await store.upsert(p)
        scope = PatternScope(group_id="g1", persona_id="default")
        deleted = await store.delete_lowest_weight(scope, 2)
        assert deleted == 2
        remaining = await store.get_by_scope(scope)
        assert len(remaining) == 3
        weights = {p.weight for p in remaining}
        assert 1.0 not in weights
        assert 2.0 not in weights

    @pytest.mark.asyncio
    async def test_mark_used(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        p = ExpressionPattern(
            situation="hello",
            expression="hi",
            group_id="g1",
            persona_id="default",
        )
        result = await store.upsert(p)
        await store.mark_used(result.pattern_id)
        patterns = await store.get_by_scope(PatternScope("g1", "default"))
        assert patterns[0].usage_count == 1

    @pytest.mark.asyncio
    async def test_get_by_scope_limit(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        for i in range(20):
            p = ExpressionPattern(
                situation=f"s{i:02d}",
                expression=f"e{i:02d}",
                group_id="g1",
                persona_id="default",
                weight=float(i),
            )
            await store.upsert(p)
        result = await store.get_by_scope(PatternScope("g1", "default"), limit=5)
        assert len(result) == 5
        assert result[0].weight > result[-1].weight

    @pytest.mark.asyncio
    async def test_get_top_sorts_full_scope_before_limit(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        for index, usage_count in enumerate((9, 3, 6)):
            await store.upsert(
                ExpressionPattern(
                    situation=f"s{index}",
                    expression=f"e{index}",
                    group_id="g1",
                    persona_id="default",
                    weight=float(index + 1),
                    usage_count=usage_count,
                )
            )

        result = await store.get_top_by_weight(
            PatternScope("g1", "default"),
            limit=2,
            sort=SortQuery("usage_count", "asc"),
        )

        assert [pattern.usage_count for pattern in result] == [3, 6]

    @pytest.mark.asyncio
    async def test_get_top_keeps_weight_descending_default(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        for index, weight in enumerate((1.0, 5.0, 3.0)):
            await store.upsert(
                ExpressionPattern(
                    situation=f"s{index}",
                    expression=f"e{index}",
                    group_id="g1",
                    persona_id="default",
                    weight=weight,
                )
            )

        result = await store.get_top_by_weight(PatternScope("g1", "default"), limit=2)

        assert [pattern.weight for pattern in result] == [5.0, 3.0]

    def test_expression_sort_columns_are_fixed(self):
        assert EXPRESSION_SORT_COLUMNS == {
            "situation": "situation COLLATE NOCASE",
            "expression": "expression COLLATE NOCASE",
            "weight": "weight",
            "usage_count": "usage_count",
            "created_at": "created_at",
            "last_used_at": "last_used_at",
        }


# ---------------------------------------------------------------------------
# GroupState buffer
# ---------------------------------------------------------------------------


class TestGroupBuffer:
    def test_buffer_message_appends(self, tmp_db_path):
        # GroupState tests don't need an initialized store
        store = ExpressionPatternStore.__new__(ExpressionPatternStore)
        learner = _make_learner(store)
        learner.buffer_message("g1", "user_1", "hello")
        learner.buffer_message("g1", "user_2", "hi")

        state = learner.get_or_create_state("g1")
        assert len(state.message_buffer) == 2
        assert state.message_count_since_last_learn == 2

    @pytest.mark.asyncio
    async def test_maybe_learn_not_enough(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)
        learner.buffer_message("g1", "user_1", "msg1")
        learner.buffer_message("g1", "user_2", "msg2")
        learner.buffer_message("g1", "bot", "msg3")

        result = await learner.maybe_learn("g1", min_messages=5)
        assert result == []

        state = learner.get_or_create_state("g1")
        assert len(state.message_buffer) == 3

    @pytest.mark.asyncio
    async def test_maybe_learn_triggers(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)
        learner.buffer_message("g1", "user_1", "hello there")
        learner.buffer_message("g1", "bot", "hi human")
        learner.buffer_message("g1", "user_2", "how are you")
        learner.buffer_message("g1", "bot", "fine thanks")
        learner.buffer_message("g1", "user_1", "good to hear")
        learner.buffer_message("g1", "bot", "me too")

        result = await learner.maybe_learn("g1", min_messages=5)
        assert len(result) > 0

        state = learner.get_or_create_state("g1")
        assert len(state.message_buffer) == 0
        assert state.message_count_since_last_learn == 0


# ---------------------------------------------------------------------------
# Message content truncation
# ---------------------------------------------------------------------------


class TestContentTruncation:
    @pytest.mark.asyncio
    async def test_situation_truncated_to_50(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)
        long_msg = "A" * 80
        messages = [
            {"sender_id": "user_1", "content": long_msg},
            {"sender_id": "bot", "content": "short reply"},
        ]
        result = await learner.process_messages(messages, "g1")
        assert len(result[0].situation) == 50
        assert result[0].situation == long_msg[:50]

    @pytest.mark.asyncio
    async def test_expression_truncated_to_100(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)
        long_reply = "B" * 150
        messages = [
            {"sender_id": "user_1", "content": "hello"},
            {"sender_id": "bot", "content": long_reply},
        ]
        result = await learner.process_messages(messages, "g1")
        assert len(result[0].expression) == 100
        assert result[0].expression == long_reply[:100]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_message_list(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)
        result = await learner.process_messages([], "g1")
        assert result == []

    @pytest.mark.asyncio
    async def test_single_message(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)
        messages = [{"sender_id": "user_1", "content": "hello"}]
        result = await learner.process_messages(messages, "g1")
        assert result == []

    @pytest.mark.asyncio
    async def test_all_bot_messages(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)
        messages = [
            {"sender_id": "bot", "content": "msg1"},
            {"sender_id": "bot", "content": "msg2"},
            {"sender_id": "bot", "content": "msg3"},
        ]
        result = await learner.process_messages(messages, "g1")
        assert result == []

    @pytest.mark.asyncio
    async def test_consecutive_user_then_bot(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)
        messages = [
            {"sender_id": "user_1", "content": "msg1"},
            {"sender_id": "user_2", "content": "msg2"},
            {"sender_id": "bot", "content": "reply"},
        ]
        result = await learner.process_messages(messages, "g1")
        assert len(result) == 1
        assert result[0].situation == "msg2"

    @pytest.mark.asyncio
    async def test_whitespace_only_content(self, tmp_db_path):
        store = await _new_store(tmp_db_path)
        learner = _make_learner(store)
        messages = [
            {"sender_id": "user_1", "content": "   "},
            {"sender_id": "bot", "content": "valid reply"},
        ]
        result = await learner.process_messages(messages, "g1")
        assert result == []
