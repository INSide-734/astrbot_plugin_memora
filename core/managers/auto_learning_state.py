"""自主学习状态文件的完整性校验、原子持久化与只读恢复。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STATE_SCHEMA_VERSION = 1

_MAX_STATE_BYTES = 8 * 1024 * 1024
_MAX_JSON_DEPTH = 32
_CHECKSUM_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{22,128}$")
_ENVELOPE_KEYS = frozenset({"schema_version", "state_revision", "payload", "checksum"})
_OPAQUE_ID_FIELDS = frozenset(
    {
        "candidate_id",
        "publication_id",
        "operation_id",
        "recovery_revision",
        "tombstone_id",
    }
)
_OWNER_COLLECTIONS: dict[str, tuple[str, ...]] = {
    "candidates": ("candidate_id",),
    "publications": ("publication_id", "publication_revision"),
    "operation_claims": ("operation_id",),
    "tombstones": ("tombstone_id", "operation_id"),
    "recovery_records": ("recovery_revision", "operation_id"),
}


class AutoLearningStateError(RuntimeError):
    """自主学习状态模块的稳定 reason-code 异常基类。"""

    def __init__(self, reason_code: str) -> None:
        """保存低敏稳定错误码，不暴露原始文件异常。"""

        super().__init__(reason_code)
        self.reason_code = reason_code


class AutoLearningStateValidationError(AutoLearningStateError):
    """状态 envelope、checksum 或载荷结构不符合契约。"""


class AutoLearningStatePersistenceError(AutoLearningStateError):
    """状态文件或 LKG 备份无法可靠持久化。"""


@dataclass(frozen=True, slots=True)
class AutoLearningStateLoadResult:
    """状态加载结果及其 fail-closed 恢复标志。"""

    payload: dict[str, Any] | None
    state_revision: str | None
    state_corrupt: bool
    recovery_required: bool
    recovered_from_backup: bool
    recovery_revision: str | None
    reason_code: str
    corruption_reason_code: str | None = None
    quarantined_path: str | None = None
    migration_required: bool = False
    migration_revision: str | None = None


class AutoLearningStateStore:
    """通过带 checksum 的 envelope 原子保存自主学习运行状态。"""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        """绑定主状态路径，并在同目录派生 LKG 与临时文件路径。"""

        self._path = Path(path)
        self._backup_path = self._path.with_name(f"{self._path.name}.lkg")

    @property
    def path(self) -> Path:
        """返回主状态文件路径。"""

        return self._path

    @property
    def backup_path(self) -> Path:
        """返回 last-known-good 备份路径。"""

        return self._backup_path

    async def save(
        self,
        payload: Mapping[str, Any],
        *,
        state_revision: str | None = None,
    ) -> str:
        """校验并原子保存载荷，返回实际写入的状态 revision。

        LKG 必须先于新主文件落盘。已有主文件损坏或仅剩备份时，本方法拒绝
        覆盖恢复证据。协程取消会原样传播，调用方必须按未知提交状态处理。
        """

        if not isinstance(payload, Mapping):
            raise AutoLearningStateValidationError("learning_state_payload_invalid")
        revision = state_revision or secrets.token_urlsafe(24)
        _validate_opaque_id(revision)
        normalized_payload = dict(payload)
        _validate_payload(normalized_payload)
        envelope_bytes = _encode_envelope(normalized_payload, revision)
        try:
            await asyncio.to_thread(self._save_sync, envelope_bytes)
        except asyncio.CancelledError:
            raise
        except AutoLearningStateError:
            raise
        except Exception as exc:
            raise AutoLearningStatePersistenceError(
                "learning_state_write_failed"
            ) from exc
        return revision

    async def load(self) -> AutoLearningStateLoadResult:
        """加载并校验主状态，损坏时隔离主文件并只读回退 LKG。

        只要主文件曾损坏、缺失但备份存在，结果就保持
        ``state_corrupt`` 与 ``recovery_required``，由上层禁止所有写动作。
        """

        try:
            return await asyncio.to_thread(self._load_sync)
        except asyncio.CancelledError:
            raise

    async def migrate_legacy(
        self,
        payload: Mapping[str, Any],
        *,
        expected_legacy_revision: str,
        state_revision: str | None = None,
    ) -> str:
        """以旧文件内容 revision 做 CAS，原子发布迁移后的新 envelope。

        本方法不生成业务 ID；调用方必须在 manager 状态锁内完成旧记录归一化和
        opaque ID 分配。旧文件已变化时拒绝覆盖，取消按未知提交状态原样传播。
        """

        if not isinstance(payload, Mapping):
            raise AutoLearningStateValidationError("learning_state_payload_invalid")
        _validate_opaque_id(expected_legacy_revision)
        revision = state_revision or secrets.token_urlsafe(24)
        _validate_opaque_id(revision)
        normalized_payload = dict(payload)
        _validate_payload(normalized_payload)
        envelope_bytes = _encode_envelope(normalized_payload, revision)
        try:
            await asyncio.to_thread(
                self._migrate_legacy_sync,
                envelope_bytes,
                expected_legacy_revision,
            )
        except asyncio.CancelledError:
            raise
        except AutoLearningStateError:
            raise
        except Exception as exc:
            raise AutoLearningStatePersistenceError(
                "learning_state_write_failed"
            ) from exc
        return revision

    def _save_sync(self, envelope_bytes: bytes) -> None:
        """同步执行 LKG 优先的双文件原子写入。"""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists() and self._backup_path.exists():
            raise AutoLearningStatePersistenceError("learning_state_recovery_required")

        backup_bytes = envelope_bytes
        if self._path.exists():
            _, _, backup_bytes = _read_verified_envelope(self._path)

        self._atomic_write(self._backup_path, backup_bytes)
        self._atomic_write(self._path, envelope_bytes)

    def _load_sync(self) -> AutoLearningStateLoadResult:
        """同步加载主状态，并将所有故障收敛为有限恢复结果。"""

        if not self._path.exists():
            if not self._backup_path.exists():
                return AutoLearningStateLoadResult(
                    payload=None,
                    state_revision=None,
                    state_corrupt=False,
                    recovery_required=False,
                    recovered_from_backup=False,
                    recovery_revision=None,
                    reason_code="learning_state_missing",
                )
            return self._recover_from_backup(
                corruption_reason_code="learning_state_primary_missing",
                quarantined_path=None,
            )

        try:
            payload, revision, _ = _read_verified_envelope(self._path)
        except AutoLearningStateValidationError as exc:
            if exc.reason_code == "learning_state_envelope_invalid":
                try:
                    legacy_payload, migration_revision = _read_legacy_state(self._path)
                except (AutoLearningStateValidationError, OSError):
                    pass
                else:
                    return AutoLearningStateLoadResult(
                        payload=legacy_payload,
                        state_revision=None,
                        state_corrupt=False,
                        recovery_required=True,
                        recovered_from_backup=False,
                        recovery_revision=None,
                        reason_code="learning_state_migration_required",
                        migration_required=True,
                        migration_revision=migration_revision,
                    )
            quarantined_path = self._quarantine_primary()
            return self._recover_from_backup(
                corruption_reason_code=exc.reason_code,
                quarantined_path=quarantined_path,
            )
        except OSError:
            quarantined_path = self._quarantine_primary()
            return self._recover_from_backup(
                corruption_reason_code="learning_state_read_failed",
                quarantined_path=quarantined_path,
            )

        return AutoLearningStateLoadResult(
            payload=payload,
            state_revision=revision,
            state_corrupt=False,
            recovery_required=False,
            recovered_from_backup=False,
            recovery_revision=None,
            reason_code="learning_state_loaded",
        )

    def _migrate_legacy_sync(
        self,
        envelope_bytes: bytes,
        expected_legacy_revision: str,
    ) -> None:
        """校验旧文件未变化后，先发布新 LKG 再替换旧主文件。"""

        try:
            _, current_revision = _read_legacy_state(self._path)
        except (AutoLearningStateValidationError, OSError) as exc:
            raise AutoLearningStatePersistenceError(
                "learning_state_migration_conflict"
            ) from exc
        if not hmac.compare_digest(current_revision, expected_legacy_revision):
            raise AutoLearningStatePersistenceError("learning_state_migration_conflict")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(self._backup_path, envelope_bytes)
        self._atomic_write(self._path, envelope_bytes)

    def _recover_from_backup(
        self,
        *,
        corruption_reason_code: str,
        quarantined_path: str | None,
    ) -> AutoLearningStateLoadResult:
        """尝试加载校验通过的 LKG，并始终保留恢复要求。"""

        try:
            payload, revision, _ = _read_verified_envelope(self._backup_path)
        except (AutoLearningStateValidationError, OSError):
            return AutoLearningStateLoadResult(
                payload=None,
                state_revision=None,
                state_corrupt=True,
                recovery_required=True,
                recovered_from_backup=False,
                recovery_revision=None,
                reason_code="learning_state_corrupt",
                corruption_reason_code=corruption_reason_code,
                quarantined_path=quarantined_path,
            )

        return AutoLearningStateLoadResult(
            payload=payload,
            state_revision=revision,
            state_corrupt=True,
            recovery_required=True,
            recovered_from_backup=True,
            recovery_revision=_derive_recovery_revision(revision),
            reason_code="learning_state_recovered_from_backup",
            corruption_reason_code=corruption_reason_code,
            quarantined_path=quarantined_path,
        )

    def _quarantine_primary(self) -> str | None:
        """把普通损坏主文件移到同目录隔离路径，失败时返回空。"""

        try:
            if not self._path.is_file() or self._path.is_symlink():
                return None
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            suffix = secrets.token_hex(4)
            quarantine_path = self._path.with_name(
                f"{self._path.name}.corrupt-{timestamp}-{suffix}"
            )
            os.replace(self._path, quarantine_path)
            _fsync_directory(self._path.parent)
            return str(quarantine_path)
        except OSError:
            return None

    def _atomic_write(self, path: Path, data: bytes) -> None:
        """通过同目录临时文件、flush/fsync 与 replace 原子发布字节。"""

        temp_path = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
        try:
            with temp_path.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
            _fsync_directory(path.parent)
        finally:
            try:
                temp_path.unlink()
            except OSError:
                pass


def _encode_envelope(payload: dict[str, Any], state_revision: str) -> bytes:
    """生成包含 schema、revision 与 SHA-256 checksum 的规范 envelope。"""

    content = {
        "schema_version": STATE_SCHEMA_VERSION,
        "state_revision": state_revision,
        "payload": payload,
    }
    checksum = hashlib.sha256(_canonical_json(content)).hexdigest()
    return _canonical_json({**content, "checksum": checksum})


def _read_verified_envelope(path: Path) -> tuple[dict[str, Any], str, bytes]:
    """读取单个 envelope，验证 schema、checksum、结构与所有者 ID。"""

    envelope, raw = _read_json_document(path)

    if not isinstance(envelope, dict) or set(envelope) != _ENVELOPE_KEYS:
        raise AutoLearningStateValidationError("learning_state_envelope_invalid")
    schema_version = envelope["schema_version"]
    if type(schema_version) is not int or schema_version != STATE_SCHEMA_VERSION:
        raise AutoLearningStateValidationError("learning_state_schema_unsupported")
    state_revision = envelope["state_revision"]
    if not isinstance(state_revision, str):
        raise AutoLearningStateValidationError("learning_state_revision_invalid")
    try:
        _validate_opaque_id(state_revision)
    except AutoLearningStateValidationError as exc:
        raise AutoLearningStateValidationError(
            "learning_state_revision_invalid"
        ) from exc

    checksum = envelope["checksum"]
    if not isinstance(checksum, str) or not _CHECKSUM_PATTERN.fullmatch(checksum):
        raise AutoLearningStateValidationError("learning_state_checksum_invalid")
    content = {
        "schema_version": schema_version,
        "state_revision": state_revision,
        "payload": envelope["payload"],
    }
    expected_checksum = hashlib.sha256(_canonical_json(content)).hexdigest()
    if not hmac.compare_digest(checksum, expected_checksum):
        raise AutoLearningStateValidationError("learning_state_checksum_mismatch")

    payload = envelope["payload"]
    if not isinstance(payload, dict):
        raise AutoLearningStateValidationError("learning_state_payload_invalid")
    _validate_payload(payload)
    return payload, state_revision, raw


def _read_legacy_state(path: Path) -> tuple[dict[str, Any], str]:
    """读取严格旧结构，并返回供显式迁移 CAS 使用的内容 revision。"""

    value, raw = _read_json_document(path)
    if not isinstance(value, dict):
        raise AutoLearningStateValidationError("learning_state_legacy_invalid")
    keys = set(value)
    required = {"candidates", "published"}
    allowed = required | {"publish_intents"}
    if not required.issubset(keys) or not keys.issubset(allowed):
        raise AutoLearningStateValidationError("learning_state_legacy_invalid")
    if not all(isinstance(value[key], dict) for key in keys):
        raise AutoLearningStateValidationError("learning_state_legacy_invalid")
    _validate_json_value(value, depth=0)
    digest = hashlib.sha256(b"auto-learning-legacy:" + raw).digest()
    revision = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return value, revision


def _read_json_document(path: Path) -> tuple[object, bytes]:
    """读取有限大小的普通文件，并严格解析 UTF-8 JSON。"""

    if path.is_symlink() or not path.is_file():
        raise AutoLearningStateValidationError("learning_state_file_invalid")
    raw = path.read_bytes()
    if not raw or len(raw) > _MAX_STATE_BYTES:
        raise AutoLearningStateValidationError("learning_state_file_invalid")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_constant,
        )
    except AutoLearningStateValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AutoLearningStateValidationError("learning_state_malformed_json") from exc
    return value, raw


def _canonical_json(value: object) -> bytes:
    """生成稳定、拒绝非有限数字的 UTF-8 JSON 字节。"""

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AutoLearningStateValidationError("learning_state_value_invalid") from exc
    if len(encoded) > _MAX_STATE_BYTES:
        raise AutoLearningStateValidationError("learning_state_file_too_large")
    return encoded


def _validate_payload(payload: dict[str, Any]) -> None:
    """验证 JSON 值、opaque ID 格式及记录归属集合内的唯一性。"""

    _validate_json_value(payload, depth=0)
    _validate_owner_collections(payload)


def _validate_json_value(value: object, *, depth: int) -> None:
    """递归拒绝非 JSON 类型、过深结构、非有限数字与非法 opaque ID。"""

    if depth > _MAX_JSON_DEPTH:
        raise AutoLearningStateValidationError("learning_state_value_invalid")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AutoLearningStateValidationError("learning_state_value_invalid")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise AutoLearningStateValidationError("learning_state_value_invalid")
            if key in _OPAQUE_ID_FIELDS and item is not None:
                if not isinstance(item, str):
                    raise AutoLearningStateValidationError(
                        "learning_state_opaque_id_invalid"
                    )
                _validate_opaque_id(item)
            _validate_json_value(item, depth=depth + 1)
        return
    raise AutoLearningStateValidationError("learning_state_value_invalid")


def _validate_owner_collections(payload: dict[str, Any]) -> None:
    """仅在各记录归属集合内部检查主 ID，允许跨集合合法引用。"""

    for collection_name, identity_fields in _OWNER_COLLECTIONS.items():
        if collection_name not in payload:
            continue
        records = _collection_records(payload[collection_name])
        seen: set[str] = set()
        for storage_key, record in records:
            identity, identity_field = _owner_identity(
                record, identity_fields, storage_key
            )
            if identity_field in _OPAQUE_ID_FIELDS:
                _validate_opaque_id(identity)
            if identity in seen:
                raise AutoLearningStateValidationError(
                    "learning_state_duplicate_opaque_id"
                )
            seen.add(identity)


def _collection_records(
    value: object,
) -> list[tuple[str | None, dict[str, Any]]]:
    """把列表或映射形式的归属集合标准化为记录序列。"""

    if isinstance(value, list):
        if not all(isinstance(item, dict) for item in value):
            raise AutoLearningStateValidationError("learning_state_payload_invalid")
        return [(None, item) for item in value]
    if isinstance(value, dict):
        records: list[tuple[str | None, dict[str, Any]]] = []
        for key, item in value.items():
            if not isinstance(key, str) or not isinstance(item, dict):
                raise AutoLearningStateValidationError("learning_state_payload_invalid")
            records.append((key, item))
        return records
    raise AutoLearningStateValidationError("learning_state_payload_invalid")


def _owner_identity(
    record: dict[str, Any],
    identity_fields: Sequence[str],
    storage_key: str | None,
) -> tuple[str, str]:
    """提取记录自身主 ID；映射键只作为显式 ID 缺失时的兼容表示。"""

    for field in identity_fields:
        value = record.get(field)
        if isinstance(value, str) and value:
            return value, field
    if storage_key:
        return storage_key, identity_fields[0]
    raise AutoLearningStateValidationError("learning_state_owner_id_missing")


def _validate_opaque_id(value: str) -> None:
    """验证长度 22 至 128 的 ASCII URL-safe opaque ID。"""

    if not _OPAQUE_ID_PATTERN.fullmatch(value):
        raise AutoLearningStateValidationError("learning_state_opaque_id_invalid")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """在 JSON 解码时拒绝会被标准字典静默覆盖的重复键。"""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AutoLearningStateValidationError("learning_state_duplicate_json_key")
        result[key] = value
    return result


def _reject_nonfinite_constant(value: str) -> None:
    """拒绝 JSON 扩展常量 NaN 与 Infinity。"""

    raise AutoLearningStateValidationError("learning_state_value_invalid")


def _derive_recovery_revision(state_revision: str) -> str:
    """从已校验 LKG revision 派生稳定且低敏的恢复 revision。"""

    digest = hashlib.sha256(
        f"auto-learning-lkg-recovery:{state_revision}".encode("ascii")
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _fsync_directory(path: Path) -> None:
    """在支持目录句柄的平台同步 replace 元数据，Windows 不支持时安全跳过。"""

    flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
