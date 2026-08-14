"""conversation feature 的包边界契约。"""


def test_conversation_config_old_path_reuses_feature_owner() -> None:
    """根配置聚合器应恒等导出 conversation feature 的配置模型。"""

    from core.features.conversation.domain import SessionManagerConfig
    from core.platform.config.config_validator import (
        SessionManagerConfig as LegacySessionManagerConfig,
    )

    assert LegacySessionManagerConfig is SessionManagerConfig
