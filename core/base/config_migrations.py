"""把已知旧配置名称迁移到当前单一公开契约。"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

_RERANKER_MIGRATION_ID = "reranker.cross_encoder_to_embedding_similarity"


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
    reranker = migrated.get("reranker")
    if not isinstance(reranker, dict):
        return migrated, ()

    applied = False
    if reranker.get("strategy") == "cross_encoder":
        reranker["strategy"] = "embedding_similarity"
        applied = True

    if "cross_encoder_lambda" in reranker:
        if "embedding_similarity_lambda" not in reranker:
            reranker["embedding_similarity_lambda"] = reranker["cross_encoder_lambda"]
        del reranker["cross_encoder_lambda"]
        applied = True

    return migrated, (_RERANKER_MIGRATION_ID,) if applied else ()


__all__ = ["migrate_legacy_config"]
