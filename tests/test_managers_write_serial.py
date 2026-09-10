"""测试 write_op_serialization module — serialization utils for repair ops."""

from __future__ import annotations

import time
from typing import Any

from core.features.memory.domain.memory_atom import (
    AtomStatus,
    AtomType,
    DecayType,
    MemoryAtom,
)
from core.features.memory.infrastructure.write_op_serialization import (
    _deserialize_atom_from_repair,
    safe_json_dict,
    serialize_atom_for_repair,
)


class TestSafeJsonDict:
    """测试 safe_json_dict 辅助函数。"""

    def test_returns_dict_unchanged(self) -> None:
        d = {"key": "value"}
        assert safe_json_dict(d) is d

    def test_returns_empty_dict_for_none(self) -> None:
        assert safe_json_dict(None) == {}
        assert safe_json_dict("") == {}
        assert safe_json_dict(0) == {}

    def test_returns_empty_dict_for_falsy(self) -> None:
        assert safe_json_dict(False) == {}

    def test_parses_json_string(self) -> None:
        result = safe_json_dict('{"a": 1, "b": 2}')
        assert result == {"a": 1, "b": 2}

    def test_returns_empty_dict_on_bad_json(self) -> None:
        assert safe_json_dict("not json") == {}

    def test_returns_empty_dict_for_list(self) -> None:
        assert safe_json_dict([1, 2, 3]) == {}

    def test_parsed_non_dict_returns_empty(self) -> None:
        assert safe_json_dict("42") == {}
        assert safe_json_dict('"string"') == {}
        assert safe_json_dict("null") == {}
        assert safe_json_dict("[]") == {}


class TestSerializeAtomForRepair:
    """测试 serialize_atom_for_repair。"""

    def test_serializes_memory_atom_dataclass(self) -> None:
        atom = MemoryAtom(
            parent_memory_id=42,
            atom_type=AtomType.EPISODIC,
            content="test content",
            entities=["entity1"],
            importance=0.8,
            confidence=0.9,
            decay_type=DecayType.EXPONENTIAL,
            status=AtomStatus.ACTIVE,
            session_id="s1",
            persona_id="p1",
        )
        result = serialize_atom_for_repair(atom)
        assert result["parent_memory_id"] == 42
        assert result["atom_type"] == "episodic"
        assert result["content"] == "test content"
        assert result["entities"] == ["entity1"]
        assert result["importance"] == 0.8
        assert result["confidence"] == 0.9
        assert result["decay_type"] == "exponential"
        assert result["status"] == "active"
        assert result["session_id"] == "s1"
        assert result["persona_id"] == "p1"

    def test_serializes_minimal_atom(self) -> None:
        atom = MemoryAtom(
            parent_memory_id=1,
            content="minimal",
        )
        result = serialize_atom_for_repair(atom)
        assert result["parent_memory_id"] == 1
        assert result["content"] == "minimal"
        assert result["atom_type"] == "unknown"  # default
        assert result["importance"] == 0.5  # default
        assert "created_at" in result

    def test_serializes_mock_like_object(self) -> None:
        """serialize_atom_for_repair uses getattr — works on any object."""
        from unittest.mock import MagicMock

        mock_atom = MagicMock()
        mock_atom.parent_memory_id = 99
        mock_atom.atom_type = AtomType.FACTUAL
        mock_atom.content = "from mock"
        mock_atom.entities = []
        mock_atom.importance = 0.3
        mock_atom.confidence = 0.5
        mock_atom.created_at = time.time()
        mock_atom.last_accessed_at = time.time()
        mock_atom.last_reinforced_at = None
        mock_atom.event_time = None
        mock_atom.ttl_days = 60.0
        mock_atom.expires_at = 0.0
        mock_atom.status = AtomStatus.ACTIVE
        mock_atom.reinforcement_count = 2
        mock_atom.decay_type = DecayType.LINEAR
        mock_atom.session_id = "sess"
        mock_atom.persona_id = "pers"
        mock_atom.metadata = {"k": "v"}

        result = serialize_atom_for_repair(mock_atom)
        assert result["parent_memory_id"] == 99
        assert result["atom_type"] == "factual"
        assert result["content"] == "from mock"
        assert result["decay_type"] == "linear"

    def test_serializes_planned_atom(self) -> None:
        atom = MemoryAtom(
            parent_memory_id=10,
            atom_type=AtomType.PLANNED,
            content="meeting tomorrow",
            decay_type=DecayType.STEP,
            event_time=time.time() + 86400,
            ttl_days=3.0,
        )
        result = serialize_atom_for_repair(atom)
        assert result["atom_type"] == "planned"
        assert result["decay_type"] == "step"
        assert result["ttl_days"] == 3.0
        assert result["event_time"] is not None


