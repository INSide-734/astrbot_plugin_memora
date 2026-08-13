"""测试 memory_processor.py — MemoryProcessor."""

from __future__ import annotations

import time
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.shared.cost_control import CostControl
from core.features.recall.processors.memory_processor import MemoryProcessor
from core.shared.contracts.conversation import Message
from core.shared.extra_llm_budget import ExtraLlmBudget, extra_llm_budget_scope

_ProcessorFactory = Callable[..., MemoryProcessor]


def _quality_control() -> CostControl:
    """构造允许一次人设解释额外调用的质量档成本门。"""

    return CostControl(mode="quality", max_extra_llm_calls_per_turn=1)


class TestMemoryProcessorInit:
    def test_init_default(self) -> None:
        proc = MemoryProcessor()
        assert proc.config == {}
        assert proc.llm_client is not None
        assert proc.json_parser is not None

    def test_init_with_context(self) -> None:
        ctx = MagicMock()
        proc = MemoryProcessor(context=ctx)
        assert proc.context is ctx

    def test_init_with_config(self) -> None:
        config = {"atom_enabled": True}
        proc = MemoryProcessor(config=config)
        assert proc.config["atom_enabled"] is True

    def test_conversation_formatter_accessor(self) -> None:
        proc = MemoryProcessor()
        assert proc.conversation_formatter is not None

    def test_llm_client_instance_accessor(self) -> None:
        proc = MemoryProcessor()
        assert proc.llm_client_instance is not None


class TestClassifyAtomsFromMetadata:
    @pytest.fixture
    def processor(self) -> MemoryProcessor:
        return MemoryProcessor(config={"atom_enabled": True})

    def test_classify_with_key_facts(self, processor: MemoryProcessor) -> None:
        metadata = {
            "key_facts": ["用户喜欢咖啡", "明天要开会"],
            "topics": ["咖啡", "会议"],
        }
        atoms = processor.classify_atoms_from_metadata(metadata)
        assert isinstance(atoms, list)

    def test_classify_disabled_returns_empty(self) -> None:
        proc = MemoryProcessor(config={"atom_enabled": False})
        metadata = {"key_facts": ["fact1"]}
        atoms = proc.classify_atoms_from_metadata(metadata)
        assert atoms == []

    def test_classify_no_key_facts_returns_empty(
        self, processor: MemoryProcessor
    ) -> None:
        metadata = {"key_facts": []}
        atoms = processor.classify_atoms_from_metadata(metadata)
        assert atoms == []

    def test_classify_with_all_metadata_fields(
        self, processor: MemoryProcessor
    ) -> None:
        metadata = {
            "key_facts": ["fact1"],
            "topics": ["topic1"],
            "participants": ["Alice"],
            "emotion_tags": ["happy"],
            "emotional_intensity": 0.8,
        }
        atoms = processor.classify_atoms_from_metadata(
            metadata=metadata,
            parent_importance=0.7,
            session_id="s1",
            persona_id="p1",
        )
        assert isinstance(atoms, list)


class TestBuildMemoryFromStructuredData:
    @pytest.fixture
    def processor(self) -> MemoryProcessor:
        return MemoryProcessor(config={"atom_enabled": True})

    def test_build_from_valid_data(self, processor: MemoryProcessor) -> None:
        data = {
            "summary": "用户讨论咖啡",
            "topics": ["咖啡"],
            "key_facts": ["用户喜欢拿铁"],
            "sentiment": "positive",
            "importance": 0.7,
        }
        result = processor.build_memory_from_structured_data(data)
        assert "content" in result
        assert "metadata" in result
        assert "importance" in result
        assert "atoms" in result
        assert result["metadata"]["schema_version"] == "v3"

    def test_build_with_fallback_excerpt(self, processor: MemoryProcessor) -> None:
        data = {
            "summary": "摘要",
            "key_facts": [],
            "topics": [],
            "sentiment": "neutral",
            "importance": 0.5,
        }
        result = processor.build_memory_from_structured_data(
            data, fallback_excerpt="fallback text here"
        )
        assert result["metadata"]["source_snippet"] is not None

    def test_build_group_chat(self, processor: MemoryProcessor) -> None:
        data = {
            "summary": "群聊",
            "topics": [],
            "key_facts": ["fact1"],
            "sentiment": "neutral",
            "importance": 0.5,
            "participants": ["张三"],
        }
        result = processor.build_memory_from_structured_data(data, is_group_chat=True)
        assert result["metadata"]["interaction_type"] == "group_chat"


