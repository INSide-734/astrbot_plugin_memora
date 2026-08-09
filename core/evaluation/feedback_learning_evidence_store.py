"""自主学习匿名评测 artifact 的私有 inbox 与生产读取 provider。"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import secrets
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from ..features.learning.domain.feedback_learning_evidence import (
    LearningEvidenceArtifact,
    artifact_from_record,
    artifact_to_record,
    validate_learning_evidence,
)
from ..features.learning.domain.feedback_learning_evidence_contract import (
    SUPPORTED_EVIDENCE_QUALITY_GATES,
    valid_evidence_binding,
)

_SCHEMA_VERSION = 1
_INBOX_PARTS = ("evaluation", "feedback_learning_evidence")
_CURRENT_FILE = "current.json"
_MAX_ARTIFACT_BYTES = 1024 * 1024
_MAX_POINTER_BYTES = 4096
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_DOCUMENT_FIELDS = frozenset(
    {"schema_version", "artifact_checksum", "artifact"}
)
_POINTER_FIELDS = frozenset(
    {
        "schema_version",
        "evidence_revision",
        "aggregation_revision",
        "source_config_revision",
        "quality_gate_version",
        "checksum",
    }
)
_INTEGRITY_REASONS = frozenset(
    {
        "evidence_revision_mismatch",
        "invalid_artifact_structure",
        "invalid_regression_failure",
        "passed_mismatch",
        "unsupported_evaluator_version",
    }
)


class LearningEvidenceInboxError(RuntimeError):
    """表示 artifact 无法安全发布到私有 inbox。"""


class FeedbackLearningEvidenceInbox:
    """保存不可变 artifact，并通过单一原子指针选择当前证据。"""

    def __init__(
        self,
        data_dir: str | os.PathLike[str],
        *,
        max_artifact_bytes: int = _MAX_ARTIFACT_BYTES,
        max_pointer_bytes: int = _MAX_POINTER_BYTES,
    ) -> None:
        """初始化固定私有目录和读取大小上限。

        参数:
            data_dir: 插件受控数据目录，而不是客户端提供的任意路径。
            max_artifact_bytes: 单个 artifact envelope 的最大字节数。
            max_pointer_bytes: 当前指针文档的最大字节数。
        """

        self._directory = Path(data_dir).joinpath(*_INBOX_PARTS)
        self._max_artifact_bytes = max(1, int(max_artifact_bytes))
        self._max_pointer_bytes = max(1, int(max_pointer_bytes))

    @property
    def directory(self) -> Path:
        """返回固定 inbox 目录，供受控离线评测器定位输出。"""

        return self._directory

    @property
    def current_path(self) -> Path:
        """返回当前 artifact 指针的固定路径。"""

        return self._directory / _CURRENT_FILE

    def artifact_path(self, evidence_revision: str) -> Path:
        """按已验证 SHA-256 revision 构造不可穿越的 artifact 路径。"""

        if not _is_sha256(evidence_revision):
            raise ValueError("learning_evidence_revision_invalid")
        return self._directory / f"{evidence_revision}.json"

    async def publish(self, artifact: LearningEvidenceArtifact) -> str:
        """原子发布匿名 artifact，并切换当前指针。

        相同 revision 的文件只能幂等复用；若已有字节不同则拒绝覆盖。
        本地提交一旦开始会先完成或失败，再传播调用方取消，避免留下未知
        的半提交状态。返回已发布的 evidence revision。
        """

        commit = asyncio.create_task(asyncio.to_thread(self._publish_sync, artifact))
        cancelled = False
        commit_error: Exception | None = None
        while not commit.done():
            try:
                await asyncio.shield(commit)
            except asyncio.CancelledError:
                cancelled = True
            except Exception as exc:
                commit_error = exc
                break
        if cancelled:
            try:
                commit.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            raise asyncio.CancelledError
        if commit_error is not None:
            raise commit_error
        return commit.result()

    async def load_current(
        self,
        *,
        aggregation_revision: str,
        source_config_revision: str,
        quality_gate_version: str,
    ) -> LearningEvidenceArtifact | None:
        """读取与当前候选、配置和 Gate 完全匹配的 artifact。

        缺失、损坏、过大、绑定漂移或 revision 篡改均 fail-closed 返回
        ``None``；调用方据此生成不可发布候选。
        """

        return await asyncio.to_thread(
            self._load_current_sync,
            aggregation_revision,
            source_config_revision,
            quality_gate_version,
        )

    def _publish_sync(self, artifact: LearningEvidenceArtifact) -> str:
        """在线程中执行校验、不可变写入和指针原子替换。"""

        artifact_document = _artifact_document(artifact)
        artifact_bytes = _canonical_bytes(artifact_document)
        if len(artifact_bytes) > self._max_artifact_bytes:
            raise LearningEvidenceInboxError("learning_evidence_artifact_too_large")

        pointer_document = _pointer_document(artifact)
        pointer_bytes = _canonical_bytes(pointer_document)
        if len(pointer_bytes) > self._max_pointer_bytes:
            raise LearningEvidenceInboxError("learning_evidence_pointer_too_large")

        _ensure_private_directory(self._directory)
        _write_immutable(
            self.artifact_path(artifact.evidence_revision),
            artifact_bytes,
        )
        _atomic_replace(self.current_path, pointer_bytes)
        return artifact.evidence_revision

    def _load_current_sync(
        self,
        aggregation_revision: str,
        source_config_revision: str,
        quality_gate_version: str,
    ) -> LearningEvidenceArtifact | None:
        """在线程中严格读取指针和不可变 artifact。"""

        if not valid_evidence_binding(
            aggregation_revision,
            source_config_revision,
            quality_gate_version,
        ):
            return None
        pointer = _read_document(self.current_path, self._max_pointer_bytes)
        if not _valid_pointer(pointer):
            return None
        if (
            pointer["aggregation_revision"] != aggregation_revision
            or pointer["source_config_revision"] != source_config_revision
            or pointer["quality_gate_version"] != quality_gate_version
        ):
            return None

        evidence_revision = pointer["evidence_revision"]
        try:
            path = self.artifact_path(evidence_revision)
        except ValueError:
            return None
        document = _read_document(path, self._max_artifact_bytes)
        artifact = _artifact_from_document(document)
        if artifact is None or artifact.evidence_revision != evidence_revision:
            return None
        if (
            artifact.aggregation_revision != aggregation_revision
            or artifact.source_config_revision != source_config_revision
            or artifact.quality_gate_version != quality_gate_version
        ):
            return None
        return artifact


class FeedbackLearningEvidenceProvider:
    """把当前聚合与权威配置 revision 映射到私有 inbox artifact。"""

    def __init__(
        self,
        inbox: FeedbackLearningEvidenceInbox,
        *,
        aggregation_revision_provider: Callable[[Sequence[object]], str],
        source_config_revision_provider: Callable[[], str | Awaitable[str]],
        quality_gate_version: str,
    ) -> None:
        """保存受信 revision 回调和固定 Gate 版本。"""

        if quality_gate_version not in SUPPORTED_EVIDENCE_QUALITY_GATES:
            raise ValueError("learning_quality_gate_version_invalid")
        self._inbox = inbox
        self._aggregation_revision_provider = aggregation_revision_provider
        self._source_config_revision_provider = source_config_revision_provider
        self._quality_gate_version = quality_gate_version

    async def __call__(
        self,
        aggregates: Sequence[object],
    ) -> LearningEvidenceArtifact | None:
        """按真实聚合和当前 ConfigManager revision 读取 artifact。"""

        aggregation_revision = self._aggregation_revision_provider(aggregates)
        source_config_revision = await self._current_config_revision()
        if not valid_evidence_binding(
            aggregation_revision,
            source_config_revision,
            self._quality_gate_version,
        ):
            return None
        artifact = await self._inbox.load_current(
            aggregation_revision=aggregation_revision,
            source_config_revision=source_config_revision,
            quality_gate_version=self._quality_gate_version,
        )
        confirmed_revision = await self._current_config_revision()
        if confirmed_revision != source_config_revision:
            return None
        return artifact

    async def validate_current(self, artifact: LearningEvidenceArtifact) -> bool:
        """确认给定 artifact 仍是当前指针和权威配置共同选择的证据。"""

        if not isinstance(artifact, LearningEvidenceArtifact):
            return False
        source_config_revision = await self._current_config_revision()
        if source_config_revision != artifact.source_config_revision:
            return False
        current = await self._inbox.load_current(
            aggregation_revision=artifact.aggregation_revision,
            source_config_revision=artifact.source_config_revision,
            quality_gate_version=artifact.quality_gate_version,
        )
        confirmed_revision = await self._current_config_revision()
        return (
            confirmed_revision == source_config_revision
            and current is not None
            and current == artifact
        )

    async def _current_config_revision(self) -> object:
        """读取一次当前 ConfigManager revision，并保持取消语义。"""

        revision_value = self._source_config_revision_provider()
        return (
            await revision_value
            if inspect.isawaitable(revision_value)
            else revision_value
        )


def _artifact_document(artifact: LearningEvidenceArtifact) -> dict[str, Any]:
    """构造带独立 checksum 的严格 artifact envelope。"""

    if not isinstance(artifact, LearningEvidenceArtifact) or not _is_sha256(
        artifact.evidence_revision
    ):
        raise LearningEvidenceInboxError("learning_evidence_artifact_invalid")
    try:
        record = artifact_to_record(artifact)
        restored = artifact_from_record(record)
    except (TypeError, ValueError) as exc:
        raise LearningEvidenceInboxError("learning_evidence_artifact_invalid") from exc
    if restored != artifact or not _artifact_integrity_valid(artifact):
        raise LearningEvidenceInboxError("learning_evidence_artifact_invalid")
    return {
        "schema_version": _SCHEMA_VERSION,
        "artifact_checksum": _checksum(record),
        "artifact": record,
    }


def _pointer_document(artifact: LearningEvidenceArtifact) -> dict[str, Any]:
    """构造只含 revision 绑定的低敏当前指针。"""

    payload = {
        "schema_version": _SCHEMA_VERSION,
        "evidence_revision": artifact.evidence_revision,
        "aggregation_revision": artifact.aggregation_revision,
        "source_config_revision": artifact.source_config_revision,
        "quality_gate_version": artifact.quality_gate_version,
    }
    return {**payload, "checksum": _checksum(payload)}


def _artifact_from_document(document: object) -> LearningEvidenceArtifact | None:
    """从严格 envelope 恢复并复核 artifact 自身完整性。"""

    if not isinstance(document, Mapping) or set(document) != _ARTIFACT_DOCUMENT_FIELDS:
        return None
    if document.get("schema_version") != _SCHEMA_VERSION:
        return None
    record = document.get("artifact")
    if not isinstance(record, Mapping):
        return None
    if document.get("artifact_checksum") != _checksum(record):
        return None
    artifact = artifact_from_record(record)
    if artifact is None or not _artifact_integrity_valid(artifact):
        return None
    return artifact


def _artifact_integrity_valid(artifact: LearningEvidenceArtifact) -> bool:
    """验证 artifact revision 与 evaluator 计算的 passed 标志未被篡改。"""

    try:
        result = validate_learning_evidence(
            artifact,
            aggregation_revision=artifact.aggregation_revision,
            source_config_revision=artifact.source_config_revision,
            quality_gate_version=artifact.quality_gate_version,
        )
    except (AttributeError, TypeError, ValueError):
        return False
    return not (_INTEGRITY_REASONS & set(result.reason_codes))


def _valid_pointer(document: object) -> bool:
    """验证当前指针 schema、checksum 与全部低敏绑定。"""

    if not isinstance(document, Mapping) or set(document) != _POINTER_FIELDS:
        return False
    if document.get("schema_version") != _SCHEMA_VERSION:
        return False
    payload = {key: document[key] for key in _POINTER_FIELDS if key != "checksum"}
    if document.get("checksum") != _checksum(payload):
        return False
    return _is_sha256(document.get("evidence_revision")) and valid_evidence_binding(
        document.get("aggregation_revision"),
        document.get("source_config_revision"),
        document.get("quality_gate_version"),
    )


def _read_document(path: Path, max_bytes: int) -> object | None:
    """在大小上限内读取拒绝重复键和非有限数值的 JSON 文档。"""

    try:
        if not path.is_file() or path.stat().st_size > max_bytes:
            return None
        raw = path.read_bytes()
        if len(raw) > max_bytes:
            return None
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_nonfinite_constant,
        )
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _write_immutable(path: Path, payload: bytes) -> None:
    """写入新 revision；已有文件仅允许字节完全相同的幂等复用。"""

    if path.exists():
        if _read_bytes(path, len(payload)) == payload:
            return
        raise LearningEvidenceInboxError("learning_evidence_artifact_collision")
    temp_path = _write_private_temp(path, payload)
    try:
        os.link(temp_path, path)
        _fsync_directory(path.parent)
    except FileExistsError:
        if _read_bytes(path, len(payload)) != payload:
            raise LearningEvidenceInboxError("learning_evidence_artifact_collision")
    except OSError as exc:
        raise LearningEvidenceInboxError(
            "learning_evidence_persistence_failed"
        ) from exc
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
    if _read_bytes(path, len(payload)) != payload:
        raise LearningEvidenceInboxError("learning_evidence_artifact_collision")


def _atomic_replace(path: Path, payload: bytes) -> None:
    """以私有临时文件、flush/fsync 和同目录 replace 原子发布文档。"""

    temp_path = _write_private_temp(path, payload)
    try:
        os.replace(temp_path, path)
        _fsync_directory(path.parent)
    except OSError as exc:
        raise LearningEvidenceInboxError(
            "learning_evidence_persistence_failed"
        ) from exc
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _write_private_temp(path: Path, payload: bytes) -> Path:
    """在目标目录写入并同步一个仅当前用户可读的临时文件。"""

    temp_path = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    try:
        descriptor = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return temp_path
    except OSError as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise LearningEvidenceInboxError(
            "learning_evidence_persistence_failed"
        ) from exc


def _ensure_private_directory(path: Path) -> None:
    """创建插件私有目录，并在平台支持时收紧目录权限。"""

    try:
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    except OSError as exc:
        raise LearningEvidenceInboxError(
            "learning_evidence_persistence_failed"
        ) from exc


def _fsync_directory(path: Path) -> None:
    """在平台支持时同步目录项；Windows 不支持时安全跳过。"""

    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _read_bytes(path: Path, expected_size: int) -> bytes | None:
    """读取既有 artifact 字节，并拒绝异常大小。"""

    try:
        if path.stat().st_size != expected_size:
            return None
        return path.read_bytes()
    except OSError:
        return None


def _checksum(value: Mapping[str, Any]) -> str:
    """计算严格 canonical JSON 的 SHA-256。"""

    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    """把 JSON mapping 编码为稳定、无 NaN 的 UTF-8 字节。"""

    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """拒绝 JSON 对象内重复键，避免校验语义歧义。"""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("learning_evidence_duplicate_key")
        result[key] = value
    return result


def _reject_nonfinite_constant(_value: str) -> None:
    """拒绝 JSON 中的 NaN 和 Infinity 扩展常量。"""

    raise ValueError("learning_evidence_nonfinite_number")


def _is_sha256(value: object) -> bool:
    """判断值是否为小写十六进制 SHA-256。"""

    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


__all__ = [
    "FeedbackLearningEvidenceInbox",
    "FeedbackLearningEvidenceProvider",
    "LearningEvidenceInboxError",
]
