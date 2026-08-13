"""测试 quality_validator.py — QualityValidator."""

from __future__ import annotations

import pytest

from core.features.recall.processors.quality_validator import QualityValidator


class TestValidateSummaryQuality:
    @pytest.fixture
    def validator(self) -> QualityValidator:
        return QualityValidator()

    def test_normal_quality(self, validator: QualityValidator) -> None:
        data = {
            "summary": "这是一个正常的摘要内容",
            "key_facts": ["事实1", "事实2"],
            "importance": 0.7,
        }
        assert validator.validate_summary_quality(data) == "normal"

    def test_low_quality_empty_summary(self, validator: QualityValidator) -> None:
        data = {"summary": "", "key_facts": ["f1"], "importance": 0.5}
        assert validator.validate_summary_quality(data) == "low"

    def test_low_quality_short_summary(self, validator: QualityValidator) -> None:
        data = {"summary": "短", "key_facts": ["f1"], "importance": 0.5}
        assert validator.validate_summary_quality(data) == "low"

    def test_low_quality_no_key_facts(self, validator: QualityValidator) -> None:
        data = {"summary": "正常长度的摘要内容", "key_facts": [], "importance": 0.5}
        assert validator.validate_summary_quality(data) == "low"

    def test_low_quality_invalid_importance(self, validator: QualityValidator) -> None:
        data = {"summary": "正常摘要", "key_facts": ["f1"], "importance": "invalid"}
        assert validator.validate_summary_quality(data) == "low"

    def test_low_quality_negative_importance(self, validator: QualityValidator) -> None:
        data = {"summary": "正常摘要", "key_facts": ["f1"], "importance": -0.5}
        assert validator.validate_summary_quality(data) == "low"

    def test_low_quality_generic_terms(self, validator: QualityValidator) -> None:
        data = {
            "summary": "某用户说了一些事情",
            "key_facts": ["f1"],
            "importance": 0.5,
        }
        assert validator.validate_summary_quality(data) == "low"


class TestNormalizeParsedData:
    @pytest.fixture
    def validator(self) -> QualityValidator:
        return QualityValidator()

    def test_normalize_private_chat(self, validator: QualityValidator) -> None:
        data = {
            "summary": "测试摘要",
            "topics": ["话题1"],
            "key_facts": ["事实1"],
            "sentiment": "positive",
            "importance": 0.8,
        }
        result = validator.normalize_parsed_data(data, is_group_chat=False)
        assert result["summary"] == "测试摘要"
        assert result["importance"] == 0.8

    def test_normalize_group_chat_adds_participants(
        self, validator: QualityValidator
    ) -> None:
        data = {
            "summary": "群聊摘要",
            "topics": ["讨论"],
            "key_facts": ["fact1"],
            "sentiment": "neutral",
            "importance": 0.5,
        }
        result = validator.normalize_parsed_data(data, is_group_chat=True)
        assert "participants" in result

    def test_normalize_missing_fields(self, validator: QualityValidator) -> None:
        data = {}
        result = validator.normalize_parsed_data(data, is_group_chat=False)
        assert result["summary"] == ""
        assert result["topics"] == []
        assert result["sentiment"] == "neutral"


class TestEnsureList:
    def test_list_input(self) -> None:
        result = QualityValidator.ensure_list(["a", "b"])
        assert result == ["a", "b"]

    def test_string_input(self) -> None:
        result = QualityValidator.ensure_list("hello")
        assert result == ["hello"]

    def test_empty_string(self) -> None:
        result = QualityValidator.ensure_list("")
        assert result == []

    def test_non_string_non_list(self) -> None:
        result = QualityValidator.ensure_list(123)
        assert result == []

    def test_none(self) -> None:
        result = QualityValidator.ensure_list(None)
        assert result == []


class TestValidateSentiment:
    def test_valid_sentiments(self) -> None:
        assert QualityValidator.validate_sentiment("positive") == "positive"
        assert QualityValidator.validate_sentiment("neutral") == "neutral"
        assert QualityValidator.validate_sentiment("negative") == "negative"

    def test_case_insensitive(self) -> None:
        assert QualityValidator.validate_sentiment("POSITIVE") == "positive"
        assert QualityValidator.validate_sentiment("Negative") == "negative"

    def test_invalid_falls_back_to_neutral(self) -> None:
        assert QualityValidator.validate_sentiment("happy") == "neutral"
        assert QualityValidator.validate_sentiment("sad") == "neutral"
        assert QualityValidator.validate_sentiment("") == "neutral"


class TestValidateImportance:
    def test_valid_importance(self) -> None:
        assert QualityValidator.validate_importance(0.6) == 0.6

    def test_clamped_high(self) -> None:
        assert QualityValidator.validate_importance(2.0) == 1.0

    def test_clamped_low(self) -> None:
        assert QualityValidator.validate_importance(-1.0) == 0.0

    def test_invalid_type_returns_default(self) -> None:
        assert QualityValidator.validate_importance("not a number") == 0.5
        assert QualityValidator.validate_importance(None) == 0.5


class TestDefaultValues:
    def test_get_default_value(self) -> None:
        assert QualityValidator.get_default_value("summary") == ""
        assert QualityValidator.get_default_value("topics") == []
        assert QualityValidator.get_default_value("sentiment") == "neutral"
        assert QualityValidator.get_default_value("importance") == 0.5

    def test_unknown_field(self) -> None:
        assert QualityValidator.get_default_value("unknown") == ""

    def test_default_structured_data_private(self) -> None:
        data = QualityValidator.get_default_structured_data(is_group_chat=False)
        assert data["summary"] == "对话记录"
        assert "participants" not in data

    def test_default_structured_data_group(self) -> None:
        data = QualityValidator.get_default_structured_data(is_group_chat=True)
        assert data["summary"] == "对话记录"
        assert "participants" in data
