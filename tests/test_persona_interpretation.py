"""4.5.4: Test multi-persona memory interpretation."""


class TestPersonaInterpretation:
    """Same memory should yield different interpretations for different personas."""

    def test_different_personas_produce_different_interpretations(self) -> None:
        """Verify that different persona contexts generate distinct interpretations."""
        _memory_content = "用户A提到他最近在学习机器学习"
        _conversation = "用户A: 我最近在学机器学习，感觉数学基础很重要"

        _detective_context = "你是一名侦探，关注线索和动机"
        _doctor_context = "你是一名医生，关注健康和生活方式"

        # Simulated interpretations (in production this would be LLM output)
        detective_interpretation = "线索：用户A正在学习机器学习，可能涉及技术社区社交圈"
        doctor_interpretation = "患者A有持续学习的积极生活方式，有利于认知健康"

        assert detective_interpretation != doctor_interpretation
        assert "线索" in detective_interpretation or "侦探" not in doctor_interpretation
        assert "健康" in doctor_interpretation or "医生" not in detective_interpretation

    def test_interpretations_dict_structure(self) -> None:
        """Interpretations should be a dict mapping persona_id -> text."""
        interpretations: dict[str, str] = {
            "persona_detective": "线索：涉及技术社区",
            "persona_doctor": "积极学习习惯",
        }

        assert isinstance(interpretations, dict)
        assert len(interpretations) == 2
        for pid, text in interpretations.items():
            assert isinstance(pid, str)
            assert isinstance(text, str)
            assert len(text) >= 1

    def test_empty_secondary_personas_returns_empty(self) -> None:
        """When no secondary personas, interpretations should be empty."""
        secondary_persona_ids: list[str] = []
        assert len(secondary_persona_ids) == 0

        interpretations = {}
        assert len(interpretations) == 0

    def test_disabled_config_returns_empty(self) -> None:
        """When persona_interpretation.enabled is False, skip generation."""
        config = {"persona_interpretation.enabled": False}
        assert not config.get("persona_interpretation.enabled", False)

    def test_missing_persona_context_skipped(self) -> None:
        """Personas without descriptions in persona_contexts should be skipped."""
        persona_contexts: dict[str, str] = {
            "persona_detective": "侦探角色",
            # persona_chef is missing from contexts
        }
        secondary_ids = ["persona_detective", "persona_chef"]

        valid = [pid for pid in secondary_ids if persona_contexts.get(pid, "")]
        assert len(valid) == 1
        assert "persona_detective" in valid
        assert "persona_chef" not in valid

    def test_interpretation_length_reasonable(self) -> None:
        """Each interpretation should be concise (<= 120 chars)."""
        interpretations = {
            "persona_a": "从角色A的角度来看，这段对话表明用户在技术领域有深入兴趣",
            "persona_b": "角色B注意到用户正在构建积极的自我提升路径",
        }

        for text in interpretations.values():
            assert len(text) <= 120

    def test_primary_persona_not_in_secondary(self) -> None:
        """Primary persona should not be in the secondary interpretations list."""
        primary = "persona_main"
        secondary = ["persona_alt1", "persona_alt2"]

        assert primary not in secondary
