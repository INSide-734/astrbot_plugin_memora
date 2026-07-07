"""数据校验与规范化"""

from typing import Any


class QualityValidator:
    """总结质量校验 + 数据规范化"""

    @staticmethod
    def validate_summary_quality(structured_data: dict[str, Any]) -> str:
        summary = structured_data.get("summary", "")
        key_facts = structured_data.get("key_facts", [])
        importance = structured_data.get("importance", 0.5)

        if not summary or len(summary.strip()) < 10:
            return "low"
        if not key_facts:
            return "low"
        if not isinstance(importance, (int, float)) or not (0.0 <= importance <= 1.0):
            return "low"

        generic_terms = [
            "某用户",
            "有人",
            "某人",
            "用户说",
            "对方说",
            "群成员",
            "某群成员",
        ]
        if any(term in summary for term in generic_terms):
            return "low"

        return "normal"

    def normalize_parsed_data(self, data: dict, is_group_chat: bool) -> dict[str, Any]:
        required_fields = ["summary", "topics", "key_facts", "sentiment", "importance"]
        if is_group_chat:
            required_fields.append("participants")

        for field in required_fields:
            if field not in data:
                data[field] = self.get_default_value(field)

        data["summary"] = str(data.get("summary", ""))
        data["topics"] = self.ensure_list(data.get("topics", []))[:5]
        data["key_facts"] = self.ensure_list(data.get("key_facts", []))[:5]
        data["sentiment"] = self.validate_sentiment(data.get("sentiment", "neutral"))
        data["importance"] = self.validate_importance(data.get("importance", 0.5))

        if is_group_chat:
            data["participants"] = self.ensure_list(data.get("participants", []))

        return data

    @staticmethod
    def ensure_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if item]
        elif isinstance(value, str):
            return [value] if value else []
        else:
            return []

    @staticmethod
    def validate_sentiment(sentiment: str) -> str:
        valid_sentiments = ["positive", "neutral", "negative"]
        sentiment = sentiment.lower()
        return sentiment if sentiment in valid_sentiments else "neutral"

    @staticmethod
    def validate_importance(importance: Any) -> float:
        try:
            score = float(importance)
            return max(0.0, min(1.0, score))
        except (ValueError, TypeError):
            return 0.5

    @staticmethod
    def get_default_value(field: str) -> Any:
        defaults = {
            "summary": "",
            "topics": [],
            "key_facts": [],
            "participants": [],
            "sentiment": "neutral",
            "importance": 0.5,
        }
        return defaults.get(field, "")

    @staticmethod
    def get_default_structured_data(is_group_chat: bool) -> dict[str, Any]:
        data = {
            "summary": "对话记录",
            "topics": [],
            "key_facts": [],
            "sentiment": "neutral",
            "importance": 0.5,
        }
        if is_group_chat:
            data["participants"] = []
        return data
