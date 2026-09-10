"""测试 shared 与各 feature 拥有的领域模型。"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# 1. core/shared/contracts/conversation.py
# ---------------------------------------------------------------------------


class TestMessageModel:
    """测试 Message 数据类。"""

    def test_basic_message_creation(self) -> None:
        from core.shared.contracts.conversation import Message

        msg = Message(
            id=1,
            session_id="sess-001",
            role="user",
            content="你好，小明",
            sender_id="user_123",
        )
        assert msg.id == 1
        assert msg.session_id == "sess-001"
        assert msg.role == "user"
        assert msg.content == "你好，小明"
        assert msg.sender_id == "user_123"
        assert msg.sender_name is None
        assert msg.group_id is None
        assert msg.platform is None

    def test_message_with_optional_fields(self) -> None:
        from core.shared.contracts.conversation import Message

        msg = Message(
            id=2,
            session_id="sess-002",
            role="assistant",
            content="你好！",
            sender_id="bot_001",
            sender_name="MyBot",
            group_id="group_456",
            platform="qq",
        )
        assert msg.sender_name == "MyBot"
        assert msg.group_id == "group_456"
        assert msg.platform == "qq"

    def test_to_dict_roundtrip(self) -> None:
        from core.shared.contracts.conversation import Message

        original = Message(
            id=3,
            session_id="sess-003",
            role="user",
            content="测试消息",
            sender_id="user_789",
            sender_name="小明",
            group_id="group_001",
            platform="qq",
        )
        d = original.to_dict()
        assert isinstance(d, dict)
        assert d["id"] == 3
        assert d["content"] == "测试消息"

    def test_from_dict_reconstructs_message(self) -> None:
        from core.shared.contracts.conversation import Message

        data: dict[str, Any] = {
            "id": 4,
            "session_id": "sess-004",
            "role": "assistant",
            "content": "回复内容",
            "sender_id": "bot_001",
            "sender_name": "MyBot",
            "group_id": "group_002",
            "platform": "discord",
            "timestamp": time.time(),
        }
        msg = Message.from_dict(data)
        assert msg.id == 4
        assert msg.session_id == "sess-004"
        assert msg.role == "assistant"
        assert msg.sender_name == "MyBot"

    def test_from_dict_minimal_data(self) -> None:
        from core.shared.contracts.conversation import Message

        data: dict[str, Any] = {
            "session_id": "sess-005",
            "role": "system",
            "content": "提示词",
            "sender_id": "system",
        }
        msg = Message.from_dict(data)
        assert msg.id == 0  # default
        assert msg.session_id == "sess-005"
        assert msg.sender_name is None

    def test_from_dict_metadata_as_json_string(self) -> None:
        from core.shared.contracts.conversation import Message

        data: dict[str, Any] = {
            "session_id": "sess-006",
            "role": "user",
            "content": "test",
            "sender_id": "user_1",
            "metadata": '{"key": "value"}',
        }
        msg = Message.from_dict(data)
        assert isinstance(msg.metadata, dict)
        assert msg.metadata["key"] == "value"

    def test_from_dict_metadata_invalid_json_defaults_to_empty(self) -> None:
        from core.shared.contracts.conversation import Message

        data: dict[str, Any] = {
            "session_id": "sess-007",
            "role": "user",
            "content": "test",
            "sender_id": "user_1",
            "metadata": "not-valid-json{{{",
        }
        msg = Message.from_dict(data)
        assert isinstance(msg.metadata, dict)
        assert msg.metadata == {}

    def test_content_to_text_none(self) -> None:
        from core.shared.contracts.conversation import Message

        assert Message.content_to_text(None) == ""

    def test_content_to_text_string(self) -> None:
        from core.shared.contracts.conversation import Message

        assert Message.content_to_text("hello world") == "hello world"

    def test_content_to_text_int_float_bool(self) -> None:
        from core.shared.contracts.conversation import Message

        assert Message.content_to_text(42) == "42"
        assert Message.content_to_text(3.14) == "3.14"
        assert Message.content_to_text(True) == "True"

    def test_content_to_text_dict_with_text_type(self) -> None:
        from core.shared.contracts.conversation import Message

        result = Message.content_to_text({"type": "text", "text": "你好"})
        assert result == "你好"

    def test_content_to_text_dict_with_content_key(self) -> None:
        from core.shared.contracts.conversation import Message

        result = Message.content_to_text({"type": "custom", "content": "some content"})
        assert result == "some content"

    def test_content_to_text_dict_with_image_type(self) -> None:
        from core.shared.contracts.conversation import Message

        result = Message.content_to_text(
            {"type": "image", "image_url": "http://example.com/img.png"}
        )
        assert result == "[图片消息]"

    def test_content_to_text_list_of_parts(self) -> None:
        from core.shared.contracts.conversation import Message

        result = Message.content_to_text(
            [{"type": "text", "text": "Hello"}, {"type": "text", "text": "World"}]
        )
        assert result == "Hello World"

    def test_format_for_llm_direct_message(self) -> None:
        from core.shared.contracts.conversation import Message

        msg = Message(
            id=1,
            session_id="sess-001",
            role="user",
            content="你好",
            sender_id="user_123",
        )
        formatted = msg.format_for_llm(include_sender_name=False)
        assert formatted["role"] == "user"
        assert formatted["content"] == "你好"

    def test_format_for_llm_group_with_bot(self) -> None:
        from core.shared.contracts.conversation import Message

        msg = Message(
            id=5,
            session_id="sess-008",
            role="assistant",
            content="回复",
            sender_id="bot_001",
            sender_name="MyBot",
            group_id="group_003",
        )
        formatted = msg.format_for_llm(include_sender_name=True)
        assert formatted["role"] == "assistant"
        assert "[Bot:" in formatted["content"]

    def test_format_for_llm_group_with_user_uses_sender_name(self) -> None:
        from core.shared.contracts.conversation import Message

        msg = Message(
            id=6,
            session_id="sess-009",
            role="user",
            content="群聊消息",
            sender_id="user_999",
            sender_name="小明",
            group_id="group_004",
        )
        formatted = msg.format_for_llm(include_sender_name=True)
        assert "[小明" in formatted["content"]


class TestSessionModel:
    """测试 Session 数据类。"""

    def test_basic_session_creation(self) -> None:
        from core.shared.contracts.conversation import Session

        now = time.time()
        sess = Session(
            id=1,
            session_id="sess-001",
            platform="qq",
            created_at=now,
            last_active_at=now,
        )
        assert sess.id == 1
        assert sess.session_id == "sess-001"
        assert sess.platform == "qq"
        assert sess.message_count == 0

    def test_add_participant(self) -> None:
        from core.shared.contracts.conversation import Session

        now = time.time()
        sess = Session(
            id=1,
            session_id="sess-001",
            platform="qq",
            created_at=now,
            last_active_at=now,
        )
        sess.add_participant("user_1")
        assert "user_1" in sess.participants
        assert len(sess.participants) == 1

    def test_add_duplicate_participant_is_ignored(self) -> None:
        from core.shared.contracts.conversation import Session

        now = time.time()
        sess = Session(
            id=1,
            session_id="sess-001",
            platform="qq",
            created_at=now,
            last_active_at=now,
        )
        sess.add_participant("user_1")
        sess.add_participant("user_1")
        assert len(sess.participants) == 1

    def test_update_activity(self) -> None:
        from core.shared.contracts.conversation import Session

        old_time = time.time() - 100
        sess = Session(
            id=1,
            session_id="sess-001",
            platform="qq",
            created_at=old_time,
            last_active_at=old_time,
        )
        sess.update_activity()
        assert sess.last_active_at > old_time

    def test_increment_message_count(self) -> None:
        from core.shared.contracts.conversation import Session

        now = time.time()
        sess = Session(
            id=1,
            session_id="sess-001",
            platform="qq",
            created_at=now,
            last_active_at=now,
        )
        assert sess.message_count == 0
        sess.increment_message_count()
        assert sess.message_count == 1
        sess.increment_message_count()
        assert sess.message_count == 2

    def test_to_dict_and_from_dict_roundtrip(self) -> None:
        from core.shared.contracts.conversation import Session

        now = time.time()
        sess = Session(
            id=7,
            session_id="sess-roundtrip",
            platform="discord",
            created_at=now,
            last_active_at=now,
            message_count=5,
            participants=["user_1", "user_2"],
        )
        d = sess.to_dict()
        reconstructed = Session.from_dict(d)
        assert reconstructed.id == 7
        assert reconstructed.session_id == "sess-roundtrip"
        assert reconstructed.platform == "discord"
        assert reconstructed.message_count == 5
        assert "user_1" in reconstructed.participants
        assert "user_2" in reconstructed.participants

    def test_from_dict_participants_as_json_string(self) -> None:
        from core.shared.contracts.conversation import Session

        now = time.time()
        data: dict[str, Any] = {
            "id": 8,
            "session_id": "sess-json",
            "platform": "qq",
            "created_at": now,
            "last_active_at": now,
            "participants": '["user_a", "user_b"]',
        }
        sess = Session.from_dict(data)
        assert isinstance(sess.participants, list)
        assert "user_a" in sess.participants
        assert "user_b" in sess.participants


class TestMemoryEventModel:
    """测试 MemoryEvent 数据类。"""

    def test_basic_memory_event_creation(self) -> None:
        from core.shared.contracts.conversation import MemoryEvent

        evt = MemoryEvent(
            memory_content="用户喜欢咖啡",
            importance_score=0.75,
            session_id="sess-001",
        )
        assert evt.memory_content == "用户喜欢咖啡"
        assert evt.importance_score == 0.75
        assert evt.session_id == "sess-001"

    def test_is_important_default_threshold(self) -> None:
        from core.shared.contracts.conversation import MemoryEvent

        important = MemoryEvent(
            memory_content="重要",
            importance_score=0.8,
            session_id="sess-001",
        )
        trivial = MemoryEvent(
            memory_content="不重要",
            importance_score=0.3,
            session_id="sess-001",
        )
        assert important.is_important() is True
        assert trivial.is_important() is False

    def test_is_important_custom_threshold(self) -> None:
        from core.shared.contracts.conversation import MemoryEvent

        evt = MemoryEvent(
            memory_content="边界",
            importance_score=0.5,
            session_id="sess-001",
        )
        assert evt.is_important(threshold=0.5) is True
        assert evt.is_important(threshold=0.51) is False

    def test_to_dict_and_from_dict_roundtrip(self) -> None:
        from core.shared.contracts.conversation import MemoryEvent

        evt = MemoryEvent(
            memory_content="周末计划去西湖",
            importance_score=0.6,
            session_id="sess-002",
            metadata={"source": "reflection"},
        )
        d = evt.to_dict()
        reconstructed = MemoryEvent.from_dict(d)
        assert reconstructed.memory_content == "周末计划去西湖"
        assert reconstructed.importance_score == 0.6
        assert reconstructed.session_id == "sess-002"
        assert reconstructed.metadata["source"] == "reflection"

    def test_from_dict_metadata_as_json_string(self) -> None:
        from core.shared.contracts.conversation import MemoryEvent

        data: dict[str, Any] = {
            "memory_content": "test",
            "importance_score": 0.5,
            "session_id": "sess-003",
            "metadata": '{"key": 123}',
        }
        evt = MemoryEvent.from_dict(data)
        assert isinstance(evt.metadata, dict)
        assert evt.metadata["key"] == 123


class TestSerializationHelpers:
    """测试 serialize_to_json 与 deserialize_from_json。"""

    def test_serialize_list(self) -> None:
        from core.shared.contracts.conversation import serialize_to_json

        result = serialize_to_json(["a", "b"])
        parsed = json.loads(result)
        assert parsed == ["a", "b"]

    def test_serialize_dict(self) -> None:
        from core.shared.contracts.conversation import serialize_to_json

        result = serialize_to_json({"key": "value"})
        parsed = json.loads(result)
        assert parsed == {"key": "value"}

    def test_serialize_non_json_type_falls_back_to_str(self) -> None:
        from core.shared.contracts.conversation import serialize_to_json

        result = serialize_to_json(42)
        assert result == "42"

    def test_deserialize_valid_json(self) -> None:
        from core.shared.contracts.conversation import deserialize_from_json

        result = deserialize_from_json('{"a": 1}')
        assert result == {"a": 1}

    def test_deserialize_none_returns_default(self) -> None:
        from core.shared.contracts.conversation import deserialize_from_json

        assert deserialize_from_json(None) == {}
        assert deserialize_from_json(None, []) == []

    def test_deserialize_empty_string_returns_default(self) -> None:
        from core.shared.contracts.conversation import deserialize_from_json

        assert deserialize_from_json("") == {}
        assert deserialize_from_json("", "fallback") == "fallback"

    def test_deserialize_invalid_json_returns_default(self) -> None:
        from core.shared.contracts.conversation import deserialize_from_json

        assert deserialize_from_json("not json{{{") == {}
        assert deserialize_from_json("bad", []) == []


# ---------------------------------------------------------------------------
# 2. core/features/memory/graph/domain/models.py
# ---------------------------------------------------------------------------


class TestGraphNode:
    """测试 GraphNode 数据类。"""

    def test_basic_node_creation(self) -> None:
        from core.features.memory.graph.domain.models import GraphNode

        node = GraphNode(
            node_type="person",
            value="小明",
            canonical_value="xiaoming",
        )
        assert node.node_type == "person"
        assert node.value == "小明"
        assert node.canonical_value == "xiaoming"
        assert node.metadata == {}

    def test_node_key_property(self) -> None:
        from core.features.memory.graph.domain.models import GraphNode

        node = GraphNode(
            node_type="entity",
            value="西湖",
            canonical_value="west_lake",
        )
        assert node.node_key == "entity:west_lake"

    def test_node_with_metadata(self) -> None:
        from core.features.memory.graph.domain.models import GraphNode

        node = GraphNode(
            node_type="topic",
            value="咖啡",
            canonical_value="coffee",
            metadata={"language": "zh"},
        )
        assert node.metadata["language"] == "zh"


class TestGraphEdge:
    """测试 GraphEdge 数据类。"""

    def test_basic_edge_creation(self) -> None:
        from core.features.memory.graph.domain.models import GraphEdge

        edge = GraphEdge(
            source_key="person:xiaoming",
            target_key="place:west_lake",
            relation_type="visited",
            source_memory_id=1,
        )
        assert edge.source_key == "person:xiaoming"
        assert edge.target_key == "place:west_lake"
        assert edge.relation_type == "visited"
        assert edge.source_memory_id == 1
        assert edge.confidence == 0.8
        assert edge.weight == 1.0
        assert edge.status == "active"

    def test_edge_key_property(self) -> None:
        from core.features.memory.graph.domain.models import GraphEdge

        edge = GraphEdge(
            source_key="a",
            target_key="b",
            relation_type="knows",
            source_memory_id=42,
        )
        assert edge.edge_key == "a|knows|b|42"

    def test_semantic_edge_key_ignores_memory_id(self) -> None:
        from core.features.memory.graph.domain.models import GraphEdge

        edge1 = GraphEdge(
            source_key="a",
            target_key="b",
            relation_type="knows",
            source_memory_id=1,
        )
        edge2 = GraphEdge(
            source_key="a",
            target_key="b",
            relation_type="knows",
            source_memory_id=99,
        )
        assert edge1.semantic_edge_key == edge2.semantic_edge_key
        assert edge1.semantic_edge_key == "a|knows|b"


class TestGraphEntry:
    """测试 GraphEntry 数据类。"""

    def test_basic_entry_creation(self) -> None:
        from core.features.memory.graph.domain.models import GraphEntry

        entry = GraphEntry(
            entry_key="ent-001",
            source_memory_id=1,
            session_id="sess-001",
            persona_id="persona-001",
            entry_type="fact",
            content="小明周末去了西湖",
        )
        assert entry.entry_key == "ent-001"
        assert entry.source_memory_id == 1
        assert entry.session_id == "sess-001"
        assert entry.entry_type == "fact"
        assert entry.content == "小明周末去了西湖"

    def test_entry_defaults(self) -> None:
        from core.features.memory.graph.domain.models import GraphEntry

        entry = GraphEntry(
            entry_key="ent-002",
            source_memory_id=2,
            session_id=None,
            persona_id=None,
            entry_type="relation",
            content="",
        )
        assert entry.metadata == {}
        assert entry.node_keys == []
        assert entry.relation_type is None


class TestExtractedGraph:
    """测试 ExtractedGraph 数据类。"""

    def test_empty_extracted_graph(self) -> None:
        from core.features.memory.graph.domain.models import ExtractedGraph

        eg = ExtractedGraph()
        assert eg.nodes == []
        assert eg.edges == []
        assert eg.entries == []

    def test_extracted_graph_with_data(self) -> None:
        from core.features.memory.graph.domain.models import (
            ExtractedGraph,
            GraphEdge,
            GraphEntry,
            GraphNode,
        )

        eg = ExtractedGraph(
            nodes=[
                GraphNode(
                    node_type="person",
                    value="小明",
                    canonical_value="xiaoming",
                )
            ],
            edges=[
                GraphEdge(
                    source_key="person:xiaoming",
                    target_key="place:west_lake",
                    relation_type="visited",
                    source_memory_id=1,
                )
            ],
            entries=[
                GraphEntry(
                    entry_key="e1",
                    source_memory_id=1,
                    session_id=None,
                    persona_id=None,
                    entry_type="fact",
                    content="小明去了西湖",
                )
            ],
        )
        assert len(eg.nodes) == 1
        assert len(eg.edges) == 1
        assert len(eg.entries) == 1


# ---------------------------------------------------------------------------
# 3. core/features/knowledge/domain/models.py
# ---------------------------------------------------------------------------


class TestKnowledgeType:
    """测试 KnowledgeType 枚举。"""

    def test_all_members(self) -> None:
        from core.features.knowledge import KnowledgeType

        values = {m.value for m in KnowledgeType}
        assert "fact" in values
        assert "concept" in values
        assert "rule" in values
        assert "event" in values
        assert "procedure" in values

    def test_is_string_enum(self) -> None:
        from core.features.knowledge import KnowledgeType

        # StrEnum 可直接与其值比较。
        assert KnowledgeType.FACT == "fact"
        assert KnowledgeType.FACT.value == "fact"
        # 字符串契约使用 .value，避免依赖 StrEnum 的限定名称表示。
        assert KnowledgeType.FACT.value == "fact"


class TestKnowledgeEntry:
    """测试 KnowledgeEntry 数据类。"""

    def test_default_entry(self) -> None:
        from core.features.knowledge import KnowledgeEntry, KnowledgeType

        entry = KnowledgeEntry()
        assert entry.title == ""
        assert entry.content == ""
        assert entry.category == KnowledgeType.FACT
        assert entry.confidence == 0.5
        assert entry.source_ids == []
        assert entry.tags == []
        assert entry.entry_id == 0

    def test_full_entry_creation(self) -> None:
        from core.features.knowledge import KnowledgeEntry, KnowledgeType

        entry = KnowledgeEntry(
            title="咖啡知识",
            content="咖啡是一种饮品",
            category=KnowledgeType.FACT,
            confidence=0.9,
            source_ids=[1, 2, 3],
            tags=["咖啡", "饮品"],
            entry_id=42,
            access_count=5,
        )
        assert entry.title == "咖啡知识"
        assert entry.confidence == 0.9
        assert entry.source_ids == [1, 2, 3]
        assert entry.access_count == 5

    def test_to_dict(self) -> None:
        from core.features.knowledge import KnowledgeEntry, KnowledgeType

        entry = KnowledgeEntry(
            title="测试标题",
            content="测试内容",
            category=KnowledgeType.CONCEPT,
            confidence=0.7,
            tags=["标签1"],
            entry_id=10,
        )
        d = entry.to_dict()
        assert d["entry_id"] == 10
        assert d["title"] == "测试标题"
        assert d["category"] == "concept"
        assert d["confidence"] == 0.7
        assert d["tags"] == ["标签1"]

    def test_from_dict_complete(self) -> None:
        from core.features.knowledge import KnowledgeEntry, KnowledgeType

        data: dict[str, Any] = {
            "entry_id": 99,
            "title": "从字典创建",
            "content": "内容",
            "category": "rule",
            "confidence": 0.85,
            "source_ids": [10, 20],
            "tags": ["规则"],
            "created_at": time.time(),
            "updated_at": time.time(),
            "expires_at": 0.0,
            "access_count": 3,
        }
        entry = KnowledgeEntry.from_dict(data)
        assert entry.entry_id == 99
        assert entry.title == "从字典创建"
        assert entry.category == KnowledgeType.RULE
        assert entry.confidence == 0.85
        assert entry.source_ids == [10, 20]

    def test_from_dict_minimal(self) -> None:
        from core.features.knowledge import KnowledgeEntry, KnowledgeType

        entry = KnowledgeEntry.from_dict({})
        assert entry.title == ""
        assert entry.content == ""
        assert entry.category == KnowledgeType.FACT
        assert entry.confidence == 0.5
        assert entry.source_ids == []
        assert entry.tags == []

    def test_from_dict_with_none_source_ids(self) -> None:
        from core.features.knowledge import KnowledgeEntry

        entry = KnowledgeEntry.from_dict({"source_ids": None})
        assert entry.source_ids == []


# ---------------------------------------------------------------------------
# 4. core/features/notes/domain/models.py
# ---------------------------------------------------------------------------


class TestNoteStatus:
    """测试 NoteStatus 枚举。"""

    def test_all_members(self) -> None:
        from core.features.notes import NoteStatus

        values = {m.value for m in NoteStatus}
        assert "active" in values
        assert "archived" in values
        assert "deleted" in values

    def test_is_string_enum(self) -> None:
        from core.features.notes import NoteStatus

        assert NoteStatus.ACTIVE == "active"


class TestNoteVersion:
    """测试 NoteVersion 数据类。"""

    def test_default_version(self) -> None:
        from core.features.notes import NoteVersion

        nv = NoteVersion()
        assert nv.version == 1
        assert nv.content == ""
        assert nv.created_at > 0

    def test_custom_version(self) -> None:
        from core.features.notes import NoteVersion

        nv = NoteVersion(version=3, content="第三次修改的内容")
        assert nv.version == 3
        assert nv.content == "第三次修改的内容"


class TestNote:
    """测试 Note 数据类。"""

    def test_default_note(self) -> None:
        from core.features.notes import Note, NoteStatus

        note = Note()
        assert note.title == ""
        assert note.content == ""
        assert note.tags == []
        assert note.status == NoteStatus.ACTIVE
        assert note.version == 1
        assert note.note_id == 0
        assert note.user_id == ""
        assert note.source_memory_ids == []

    def test_full_note_creation(self) -> None:
        from core.features.notes import Note, NoteStatus

        note = Note(
            title="西湖游玩计划",
            content="周末去西湖划船",
            tags=["西湖", "计划"],
            status=NoteStatus.ACTIVE,
            version=2,
            note_id=15,
            user_id="user_001",
            source_memory_ids=[101, 102],
        )
        assert note.title == "西湖游玩计划"
        assert note.note_id == 15
        assert note.tags == ["西湖", "计划"]
        assert note.source_memory_ids == [101, 102]

    def test_to_dict(self) -> None:
        from core.features.notes import Note

        note = Note(
            title="笔记标题",
            content="笔记内容",
            tags=["tag1"],
            note_id=5,
            user_id="u1",
        )
        d = note.to_dict()
        assert d["note_id"] == 5
        assert d["title"] == "笔记标题"
        assert d["status"] == "active"
        assert d["version"] == 1
        assert d["user_id"] == "u1"

    def test_from_dict_complete(self) -> None:
        from core.features.notes import Note, NoteStatus

        data: dict[str, Any] = {
            "note_id": 7,
            "title": "测试笔记",
            "content": "测试正文",
            "tags": ["a", "b"],
            "status": "archived",
            "version": 3,
            "user_id": "user_x",
            "source_memory_ids": [1],
        }
        note = Note.from_dict(data)
        assert note.note_id == 7
        assert note.title == "测试笔记"
        assert note.status == NoteStatus.ARCHIVED
        assert note.version == 3
        assert note.tags == ["a", "b"]

    def test_from_dict_minimal(self) -> None:
        from core.features.notes import Note, NoteStatus

        note = Note.from_dict({})
        assert note.title == ""
        assert note.status == NoteStatus.ACTIVE
        assert note.note_id == 0
        assert note.tags == []

    def test_from_dict_none_lists(self) -> None:
        from core.features.notes import Note

        note = Note.from_dict({"tags": None, "source_memory_ids": None})
        assert note.tags == []
        assert note.source_memory_ids == []


# ---------------------------------------------------------------------------
# 5. core/shared/recall_strategy.py
# ---------------------------------------------------------------------------


class TestRecallStrategy:
    """测试 RecallStrategy 枚举。"""

    def test_all_strategies_exist(self) -> None:
        from core.shared.recall_strategy import RecallStrategy

        values = {m.value for m in RecallStrategy}
        assert "contextual_similarity" in values
        assert "topic_association" in values
        assert "preference_query" in values
        assert "relationship_review" in values

    def test_is_string_enum(self) -> None:
        from core.shared.recall_strategy import RecallStrategy

        assert RecallStrategy.CONTEXTUAL_SIMILARITY == "contextual_similarity"


class TestRecallRequest:
    """测试 RecallRequest 冻结数据类。"""

    def test_basic_request_creation(self) -> None:
        from core.shared.recall_strategy import RecallRequest, RecallStrategy

        req = RecallRequest(
            strategy=RecallStrategy.CONTEXTUAL_SIMILARITY,
            query="用户喜欢什么咖啡？",
        )
        assert req.strategy == RecallStrategy.CONTEXTUAL_SIMILARITY
        assert req.query == "用户喜欢什么咖啡？"
        assert req.k == 5
        assert req.session_id is None
        assert req.persona_id is None
        assert req.emotion_context is None
        assert req.memory_types is None

    def test_request_with_optional_fields(self) -> None:
        from core.shared.recall_strategy import RecallRequest, RecallStrategy

        req = RecallRequest(
            strategy=RecallStrategy.TOPIC_ASSOCIATION,
            query="相关话题",
            k=10,
            session_id="sess-001",
            persona_id="persona-001",
            emotion_context=["happy", "excited"],
            memory_types=["EPISODIC", "FACTUAL"],
        )
        assert req.k == 10
        assert req.session_id == "sess-001"
        assert req.persona_id == "persona-001"
        assert req.emotion_context == ["happy", "excited"]
        assert req.memory_types == ["EPISODIC", "FACTUAL"]

    def test_request_is_frozen(self) -> None:
        from core.shared.recall_strategy import RecallRequest, RecallStrategy

        req = RecallRequest(
            strategy=RecallStrategy.PREFERENCE_QUERY,
            query="偏好",
        )
        with pytest.raises(Exception):
            req.k = 20  # type: ignore[misc]

    @pytest.mark.parametrize(
        "strategy",
        [
            "CONTEXTUAL_SIMILARITY",
            "TOPIC_ASSOCIATION",
            "PREFERENCE_QUERY",
            "RELATIONSHIP_REVIEW",
        ],
    )
    def test_all_strategies_can_create_request(self, strategy: str) -> None:
        from core.shared.recall_strategy import RecallRequest, RecallStrategy

        strat = getattr(RecallStrategy, strategy)
        req = RecallRequest(strategy=strat, query="test")
        assert req.strategy == strat


# ---------------------------------------------------------------------------
# 6. core/features/profiles/domain/models.py
# ---------------------------------------------------------------------------


class TestTagCategory:
    """测试 TagCategory 枚举。"""

    def test_all_categories(self) -> None:
        from core.features.profiles import TagCategory

        values = {m.value for m in TagCategory}
        expected = {
            "interest",
            "personality",
            "habit",
            "relation",
            "knowledge",
            "preference",
            "custom",
        }
        assert values == expected

    def test_is_string_enum(self) -> None:
        from core.features.profiles import TagCategory

        assert TagCategory.INTEREST == "interest"


class TestUserTag:
    """测试 UserTag 数据类。"""

    def test_default_tag(self) -> None:
        from core.features.profiles import TagCategory, UserTag

        tag = UserTag()
        assert tag.category == TagCategory.CUSTOM
        assert tag.value == ""
        assert tag.confidence == 0.5
        assert tag.source == "auto"
        assert tag.occurrence_count == 1

    def test_custom_tag(self) -> None:
        from core.features.profiles import TagCategory, UserTag

        tag = UserTag(
            category=TagCategory.INTEREST,
            value="咖啡",
            confidence=0.9,
            source="manual",
            occurrence_count=3,
        )
        assert tag.category == TagCategory.INTEREST
        assert tag.value == "咖啡"
        assert tag.confidence == 0.9
        assert tag.source == "manual"
        assert tag.occurrence_count == 3

    def test_to_dict(self) -> None:
        from core.features.profiles import TagCategory, UserTag

        tag = UserTag(
            category=TagCategory.HABIT,
            value="早起",
            confidence=0.7,
        )
        d = tag.to_dict()
        assert d["category"] == "habit"
        assert d["value"] == "早起"
        assert d["confidence"] == 0.7
        assert d["source"] == "auto"
        assert d["occurrence_count"] == 1

    def test_from_dict(self) -> None:
        from core.features.profiles import TagCategory, UserTag

        data: dict[str, Any] = {
            "category": "preference",
            "value": "拿铁",
            "confidence": 0.85,
            "source": "auto",
            "occurrence_count": 5,
        }
        tag = UserTag.from_dict(data)
        assert tag.category == TagCategory.PREFERENCE
        assert tag.value == "拿铁"
        assert tag.confidence == 0.85
        assert tag.occurrence_count == 5

    def test_from_dict_defaults(self) -> None:
        from core.features.profiles import TagCategory, UserTag

        tag = UserTag.from_dict({})
        assert tag.category == TagCategory.CUSTOM
        assert tag.value == ""
        assert tag.confidence == 0.5


class TestUserPreferences:
    """测试 UserPreferences 数据类。"""

    def test_default_preferences(self) -> None:
        from core.features.profiles import UserPreferences

        prefs = UserPreferences()
        assert prefs.reply_style == "casual"
        assert prefs.preferred_topics == []
        assert prefs.avoided_topics == []
        assert prefs.active_hours == []
        assert prefs.avg_reply_length == 0
        assert prefs.interaction_frequency == 0.0

    def test_custom_preferences(self) -> None:
        from core.features.profiles import UserPreferences

        prefs = UserPreferences(
            reply_style="formal",
            preferred_topics=["科技", "咖啡"],
            avoided_topics=["政治"],
            active_hours=[9, 10, 14, 15],
            avg_reply_length=120,
            interaction_frequency=3.5,
        )
        assert prefs.reply_style == "formal"
        assert "科技" in prefs.preferred_topics
        assert "政治" in prefs.avoided_topics
        assert 9 in prefs.active_hours
        assert prefs.avg_reply_length == 120
        assert prefs.interaction_frequency == 3.5

    def test_to_dict(self) -> None:
        from core.features.profiles import UserPreferences

        prefs = UserPreferences(
            reply_style="casual",
            preferred_topics=["游戏"],
        )
        d = prefs.to_dict()
        assert d["reply_style"] == "casual"
        assert d["preferred_topics"] == ["游戏"]

    def test_from_dict_complete(self) -> None:
        from core.features.profiles import UserPreferences

        data: dict[str, Any] = {
            "reply_style": "technical",
            "preferred_topics": ["AI"],
            "avoided_topics": ["small_talk"],
            "active_hours": [10, 11],
            "avg_reply_length": 200,
            "interaction_frequency": 1.2,
        }
        prefs = UserPreferences.from_dict(data)
        assert prefs.reply_style == "technical"
        assert prefs.preferred_topics == ["AI"]

    def test_from_dict_none_returns_default(self) -> None:
        from core.features.profiles import UserPreferences

        prefs = UserPreferences.from_dict(None)
        assert prefs.reply_style == "casual"
        assert prefs.preferred_topics == []

    def test_from_dict_empty_dict(self) -> None:
        from core.features.profiles import UserPreferences

        prefs = UserPreferences.from_dict({})
        assert prefs.reply_style == "casual"


class TestUserProfile:
    """测试 UserProfile 数据类及其方法。"""

    def test_default_profile(self) -> None:
        from core.features.profiles import UserProfile

        profile = UserProfile()
        assert profile.user_id == ""
        assert profile.display_name == ""
        assert profile.tags == []
        assert profile.total_messages == 0
        assert profile.total_sessions == 0

    def test_profile_with_user_id(self) -> None:
        from core.features.profiles import UserProfile

        profile = UserProfile(
            user_id="u-123",
            display_name="小明",
            total_messages=42,
            total_sessions=5,
        )
        assert profile.user_id == "u-123"
        assert profile.display_name == "小明"
        assert profile.total_messages == 42
        assert profile.total_sessions == 5

    def test_upsert_tag_new_tag(self) -> None:
        from core.features.profiles import TagCategory, UserProfile, UserTag

        profile = UserProfile(user_id="u-1")
        tag = UserTag(
            category=TagCategory.INTEREST,
            value="咖啡",
            confidence=0.7,
        )
        result = profile.upsert_tag(tag)
        assert result is True  # new tag added
        assert len(profile.tags) == 1
        assert profile.tags[0].value == "咖啡"

    def test_upsert_tag_existing_updates_confidence(self) -> None:
        from core.features.profiles import TagCategory, UserProfile, UserTag

        profile = UserProfile(user_id="u-1")
        tag1 = UserTag(
            category=TagCategory.INTEREST,
            value="咖啡",
            confidence=0.7,
        )
        profile.upsert_tag(tag1)

        tag2 = UserTag(
            category=TagCategory.INTEREST,
            value="咖啡",
            confidence=0.9,
        )
        result = profile.upsert_tag(tag2)
        assert result is False  # existing tag updated, not added
        assert len(profile.tags) == 1
        assert profile.tags[0].confidence == 0.9
        assert profile.tags[0].occurrence_count == 2

    def test_upsert_tag_different_category_same_value_adds(self) -> None:
        from core.features.profiles import TagCategory, UserProfile, UserTag

        profile = UserProfile(user_id="u-1")
        tag1 = UserTag(category=TagCategory.INTEREST, value="咖啡")
        tag2 = UserTag(category=TagCategory.HABIT, value="咖啡", confidence=0.8)
        profile.upsert_tag(tag1)
        result = profile.upsert_tag(tag2)
        assert result is True  # different category = new tag
        assert len(profile.tags) == 2

    def test_get_tags_by_category(self) -> None:
        from core.features.profiles import TagCategory, UserProfile, UserTag

        profile = UserProfile(user_id="u-1")
        profile.upsert_tag(
            UserTag(category=TagCategory.INTEREST, value="咖啡", confidence=0.9)
        )
        profile.upsert_tag(
            UserTag(category=TagCategory.INTEREST, value="编程", confidence=0.5)
        )
        profile.upsert_tag(
            UserTag(category=TagCategory.HABIT, value="早起", confidence=0.8)
        )

        interest_tags = profile.get_tags_by_category(TagCategory.INTEREST)
        assert len(interest_tags) == 2
        # 应按置信度降序排列。
        assert interest_tags[0].value == "咖啡"
        assert interest_tags[1].value == "编程"

    def test_get_tags_by_category_empty(self) -> None:
        from core.features.profiles import TagCategory, UserProfile

        profile = UserProfile(user_id="u-1")
        result = profile.get_tags_by_category(TagCategory.RELATION)
        assert result == []

    def test_get_top_tags(self) -> None:
        from core.features.profiles import TagCategory, UserProfile, UserTag

        profile = UserProfile(user_id="u-1")
        for i in range(15):
            profile.upsert_tag(
                UserTag(
                    category=TagCategory.INTEREST,
                    value=f"tag_{i}",
                    confidence=float(i) / 20.0,
                )
            )
        top = profile.get_top_tags(limit=5)
        assert len(top) == 5
        assert top[0].confidence > top[4].confidence

    def test_get_tag_values(self) -> None:
        from core.features.profiles import TagCategory, UserProfile, UserTag

        profile = UserProfile(user_id="u-1")
        profile.upsert_tag(
            UserTag(category=TagCategory.INTEREST, value="咖啡", confidence=0.9)
        )
        profile.upsert_tag(
            UserTag(category=TagCategory.INTEREST, value="茶", confidence=0.2)
        )
        profile.upsert_tag(
            UserTag(category=TagCategory.HABIT, value="早起", confidence=0.5)
        )

        values = profile.get_tag_values()
        # “茶”的置信度 0.2 低于 0.3 阈值，应被排除。
        assert "咖啡" in values
        assert "早起" in values
        assert "茶" not in values

    def test_get_weight_vector(self) -> None:
        from core.features.profiles import TagCategory, UserProfile, UserTag

        profile = UserProfile(user_id="u-1")
        profile.upsert_tag(
            UserTag(
                category=TagCategory.INTEREST,
                value="咖啡",
                confidence=0.8,
                occurrence_count=5,
            )
        )
        # 低于 0.2 的置信度阈值。
        profile.upsert_tag(
            UserTag(
                category=TagCategory.HABIT,
                value="早起",
                confidence=0.15,
                occurrence_count=10,
            )
        )

        weights = profile.get_weight_vector()
        assert "咖啡" in weights
        expected_weight = 0.8 * min(1.0, 5 / 10.0)  # confidence * min(1.0, occ/10)
        assert weights["咖啡"] == pytest.approx(expected_weight, abs=1e-4)
        assert "早起" not in weights  # below 0.2 threshold

    def test_decay_tags_reduces_confidence(self) -> None:
        from core.features.profiles import TagCategory, UserProfile, UserTag

        profile = UserProfile(user_id="u-1")
        tag = UserTag(
            category=TagCategory.INTEREST,
            value="咖啡",
            confidence=0.9,
            last_seen_at=time.time() - 86400 * 60,  # 60 days ago
        )
        profile.upsert_tag(tag)

        # 以当前时间而非 last_seen_at 作为衰减参考时间。
        profile.decay_tags(reference_time=time.time())
        assert profile.tags[0].confidence < 0.9
        assert profile.tags[0].confidence > 0.0

    def test_decay_tags_no_decay_for_recent_tag(self) -> None:
        from core.features.profiles import TagCategory, UserProfile, UserTag

        profile = UserProfile(user_id="u-1")
        now = time.time()
        tag = UserTag(
            category=TagCategory.INTEREST,
            value="咖啡",
            confidence=0.9,
            last_seen_at=now - 1,  # 1 second ago
        )
        profile.upsert_tag(tag)

        profile.decay_tags(reference_time=now)
        # 一秒内几乎不发生衰减。
        assert profile.tags[0].confidence == pytest.approx(0.9, abs=0.001)

    def test_remove_stale_tags(self) -> None:
        from core.features.profiles import TagCategory, UserProfile, UserTag

        profile = UserProfile(user_id="u-1")
        profile.upsert_tag(
            UserTag(category=TagCategory.INTEREST, value="strong", confidence=0.8)
        )
        profile.upsert_tag(
            UserTag(category=TagCategory.HABIT, value="weak", confidence=0.05)
        )

        removed = profile.remove_stale_tags(min_confidence=0.1)
        assert removed == 1
        assert len(profile.tags) == 1
        assert profile.tags[0].value == "strong"

    def test_to_dict_and_from_dict_roundtrip(self) -> None:
        from core.features.profiles import TagCategory, UserProfile, UserTag

        profile = UserProfile(
            user_id="u-roundtrip",
            display_name="测试用户",
            total_messages=10,
            total_sessions=2,
        )
        profile.upsert_tag(
            UserTag(
                category=TagCategory.INTEREST,
                value="编程",
                confidence=0.85,
            )
        )

        d = profile.to_dict()
        recon = UserProfile.from_dict(d)
        assert recon.user_id == "u-roundtrip"
        assert recon.display_name == "测试用户"
        assert len(recon.tags) == 1
        assert recon.tags[0].value == "编程"
        assert recon.tags[0].confidence == 0.85

    def test_from_dict_minimal(self) -> None:
        from core.features.profiles import UserProfile

        profile = UserProfile.from_dict({})
        assert profile.user_id == ""
        assert profile.display_name == ""
        assert profile.tags == []

    def test_from_dict_with_none_tags(self) -> None:
        from core.features.profiles import UserProfile

        data: dict[str, Any] = {"user_id": "u-1", "tags": None}
        profile = UserProfile.from_dict(data)
        assert profile.tags == []


# ---------------------------------------------------------------------------
# 7. core/shared/default_stopwords.py
# ---------------------------------------------------------------------------


class TestDefaultStopwords:
    """测试 DEFAULT_STOPWORDS 冻结集合。"""

    def test_is_frozenset(self) -> None:
        from core.shared.default_stopwords import DEFAULT_STOPWORDS

        assert isinstance(DEFAULT_STOPWORDS, frozenset)

    def test_non_empty(self) -> None:
        from core.shared.default_stopwords import DEFAULT_STOPWORDS

        assert len(DEFAULT_STOPWORDS) > 100

    def test_all_elements_are_strings(self) -> None:
        from core.shared.default_stopwords import DEFAULT_STOPWORDS

        for word in DEFAULT_STOPWORDS:
            assert isinstance(word, str), f"Expected str, got {type(word)}: {word!r}"

    def test_contains_common_chinese_stopwords(self) -> None:
        from core.shared.default_stopwords import DEFAULT_STOPWORDS

        # 常见中文停用词应全部存在。
        expected = {"的", "了", "是", "我", "你", "他", "在", "和"}
        found = expected & DEFAULT_STOPWORDS
        assert found == expected, f"Missing common stopwords: {expected - found}"

    def test_contains_function_words_from_multiple_categories(self) -> None:
        from core.shared.default_stopwords import DEFAULT_STOPWORDS

        pronouns = {"我", "你", "他们"}
        particles = {"的", "了", "吗", "吧"}
        conjunctions = {"和", "但是", "因为"}
        prepositions = {"在", "从", "对"}
        measure_words = {"个", "次", "点"}

        assert pronouns & DEFAULT_STOPWORDS == pronouns
        assert particles & DEFAULT_STOPWORDS == particles
        assert conjunctions & DEFAULT_STOPWORDS == conjunctions
        assert prepositions & DEFAULT_STOPWORDS == prepositions
        assert measure_words & DEFAULT_STOPWORDS == measure_words

    def test_is_immutable(self) -> None:
        from core.shared.default_stopwords import DEFAULT_STOPWORDS

        # frozenset 不允许修改。
        with pytest.raises(AttributeError):
            DEFAULT_STOPWORDS.add("new_word")  # type: ignore[union-attr]

    def test_no_duplicates(self) -> None:
        from core.shared.default_stopwords import DEFAULT_STOPWORDS

        # frozenset 天然去重，仍需确认其大小等于来源字符串的唯一值数量。
        assert len(DEFAULT_STOPWORDS) == len(set(DEFAULT_STOPWORDS))
