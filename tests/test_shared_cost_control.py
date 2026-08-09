"""验证成本许可策略迁移到 shared 后的兼容契约。"""

from core.base.cost_control import CostControl as LegacyCostControl
from core.shared.contracts import CostControlPort
from core.shared.cost_control import CostControl


def test_legacy_cost_control_export_preserves_object_identity() -> None:
    """旧路径必须精确导出 shared 中的唯一策略类。"""

    assert LegacyCostControl is CostControl


def test_shared_cost_control_preserves_mode_semantics() -> None:
    """shared 策略必须保持三种成本模式和显式许可语义。"""

    quality = CostControl(mode="quality")
    low_cost = CostControl(mode="low_cost")
    balanced = CostControl(
        mode="balanced",
        allow_llm_reranker_in_passive_recall=True,
    )

    assert isinstance(quality, CostControlPort)
    assert quality.allow("note_generation") is True
    assert low_cost.allow("llm_reranker") is False
    assert balanced.allow("llm_reranker") is True
    assert balanced.allow("llm_query_rewrite") is False
