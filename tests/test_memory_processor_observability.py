"""反思生成阶段的隐私安全诊断契约。"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import core.features.recall.processors.reflection_generation_observability as generation_observability
from core.features.recall.processors.memory_processor import MemoryProcessor
from core.models.conversation_models import Message


@pytest.mark.asyncio
async def test_process_conversation_reports_safe_generation_stage_scalars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """生成诊断必须覆盖六个阶段且只包含允许的标量。"""

    source = "我喜欢喝咖啡。"
    payload = {
        "memories": [
            {
                "summary": "用户喜欢喝咖啡。",
                "key_facts": ["用户喜欢喝咖啡。"],
                "topics": ["咖啡偏好"],
                "importance": 0.8,
                "sentiment": "positive",
                "source_refs": [{"message_index": 0, "start": 0, "end": len(source)}],
            }
        ],
        "confidence": 0.9,
        "extraction_quality": "high",
    }
    provider = MagicMock()
    provider.text_chat = AsyncMock(
        return_value=SimpleNamespace(
            completion_text=json.dumps(payload, ensure_ascii=False),
            usage=SimpleNamespace(input=120, output=40),
        )
    )
    events: list[dict[str, object]] = []

    def capture_event(event_name: str, **fields: object) -> None:
        """捕获阶段事件而不保存 Prompt 或响应正文。"""

        events.append({"event": event_name, **fields})

    monkeypatch.setattr(generation_observability, "report_debug_event", capture_event)
    processor = MemoryProcessor(llm_provider=provider)
    messages = [
        Message(
            id=1,
            session_id="session-1",
            role="user",
            content=source,
            sender_id="user-1",
            sender_name="Alice",
            timestamp=time.time(),
        )
    ]

    result = await processor.process_conversation(messages)

    assert len(result) == 1
    assert [event["stage"] for event in events] == [
        "prompt_build",
        "provider",
        "parse",
        "segmentation",
        "grounding",
        "window_total",
    ]
    provider_event = events[1]
    assert provider_event["prompt_tokens"] == 120
    assert provider_event["completion_tokens"] == 40
    assert int(provider_event["prompt_chars"]) > 0
    assert int(provider_event["response_chars"]) > 0
    assert all(float(event["duration_ms"]) >= 0 for event in events)
    forbidden = {"prompt", "response", "content", "source_refs", "session_id"}
    assert all(forbidden.isdisjoint(event) for event in events)