class TestProcessConversation:
    @pytest.fixture
    def make_processor(self) -> _ProcessorFactory:
        def _make(
            llm_response: str = "", config: dict | None = None
        ) -> MemoryProcessor:
            ctx = MagicMock()
            ctx.get_using_provider.return_value = None
            ctx.persona_manager = None
            ctx.get_registered_llm_tools.return_value = []

            provider = MagicMock()
            response = AsyncMock()
            response.completion_text = llm_response or (
                '{"summary": "测试摘要", "topics": ["测试"], "key_facts": ["事实1"], '
                '"sentiment": "positive", "importance": 0.7}'
            )
            provider.text_chat = AsyncMock(return_value=response)

            proc = MemoryProcessor(
                context=ctx,
                llm_provider=provider,
                config=config or {},
            )
            return proc

        return _make

    @pytest.fixture
    def sample_messages(self) -> list[Message]:
        ts = time.time()
        return [
            Message(
                id=1,
                session_id="s1",
                role="user",
                content="你好，我喜欢喝咖啡",
                sender_id="user1",
                sender_name="Alice",
                timestamp=ts,
            ),
            Message(
                id=2,
                session_id="s1",
                role="assistant",
                content="好的，我记住了你喜欢咖啡",
                sender_id="bot1",
                sender_name="Bot",
                timestamp=ts + 1,
                metadata={"is_bot_message": True},
            ),
        ]

    def test_process_basic_private_chat(
        self, make_processor: _ProcessorFactory, sample_messages: list[Message]
    ) -> None:
        import asyncio

        proc = make_processor()
        results = asyncio.run(
            proc.process_conversation(sample_messages, is_group_chat=False)
        )
        assert len(results) >= 1
        assert "content" in results[0]
        assert "importance" in results[0]

    def test_process_group_chat(
        self, make_processor: _ProcessorFactory, sample_messages: list[Message]
    ) -> None:
        import asyncio

        response = (
            '{"summary": "群聊摘要", "topics": ["测试"], "key_facts": ["fact1"], '
            '"sentiment": "neutral", "importance": 0.5, "participants": ["Alice", "Bob"]}'
        )
        proc = make_processor(llm_response=response)
        results = asyncio.run(
            proc.process_conversation(sample_messages, is_group_chat=True)
        )
        assert len(results) >= 1

    def test_trusted_message_identity_overrides_llm_participants(
        self,
        make_processor: _ProcessorFactory,
    ) -> None:
        """可信 Message 身份应确定参与者顺序、标签和当前名称快照。"""

        import asyncio

        response = (
            '{"summary":"改名后的消息","topics":["测试"],'
            '"key_facts":["改名后的消息"],"participants":["模型伪造名称"],'
            '"source_refs":[{"message_index":2,"start":0,"end":6}],'
            '"importance":0.7}'
        )
        processor = make_processor(
            llm_response=response,
            config={"atom_enabled": False},
        )
        processor.llm_client.call_llm_with_retry_result = AsyncMock(
            return_value=SimpleNamespace(
                text=response,
                prompt_tokens=None,
                completion_tokens=None,
            )
        )
        messages = [
            Message(
                id=1,
                session_id="s1",
                role="user",
                content="第一条",
                sender_id="10001",
                sender_name="旧昵称",
                metadata={
                    "identity_trusted": True,
                    "identity_protocol": "onebot11",
                    "identity_namespace": "qq",
                    "stable_user_id": "10001",
                    "canonical_user_id": "10001",
                    "identity_label": "QQ:10001",
                },
            ),
            Message(
                id=2,
                session_id="s1",
                role="user",
                content="第二条",
                sender_id="10002",
                sender_name="成员乙",
                metadata={
                    "identity_trusted": True,
                    "identity_protocol": "onebot11",
                    "identity_namespace": "qq",
                    "stable_user_id": "10002",
                    "canonical_user_id": "10002",
                    "identity_label": "QQ:10002",
                },
            ),
            Message(
                id=3,
                session_id="s1",
                role="user",
                content="改名后的消息",
                sender_id="10001",
                sender_name="新昵称",
                metadata={
                    "identity_trusted": True,
                    "identity_protocol": "onebot11",
                    "identity_namespace": "qq",
                    "stable_user_id": "10001",
                    "canonical_user_id": "10001",
                    "identity_label": "QQ:10001",
                },
            ),
        ]

        results = asyncio.run(
            processor.process_conversation(messages, is_group_chat=True)
        )

        metadata = results[0]["metadata"]
        assert metadata["identity_schema_version"] == "stable-identity-v1"
        assert metadata["participant_ids"] == ["10001", "10002"]
        assert metadata["participants"] == ["QQ:10001", "QQ:10002"]
        assert metadata["participant_name_snapshots"] == {
            "10001": "新昵称",
            "10002": "成员乙",
        }
        assert metadata["subject_ids"] == ["10001"]
        llm_call = processor.llm_client.call_llm_with_retry_result.await_args
        assert llm_call is not None
        prompt = llm_call.kwargs["prompt"]
        assert "新昵称（QQ:10001）" in prompt
        assert "禁止猜测、改写或交换稳定标识" in prompt

    def test_untrusted_message_metadata_keeps_legacy_participants(
        self,
        make_processor: _ProcessorFactory,
    ) -> None:
        """缺少可信标志时不得接受伪造 canonical 字段，旧参与者语义保持不变。"""

        import asyncio

        response = (
            '{"summary":"兼容测试","topics":["测试"],'
            '"key_facts":["事实"],"participants":["旧参与者"],'
            '"importance":0.5}'
        )
        processor = make_processor(llm_response=response)
        messages = [
            Message(
                id=1,
                session_id="s1",
                role="user",
                content="消息",
                sender_id="legacy",
                sender_name="旧参与者",
                metadata={
                    "canonical_user_id": "伪造值",
                    "identity_label": "QQ:99999",
                },
            )
        ]

        results = asyncio.run(
            processor.process_conversation(messages, is_group_chat=True)
        )

        metadata = results[0]["metadata"]
        assert metadata["participants"] == ["旧参与者"]
        assert "identity_schema_version" not in metadata

    def test_process_empty_messages_raises(self) -> None:
        import asyncio

        proc = MemoryProcessor()
        with pytest.raises(ValueError, match="不能为空"):
            asyncio.run(proc.process_conversation([]))

    def test_process_with_serial_position_hint(
        self, make_processor: _ProcessorFactory, sample_messages: list[Message]
    ) -> None:
        import asyncio

        proc = make_processor()
        results = asyncio.run(
            proc.process_conversation(
                sample_messages,
                serial_position_hint="first",
            )
        )
        assert len(results) >= 1
        # Primacy effect should boost importance
        assert results[0]["importance"] >= 0.5

    def test_process_with_interest_profile(
        self, make_processor: _ProcessorFactory, sample_messages: list[Message]
    ) -> None:
        import asyncio

        response = (
            '{"summary": "咖啡讨论", "topics": ["咖啡", "饮食"], "key_facts": ["fact1"], '
            '"sentiment": "positive", "importance": 0.6}'
        )
        proc = make_processor(llm_response=response)
        results = asyncio.run(
            proc.process_conversation(
                sample_messages,
                interest_profile=["咖啡", "美食"],
            )
        )
        assert len(results) >= 1

    def test_process_with_emotion_tags(
        self, make_processor: _ProcessorFactory, sample_messages: list[Message]
    ) -> None:
        import asyncio

        response = (
            '{"summary": "s", "topics": ["t"], "key_facts": ["f"], '
            '"sentiment": "positive", "importance": 0.5, "emotion_tags": ["joy", "excited"]}'
        )
        proc = make_processor(llm_response=response)
        results = asyncio.run(proc.process_conversation(sample_messages))
        assert len(results) >= 1
        assert "emotion_tags" in results[0]["metadata"]

    def test_process_with_causal_relations(
        self, make_processor: _ProcessorFactory, sample_messages: list[Message]
    ) -> None:
        import asyncio

        response = (
            '{"summary": "s", "topics": ["t"], "key_facts": ["f"], '
            '"sentiment": "neutral", "importance": 0.5, '
            '"causal_relations": [{"cause": "下雨", "effect": "没去公园"}]}'
        )
        proc = make_processor(llm_response=response)
        results = asyncio.run(proc.process_conversation(sample_messages))
        assert len(results) >= 1

    def test_process_with_memories_array(
        self, make_processor: _ProcessorFactory, sample_messages: list[Message]
    ) -> None:
        import asyncio

        response = (
            '{"memories": ['
            '{"summary": "话题1", "topics": ["t1"], "key_facts": ["f1"], "importance": 0.7},'
            '{"summary": "话题2", "topics": ["t2"], "key_facts": ["f2"], "importance": 0.5}'
            "]}"
        )
        proc = make_processor(llm_response=response)
        results = asyncio.run(proc.process_conversation(sample_messages))
        assert len(results) >= 2

    def test_process_llm_failure_raises(self, sample_messages: list[Message]) -> None:
        import asyncio

        ctx = MagicMock()
        provider = MagicMock()
        provider.text_chat = AsyncMock(side_effect=RuntimeError("LLM down"))
        proc = MemoryProcessor(context=ctx, llm_provider=provider)

        with pytest.raises(Exception):
            asyncio.run(proc.process_conversation(sample_messages))

    def test_process_memories_with_string_entry_skipped(
        self, make_processor: _ProcessorFactory, sample_messages: list[Message]
    ) -> None:
        import asyncio

        response = (
            '{"memories": ['
            '"a string entry that should be skipped",'
            '{"summary": "valid", "topics": ["t"], "key_facts": ["f"], "importance": 0.5}'
            "]}"
        )
        proc = make_processor(llm_response=response)
        results = asyncio.run(proc.process_conversation(sample_messages))
        assert len(results) >= 1

    def test_process_memories_empty_summary_and_facts_skipped(
        self, make_processor: _ProcessorFactory, sample_messages: list[Message]
    ) -> None:
        import asyncio

        response = (
            '{"memories": ['
            '{"summary": "", "key_facts": [], "topics": []},'
            '{"summary": "valid", "topics": ["t"], "key_facts": ["f"], "importance": 0.5}'
            "]}"
        )
        proc = make_processor(llm_response=response)
        results = asyncio.run(proc.process_conversation(sample_messages))
        assert len(results) >= 1

    def test_process_with_emotional_intensity_boost(
        self, make_processor: _ProcessorFactory, sample_messages: list[Message]
    ) -> None:
        import asyncio

        proc = make_processor()
        results = asyncio.run(
            proc.process_conversation(
                sample_messages,
                emotional_intensity=0.9,
            )
        )
        assert len(results) >= 1
        # High emotional intensity boosts importance
        assert results[0]["importance"] >= 0.5

    def test_process_with_persona_id(
        self, make_processor: _ProcessorFactory, sample_messages: list[Message]
    ) -> None:
        import asyncio

        proc = make_processor()
        results = asyncio.run(
            proc.process_conversation(sample_messages, persona_id="persona_1")
        )
        assert len(results) >= 1

    def test_process_with_continuity_context(
        self, make_processor: _ProcessorFactory, sample_messages: list[Message]
    ) -> None:
        import asyncio

        proc = make_processor()
        results = asyncio.run(
            proc.process_conversation(
                sample_messages,
                continuity_context="previous conversation about coffee",
            )
        )
        assert len(results) >= 1

    def test_process_with_serial_position_first_and_last(
        self, make_processor: _ProcessorFactory, sample_messages: list[Message]
    ) -> None:
        import asyncio

        proc = make_processor()
        results = asyncio.run(
            proc.process_conversation(
                sample_messages,
                serial_position_hint="first_and_last",
            )
        )
        assert len(results) >= 1
        assert results[0]["importance"] >= 0.5

    def test_process_serpent_metadata_serial_position(
        self, make_processor: _ProcessorFactory, sample_messages: list[Message]
    ) -> None:
        import asyncio

        response = (
            '{"summary": "s", "topics": ["t"], "key_facts": ["f"], '
            '"sentiment": "neutral", "importance": 0.5, '
            '"emotion_tags": ["joy"]}'
        )
        proc = make_processor(llm_response=response)
        results = asyncio.run(
            proc.process_conversation(
                sample_messages,
                serial_position_hint="first_and_last",
                emotional_intensity=0.8,
            )
        )
        assert len(results) >= 1
        assert "emotion_tags" in results[0]["metadata"]

    def test_process_low_quality_still_returns(
        self, make_processor: _ProcessorFactory, sample_messages: list[Message]
    ) -> None:
        import asyncio

        # Need at least one key_fact for the memory to be created, even with low quality
        response = '{"summary": "low quality", "topics": [], "key_facts": ["minimal fact"], "sentiment": "neutral", "importance": 0.1}'
        proc = make_processor(llm_response=response)
        results = asyncio.run(proc.process_conversation(sample_messages))
        # Low quality still produces results (at least 1 since we have a key_fact)
        assert len(results) >= 1

    def test_process_guardrails_valid_memory_extraction(
        self, make_processor: _ProcessorFactory, sample_messages: list[Message]
    ) -> None:
        import asyncio

        response = (
            '{"memories": ['
            '{"content": "用户喜欢深烘咖啡", "atom_type": "preference", '
            '"importance": 0.8, "entities": ["咖啡"], "emotion_tags": ["喜欢"]}'
            '], "confidence": 0.9, "extraction_quality": "high"}'
        )
        proc = make_processor(llm_response=response)
        results = asyncio.run(proc.process_conversation(sample_messages))
        assert len(results) == 1
        assert results[0]["metadata"]["guardrails_validated"] is True
        assert results[0]["importance"] == 0.8

    def test_process_guardrails_accepts_configured_summary_contract(
        self, make_processor: _ProcessorFactory, sample_messages: list[Message]
    ) -> None:
        """Prompt 约定的 summary 输出应通过护栏并完整进入存储 metadata。"""

        import asyncio

        response = (
            '{"memories": [{"summary": "我记得用户明确说过喜欢深烘咖啡", '
            '"topics": ["咖啡偏好"], "key_facts": ["用户喜欢深烘咖啡"], '
            '"participants": ["用户"], "sentiment": "positive", '
            '"importance": 0.8, "emotion_tags": ["开心"], '
            '"causal_relations": []}]}'
        )
        proc = make_processor(llm_response=response)

        results = asyncio.run(proc.process_conversation(sample_messages))

        assert len(results) == 1
        assert results[0]["metadata"]["guardrails_validated"] is True
        assert results[0]["metadata"]["topics"] == ["咖啡偏好"]
        assert results[0]["metadata"]["key_facts"] == ["用户喜欢深烘咖啡"]
        assert results[0]["metadata"].get("guardrail_fallback") is not True

    def test_process_guardrails_empty_memories_falls_back_to_legacy_parser(
        self, make_processor: _ProcessorFactory, sample_messages: list[Message]
    ) -> None:
        import asyncio

        response = (
            '{"summary": "旧格式摘要", "topics": ["旧格式"], '
            '"key_facts": ["旧格式事实"], "sentiment": "neutral", "importance": 0.6}'
        )
        proc = make_processor(llm_response=response)
        results = asyncio.run(proc.process_conversation(sample_messages))
        assert len(results) == 1
        assert results[0]["metadata"]["guardrail_fallback"] is True
        assert results[0]["metadata"].get("guardrails_validated") is not True


