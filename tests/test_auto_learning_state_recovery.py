"""自主学习状态文件的完整性、备份与故障恢复契约。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from core.features.learning.infrastructure.auto_learning_state import (
    STATE_SCHEMA_VERSION,
    AutoLearningStatePersistenceError,
    AutoLearningStateStore,
    AutoLearningStateValidationError,
)


def _candidate(candidate_id: str, *, marker: str) -> dict[str, object]:
    """构造只含低敏字段的候选状态。"""

    return {
        "candidate_id": candidate_id,
        "aggregation_revision": f"agg-{marker}",
        "state": "ready_for_review",
    }


def _payload(candidate_id: str, *, marker: str) -> dict[str, object]:
    """构造可持久化的最小状态载荷。"""

    return {
        "candidates": [_candidate(candidate_id, marker=marker)],
        "publications": [],
        "operation_claims": [],
        "recovery_records": [],
        "marker": marker,
    }


def _canonical_json(value: object) -> bytes:
    """按生产格式生成 checksum 使用的规范 JSON。"""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _write_envelope(
    path: Path,
    payload: object,
    *,
    schema_version: int = STATE_SCHEMA_VERSION,
    state_revision: str = "state-revision-0000000001",
    checksum_override: str | None = None,
) -> None:
    """直接写入测试 envelope，以注入磁盘损坏场景。"""

    content = {
        "schema_version": schema_version,
        "state_revision": state_revision,
        "payload": payload,
    }
    checksum = hashlib.sha256(_canonical_json(content)).hexdigest()
    envelope = {**content, "checksum": checksum_override or checksum}
    path.write_bytes(_canonical_json(envelope))


@pytest.mark.asyncio
async def test_save_round_trip_writes_schema_checksum_and_lkg_backup(
    tmp_path: Path,
) -> None:
    """首次保存即发布带 checksum 的主文件和可校验 LKG 备份。"""

    state_path = tmp_path / "auto_learning.json"
    store = AutoLearningStateStore(state_path)
    payload = _payload("CandidateAlphaToken000001", marker="v1")

    revision = await store.save(payload)
    result = await store.load()

    raw = json.loads(state_path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == STATE_SCHEMA_VERSION
    assert raw["state_revision"] == revision
    assert len(raw["checksum"]) == 64
    assert store.backup_path.is_file()
    assert not list(tmp_path.glob("*.tmp"))
    assert result.payload == payload
    assert result.state_revision == revision
    assert result.state_corrupt is False
    assert result.recovery_required is False
    assert result.recovered_from_backup is False


@pytest.mark.asyncio
async def test_malformed_primary_recovers_lkg_but_stays_fail_closed(
    tmp_path: Path,
) -> None:
    """主文件 JSON 损坏时恢复 LKG 证据，但写状态保持关闭。"""

    state_path = tmp_path / "auto_learning.json"
    store = AutoLearningStateStore(state_path)
    first = _payload("CandidateAlphaToken000001", marker="v1")
    second = _payload("CandidateBetaToken0000002", marker="v2")
    first_revision = await store.save(first)
    await store.save(second)
    state_path.write_text("{not-json", encoding="utf-8")

    result = await store.load()

    assert result.payload == first
    assert result.state_revision == first_revision
    assert result.state_corrupt is True
    assert result.recovery_required is True
    assert result.recovered_from_backup is True
    assert result.recovery_revision
    assert result.reason_code == "learning_state_recovered_from_backup"
    assert result.corruption_reason_code == "learning_state_malformed_json"
    assert result.quarantined_path is not None
    assert Path(result.quarantined_path).is_file()


@pytest.mark.asyncio
async def test_partial_primary_write_uses_valid_backup(tmp_path: Path) -> None:
    """截断的 partial write 不得被解释为空状态。"""

    state_path = tmp_path / "auto_learning.json"
    store = AutoLearningStateStore(state_path)
    payload = _payload("CandidateAlphaToken000001", marker="stable")
    await store.save(payload)
    state_path.write_text('{"schema_version":1,"payload":', encoding="utf-8")

    result = await store.load()

    assert result.payload == payload
    assert result.state_corrupt is True
    assert result.recovery_required is True
    assert result.corruption_reason_code == "learning_state_malformed_json"


@pytest.mark.asyncio
async def test_checksum_mismatch_uses_valid_backup(tmp_path: Path) -> None:
    """主文件内容被篡改且 checksum 不匹配时只能回退备份。"""

    state_path = tmp_path / "auto_learning.json"
    store = AutoLearningStateStore(state_path)
    payload = _payload("CandidateAlphaToken000001", marker="stable")
    await store.save(payload)
    _write_envelope(state_path, payload, checksum_override="0" * 64)

    result = await store.load()

    assert result.payload == payload
    assert result.recovered_from_backup is True
    assert result.corruption_reason_code == "learning_state_checksum_mismatch"


@pytest.mark.asyncio
async def test_unknown_schema_uses_valid_backup(tmp_path: Path) -> None:
    """未知 schema 不能按当前结构猜测解释。"""

    state_path = tmp_path / "auto_learning.json"
    store = AutoLearningStateStore(state_path)
    payload = _payload("CandidateAlphaToken000001", marker="stable")
    await store.save(payload)
    _write_envelope(state_path, payload, schema_version=999)

    result = await store.load()

    assert result.payload == payload
    assert result.recovered_from_backup is True
    assert result.corruption_reason_code == "learning_state_schema_unsupported"


@pytest.mark.asyncio
async def test_duplicate_owner_ids_are_rejected_without_rejecting_references(
    tmp_path: Path,
) -> None:
    """同一归属集合拒绝重复 ID，跨集合合法引用不被误伤。"""

    state_path = tmp_path / "auto_learning.json"
    store = AutoLearningStateStore(state_path)
    candidate_id = "CandidateAlphaToken000001"
    duplicate = {
        "candidates": [
            _candidate(candidate_id, marker="one"),
            _candidate(candidate_id, marker="two"),
        ],
        "publications": [],
        "operation_claims": [],
    }

    with pytest.raises(AutoLearningStateValidationError) as exc_info:
        await store.save(duplicate)

    assert exc_info.value.reason_code == "learning_state_duplicate_opaque_id"
    assert not state_path.exists()

    referenced = {
        "candidates": [_candidate(candidate_id, marker="one")],
        "publications": [
            {
                "publication_id": "PublicationAlphaToken0001",
                "candidate_id": candidate_id,
            }
        ],
        "operation_claims": [
            {
                "operation_id": "OperationAlphaToken0000001",
                "candidate_id": candidate_id,
            }
        ],
    }
    await store.save(referenced)
    assert (await store.load()).payload == referenced


@pytest.mark.asyncio
async def test_duplicate_owner_ids_on_disk_trigger_backup_recovery(
    tmp_path: Path,
) -> None:
    """checksum 正确但含重复归属 ID 的主文件仍视为损坏。"""

    state_path = tmp_path / "auto_learning.json"
    store = AutoLearningStateStore(state_path)
    stable = _payload("CandidateAlphaToken000001", marker="stable")
    await store.save(stable)
    duplicate = {
        "candidates": [
            _candidate("CandidateBetaToken0000002", marker="one"),
            _candidate("CandidateBetaToken0000002", marker="two"),
        ],
        "publications": [],
        "operation_claims": [],
    }
    _write_envelope(state_path, duplicate)

    result = await store.load()

    assert result.payload == stable
    assert result.corruption_reason_code == "learning_state_duplicate_opaque_id"
    assert result.recovery_required is True


@pytest.mark.asyncio
async def test_illegal_payload_structure_and_nonfinite_number_are_rejected(
    tmp_path: Path,
) -> None:
    """非法容器与非有限数字不能进入状态文件。"""

    store = AutoLearningStateStore(tmp_path / "auto_learning.json")

    with pytest.raises(AutoLearningStateValidationError) as list_error:
        await store.save(["not", "a", "mapping"])  # type: ignore[arg-type]
    assert list_error.value.reason_code == "learning_state_payload_invalid"

    with pytest.raises(AutoLearningStateValidationError) as number_error:
        await store.save({"candidates": [], "score": float("nan")})
    assert number_error.value.reason_code == "learning_state_value_invalid"


@pytest.mark.asyncio
async def test_primary_and_backup_corruption_returns_no_synthetic_empty_state(
    tmp_path: Path,
) -> None:
    """主备都损坏时返回显式 fail-closed，绝不伪造空载荷。"""

    state_path = tmp_path / "auto_learning.json"
    store = AutoLearningStateStore(state_path)
    state_path.write_text("{broken-primary", encoding="utf-8")
    store.backup_path.write_text("{broken-backup", encoding="utf-8")

    result = await store.load()

    assert result.payload is None
    assert result.state_revision is None
    assert result.state_corrupt is True
    assert result.recovery_required is True
    assert result.recovered_from_backup is False
    assert result.reason_code == "learning_state_corrupt"
    assert result.corruption_reason_code == "learning_state_malformed_json"


@pytest.mark.asyncio
async def test_missing_primary_with_backup_is_recovery_not_fresh_state(
    tmp_path: Path,
) -> None:
    """主文件消失但备份存在时不能被当作首次启动空状态。"""

    state_path = tmp_path / "auto_learning.json"
    store = AutoLearningStateStore(state_path)
    payload = _payload("CandidateAlphaToken000001", marker="stable")
    await store.save(payload)
    state_path.unlink()

    result = await store.load()

    assert result.payload == payload
    assert result.state_corrupt is True
    assert result.recovery_required is True
    assert result.corruption_reason_code == "learning_state_primary_missing"


@pytest.mark.asyncio
async def test_known_legacy_state_requires_explicit_cas_migration(
    tmp_path: Path,
) -> None:
    """已知旧结构只读返回迁移载荷，并通过显式 CAS 发布新 envelope。"""

    state_path = tmp_path / "auto_learning.json"
    store = AutoLearningStateStore(state_path)
    legacy = {
        "candidates": {
            "global:legacy": {
                "candidate_key": "global:legacy",
                "status": "ready_for_review",
            }
        },
        "published": {},
        "publish_intents": {},
    }
    state_path.write_text(json.dumps(legacy), encoding="utf-8")

    migration = await store.load()

    assert migration.payload == legacy
    assert migration.reason_code == "learning_state_migration_required"
    assert migration.migration_required is True
    assert migration.migration_revision
    assert migration.recovery_required is True
    assert migration.state_corrupt is False

    migrated = _payload("CandidateAlphaToken000001", marker="migrated")
    new_revision = await store.migrate_legacy(
        migrated,
        expected_legacy_revision=migration.migration_revision,
    )
    loaded = await store.load()

    assert loaded.payload == migrated
    assert loaded.state_revision == new_revision
    assert loaded.migration_required is False
    assert loaded.recovery_required is False
    assert store.backup_path.is_file()


@pytest.mark.asyncio
async def test_legacy_migration_conflict_preserves_latest_legacy_file(
    tmp_path: Path,
) -> None:
    """旧文件在读取后变化时，迁移 CAS 必须拒绝覆盖最新内容。"""

    state_path = tmp_path / "auto_learning.json"
    store = AutoLearningStateStore(state_path)
    legacy = {"candidates": {}, "published": {}, "publish_intents": {}}
    state_path.write_text(json.dumps(legacy), encoding="utf-8")
    migration = await store.load()
    latest = {
        "candidates": {},
        "published": {},
        "publish_intents": {"latest": {"phase": "prepared"}},
    }
    state_path.write_text(json.dumps(latest), encoding="utf-8")

    with pytest.raises(AutoLearningStatePersistenceError) as exc_info:
        await store.migrate_legacy(
            _payload("CandidateAlphaToken000001", marker="migrated"),
            expected_legacy_revision=migration.migration_revision or "",
        )

    assert exc_info.value.reason_code == "learning_state_migration_conflict"
    assert json.loads(state_path.read_text(encoding="utf-8")) == latest
    assert not store.backup_path.exists()


@pytest.mark.asyncio
async def test_backup_write_failure_keeps_existing_primary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LKG 无法发布时不得覆盖仍有效的主状态。"""

    state_path = tmp_path / "auto_learning.json"
    store = AutoLearningStateStore(state_path)
    original = _payload("CandidateAlphaToken000001", marker="stable")
    await store.save(original)
    original_bytes = state_path.read_bytes()
    real_atomic_write = store._atomic_write

    def fail_backup(path: Path, data: bytes) -> None:
        """仅在更新 LKG 时注入持久化失败。"""

        if path == store.backup_path:
            raise OSError("simulated")
        real_atomic_write(path, data)

    monkeypatch.setattr(store, "_atomic_write", fail_backup)
    updated = _payload("CandidateBetaToken0000002", marker="new")

    with pytest.raises(AutoLearningStatePersistenceError) as exc_info:
        await store.save(updated)

    assert exc_info.value.reason_code == "learning_state_write_failed"
    assert state_path.read_bytes() == original_bytes


@pytest.mark.asyncio
async def test_cancelled_save_propagates_without_error_wrapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """协程取消必须原样传播，不能伪装成普通写入失败。"""

    store = AutoLearningStateStore(tmp_path / "auto_learning.json")

    def cancel_write(_: bytes) -> None:
        """在同步工作线程入口注入取消。"""

        raise asyncio.CancelledError

    monkeypatch.setattr(store, "_save_sync", cancel_write)

    with pytest.raises(asyncio.CancelledError):
        await store.save(_payload("CandidateAlphaToken000001", marker="cancelled"))


@pytest.mark.asyncio
async def test_fresh_missing_state_is_explicitly_reported(tmp_path: Path) -> None:
    """主备均不存在时返回明确 missing，而不是 corruption。"""

    store = AutoLearningStateStore(tmp_path / "auto_learning.json")

    result = await store.load()

    assert result.payload is None
    assert result.reason_code == "learning_state_missing"
    assert result.state_corrupt is False
    assert result.recovery_required is False
