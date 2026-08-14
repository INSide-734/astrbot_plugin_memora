"""记忆更新历史追踪"""

import json
from typing import Any


class HistoryTracker:
    """追加更新历史记录（保留最近 20 条）"""

    @classmethod
    def append_update_history(
        cls,
        metadata: dict[str, Any],
        *,
        field: str,
        old_value: Any,
        new_value: Any,
        reason: str,
        timestamp: float,
    ) -> list[dict[str, Any]]:
        raw_history = metadata.get("update_history", [])
        history = raw_history if isinstance(raw_history, list) else []
        next_history = [item for item in history[-19:] if isinstance(item, dict)]
        next_history.append(
            {
                "timestamp": timestamp,
                "field": field,
                "old_value": cls._value(old_value),
                "new_value": cls._value(new_value),
                "reason": reason,
                "description": cls._description(field, old_value, new_value, reason),
            }
        )
        return next_history

    @staticmethod
    def _value(value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)

    @classmethod
    def _description(cls, field: str, old_v: Any, new_v: Any, reason: str) -> str:
        old_text = cls._short_text(old_v)
        new_text = cls._short_text(new_v)
        suffix = f" ({reason})" if reason else ""
        return f"{field}: {old_text} → {new_text}{suffix}"

    @staticmethod
    def _short_text(value: Any) -> str:
        text = str(value if value is not None else "")
        text = " ".join(text.split())
        return text if len(text) <= 64 else f"{text[:61]}..."
