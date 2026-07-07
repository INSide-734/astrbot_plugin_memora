"""测试发送者解析 — sender name resolution from AstrBot events."""

from __future__ import annotations

import pytest

from core.managers.sender_resolver import (
    _format_raw_user_name,
    _iter_raw_sender_candidates,
    _normalize_sender_name,
    _raw_get,
    _resolve_sender_name,
)


# ---------------------------------------------------------------------------
# _normalize_sender_name tests
# ---------------------------------------------------------------------------


class TestNormalizeSenderName:
    """测试 _normalize_sender_name 函数。"""

    def test_none_input(self) -> None:
        """None input returns None."""
        assert _normalize_sender_name(None) is None

    def test_empty_string(self) -> None:
        """空 string returns None (in UNKNOWN_SENDER_NAMES)."""
        assert _normalize_sender_name("") is None

    @pytest.mark.parametrize(
        "name",
        ["unknown", "Unknown", "none", "null", "n/a", "na", "user_", "tg", "未知"],
    )
    def test_unknown_placeholder_names(self, name: str) -> None:
        """已知 placeholder names are filtered out (return None)."""
        assert _normalize_sender_name(name) is None

    def test_valid_name(self) -> None:
        """A valid readable name is returned as-is."""
        assert _normalize_sender_name("Alice") == "Alice"

    def test_whitespace_trimmed(self) -> None:
        """Whitespace is stripped."""
        assert _normalize_sender_name("  Bob  ") == "Bob"

    def test_case_insensitive_matching(self) -> None:
        """Placeholder matching is case-insensitive."""
        assert _normalize_sender_name("USER") is None
        assert _normalize_sender_name("None") is None
        assert _normalize_sender_name("N/A") is None

    def test_non_string_converted_to_string(self) -> None:
        """Non-string values are converted to string."""
        assert _normalize_sender_name(123) == "123"


# ---------------------------------------------------------------------------
# _raw_get tests
# ---------------------------------------------------------------------------


class TestRawGet:
    """测试 _raw_get 辅助函数。"""

    def test_none_obj(self) -> None:
        """None object returns None for any key."""
        assert _raw_get(None, "any_key") is None

    def test_dict_access(self) -> None:
        """字典 behavior: returns value for key or None if missing."""
        assert _raw_get({"name": "Alice"}, "name") == "Alice"
        assert _raw_get({"name": "Alice"}, "missing") is None

    def test_object_access(self) -> None:
        """Object behavior: returns attribute value or None if missing."""

        class User:
            name = "Bob"

        assert _raw_get(User(), "name") == "Bob"
        assert _raw_get(User(), "missing") is None


# ---------------------------------------------------------------------------
# _format_raw_user_name tests
# ---------------------------------------------------------------------------


class TestFormatRawUserName:
    """测试 _format_raw_user_name 函数。"""

    def test_username_first(self) -> None:
        """Username is returned first if available and valid."""
        raw_user = {"username": "alice99", "first_name": "Alice"}
        result = _format_raw_user_name(raw_user, "fallback_id")
        assert result == "alice99"

    def test_falls_back_to_first_last(self) -> None:
        """当 username is empty, falls back to first_name + last_name."""
        raw_user = {"first_name": "Alice", "last_name": "Smith"}
        result = _format_raw_user_name(raw_user, "fallback_id")
        assert result == "Alice Smith"

    def test_falls_back_to_full_name(self) -> None:
        """当 first/last are empty, falls back to full_name."""
        raw_user = {"full_name": "Alice Smith"}
        result = _format_raw_user_name(raw_user, "fallback_id")
        assert result == "Alice Smith"

    def test_falls_back_to_id(self) -> None:
        """当 names are empty, falls back to raw id."""
        raw_user = {"id": 12345}
        result = _format_raw_user_name(raw_user, "fallback_id")
        assert result == "12345"

    def test_falls_back_to_sender_id(self) -> None:
        """当 no fields are valid, falls back to sender_id."""
        raw_user = {"username": "unknown"}
        result = _format_raw_user_name(raw_user, "fallback_sender")
        assert result == "fallback_sender"

    def test_unknown_placeholders_filtered(self) -> None:
        """未知 placeholders in name fields are filtered out."""
        raw_user = {
            "username": "unknown",
            "first_name": "none",
            "full_name": "Alice",
        }
        result = _format_raw_user_name(raw_user, "fallback")
        assert result == "Alice"

    def test_object_access_too(self) -> None:
        """Also works with object attributes via _raw_get."""

        class RawUser:
            username = "charlie"

        result = _format_raw_user_name(RawUser(), "fallback")
        assert result == "charlie"


