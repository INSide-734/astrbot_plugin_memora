"""human_like_formatter.py 测试 — HumanLikeMemoryFormatter。"""

from __future__ import annotations

import time

import pytest

from core.processors.human_like_formatter import HumanLikeMemoryFormatter


class TestHumanLikeFormatter:
    @pytest.fixture
    def formatter(self) -> HumanLikeMemoryFormatter:
        return HumanLikeMemoryFormatter(max_fragments=5, max_fragment_length=80)

    def test_empty_memories(self, formatter: HumanLikeMemoryFormatter) -> None:
        result = formatter.format([])
        assert result == ["没有特别的记忆浮现"]

    def test_episodic_with_time_hint(self, formatter: HumanLikeMemoryFormatter) -> None:
        now = time.time()
        memory = {
            "memory_type": "EPISODIC",
            "content": "和小明去了西湖划船",
            "create_time": now - 3600,  # 1 hour ago = "刚才"
        }
        result = formatter.format([memory])
        assert len(result) == 1
        assert "刚才" in result[0]

    def test_episodic_recent(self, formatter: HumanLikeMemoryFormatter) -> None:
        now = time.time()
        memory = {
            "memory_type": "EPISODIC",
            "content": "上周去了北京",
            "create_time": now - 86400 * 3,  # 3 days ago = "前几天"
        }
        result = formatter.format([memory])
        assert len(result) == 1
        assert "前几天" in result[0]

    def test_episodic_old(self, formatter: HumanLikeMemoryFormatter) -> None:
        now = time.time()
        memory = {
            "memory_type": "EPISODIC",
            "content": "前年去了北京",
            "create_time": now - 86400 * 400,  # > 1 year
        }
        result = formatter.format([memory])
        assert len(result) == 1
        # 400 days ago should be "去年" (>1 year, <2 years)
        assert any(hint in result[0] for hint in ["去年", "前年", "年前"])

    def test_factual_format(self, formatter: HumanLikeMemoryFormatter) -> None:
        memory = {
            "memory_type": "FACTUAL",
            "content": "北京是中国的首都",
        }
        result = formatter.format([memory])
        assert len(result) == 1
        assert "ta" in result[0]

    def test_preference_with_marker(self, formatter: HumanLikeMemoryFormatter) -> None:
        memory = {
            "memory_type": "PREFERENCE",
            "content": "用户喜欢喝咖啡尤其是拿铁",
        }
        result = formatter.format([memory])
        assert len(result) == 1
        assert "喜欢" in result[0]

    def test_preference_without_marker(
        self, formatter: HumanLikeMemoryFormatter
    ) -> None:
        memory = {
            "memory_type": "PREFERENCE",
            "content": "咖啡",
        }
        result = formatter.format([memory])
        assert len(result) == 1
        assert "ta喜欢" in result[0]

    def test_relational_format(self, formatter: HumanLikeMemoryFormatter) -> None:
        memory = {
            "memory_type": "RELATIONAL",
            "content": "小明是大学室友",
        }
        result = formatter.format([memory])
        assert len(result) == 1
        assert "小明是大学室友" in result[0]

    def test_other_type_falls_back_to_factual(
        self, formatter: HumanLikeMemoryFormatter
    ) -> None:
        memory = {
            "memory_type": "UNKNOWN",
            "content": "some content",
        }
        result = formatter.format([memory])
        assert len(result) == 1

    def test_resolve_type_from_atom_type(
        self, formatter: HumanLikeMemoryFormatter
    ) -> None:
        memory = {
            "atom_type": "EPISODIC",
            "content": "went shopping",
        }
        result = formatter.format([memory])
        assert len(result) == 1

    def test_resolve_type_case_insensitive(
        self, formatter: HumanLikeMemoryFormatter
    ) -> None:
        memory = {
            "memory_type": "episodic",
            "content": "lowercase episodic",
        }
        result = formatter.format([memory])
        assert len(result) == 1

    def test_extract_content_from_key_facts(
        self, formatter: HumanLikeMemoryFormatter
    ) -> None:
        memory = {
            "memory_type": "FACTUAL",
            "key_facts": "关键事实内容",
        }
        result = formatter.format([memory])
        assert len(result) == 1
        assert "关键事实内容" in result[0]

    def test_extract_content_from_metadata_key_facts(
        self, formatter: HumanLikeMemoryFormatter
    ) -> None:
        memory = {
            "memory_type": "FACTUAL",
            "metadata": {"key_facts": "元数据中的关键事实"},
        }
        result = formatter.format([memory])
        assert len(result) == 1
        assert "元数据中的关键事实" in result[0]

    def test_max_fragments_enforced(self) -> None:
        formatter = HumanLikeMemoryFormatter(max_fragments=2, max_fragment_length=80)
        memories = [{"memory_type": "FACTUAL", "content": f"fact{i}"} for i in range(5)]
        result = formatter.format(memories)
        assert len(result) <= 2

    def test_max_fragment_length_enforced(self) -> None:
        formatter = HumanLikeMemoryFormatter(max_fragments=5, max_fragment_length=10)
        memory = {
            "memory_type": "FACTUAL",
            "content": "a" * 50,
        }
        result = formatter.format([memory])
        assert len(result) == 1
        assert len(result[0]) <= 10 + 2  # "ta" prefix + content

    def test_deduplication_removes_overlap(
        self, formatter: HumanLikeMemoryFormatter
    ) -> None:
        memories = [
            {"memory_type": "FACTUAL", "content": "用户喜欢咖啡"},
            {"memory_type": "FACTUAL", "content": "用户喜欢咖啡"},  # identical
        ]
        result = formatter.format(memories)
        # Only one should remain after dedup
        assert len(result) == 1

    def test_no_timestamp_no_time_hint(
        self, formatter: HumanLikeMemoryFormatter
    ) -> None:
        memory = {
            "memory_type": "EPISODIC",
            "content": "something happened",
        }
        result = formatter.format([memory])
        assert len(result) == 1
        assert "想起" in result[0]

    def test_timestamp_in_metadata(self, formatter: HumanLikeMemoryFormatter) -> None:
        now = time.time()
        memory = {
            "memory_type": "EPISODIC",
            "content": "event from metadata",
            "metadata": {"create_time": now - 3600},
        }
        result = formatter.format([memory])
        assert "刚才" in result[0]

    def test_timestamp_from_metadata_timestamp_field(
        self, formatter: HumanLikeMemoryFormatter
    ) -> None:
        now = time.time()
        memory = {
            "memory_type": "EPISODIC",
            "content": "event from metadata.timestamp",
            "metadata": {"timestamp": now - 3600},
        }
        result = formatter.format([memory])
        assert "刚才" in result[0]

    def test_invalid_timestamp_in_metadata(
        self, formatter: HumanLikeMemoryFormatter
    ) -> None:
        memory = {
            "memory_type": "EPISODIC",
            "content": "bad timestamp",
            "metadata": {"create_time": "not_a_number"},
        }
        result = formatter.format([memory])
        assert "想起" in result[0]

    def test_timestamp_weeks_ago(self, formatter: HumanLikeMemoryFormatter) -> None:
        now = time.time()
        memory = {
            "memory_type": "EPISODIC",
            "content": "event weeks ago",
            "create_time": now - 86400 * 10,  # 10 days ago
        }
        result = formatter.format([memory])
        assert "几周前" in result[0]

    def test_timestamp_months_ago(self, formatter: HumanLikeMemoryFormatter) -> None:
        now = time.time()
        memory = {
            "memory_type": "EPISODIC",
            "content": "event months ago",
            "create_time": now - 86400 * 45,  # ~1.5 months ago
        }
        result = formatter.format([memory])
        assert "一两个月前" in result[0]

    def test_timestamp_half_year_ago(self, formatter: HumanLikeMemoryFormatter) -> None:
        now = time.time()
        memory = {
            "memory_type": "EPISODIC",
            "content": "event half year ago",
            "create_time": now - 86400 * 200,  # ~6.5 months
        }
        result = formatter.format([memory])
        assert "半年前" in result[0]

    def test_timestamp_last_year(self, formatter: HumanLikeMemoryFormatter) -> None:
        now = time.time()
        memory = {
            "memory_type": "EPISODIC",
            "content": "event last year",
            "create_time": now - 86400 * 400,  # > 1 year
        }
        result = formatter.format([memory])
        assert "去年" in result[0]

    def test_timestamp_two_years_ago(self, formatter: HumanLikeMemoryFormatter) -> None:
        now = time.time()
        memory = {
            "memory_type": "EPISODIC",
            "content": "event 2 years ago",
            "create_time": now - 86400 * 800,  # > 2 years
        }
        result = formatter.format([memory])
        assert "前年" in result[0]

    def test_timestamp_many_years_ago(
        self, formatter: HumanLikeMemoryFormatter
    ) -> None:
        now = time.time()
        memory = {
            "memory_type": "EPISODIC",
            "content": "event many years ago",
            "create_time": now - 86400 * 1200,  # > 3 years
        }
        result = formatter.format([memory])
        assert "年前" in result[0]

    def test_empty_content_falls_back_to_key_facts(
        self, formatter: HumanLikeMemoryFormatter
    ) -> None:
        memory = {
            "memory_type": "FACTUAL",
            "content": "",
            "key_facts": "事实来自key_facts",
        }
        result = formatter.format([memory])
        assert len(result) == 1

    def test_content_from_dict_key_facts_single_string(
        self, formatter: HumanLikeMemoryFormatter
    ) -> None:
        memory = {
            "memory_type": "FACTUAL",
            "key_facts": ["事实A", "事实B"],
        }
        # key_facts is a list, _extract_content checks isinstance(key_facts, str)
        # So it falls through to content check
        result = formatter.format([memory])
        assert len(result) == 1

    def test_extract_content_max_length_truncation(self) -> None:
        formatter = HumanLikeMemoryFormatter(max_fragments=5, max_fragment_length=5)
        memory = {
            "memory_type": "FACTUAL",
            "content": "1234567890",
        }
        result = formatter.format([memory])
        assert len(result) == 1
        assert len(result[0]) <= 5 + 2  # "ta" prefix

    def test_resolve_type_empty_string(self) -> None:
        memory = {"memory_type": "", "content": "test"}
        result = HumanLikeMemoryFormatter._resolve_type(memory)
        assert result == "OTHER"

    def test_resolve_type_none(self) -> None:
        memory = {"content": "test"}
        result = HumanLikeMemoryFormatter._resolve_type(memory)
        assert result == "OTHER"

    def test_format_no_fragments_produced(self) -> None:
        formatter = HumanLikeMemoryFormatter()
        memories = [{"memory_type": "OTHER", "content": ""}]
        result = formatter.format(memories)
        assert result == ["没有特别的记忆浮现"]

    def test_dedup_shorter_than_4_never_overlaps(self) -> None:
        result = HumanLikeMemoryFormatter._is_overlapping("abc", ["abc", "def"])
        assert result is False

    def test_dedup_non_overlapping(self) -> None:
        result = HumanLikeMemoryFormatter._is_overlapping("abcdef", ["xyz123"])
        assert result is False

    def test_preference_without_marker_ta_prefix(
        self, formatter: HumanLikeMemoryFormatter
    ) -> None:
        memory = {
            "memory_type": "PREFERENCE",
            "content": "冰淇淋",
        }
        result = formatter.format([memory])
        assert "ta喜欢" in result[0]

    def test_episodic_no_content(self, formatter: HumanLikeMemoryFormatter) -> None:
        memory = {
            "memory_type": "EPISODIC",
        }
        result = formatter.format([memory])
        assert result == ["没有特别的记忆浮现"]

    def test_format_all_types_in_one_call(
        self, formatter: HumanLikeMemoryFormatter
    ) -> None:
        now = time.time()
        memories = [
            {
                "memory_type": "EPISODIC",
                "content": "went skiing",
                "create_time": now - 3600,
            },
            {"memory_type": "FACTUAL", "content": "Beijing is capital"},
            {"memory_type": "PREFERENCE", "content": "喜欢咖啡"},
            {"memory_type": "RELATIONAL", "content": "friend with Bob"},
        ]
        result = formatter.format(memories)
        assert len(result) >= 1
