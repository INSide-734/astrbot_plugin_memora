"""core.security.guardrails 测试 — Pydantic LLM 输出验证。"""

from __future__ import annotations

from pathlib import Path
import sys

# Ensure plugin root on path
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

import pytest
from pydantic import ValidationError
from core.security.guardrails import (
    GraphExtractionResult,
    MemoryAtomSchema,
    MemoryExtractionResult,
    safe_validate,
    validate_and_clean_json,
    validate_llm_response,
)


# =============================================================================
# MemoryAtomSchema
# =============================================================================

class TestMemoryAtomSchema:
    """记忆原子 schema 测试"""

    def test_valid_atom_minimal(self):
        atom = MemoryAtomSchema(content="用户喜欢猫")
        assert atom.content == "用户喜欢猫"
        assert atom.atom_type == "fact"
        assert atom.importance == 0.5

    def test_valid_atom_full(self):
        atom = MemoryAtomSchema(
            content="用户上周去了北京旅游",
            atom_type="event",
            importance=0.9,
            entities=["北京", "用户"],
            emotion_tags=["开心"],
            confidence=0.95,
        )
        assert atom.entities == ["北京", "用户"]
        assert atom.confidence == 0.95

    def test_content_too_short_raises(self):
        with pytest.raises(ValidationError):
            MemoryAtomSchema(content="ab")

    def test_content_too_long_raises(self):
        with pytest.raises(ValidationError):
            MemoryAtomSchema(content="x" * 2001)

    def test_importance_out_of_range_raises(self):
        with pytest.raises(ValidationError):
            MemoryAtomSchema(content="正常内容", importance=1.5)

        with pytest.raises(ValidationError):
            MemoryAtomSchema(content="正常内容", importance=-0.1)

    def test_invalid_atom_type_raises(self):
        with pytest.raises(ValidationError):
            MemoryAtomSchema(content="正常内容", atom_type="invalid_type")

    def test_content_blank_stripped_raises(self):
        with pytest.raises(ValidationError):
            MemoryAtomSchema(content="   ")

    def test_default_values(self):
        atom = MemoryAtomSchema(content="测试记忆内容")
        assert atom.atom_type == "fact"
        assert atom.importance == 0.5
        assert atom.entities == []
        assert atom.emotion_tags == []
        assert atom.confidence is None


# =============================================================================
# MemoryExtractionResult
# =============================================================================

class TestMemoryExtractionResult:
    """记忆抽取结果测试"""

    def test_valid_extraction_result(self):
        result_data = {
            "memories": [
                {"content": "用户喜欢咖啡"},
                {"content": "用户在学Python", "atom_type": "knowledge"},
            ],
            "confidence": 0.8,
            "extraction_quality": "high",
        }
        result = MemoryExtractionResult(**result_data)
        assert len(result.memories) == 2
        assert result.confidence == 0.8

    def test_empty_memories(self):
        result = MemoryExtractionResult()
        assert result.memories == []
        assert result.confidence == 0.5

    def test_invalid_confidence_raises(self):
        with pytest.raises(ValidationError):
            MemoryExtractionResult(confidence=1.5)

    def test_invalid_quality_raises(self):
        with pytest.raises(ValidationError):
            MemoryExtractionResult(extraction_quality="super")

    def test_accepts_summary_prompt_contract(self):
        """总结 Prompt 的 summary 结构应通过护栏且保留业务字段。"""

        result = MemoryExtractionResult(
            memories=[
                {
                    "summary": "我记得用户明确说过喜欢深烘咖啡",
                    "topics": ["咖啡偏好"],
                    "key_facts": ["用户喜欢深烘咖啡"],
                    "participants": ["用户"],
                    "sentiment": "positive",
                    "importance": 0.8,
                    "emotion_tags": ["开心"],
                    "causal_relations": [],
                }
            ]
        )

        memory = result.memories[0]
        assert memory.content == "我记得用户明确说过喜欢深烘咖啡"
        assert memory.topics == ["咖啡偏好"]
        assert memory.key_facts == ["用户喜欢深烘咖啡"]
        assert memory.participants == ["用户"]
        assert memory.sentiment == "positive"


# =============================================================================
# GraphExtractionResult
# =============================================================================