class TestDeserializeAtomFromRepair:
    """测试 _deserialize_atom_from_repair."""

    def test_deserializes_valid_payload(self) -> None:
        payload: dict[str, Any] = {
            "content": "deserialized content",
            "atom_type": "factual",
            "decay_type": "exponential",
            "status": "active",
            "entities": ["e1", "e2"],
            "importance": 0.7,
            "confidence": 0.85,
            "reinforcement_count": 3,
            "ttl_days": 90.0,
            "session_id": "my_session",
            "persona_id": "my_persona",
        }
        atom = _deserialize_atom_from_repair(
            payload, 42, "fallback_sess", "fallback_pers"
        )
        assert atom is not None
        assert atom.parent_memory_id == 42
        assert atom.content == "deserialized content"
        assert atom.atom_type == AtomType.FACTUAL
        assert atom.decay_type == DecayType.EXPONENTIAL
        assert atom.status == AtomStatus.ACTIVE
        assert atom.importance == 0.7
        assert atom.confidence == 0.85
        assert atom.reinforcement_count == 3
        assert atom.ttl_days == 90.0
        assert atom.entities == ["e1", "e2"]
        assert atom.session_id == "my_session"
        assert atom.persona_id == "my_persona"

    def test_returns_none_for_empty_content(self) -> None:
        assert _deserialize_atom_from_repair({"content": ""}, 1, None, None) is None
        assert _deserialize_atom_from_repair({"content": "  "}, 1, None, None) is None

    def test_returns_none_for_missing_content(self) -> None:
        assert _deserialize_atom_from_repair({}, 1, None, None) is None

    def test_falls_back_session_persona(self) -> None:
        payload = {"content": "test"}
        atom = _deserialize_atom_from_repair(payload, 1, "fb_sess", "fb_pers")
        assert atom is not None
        assert atom.session_id == "fb_sess"
        assert atom.persona_id == "fb_pers"

    def test_defaults_for_unknown_enum_values(self) -> None:
        payload = {
            "content": "test",
            "atom_type": "bogus_type",
            "decay_type": "bogus_decay",
            "status": "bogus_status",
        }
        atom = _deserialize_atom_from_repair(payload, 1, None, None)
        assert atom is not None
        assert atom.atom_type == AtomType.UNKNOWN
        assert atom.decay_type == DecayType.EXPONENTIAL
        assert atom.status == AtomStatus.ACTIVE

    def test_all_defaults_applied(self) -> None:
        payload: dict[str, Any] = {"content": "bare minimum"}
        atom = _deserialize_atom_from_repair(payload, 7, None, None)
        assert atom is not None
        assert atom.parent_memory_id == 7
        assert atom.atom_type == AtomType.UNKNOWN
        assert atom.importance == 0.5
        assert atom.confidence == 0.7
        assert atom.reinforcement_count == 0
        assert atom.ttl_days == 30.0
        assert atom.expires_at == 0.0
        assert atom.status == AtomStatus.ACTIVE
        assert atom.decay_type == DecayType.EXPONENTIAL
        assert atom.session_id is None
        assert atom.persona_id is None
