"""测试 memory_atom.py — TTL computation, flashbulb memory (C1), decay scores.

Loads memory_atom.py directly via importlib to avoid triggering core.__init__
which has deep transitive dependencies on the astrbot framework.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load memory_atom as a standalone module (bypasses core.__init__)
# ---------------------------------------------------------------------------
_MODULE_PATH = Path(__file__).resolve().parent.parent / "core" / "models" / "memory_atom.py"
_spec = importlib.util.spec_from_file_location("memory_atom", _MODULE_PATH)
assert _spec is not None, f"Could not load spec from {_MODULE_PATH}"
_memory_atom = importlib.util.module_from_spec(_spec)
sys.modules["memory_atom"] = _memory_atom
_spec.loader.exec_module(_memory_atom)  # type: ignore[arg-type]

# Now import the symbols we need
AtomType = _memory_atom.AtomType
DecayType = _memory_atom.DecayType
compute_decay_score = _memory_atom.compute_decay_score
compute_ttl = _memory_atom.compute_ttl


# ============================================================================
# Flashbulb memory — emotional_intensity >= 0.85
# ============================================================================

class TestFlashbulbTTL:
    """Flashbulb memory bypasses standard decay with minimum 365-day TTL."""

    FLASHBULB_THRESHOLD = 0.85

    def test_flashbulb_min_365_days(self) -> None:
        ttl, dtype = compute_ttl(
            AtomType.EPISODIC, importance=0.7,
            emotional_intensity=self.FLASHBULB_THRESHOLD,
        )
        assert ttl >= 365.0, f"Expected TTL >= 365, got {ttl}"
        assert dtype == DecayType.LINEAR

    def test_flashbulb_scales_with_importance(self) -> None:
        ttl_low, _ = compute_ttl(
            AtomType.EPISODIC, importance=0.7, emotional_intensity=0.90,
        )
        ttl_high, _ = compute_ttl(
            AtomType.EPISODIC, importance=0.95, emotional_intensity=0.90,
        )
        assert ttl_high > ttl_low

    def test_flashbulb_clamps_importance_min_070(self) -> None:
        ttl, _ = compute_ttl(
            AtomType.EPISODIC, importance=0.3, emotional_intensity=0.90,
        )
        assert ttl >= 365.0

    def test_flashbulb_at_exact_threshold(self) -> None:
        ttl, dtype = compute_ttl(
            AtomType.FACTUAL, importance=0.5, emotional_intensity=0.85,
        )
        assert ttl >= 365.0
        assert dtype == DecayType.LINEAR

    def test_just_below_threshold_no_flashbulb(self) -> None:
        ttl, dtype = compute_ttl(
            AtomType.FACTUAL, importance=0.5, emotional_intensity=0.84,
        )
        assert ttl < 365.0, f"Expected <365 for below-threshold, got {ttl}"
        assert dtype == DecayType.EXPONENTIAL


# ============================================================================
# Normal TTL computation (regression)
# ============================================================================

class TestNormalTTL:
    """Regression: standard TTL computation for all atom types."""

    def test_episodic_base_ttl(self) -> None:
        ttl, dtype = compute_ttl(AtomType.EPISODIC, importance=0.5, emotional_intensity=0.5)
        assert 7.0 <= ttl <= 15.0
        assert dtype == DecayType.EXPONENTIAL

    def test_factual_long_ttl(self) -> None:
        ttl, _ = compute_ttl(AtomType.FACTUAL, importance=0.5, emotional_intensity=0.5)
        assert ttl > 100.0

    def test_planned_accounts_for_event_time(self) -> None:
        import time
        future = time.time() + 86400 * 10
        ttl, dtype = compute_ttl(
            AtomType.PLANNED, importance=0.5,
            event_time=future, emotional_intensity=0.5,
        )
        assert ttl >= 10.0
        assert dtype == DecayType.STEP

    def test_reinforcement_increases_ttl(self) -> None:
        ttl_0, _ = compute_ttl(AtomType.EPISODIC, importance=0.5, reinforcement_count=0, emotional_intensity=0.5)
        ttl_3, _ = compute_ttl(AtomType.EPISODIC, importance=0.5, reinforcement_count=3, emotional_intensity=0.5)
        assert ttl_3 > ttl_0

    def test_emotional_intensity_increases_ttl(self) -> None:
        ttl_neutral, _ = compute_ttl(AtomType.EPISODIC, importance=0.5, emotional_intensity=0.1)
        ttl_intense, _ = compute_ttl(AtomType.EPISODIC, importance=0.5, emotional_intensity=0.80)
        assert ttl_intense > ttl_neutral

    def test_ttl_minimum_is_1_day(self) -> None:
        ttl, _ = compute_ttl(AtomType.PLANNED, importance=0.0, emotional_intensity=0.0)
        assert ttl >= 1.0


# ============================================================================
# Persona decay modifier
# ============================================================================

class TestPersonaDecayModifier:
    """Persona-modulated forgetting rate."""

    def test_default_modifier_is_identity(self) -> None:
        ttl_default, _ = compute_ttl(AtomType.EPISODIC, importance=0.5, emotional_intensity=0.5)
        ttl_explicit, _ = compute_ttl(
            AtomType.EPISODIC, importance=0.5, emotional_intensity=0.5,
            persona_decay_modifier=1.0,
        )
        assert ttl_default == ttl_explicit

    def test_faster_forgetting(self) -> None:
        ttl_norm, _ = compute_ttl(AtomType.EPISODIC, importance=0.5, emotional_intensity=0.5, persona_decay_modifier=1.0)
        ttl_fast, _ = compute_ttl(AtomType.EPISODIC, importance=0.5, emotional_intensity=0.5, persona_decay_modifier=2.0)
        assert ttl_fast < ttl_norm

    def test_slower_forgetting(self) -> None:
        ttl_norm, _ = compute_ttl(AtomType.EPISODIC, importance=0.5, emotional_intensity=0.5, persona_decay_modifier=1.0)
        ttl_slow, _ = compute_ttl(AtomType.EPISODIC, importance=0.5, emotional_intensity=0.5, persona_decay_modifier=0.5)
        assert ttl_slow > ttl_norm

    def test_modifier_clamped(self) -> None:
        ttl, _ = compute_ttl(AtomType.EPISODIC, importance=0.5, emotional_intensity=0.5, persona_decay_modifier=100.0)
        assert ttl > 0.0
        ttl, _ = compute_ttl(AtomType.EPISODIC, importance=0.5, emotional_intensity=0.5, persona_decay_modifier=0.001)
        assert ttl > 0.0


# ============================================================================
# compute_decay_score
# ============================================================================

class TestDecayScore:
    """单元测试：decay curve computation."""

    def test_linear_fresh(self) -> None:
        assert compute_decay_score(DecayType.LINEAR, 30.0, 0.0) == 1.0

    def test_linear_half_ttl(self) -> None:
        assert compute_decay_score(DecayType.LINEAR, 30.0, 15.0) == pytest.approx(0.5)

    def test_linear_expired(self) -> None:
        assert compute_decay_score(DecayType.LINEAR, 30.0, 35.0) == 0.0

    def test_step_before_ttl(self) -> None:
        assert compute_decay_score(DecayType.STEP, 7.0, 3.0) == 1.0

    def test_step_after_ttl(self) -> None:
        assert compute_decay_score(DecayType.STEP, 7.0, 10.0) == 0.05

    def test_exponential_half_life(self) -> None:
        score = compute_decay_score(DecayType.EXPONENTIAL, 30.0, 15.0)
        assert 0.45 <= score <= 0.55

    def test_ttl_minimum_1_day(self) -> None:
        score = compute_decay_score(DecayType.EXPONENTIAL, 0.5, 5.0)
        assert 0.0 <= score <= 1.0
