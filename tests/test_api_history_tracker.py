"""core/api/history_tracker.py — HistoryTracker 类测试。"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from core.api.history_tracker import HistoryTracker


class TestHistoryTrackerAppend:
    """append_update_history 构建并限制更新历史长度。"""

    def test_appends_first_entry_to_empty_metadata(self) -> None:
        metadata: dict[str, Any] = {}
        ts = time.time()
        result = HistoryTracker.append_update_history(
            metadata,
            field="importance",
            old_value=0.5,
            new_value=0.8,
            reason="user feedback",
            timestamp=ts,
        )
        assert isinstance(result, list)
        assert len(result) == 1
        entry = result[0]
        assert entry["field"] == "importance"
        assert entry["old_value"] == 0.5
        assert entry["new_value"] == 0.8
        assert entry["reason"] == "user feedback"
        assert entry["timestamp"] == ts

    def test_appends_to_existing_history(self) -> None:
        metadata: dict[str, Any] = {
            "update_history": [
                {
                    "timestamp": 1000.0,
                    "field": "status",
                    "old_value": "active",
                    "new_value": "archived",
                    "reason": "",
                    "description": "status: active -> archived",
                }
            ]
        }
        ts = 2000.0
        result = HistoryTracker.append_update_history(
            metadata,
            field="importance",
            old_value=0.5,
            new_value=0.8,
            reason="user feedback",
            timestamp=ts,
        )
        assert len(result) == 2
        assert result[0]["field"] == "status"
        assert result[1]["field"] == "importance"

    def test_caps_history_at_20_entries(self) -> None:
        metadata: dict[str, Any] = {}
        for i in range(25):
            metadata["update_history"] = HistoryTracker.append_update_history(
                metadata,
                field=f"field_{i}",
                old_value=i,
                new_value=i + 1,
                reason="",
                timestamp=float(i),
            )
        result = metadata["update_history"]
        assert len(result) == 20
        # Should keep the most recent 20 entries
        assert result[0]["field"] == "field_5"
        assert result[-1]["field"] == "field_24"

    def test_includes_description_field(self) -> None:
        metadata: dict[str, Any] = {}
        ts = time.time()
        result = HistoryTracker.append_update_history(
            metadata,
            field="content",
            old_value="hello",
            new_value="hello world",
            reason="correction",
            timestamp=ts,
        )
        assert "description" in result[0]
        assert "hello" in result[0]["description"]
        assert "hello world" in result[0]["description"]
        assert "(correction)" in result[0]["description"]

    def test_no_reason_omits_suffix(self) -> None:
        metadata: dict[str, Any] = {}
        ts = time.time()
        result = HistoryTracker.append_update_history(
            metadata,
            field="status",
            old_value="active",
            new_value="deleted",
            reason="",
            timestamp=ts,
        )
        assert "(" not in result[0]["description"]

    def test_handles_non_list_raw_history(self) -> None:
        metadata: dict[str, Any] = {"update_history": "not_a_list"}
        ts = time.time()
        result = HistoryTracker.append_update_history(
            metadata,
            field="type",
            old_value="GENERAL",
            new_value="FACTUAL",
            reason="",
            timestamp=ts,
        )
        assert len(result) == 1
        assert result[0]["field"] == "type"

    def test_handles_none_raw_history(self) -> None:
        metadata: dict[str, Any] = {"update_history": None}
        ts = time.time()
        result = HistoryTracker.append_update_history(
            metadata,
            field="type",
            old_value="EPISODIC",
            new_value="FACTUAL",
            reason="",
            timestamp=ts,
        )
        assert len(result) == 1

    def test_does_not_mutate_original_metadata(self) -> None:
        metadata: dict[str, Any] = {"other_key": "val"}
        ts = time.time()
        result = HistoryTracker.append_update_history(
            metadata,
            field="importance",
            old_value=0.3,
            new_value=0.9,
            reason="test",
            timestamp=ts,
        )
        assert result is not metadata
        assert "update_history" not in metadata  # original unchanged


class TestHistoryTrackerValue:
    """历史条目的 _value 序列化。"""

    def test_string_passes_through(self) -> None:
        assert HistoryTracker._value("hello") == "hello"

    def test_int_passes_through(self) -> None:
        assert HistoryTracker._value(42) == 42

    def test_float_passes_through(self) -> None:
        assert HistoryTracker._value(3.14) == 3.14

    def test_bool_passes_through(self) -> None:
        assert HistoryTracker._value(True) is True

    def test_none_passes_through(self) -> None:
        assert HistoryTracker._value(None) is None

    def test_list_jsonifies(self) -> None:
        result = HistoryTracker._value([1, 2, 3])
        assert isinstance(result, str)
        assert json.loads(result) == [1, 2, 3]

    def test_dict_jsonifies(self) -> None:
        result = HistoryTracker._value({"key": "val"})
        assert isinstance(result, str)
        assert json.loads(result) == {"key": "val"}

    def test_unserializable_falls_back_to_str(self) -> None:
        class Unserializable:
            def __str__(self) -> str:
                return "custom_str"

        result = HistoryTracker._value(Unserializable())
        assert result == "custom_str"


class TestHistoryTrackerShortText:
    """_short_text 截断过长值。"""

    def test_short_text_stays_as_is(self) -> None:
        assert HistoryTracker._short_text("hello") == "hello"

    def test_long_text_truncated_to_64_chars(self) -> None:
        long_text = "x" * 100
        result = HistoryTracker._short_text(long_text)
        assert len(result) <= 64
        assert result.endswith("...")

    def test_exactly_64_chars_not_truncated(self) -> None:
        text = "a" * 64
        result = HistoryTracker._short_text(text)
        assert result == text
        assert not result.endswith("...")

    def test_65_chars_truncated(self) -> None:
        text = "a" * 65
        result = HistoryTracker._short_text(text)
        assert len(result) == 64
        assert result.endswith("...")

    def test_none_becomes_empty_string(self) -> None:
        assert HistoryTracker._short_text(None) == ""

    def test_whitespace_collapsed(self) -> None:
        assert HistoryTracker._short_text("a   b\n\tc") == "a b c"


class TestHistoryTrackerDescription:
    """_description 构建人类可读的更新摘要。"""

    def test_description_with_reason(self) -> None:
        desc = HistoryTracker._description(
            field="importance", old_v=0.5, new_v=0.8, reason="user corrected"
        )
        assert desc.startswith("importance:")
        assert "0.5" in desc
        assert "0.8" in desc
        assert "(user corrected)" in desc

    def test_description_without_reason(self) -> None:
        desc = HistoryTracker._description(
            field="status", old_v="active", new_v="archived", reason=""
        )
        assert "(" not in desc
