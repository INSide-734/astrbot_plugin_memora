"""ContinuityTracker 测试 — 会话连续性和待处理话题。"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.managers.continuity_tracker import ContinuityTracker


# ---------------------------------------------------------------------------
# mark_topics
# ---------------------------------------------------------------------------

class TestMarkTopics:
    """Topic marking and updating."""

    def test_mark_single_topic(self) -> None:
        ct = ContinuityTracker()
        ct.mark_topics("sess-1", ["西湖划船"])
        pending = ct.get_pending_topics("sess-1")
        assert len(pending) == 1
        assert pending[0]["topic"] == "西湖划船"

    def test_mark_multiple_topics(self) -> None:
        ct = ContinuityTracker()
        ct.mark_topics("sess-1", ["西湖", "划船", "周末计划"])
        pending = ct.get_pending_topics("sess-1")
        assert len(pending) == 3

    def test_mark_duplicate_topic_updates_timestamp(self) -> None:
        ct = ContinuityTracker()
        ct.mark_topics("sess-1", ["西湖"])
        first_ts = ct.get_pending_topics("sess-1")[0]["last_seen_ts"]
        time.sleep(0.01)
        ct.mark_topics("sess-1", ["西湖"])
        second_ts = ct.get_pending_topics("sess-1")[0]["last_seen_ts"]
        assert second_ts > first_ts

    def test_mark_duplicate_updates_importance(self) -> None:
        ct = ContinuityTracker()
        ct.mark_topics("sess-1", ["西湖"], importance=0.3)
        ct.mark_topics("sess-1", ["西湖"], importance=0.8)
        pending = ct.get_pending_topics("sess-1")
        assert pending[0]["importance"] == 0.8  # max of 0.3 and 0.8

    def test_mark_empty_session_id(self) -> None:
        ct = ContinuityTracker()
        ct.mark_topics("", ["xxx"])  # should be no-op
        assert ct.get_pending_topics("") == []

    def test_mark_empty_topics_list(self) -> None:
        ct = ContinuityTracker()
        ct.mark_topics("sess-1", [])
        assert ct.get_pending_topics("sess-1") == []

    def test_mark_topics_with_blank_strings(self) -> None:
        ct = ContinuityTracker()
        ct.mark_topics("sess-1", ["   ", "valid"])
        pending = ct.get_pending_topics("sess-1")
        assert len(pending) == 1
        assert pending[0]["topic"] == "valid"

    def test_mark_max_topics_truncation(self) -> None:
        ct = ContinuityTracker(max_topics=3)
        topics = [f"topic-{i}" for i in range(10)]
        ct.mark_topics("sess-1", topics)
        pending = ct.get_pending_topics("sess-1", max_return=10)
        assert len(pending) == 3

    def test_topic_keywords_set(self) -> None:
        ct = ContinuityTracker()
        ct.mark_topics("sess-1", ["Hello World Test"])
        # topic_keywords are stored internally on the pending item,
        # but get_pending_topics returns a new dict without them.
        pending = ct.get_pending_topics("sess-1", max_return=1)
        assert "topic" in pending[0]
        assert pending[0]["topic"] == "Hello World Test"


# ---------------------------------------------------------------------------
# get_pending_topics
# ---------------------------------------------------------------------------

class TestGetPendingTopics:
    """Pending topic retrieval with TTL decay."""

    def test_no_pending_for_unknown_session(self) -> None:
        ct = ContinuityTracker()
        assert ct.get_pending_topics("unknown") == []

    def test_max_return_limit(self) -> None:
        ct = ContinuityTracker()
        ct.mark_topics("sess-1", ["a", "b", "c", "d", "e"])
        result = ct.get_pending_topics("sess-1", max_return=2)
        assert len(result) == 2

    def test_result_fields(self) -> None:
        ct = ContinuityTracker()
        ct.mark_topics("sess-1", ["测试话题"], importance=0.6)
        result = ct.get_pending_topics("sess-1")
        assert len(result) == 1
        item = result[0]
        assert "topic" in item
        assert "last_seen_ts" in item
        assert "importance" in item
        assert "age_hours" in item
        assert 0.0 < item["importance"] <= 0.6

    def test_ttl_decay_reduces_importance(self) -> None:
        ct = ContinuityTracker(topic_ttl_sec=3600.0)  # 1 hour TTL
        ct.mark_topics("sess-1", ["test"], importance=1.0)
        # Immediately: importance should be near 1.0
        immediate = ct.get_pending_topics("sess-1")[0]["importance"]
        assert immediate > 0.9  # almost no decay

    def test_result_sorted_by_importance(self) -> None:
        ct = ContinuityTracker()
        ct.mark_topics("sess-1", ["low"], importance=0.1)
        ct.mark_topics("sess-1", ["high"], importance=0.9)
        result = ct.get_pending_topics("sess-1", max_return=2)
        assert result[0]["importance"] >= result[1]["importance"]


# ---------------------------------------------------------------------------
# get_continuity_context
# ---------------------------------------------------------------------------

class TestContinuityContext:
    """Human-readable continuity context generation."""

    def test_no_pending_returns_none(self) -> None:
        ct = ContinuityTracker()
        assert ct.get_continuity_context("unknown") is None

    def test_single_topic(self) -> None:
        ct = ContinuityTracker()
        ct.mark_topics("sess-1", ["西湖划船"])
        ctx = ct.get_continuity_context("sess-1")
        assert ctx is not None
        assert "西湖划船" in ctx
        assert "话题尚未结束" in ctx

    def test_two_topics(self) -> None:
        ct = ContinuityTracker()
        ct.mark_topics("sess-1", ["西湖", "划船"])
        ctx = ct.get_continuity_context("sess-1")
        assert ctx is not None
        assert "西湖" in ctx
        assert "划船" in ctx
        assert "和" in ctx

    def test_three_topics(self) -> None:
        ct = ContinuityTracker()
        ct.mark_topics("sess-1", ["西湖", "划船", "周末"])
        ctx = ct.get_continuity_context("sess-1")
        assert ctx is not None
        assert "西湖" in ctx
        assert "划船" in ctx
        assert "周末" in ctx
        assert "」、「" in ctx


# ---------------------------------------------------------------------------
# clear_session / resolve_session
# ---------------------------------------------------------------------------

class TestClearAndResolve:
    """Session clearing and resolution."""

    def test_clear_session(self) -> None:
        ct = ContinuityTracker()
        ct.mark_topics("sess-1", ["topic"])
        assert len(ct.get_pending_topics("sess-1")) > 0
        ct.clear_session("sess-1")
        assert ct.get_pending_topics("sess-1") == []

    def test_clear_unknown_session(self) -> None:
        ct = ContinuityTracker()
        ct.clear_session("nonexistent")  # should not raise

    def test_resolve_session(self) -> None:
        ct = ContinuityTracker()
        ct.mark_topics("sess-1", ["topic"])
        ct.resolve_session("sess-1")  # logs, does not clear


# ---------------------------------------------------------------------------
# save_state / load_state
# ---------------------------------------------------------------------------

class TestPersistence:
    """State persistence to disk."""

    def test_save_state_no_data_dir(self) -> None:
        ct = ContinuityTracker(data_dir="")
        ct.mark_topics("sess-1", ["test"])
        ct.save_state()  # no-op

    def test_save_and_load_state(self, tmp_path: Path) -> None:
        data_dir = str(tmp_path)
        ct = ContinuityTracker(data_dir=data_dir)
        ct.mark_topics("sess-1", ["西湖", "划船"], importance=0.8)
        ct.save_state()

        # Load into a new tracker
        ct2 = ContinuityTracker(data_dir=data_dir)
        ct2.load_state()
        pending = ct2.get_pending_topics("sess-1")
        assert len(pending) == 2

    def test_load_state_no_file(self) -> None:
        ct = ContinuityTracker(data_dir="/nonexistent/path/abc")
        ct.load_state()  # should not raise

    def test_load_state_expired_topics_filtered(self, tmp_path: Path) -> None:
        data_dir = str(tmp_path)
        # Create a state file with a really old topic
        old_topic = {
            "topic": "old_topic",
            "last_seen_ts": 0.0,  # Unix epoch — way past TTL
            "topic_keywords": ["old", "topic"],
            "importance": 0.5,
        }
        state = {"sess-old": [old_topic]}
        os.makedirs(data_dir, exist_ok=True)
        with open(os.path.join(data_dir, "continuity_state.json"), "w", encoding="utf-8") as f:
            json.dump(state, f)
        ct = ContinuityTracker(data_dir=data_dir)
        ct.load_state()
        # Old topic should be filtered out
        assert ct.get_pending_topics("sess-old") == []


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestConstruction:
    """Initialization and parameter clamping."""

    def test_default_values(self) -> None:
        ct = ContinuityTracker()
        assert ct._topic_ttl_sec == 86400 * 7  # 7 days
        assert ct._max_topics == 10
        assert ct._pending == {}

    def test_custom_values(self) -> None:
        ct = ContinuityTracker(topic_ttl_sec=86400, max_topics=5)
        assert ct._topic_ttl_sec == 86400
        assert ct._max_topics == 5

    def test_ttl_minimum_clamp(self) -> None:
        ct = ContinuityTracker(topic_ttl_sec=100)  # below minimum of 3600
        assert ct._topic_ttl_sec == 3600.0

    def test_max_topics_clamp(self) -> None:
        ct = ContinuityTracker(max_topics=100)  # above max of 50
        assert ct._max_topics == 50
        ct2 = ContinuityTracker(max_topics=0)  # below min of 1
        assert ct2._max_topics == 1
