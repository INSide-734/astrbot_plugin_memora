"""平台配置边界把已知旧名称迁移到当前单一公开契约。"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

_RERANKER_MIGRATION_ID = "reranker.cross_encoder_to_embedding_similarity"
_FORMATTER_LLM_MIGRATION_ID = "human_like_memory.formatter_llm_to_rule"


def migrate_legacy_config(
    user_config: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """迁移受支持的旧配置键，并返回隔离副本和已应用迁移标识。

    参数:
        user_config: 外部配置源的只读视图。

    返回:
        迁移后的深拷贝以及稳定迁移标识；没有命中旧键时标识为空。
    """

    migrated = copy.deepcopy(dict(user_config))
    applied: list[str] = []

    reranker = migrated.get("reranker")
    if isinstance(reranker, dict):
        if reranker.get("strategy") == "cross_encoder":
            reranker["strategy"] = "embedding_similarity"
            applied.append(_RERANKER_MIGRATION_ID)

        if "cross_encoder_lambda" in reranker:
            if "embedding_similarity_lambda" not in reranker:
                reranker["embedding_similarity_lambda"] = reranker[
                    "cross_encoder_lambda"
                ]
            del reranker["cross_encoder_lambda"]
            if _RERANKER_MIGRATION_ID not in applied:
                applied.append(_RERANKER_MIGRATION_ID)

    # 早期文档宣传过 llm 格式化模式但从未实现；迁移到语义最接近的 rule 模式，
    # 避免整节按分支降级而丢失同节其它用户设置。
    human_like = migrated.get("human_like_memory")
    if (
        isinstance(human_like, dict)
        and human_like.get("human_like_formatter_mode") == "llm"
    ):
        human_like["human_like_formatter_mode"] = "rule"
        applied.append(_FORMATTER_LLM_MIGRATION_ID)

    return migrated, tuple(applied)


__all__ = ["migrate_legacy_config"]