# ---------------------------------------------------------------------------
# _resolve_sender_name tests
# ---------------------------------------------------------------------------


class TestResolveSenderName:
    """测试 _resolve_sender_name function."""

    def test_uses_get_sender_name_from_event(self) -> None:
        """当 event has get_sender_name(), it is used."""
        from unittest.mock import MagicMock

        event = MagicMock()
        event.get_sender_name.return_value = "Alice"
        result = _resolve_sender_name(event, "sender-123")
        assert result == "Alice"

    def test_uses_sender_name_attribute(self) -> None:
        """当 get_sender_name() is not available, sender_name attr is used."""
        from unittest.mock import MagicMock

        event = MagicMock(spec=["sender_name", "message_obj"])
        event.sender_name = "Bob"
        event.message_obj = MagicMock(spec=["sender"])
        event.message_obj.sender = MagicMock(spec=["first_name", "last_name", "nickname"])
        event.message_obj.sender.first_name = None
        event.message_obj.sender.last_name = None
        event.message_obj.sender.nickname = None

        result = _resolve_sender_name(event, "sender-456")
        assert result == "Bob"

    def test_normalized_sender_name_placeholder(self) -> None:
        """当 sender_name is a placeholder, falls through to raw sender."""
        from unittest.mock import MagicMock

        event = MagicMock()
        event.get_sender_name.return_value = "unknown"  # placeholder
        # Provide raw sender first/last name as fallback
        event.message_obj = MagicMock()
        event.message_obj.sender = MagicMock()
        event.message_obj.sender.first_name = "Charlie"
        event.message_obj.sender.last_name = "Brown"

        result = _resolve_sender_name(event, "sender-789")
        assert result == "Charlie Brown"

    def test_falls_back_to_raw_sender_nickname(self) -> None:
        """当 no sender name and no first/last, uses raw sender nickname."""
        from unittest.mock import MagicMock

        event = MagicMock()
        event.get_sender_name.return_value = None
        event.message_obj = MagicMock()
        event.message_obj.sender = MagicMock()
        event.message_obj.sender.first_name = None
        event.message_obj.sender.last_name = None
        event.message_obj.sender.nickname = "Nick456"

        result = _resolve_sender_name(event, "sender-000")
        assert result == "Nick456"

    def test_falls_back_to_sender_id(self) -> None:
        """当 all sources fail, falls back to sender_id."""
        from unittest.mock import MagicMock

        event = MagicMock()
        event.get_sender_name.return_value = None
        event.message_obj = MagicMock(spec=["sender", "raw_message"])
        event.message_obj.sender = None
        # raw_message exists but has None for all candidates and no extra attributes
        raw = MagicMock(spec=["from_user", "message", "effective_message",
                              "callback_query", "effective_user"])
        raw.from_user = None
        raw.message = None
        raw.effective_message = None
        raw.callback_query = None
        raw.effective_user = None
        event.message_obj.raw_message = raw

        result = _resolve_sender_name(event, "final-fallback")
        assert result == "final-fallback"

    def test_callback_query_from_user_extraction(self) -> None:
        """当 raw_message has callback_query, from_user is extracted."""
        from unittest.mock import MagicMock

        event = MagicMock()
        event.get_sender_name.return_value = None
        event.message_obj = MagicMock()
        # sender has no valid names
        event.message_obj.sender = MagicMock(spec=["first_name", "last_name", "nickname"])
        event.message_obj.sender.first_name = None
        event.message_obj.sender.last_name = None
        event.message_obj.sender.nickname = None

        # Set up raw_message with callback_query->from_user
        raw = MagicMock(spec=["from_user", "message", "effective_message",
                              "callback_query", "effective_user"])
        raw.from_user = None
        raw.message = None
        raw.effective_message = None
        raw.effective_user = None
        cq = MagicMock(spec=["from_user"])
        cq.from_user = {"username": "callback_user_99"}
        raw.callback_query = cq
        event.message_obj.raw_message = raw

        result = _resolve_sender_name(event, "sid")
        assert result == "callback_user_99"

    def test_effective_user_extracted(self) -> None:
        """当 raw_message has effective_user, it is tried."""
        from unittest.mock import MagicMock

        event = MagicMock()
        event.get_sender_name.return_value = None
        event.message_obj = MagicMock()
        event.message_obj.sender = MagicMock(spec=["first_name", "last_name", "nickname"])
        event.message_obj.sender.first_name = None
        event.message_obj.sender.last_name = None
        event.message_obj.sender.nickname = None

        raw = MagicMock(spec=["from_user", "message", "effective_message",
                              "callback_query", "effective_user"])
        raw.from_user = None
        raw.message = None
        raw.callback_query = None
        raw.effective_message = MagicMock(spec=["from_user"])
        raw.effective_message.from_user = None
        eu = MagicMock(spec=["username", "first_name", "last_name", "full_name", "id"])
        eu.username = None
        eu.first_name = "EffectiveBob"
        eu.last_name = None
        eu.full_name = None
        eu.id = None
        raw.effective_user = eu
        event.message_obj.raw_message = raw

        result = _resolve_sender_name(event, "sid")
        assert result == "EffectiveBob"

    def test_first_name_only_from_raw_sender(self) -> None:
        """当 only first_name is available on raw sender, it is used."""
        from unittest.mock import MagicMock

        event = MagicMock()
        event.get_sender_name.return_value = None
        event.message_obj = MagicMock()
        event.message_obj.sender = MagicMock()
        event.message_obj.sender.first_name = "Eva"
        event.message_obj.sender.last_name = None
        event.message_obj.sender.nickname = None

        result = _resolve_sender_name(event, None)
        assert result == "Eva"

    def test_sender_id_normalized_when_placeholder(self) -> None:
        """当 sender_id itself is a placeholder, returns None."""
        from unittest.mock import MagicMock

        event = MagicMock()
        event.get_sender_name.return_value = None
        event.message_obj = MagicMock(spec=["sender", "raw_message"])
        event.message_obj.sender = None
        raw = MagicMock(spec=["from_user", "message", "effective_message",
                              "callback_query", "effective_user"])
        raw.from_user = None
        raw.message = None
        raw.effective_message = None
        raw.callback_query = None
        raw.effective_user = None
        event.message_obj.raw_message = raw

        result = _resolve_sender_name(event, "unknown")
        assert result is None


