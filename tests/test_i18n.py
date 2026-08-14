"""core/i18n_backend.py 测试 — i18n 本地化函数。"""

from __future__ import annotations

from unittest.mock import patch

import core.platform.resources.i18n_backend as i18n_mod


class TestGetHelper:
    """Tests for _get() — dot-notation dict access."""

    def test_top_level_key(self) -> None:
        data = {"hello": "world"}
        assert i18n_mod._get(data, "hello") == "world"

    def test_nested_key(self) -> None:
        data = {"a": {"b": {"c": "found"}}}
        assert i18n_mod._get(data, "a.b.c") == "found"

    def test_missing_key_returns_none(self) -> None:
        data = {"a": 1}
        assert i18n_mod._get(data, "nonexistent") is None

    def test_missing_intermediate_returns_none(self) -> None:
        data = {"a": {"b": 2}}
        assert i18n_mod._get(data, "a.c.d") is None

    def test_key_ending_at_non_dict(self) -> None:
        data = {"a": "string_value"}
        assert i18n_mod._get(data, "a.b") is None

    def test_empty_key_returns_none(self) -> None:
        data = {"a": 1}
        assert i18n_mod._get(data, "") is None

    def test_empty_dict(self) -> None:
        assert i18n_mod._get({}, "any.key") is None


class TestTranslationFunction:
    """Tests for t() function."""

    def test_falls_back_to_key_when_no_translations(self) -> None:
        """When neither translations nor fallback has the key, return the key itself."""
        assert i18n_mod.t("some.missing.key") == "some.missing.key"

    def test_value_is_none_returns_key(self) -> None:
        """When _get returns None, fall back to key."""
        result = i18n_mod.t("completely.nonexistent.key")
        assert result == "completely.nonexistent.key"


class TestTranslateList:
    """Tests for t_list() function."""

    def test_returns_empty_list_for_missing_key(self) -> None:
        result = i18n_mod.t_list("nonexistent.list.key")
        assert result == []

    def test_returns_list_of_strings(self) -> None:
        """If a list value is found, all items are converted to strings."""
        with patch("core.platform.resources.i18n_backend._get", return_value=[1, 2, 3]):
            result = i18n_mod.t_list("some.list")
            assert result == ["1", "2", "3"]

    def test_returns_empty_list_for_non_list_value(self) -> None:
        """When the value exists but is not a list, return empty list."""
        with patch(
            "core.platform.resources.i18n_backend._get", return_value="not_a_list"
        ):
            result = i18n_mod.t_list("some.non.list")
            assert result == []


class TestInit:
    """Tests for init() function — uses module-level attribute access to avoid stale references."""

    def test_language_validation_accepts_valid(self) -> None:
        """Valid languages should be accepted (files exist for zh/en/ru)."""
        i18n_mod.init("zh")
        assert i18n_mod._current_lang == "zh"

    def test_invalid_language_falls_back_to_zh(self) -> None:
        i18n_mod.init("fr")
        assert i18n_mod._current_lang == "zh"

    def test_none_language_falls_back_to_zh(self) -> None:
        i18n_mod.init(None)  # type: ignore[arg-type]
        assert i18n_mod._current_lang == "zh"

    def test_empty_string_falls_back_to_zh(self) -> None:
        i18n_mod.init("")
        assert i18n_mod._current_lang == "zh"

    def test_en_language_is_accepted(self) -> None:
        """en.json exists, so init('en') should set lang=en."""
        i18n_mod.init("en")
        assert i18n_mod._current_lang == "en"

    def test_ru_language_is_accepted(self) -> None:
        """ru.json exists, so init('ru') should set lang=ru."""
        i18n_mod.init("ru")
        assert i18n_mod._current_lang == "ru"

    def test_zh_fallback_populated_after_init(self) -> None:
        """After init() with zh, _fallback should contain keys from zh.json."""
        i18n_mod.init("zh")
        assert isinstance(i18n_mod._fallback, dict)
        assert len(i18n_mod._fallback) > 0
