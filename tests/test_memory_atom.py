"""测试 MemoryAtom 的 TTL、闪光记忆与衰减分数。

通过 importlib 直接加载 feature owner 文件，验证领域模型可独立运行，
无需依赖 ``core`` 包级聚合入口。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 将 memory_atom 作为独立模块加载，避免依赖 core 包级入口
# ---------------------------------------------------------------------------
_MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "core"
    / "features"
    / "memory"
    / "domain"
    / "memory_atom.py"
)
_spec = importlib.util.spec_from_file_location("memory_atom", _MODULE_PATH)
assert _spec is not None, f"无法从 {_MODULE_PATH} 加载模块定义"
_memory_atom = importlib.util.module_from_spec(_spec)
sys.modules["memory_atom"] = _memory_atom
_spec.loader.exec_module(_memory_atom)  # type: ignore[arg-type]

# 提取当前测试所需的领域符号
AtomType = _memory_atom.AtomType
DecayType = _memory_atom.DecayType
compute_decay_score = _memory_atom.compute_decay_score
compute_ttl = _memory_atom.compute_ttl


# ============================================================================
# 闪光记忆：emotional_intensity >= 0.85
# ============================================================================


class TestFlashbulbTTL:
    """验证闪光记忆绕过常规衰减并至少保留 365 天。"""

    FLASHBULB_THRESHOLD = 0.85

    def test_flashbulb_min_365_days(self) -> None:
        """验证阈值强度下的 TTL 至少为 365 天。"""

        ttl, dtype = compute_ttl(
            AtomType.EPISODIC,
            importance=0.7,
            emotional_intensity=self.FLASHBULB_THRESHOLD,
        )
        assert ttl >= 365.0, f"TTL 应至少为 365 天，实际为 {ttl}"
        assert dtype == DecayType.LINEAR

    def test_flashbulb_scales_with_importance(self) -> None:
        """验证更高重要性会延长闪光记忆 TTL。"""

        ttl_low, _ = compute_ttl(
            AtomType.EPISODIC,
            importance=0.7,
            emotional_intensity=0.90,
        )
        ttl_high, _ = compute_ttl(
            AtomType.EPISODIC,
            importance=0.95,
            emotional_intensity=0.90,
        )
        assert ttl_high > ttl_low

    def test_flashbulb_clamps_importance_min_070(self) -> None:
        """验证闪光记忆路径会钳制过低的重要性。"""

        ttl, _ = compute_ttl(
            AtomType.EPISODIC,
            importance=0.3,
            emotional_intensity=0.90,
        )
        assert ttl >= 365.0

    def test_flashbulb_at_exact_threshold(self) -> None:
        """验证恰好达到阈值时进入闪光记忆路径。"""

        ttl, dtype = compute_ttl(
            AtomType.FACTUAL,
            importance=0.5,
            emotional_intensity=0.85,
        )
        assert ttl >= 365.0
        assert dtype == DecayType.LINEAR

    def test_just_below_threshold_no_flashbulb(self) -> None:
        """验证略低于阈值时仍使用常规指数衰减。"""

        ttl, dtype = compute_ttl(
            AtomType.FACTUAL,
            importance=0.5,
            emotional_intensity=0.84,
        )
        assert ttl < 365.0, f"低于阈值时 TTL 应小于 365 天，实际为 {ttl}"
        assert dtype == DecayType.EXPONENTIAL


# ============================================================================
# 常规 TTL 计算回归
# ============================================================================


class TestNormalTTL:
    """验证各类记忆原子的常规 TTL 计算。"""

    def test_episodic_base_ttl(self) -> None:
        """验证情景记忆的基础 TTL 与衰减类型。"""

        ttl, dtype = compute_ttl(
            AtomType.EPISODIC, importance=0.5, emotional_intensity=0.5
        )
        assert 7.0 <= ttl <= 15.0
        assert dtype == DecayType.EXPONENTIAL

    def test_factual_long_ttl(self) -> None:
        """验证事实记忆具有较长的 TTL。"""

        ttl, _ = compute_ttl(AtomType.FACTUAL, importance=0.5, emotional_intensity=0.5)
        assert ttl > 100.0

    def test_planned_accounts_for_event_time(self) -> None:
        """验证计划记忆会覆盖未来事件的剩余时间。"""

        import time

        future = time.time() + 86400 * 10
        ttl, dtype = compute_ttl(
            AtomType.PLANNED,
            importance=0.5,
            event_time=future,
            emotional_intensity=0.5,
        )
        assert ttl >= 10.0
        assert dtype == DecayType.STEP

    def test_reinforcement_increases_ttl(self) -> None:
        """验证强化次数增加时 TTL 延长。"""

        ttl_0, _ = compute_ttl(
            AtomType.EPISODIC,
            importance=0.5,
            reinforcement_count=0,
            emotional_intensity=0.5,
        )
        ttl_3, _ = compute_ttl(
            AtomType.EPISODIC,
            importance=0.5,
            reinforcement_count=3,
            emotional_intensity=0.5,
        )
        assert ttl_3 > ttl_0

    def test_emotional_intensity_increases_ttl(self) -> None:
        """验证情绪强度增加时 TTL 延长。"""

        ttl_neutral, _ = compute_ttl(
            AtomType.EPISODIC, importance=0.5, emotional_intensity=0.1
        )
        ttl_intense, _ = compute_ttl(
            AtomType.EPISODIC, importance=0.5, emotional_intensity=0.80
        )
        assert ttl_intense > ttl_neutral

    def test_ttl_minimum_is_1_day(self) -> None:
        """验证常规计算得到的 TTL 不低于一天。"""

        ttl, _ = compute_ttl(AtomType.PLANNED, importance=0.0, emotional_intensity=0.0)
        assert ttl >= 1.0


# ============================================================================
# 人格衰减倍率
# ============================================================================


class TestPersonaDecayModifier:
    """验证人格衰减倍率对遗忘速度的影响。"""

    def test_default_modifier_is_identity(self) -> None:
        """验证默认倍率与显式 1.0 的结果一致。"""

        ttl_default, _ = compute_ttl(
            AtomType.EPISODIC, importance=0.5, emotional_intensity=0.5
        )
        ttl_explicit, _ = compute_ttl(
            AtomType.EPISODIC,
            importance=0.5,
            emotional_intensity=0.5,
            persona_decay_modifier=1.0,
        )
        assert ttl_default == ttl_explicit

    def test_faster_forgetting(self) -> None:
        """验证较高倍率会加快遗忘。"""

        ttl_norm, _ = compute_ttl(
            AtomType.EPISODIC,
            importance=0.5,
            emotional_intensity=0.5,
            persona_decay_modifier=1.0,
        )
        ttl_fast, _ = compute_ttl(
            AtomType.EPISODIC,
            importance=0.5,
            emotional_intensity=0.5,
            persona_decay_modifier=2.0,
        )
        assert ttl_fast < ttl_norm

    def test_slower_forgetting(self) -> None:
        """验证较低倍率会减慢遗忘。"""

        ttl_norm, _ = compute_ttl(
            AtomType.EPISODIC,
            importance=0.5,
            emotional_intensity=0.5,
            persona_decay_modifier=1.0,
        )
        ttl_slow, _ = compute_ttl(
            AtomType.EPISODIC,
            importance=0.5,
            emotional_intensity=0.5,
            persona_decay_modifier=0.5,
        )
        assert ttl_slow > ttl_norm

    def test_modifier_clamped(self) -> None:
        """验证极端人格衰减倍率会被安全钳制。"""

        ttl, _ = compute_ttl(
            AtomType.EPISODIC,
            importance=0.5,
            emotional_intensity=0.5,
            persona_decay_modifier=100.0,
        )
        assert ttl > 0.0
        ttl, _ = compute_ttl(
            AtomType.EPISODIC,
            importance=0.5,
            emotional_intensity=0.5,
            persona_decay_modifier=0.001,
        )
        assert ttl > 0.0


# ============================================================================
# 衰减分数计算
# ============================================================================


class TestDecayScore:
    """验证线性、阶跃与指数衰减曲线。"""

    def test_linear_fresh(self) -> None:
        """验证新鲜线性记忆的分数为 1。"""

        assert compute_decay_score(DecayType.LINEAR, 30.0, 0.0) == 1.0

    def test_linear_half_ttl(self) -> None:
        """验证线性记忆经过半个 TTL 后分数减半。"""

        assert compute_decay_score(DecayType.LINEAR, 30.0, 15.0) == pytest.approx(0.5)

    def test_linear_expired(self) -> None:
        """验证线性记忆过期后的分数为 0。"""

        assert compute_decay_score(DecayType.LINEAR, 30.0, 35.0) == 0.0

    def test_step_before_ttl(self) -> None:
        """验证阶跃记忆在 TTL 前保持满分。"""

        assert compute_decay_score(DecayType.STEP, 7.0, 3.0) == 1.0

    def test_step_after_ttl(self) -> None:
        """验证阶跃记忆过期后保留最低分。"""

        assert compute_decay_score(DecayType.STEP, 7.0, 10.0) == 0.05

    def test_exponential_half_life(self) -> None:
        """验证指数记忆经过半衰期后的分数约为一半。"""

        score = compute_decay_score(DecayType.EXPONENTIAL, 30.0, 15.0)
        assert 0.45 <= score <= 0.55

    def test_ttl_minimum_1_day(self) -> None:
        """验证衰减计算会把 TTL 最低按一天处理。"""

        score = compute_decay_score(DecayType.EXPONENTIAL, 0.5, 5.0)
        assert 0.0 <= score <= 1.0