# ---------------------------------------------------------------------------
# _iter_raw_sender_candidates tests
# ---------------------------------------------------------------------------


class TestIterRawSenderCandidates:
    """测试 _iter_raw_sender_candidates generator."""

    def test_yields_from_user_from_raw_message(self) -> None:
        """Yield from_user from raw_message."""
        from unittest.mock import MagicMock

        event = MagicMock()
        user = MagicMock()
        raw = MagicMock(spec=["from_user", "message", "effective_message",
                              "callback_query", "effective_user"])
        raw.from_user = user
        raw.message = None
        raw.effective_message = None
        raw.callback_query = None
        raw.effective_user = None
        event.message_obj = MagicMock()
        event.message_obj.raw_message = raw

        candidates = list(_iter_raw_sender_candidates(event))
        assert len(candidates) == 1
        assert candidates[0] is user

    def test_yields_from_message_nested(self) -> None:
        """Yield from_user from nested raw_message->message."""
        from unittest.mock import MagicMock

        event = MagicMock()
        user = MagicMock()
        inner = MagicMock(spec=["from_user"])
        inner.from_user = user
        raw = MagicMock(spec=["from_user", "message", "effective_message",
                              "callback_query", "effective_user"])
        raw.from_user = None
        raw.message = inner
        raw.effective_message = None
        raw.callback_query = None
        raw.effective_user = None
        event.message_obj = MagicMock()
        event.message_obj.raw_message = raw

        candidates = list(_iter_raw_sender_candidates(event))
        assert len(candidates) == 1
        assert candidates[0] is user

    def test_yields_from_effective_message_and_user(self) -> None:
        """Yield from_user from effective_message and effective_user."""
        from unittest.mock import MagicMock

        event = MagicMock()
        raw = MagicMock()
        raw.from_user = None
        raw.message = None
        raw.effective_message = MagicMock()
        raw.effective_message.from_user = None
        raw.callback_query = None
        eff_user = MagicMock()
        raw.effective_user = eff_user
        event.message_obj = MagicMock()
        event.message_obj.raw_message = raw

        candidates = list(_iter_raw_sender_candidates(event))
        assert len(candidates) == 1
        assert candidates[0] is eff_user
