"""conversation feature 的包边界契约。"""


def test_conversation_config_old_path_reuses_feature_owner() -> None:
    """根配置聚合器应恒等导出 conversation feature 的配置模型。"""

    from core.base.config_validator import (
        SessionManagerConfig as LegacySessionManagerConfig,
    )
    from core.features.conversation.domain import SessionManagerConfig

    assert LegacySessionManagerConfig is SessionManagerConfig
