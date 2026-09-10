"""配置变更审计日志。

记录灰度切换、配置变更的决策链，支持追溯和回滚。
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class ConfigAuditEntry:
    """配置变更审计条目。

    字段遵循观测 allowlist：只记录配置结构和聚合标量，不记录输入正文、
    身份、scope 或 Prompt 数据。
    """

    timestamp: str
    entry_id: str
    operator: str
    evidence_source: str
    reason: str
    before: dict[str, Any]
    after: dict[str, Any]
    changed_buckets: list[str] = field(default_factory=list)
    config_revision_before: str | None = None
    config_revision_after: str | None = None
    report_sha256: str | None = None
    sample_counts: dict[str, int] = field(default_factory=dict)
    rollback_of: str | None = None

    @classmethod
    def create(
        cls,
        operator: str,
        evidence_source: str,
        reason: str,
        before: dict[str, Any],
        after: dict[str, Any],
        *,
        changed_buckets: list[str] | None = None,
        config_revision_before: str | None = None,
        config_revision_after: str | None = None,
        report_sha256: str | None = None,
        sample_counts: dict[str, int] | None = None,
        rollback_of: str | None = None,
    ) -> "ConfigAuditEntry":
        """创建新的审计条目并生成 UTC 时间戳和唯一标识。

        参数：
            operator: 固定的控制面操作者类型。
            evidence_source: 稳定证据来源标识，不能包含敏感路径。
            reason: 变更原因码。
            before: 变更前的 allowlist 配置快照。
            after: 变更后的 allowlist 配置快照。
            changed_buckets: 受影响的规模桶。
            config_revision_before: 应用前的 ConfigManager 修订号。
            config_revision_after: 应用后的 ConfigManager 修订号。
            report_sha256: 证据报告内容的 SHA-256。
            sample_counts: 仅包含聚合样本量的字典。
            rollback_of: 回滚时引用的原审计条目 ID。

        返回：
            新的审计条目。
        """
        return cls(
            timestamp=datetime.now(timezone.utc).isoformat(),
            entry_id=str(uuid.uuid4()),
            operator=operator,
            evidence_source=evidence_source,
            reason=reason,
            before=dict(before),
            after=dict(after),
            changed_buckets=list(changed_buckets or []),
            config_revision_before=config_revision_before,
            config_revision_after=config_revision_after,
            report_sha256=report_sha256,
            sample_counts=dict(sample_counts or {}),
            rollback_of=rollback_of,
        )

    def to_json(self) -> str:
        """序列化为单行 JSON，保留所有可审计的可选字段。"""
        return json.dumps(
            {
                "timestamp": self.timestamp,
                "entry_id": self.entry_id,
                "operator": self.operator,
                "evidence_source": self.evidence_source,
                "reason": self.reason,
                "before": self.before,
                "after": self.after,
                "changed_buckets": self.changed_buckets,
                "config_revision_before": self.config_revision_before,
                "config_revision_after": self.config_revision_after,
                "report_sha256": self.report_sha256,
                "sample_counts": self.sample_counts,
                "rollback_of": self.rollback_of,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, line: str) -> "ConfigAuditEntry":
        """从 JSON Lines 反序列化条目，并兼容缺少新字段的旧行。

        参数：
            line: 单行 JSON 文本。

        返回：
            已校验的审计条目。

        异常：
            ValueError: JSON 或字段形状无效。
        """
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("审计条目 JSON 无效") from exc
        if not isinstance(data, dict):
            raise ValueError("审计条目必须是对象")

        try:
            before = data["before"]
            after = data["after"]
            if not isinstance(before, dict) or not isinstance(after, dict):
                raise ValueError("审计配置快照必须是对象")
            changed_buckets = data.get("changed_buckets", [])
            if not isinstance(changed_buckets, list) or not all(
                isinstance(bucket, str) for bucket in changed_buckets
            ):
                raise ValueError("审计规模桶字段无效")
            sample_counts = data.get("sample_counts", {})
            if not isinstance(sample_counts, dict) or any(
                not isinstance(name, str) or type(count) is not int or count < 0
                for name, count in sample_counts.items()
            ):
                raise ValueError("审计样本量字段无效")
            return cls(
                timestamp=_required_string(data, "timestamp"),
                entry_id=_required_string(data, "entry_id"),
                operator=_required_string(data, "operator"),
                evidence_source=_required_string(data, "evidence_source"),
                reason=_required_string(data, "reason"),
                before=dict(before),
                after=dict(after),
                changed_buckets=list(changed_buckets),
                config_revision_before=_optional_string(data, "config_revision_before"),
                config_revision_after=_optional_string(data, "config_revision_after"),
                report_sha256=_optional_string(data, "report_sha256"),
                sample_counts=dict(sample_counts),
                rollback_of=_optional_string(data, "rollback_of"),
            )
        except KeyError as exc:
            raise ValueError("审计条目缺少必填字段") from exc


def _required_string(data: dict[str, Any], key: str) -> str:
    """读取必须存在的非空字符串审计字段。"""
    value = data[key]
    if not isinstance(value, str) or not value:
        raise ValueError("审计字符串字段无效")
    return value


def _optional_string(data: dict[str, Any], key: str) -> str | None:
    """读取可选字符串审计字段，并拒绝其他 JSON 类型。"""
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError("审计可选字符串字段无效")
    return value


def write_audit_entry(entry: ConfigAuditEntry, path: Path) -> None:
    """追加写入并 flush/fsync 一条规范审计记录。

    审计写入是配置事务成功后的 durability 边界；任何目录、编码、写入
    或 fsync 错误都向调用方传播，由控制面执行保守补偿。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(entry.to_json() + "\n")
        file.flush()
        os.fsync(file.fileno())


def read_audit_log(path: Path, limit: int | None = None) -> list[ConfigAuditEntry]:
    """读取审计日志，跳过损坏行并按时间倒序返回。"""
    if not path.exists():
        return []

    entries: list[ConfigAuditEntry] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(ConfigAuditEntry.from_json(line))
            except ValueError:
                continue

    entries.sort(key=lambda entry: entry.timestamp, reverse=True)
    return entries if limit is None else entries[:limit]


def get_entry_by_id(entry_id: str, path: Path) -> ConfigAuditEntry | None:
    """按唯一 ID 查找一条有效的审计记录。"""
    if not path.exists():
        return None

    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                entry = ConfigAuditEntry.from_json(line)
            except ValueError:
                continue
            if entry.entry_id == entry_id:
                return entry
    return None


def get_changed_buckets(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """返回前后桶覆盖映射中值发生变化的已排序桶名。"""
    all_keys = set(before) | set(after)
    return sorted(key for key in all_keys if before.get(key) != after.get(key))