class TestMemoryProcessorTopicGuidance:
    def test_load_topic_guidance_empty_dir(self) -> None:
        result = MemoryProcessor._load_topic_guidance(None)
        assert result == ""

    def test_load_topic_guidance_missing_file(self, tmp_path) -> None:
        result = MemoryProcessor._load_topic_guidance(tmp_path)
        assert result == ""

    def test_load_topic_guidance_with_file(self, tmp_path) -> None:
        guidance_file = tmp_path / "topic_segmentation_guidance.txt"
        guidance_file.write_text("Test guidance content", encoding="utf-8")
        result = MemoryProcessor._load_topic_guidance(tmp_path)
        assert result == "Test guidance content"


class TestGeneratePersonaInterpretations:
    @pytest.mark.asyncio
    async def test_disabled_returns_empty(self) -> None:
        proc = MemoryProcessor(config={"persona_interpretation.enabled": False})
        result = await proc.generate_persona_interpretations(
            "content", "conv_text", "primary", ["secondary"], {"secondary": "desc"}
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_empty_secondary_ids_returns_empty(self) -> None:
        proc = MemoryProcessor(config={"persona_interpretation.enabled": True})
        result = await proc.generate_persona_interpretations(
            "content", "conv_text", "primary", [], {}
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_missing_persona_context_skips(self) -> None:
        proc = MemoryProcessor(config={"persona_interpretation.enabled": True})
        result = await proc.generate_persona_interpretations(
            "content",
            "conv_text",
            "primary",
            ["no_context_persona"],
            {},  # no context for this persona
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_generates_interpretation(self) -> None:
        ctx = MagicMock()
        provider = MagicMock()
        response = MagicMock()
        response.completion_text = "这条记忆意味着需要关注用户的咖啡偏好"
        provider.text_chat = AsyncMock(return_value=response)

        proc = MemoryProcessor(
            context=ctx,
            llm_provider=provider,
            config={"persona_interpretation.enabled": True},
            cost_control=_quality_control(),
        )
        with extra_llm_budget_scope(ExtraLlmBudget(max_calls=1)):
            result = await proc.generate_persona_interpretations(
                "用户喜欢喝咖啡",
                "用户: 我喜欢喝咖啡",
                "primary_persona",
                ["coffee_expert"],
                {"coffee_expert": "你是咖啡专家，关注用户的咖啡消费习惯"},
            )
        assert len(result) >= 1
        assert "coffee_expert" in result

    @pytest.mark.asyncio
    async def test_interpretation_short_text_discarded(self) -> None:
        ctx = MagicMock()
        provider = MagicMock()
        response = MagicMock()
        response.completion_text = "ab"  # shorter than 3 chars
        provider.text_chat = AsyncMock(return_value=response)

        proc = MemoryProcessor(
            context=ctx,
            llm_provider=provider,
            config={"persona_interpretation.enabled": True},
            cost_control=_quality_control(),
        )
        with extra_llm_budget_scope(ExtraLlmBudget(max_calls=1)):
            result = await proc.generate_persona_interpretations(
                "content",
                "conv",
                "primary",
                ["secondary"],
                {"secondary": "desc"},
            )
        # Short text (< 3 chars) is discarded
        assert "secondary" not in result

    @pytest.mark.asyncio
    async def test_interpretation_llm_error_handled(self) -> None:
        ctx = MagicMock()
        provider = MagicMock()
        provider.text_chat = AsyncMock(side_effect=RuntimeError("LLM error"))

        proc = MemoryProcessor(
            context=ctx,
            llm_provider=provider,
            config={"persona_interpretation.enabled": True},
            cost_control=_quality_control(),
        )
        with extra_llm_budget_scope(ExtraLlmBudget(max_calls=1)):
            result = await proc.generate_persona_interpretations(
                "content",
                "conv",
                "primary",
                ["secondary"],
                {"secondary": "desc"},
            )
        # Error should be handled, should not raise
        assert "secondary" not in result
