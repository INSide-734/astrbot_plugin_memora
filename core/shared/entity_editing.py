"""跨 feature 共享的实体编辑异常与修订版本原语。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def compute_entity_revision(value: Mapping[str, Any]) -> str:
    """基于规范化 JSON 计算实体的不透明 SHA-256 修订版本。"""

    canonical = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class EntityEditingError(Exception):
    """实体编辑领域异常基类。"""


class EntityAlreadyExistsError(EntityEditingError):
    """待创建的实体已经存在。"""


class EntityNotFoundError(EntityEditingError):
    """待编辑的实体不存在。"""


class EntityValidationError(EntityEditingError):
    """实体字段校验失败。"""

    def __init__(self, field_errors: Mapping[str, str]):
        """保存字段级校验错误。"""

        self.field_errors = dict(field_errors)
        super().__init__("实体校验失败")


class EditConflictError(EntityEditingError):
    """实体的提交版本落后于后台当前版本。"""

    def __init__(
        self,
        current_entity: Mapping[str, Any],
        current_revision: str,
    ):
        """保存最新实体快照和修订版本，供上层生成冲突响应。"""

        self.current_entity = dict(current_entity)
        self.current_revision = current_revision
        super().__init__("记录已被其他请求更新")


__all__ = [
    "EditConflictError",
    "EntityAlreadyExistsError",
    "EntityEditingError",
    "EntityNotFoundError",
    "EntityValidationError",
    "compute_entity_revision",
]
