"""RecallHandler 的 projection 可见字段清理测试。"""

from types import SimpleNamespace

from core.handlers.recall_handler import RecallHandler


def test_safe_candidates_only_keeps_projection_allowlist() -> None:
    candidate = SimpleNamespace(
        doc_id=17,
        content="canonical",
        final_score=0.9,
        metadata={
            "derived_projections": [
                {
                    "type": "episode_summary",
                    "summary": "  摘要  ",
                    "confidence": 1.5,
                    "projection_id": "内部编号",
                    "source_memory_ids": [17, 18],
                    "revision_token": "内部版本",
                }
            ]
        },
    )

    safe = RecallHandler._safe_candidates([candidate])[0]

    assert safe["metadata"]["derived_projections"] == [
        {"type": "episode_summary", "summary": "摘要", "confidence": 1.0}
    ]
    assert "projection_id" not in safe["metadata"]["derived_projections"][0]
    assert "source_memory_ids" not in safe["metadata"]["derived_projections"][0]
    assert safe["content"] == "canonical"
    assert safe["id"] == 17


def test_safe_candidates_drops_invalid_projection_without_dropping_memory() -> None:
    candidate = SimpleNamespace(
        doc_id=18,
        content="canonical",
        final_score=0.8,
        metadata={
            "derived_projections": [
                {"type": "unknown", "summary": "不应出现", "confidence": 0.8},
                {"type": "episode_summary", "summary": "", "confidence": 0.8},
                {"type": "episode_summary", "summary": "非法置信度", "confidence": "nan"},
            ]
        },
    )

    safe = RecallHandler._safe_candidates([candidate])[0]

    assert "derived_projections" not in safe["metadata"]
    assert safe["content"] == "canonical"
    assert safe["id"] == 18