class TestGraphExtractionResult:
    """图抽取结果测试"""

    def test_valid_graph_result(self):
        result = GraphExtractionResult(
            entities=[
                {"name": "用户A", "type": "person"},
                {"name": "Python", "type": "skill"},
            ],
            relations=[
                {"source": "用户A", "target": "Python", "relation": "学习"},
            ],
        )
        assert len(result.entities) == 2
        assert len(result.relations) == 1

    def test_entity_missing_name_raises(self):
        with pytest.raises(ValidationError):
            GraphExtractionResult(
                entities=[{"type": "person"}],  # missing 'name'
            )

    def test_entity_missing_type_raises(self):
        with pytest.raises(ValidationError):
            GraphExtractionResult(
                entities=[{"name": "用户A"}],  # missing 'type'
            )

    def test_relation_missing_source_raises(self):
        with pytest.raises(ValidationError):
            GraphExtractionResult(
                relations=[{"target": "B", "relation": "knows"}],  # missing 'source'
            )

    def test_relation_missing_target_raises(self):
        with pytest.raises(ValidationError):
            GraphExtractionResult(
                relations=[{"source": "A", "relation": "knows"}],  # missing 'target'
            )

    def test_relation_missing_relation_raises(self):
        with pytest.raises(ValidationError):
            GraphExtractionResult(
                relations=[{"source": "A", "target": "B"}],  # missing 'relation'
            )


# =============================================================================
# JSON Cleaning Pipeline
# =============================================================================

class TestValidateAndCleanJson:
    """JSON 清洗管道测试"""

    def test_clean_valid_json(self):
        result = validate_and_clean_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_remove_markdown_fences(self):
        result = validate_and_clean_json('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_extract_from_text_with_prefix(self):
        result = validate_and_clean_json('前缀文本 {"key": "value"} 后缀文本')
        assert result == {"key": "value"}

    def test_nested_braces(self):
        result = validate_and_clean_json('{"outer": {"inner": [1, 2, 3]}}')
        assert result == {"outer": {"inner": [1, 2, 3]}}

    def test_repair_single_quotes(self):
        result = validate_and_clean_json("{'key': 'value'}")
        assert result == {"key": "value"}

    def test_repair_trailing_comma(self):
        result = validate_and_clean_json('{"key": "value",}')
        assert result == {"key": "value"}

    def test_repair_python_bool(self):
        result = validate_and_clean_json('{"active": True, "deleted": False, "data": None}')
        assert result == {"active": True, "deleted": False, "data": None}

    def test_empty_input_raises(self):
        with pytest.raises(ValueError):
            validate_and_clean_json("")

    def test_empty_input_fallback_none(self):
        result = validate_and_clean_json("", fallback_return_none=True)
        assert result is None

    def test_unrepairable_json_raises(self):
        with pytest.raises(ValueError):
            validate_and_clean_json("这不是 JSON)}}")

    def test_unrepairable_json_fallback_none(self):
        result = validate_and_clean_json(
            "这不是 JSON)}}", fallback_return_none=True
        )
        assert result is None

    def test_array_json(self):
        result = validate_and_clean_json('[{"content": "记忆1"}, {"content": "记忆2"}]')
        assert isinstance(result, list)
        assert len(result) == 2


# =============================================================================
# validate_llm_response & safe_validate
# =============================================================================

class TestValidateLlmResponse:
    """LLM 响应验证测试"""

    def test_validate_memory_extraction(self):
        response = '{"memories": [{"content": "用户喜欢猫"}], "confidence": 0.8}'
        result = validate_llm_response(response, MemoryExtractionResult)
        assert result is not None
        assert len(result.memories) == 1
        assert result.confidence == 0.8

    def test_validate_markdown_wrapped_response(self):
        response = '```json\n{"memories": [{"content": "test memory content"}], "confidence": 0.7}\n```'
        result = validate_llm_response(response, MemoryExtractionResult)
        assert result is not None
        assert len(result.memories) == 1

    def test_invalid_response_returns_none(self):
        result = validate_llm_response(
            "not json at all",
            MemoryExtractionResult,
            fallback_return_none=True,
        )
        assert result is None

    def test_invalid_response_raises_without_fallback(self):
        with pytest.raises(ValueError):
            validate_llm_response(
                "not json at all",
                MemoryExtractionResult,
                fallback_return_none=False,
            )


class TestSafeValidate:
    """safe_validate 测试"""

    def test_valid_data(self):
        result = safe_validate(
            MemoryAtomSchema,
            {"content": "有效数据到位"},
        )
        assert result is not None
        assert result.content == "有效数据到位"

    def test_invalid_data_returns_none(self):
        result = safe_validate(
            MemoryAtomSchema,
            {"content": "ab"},  # too short
        )
        assert result is None

    def test_invalid_data_raises_without_fallback(self):
        with pytest.raises(ValidationError):
            safe_validate(
                MemoryAtomSchema,
                {"content": "ab"},
                fallback_return_none=False,
            )
