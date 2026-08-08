"""验证重构前后 runtime ZIP 在同一数据目录上的可回退性。"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import zipfile
import zlib
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from _refactor_rollback_data import (
        build_derived_rebuild_evidence,
        inspect_schema_contract,
    )
    from _refactor_rollback_runtime import exercise_runtime_update_rollback
    from verify_plugin_lifecycle import (
        LifecycleVerificationError,
        atomic_write_json,
        prepare_empty_data_root,
        run_worker_subprocess,
    )
except ModuleNotFoundError:  # 允许 pytest 以 scripts namespace 导入。
    from scripts._refactor_rollback_data import (
        build_derived_rebuild_evidence,
        inspect_schema_contract,
    )
    from scripts._refactor_rollback_runtime import exercise_runtime_update_rollback
    from scripts.verify_plugin_lifecycle import (
        LifecycleVerificationError,
        atomic_write_json,
        prepare_empty_data_root,
        run_worker_subprocess,
    )

REPORT_SCHEMA = "memora-refactor-rollback-report-v1"
FIXTURE_SCHEMA = "memora-refactor-rollback-fixture-v1"
PLUGIN_DIR_NAME = "astrbot_plugin_memora"
MAX_ARCHIVE_MEMBERS = 20_000
MAX_ARCHIVE_MEMBER_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_COMPRESSION_RATIO = 200.0


class RollbackVerificationError(RuntimeError):
    """表示归档、fixture、SQLite 或回退编排错误。"""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """解析同数据目录回退验证命令。"""

    parser = argparse.ArgumentParser(description="验证 Memora runtime ZIP 回退契约")
    parser.add_argument("--old-runtime", required=True, type=Path)
    parser.add_argument("--new-runtime", required=True, type=Path)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument(
        "--astrbot-source",
        type=Path,
        help="可选的 AstrBot 源码根；默认使用锁定环境中的 AstrBot",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def _sha256_bytes(value: bytes) -> str:
    """返回字节串的小写 SHA-256。"""

    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    """流式计算普通文件 SHA-256。"""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(value: Any) -> bytes:
    """将结构化值编码为稳定 JSON 字节。"""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """构造 JSON 对象并拒绝重复键。"""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RollbackVerificationError("fixture_json_duplicate_key")
        result[key] = value
    return result


def load_fixture(path: Path) -> dict[str, Any]:
    """读取并严格校验版本化回退 fixture。"""

    if path.is_symlink():
        raise RollbackVerificationError("fixture_symlink_path")
    path = path.resolve()
    if not path.is_dir():
        raise RollbackVerificationError("fixture_missing")
    definition = path / "fixture.json"
    try:
        payload = json.loads(
            definition.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
        )
    except RollbackVerificationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RollbackVerificationError("fixture_invalid") from exc
    if not isinstance(payload, dict) or payload.get("schema") != FIXTURE_SCHEMA:
        raise RollbackVerificationError("fixture_schema_invalid")
    required = {
        "database",
        "seed_sql",
        "derived_sql",
        "canonical_count",
        "schema_version",
        "schema_contract",
        "idempotency_mapping",
        "derived_queries",
        "fingerprint_files",
        "allowed_manifest_changes",
    }
    if not required.issubset(payload):
        raise RollbackVerificationError("fixture_fields_missing")
    fingerprint_files = payload["fingerprint_files"]
    if not isinstance(fingerprint_files, list) or any(
        not isinstance(item, str) for item in fingerprint_files
    ):
        raise RollbackVerificationError("fixture_fingerprint_files_invalid")
    names = [
        payload["database"],
        payload["seed_sql"],
        payload["derived_sql"],
        *fingerprint_files,
    ]
    if any(not isinstance(item, str) or Path(item).name != item for item in names):
        raise RollbackVerificationError("fixture_filename_invalid")
    for name in names[1:]:
        item = path / name
        if item.is_symlink() or not item.is_file():
            raise RollbackVerificationError("fixture_file_missing")
    if not isinstance(payload["canonical_count"], int) or isinstance(
        payload["canonical_count"], bool
    ):
        raise RollbackVerificationError("fixture_count_invalid")
    if not isinstance(payload["schema_version"], int) or isinstance(
        payload["schema_version"], bool
    ):
        raise RollbackVerificationError("fixture_version_invalid")
    if not isinstance(payload["schema_contract"], dict) or not isinstance(
        payload["idempotency_mapping"], dict
    ):
        raise RollbackVerificationError("fixture_schema_contract_invalid")
    mapping = payload["idempotency_mapping"]
    if any(
        not isinstance(key, str)
        or not key.strip()
        or not isinstance(value, int)
        or isinstance(value, bool)
        for key, value in mapping.items()
    ):
        raise RollbackVerificationError("fixture_idempotency_mapping_invalid")
    queries = payload["derived_queries"]
    if (
        not isinstance(queries, dict)
        or not isinstance(queries.get("fts5"), str)
        or not queries["fts5"].strip()
    ):
        raise RollbackVerificationError("fixture_derived_queries_invalid")
    patterns = payload["allowed_manifest_changes"]
    if not isinstance(patterns, list) or any(
        not isinstance(item, str) for item in patterns
    ):
        raise RollbackVerificationError("fixture_manifest_allowlist_invalid")
    payload["root"] = path
    return payload


def _safe_zip_name(name: str) -> PurePosixPath:
    """校验 ZIP 成员路径并返回 POSIX 路径。"""

    if not name or "\\" in name:
        raise RollbackVerificationError("runtime_archive_unsafe")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
        raise RollbackVerificationError("runtime_archive_unsafe")
    if not path.parts or path.parts[0] != PLUGIN_DIR_NAME:
        raise RollbackVerificationError("runtime_archive_top_level_invalid")
    return path


def _validate_archive_members(
    infos: Sequence[zipfile.ZipInfo],
) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
    """完整校验 ZIP 元数据，并返回规范化且无冲突的成员。"""

    if not infos or len(infos) > MAX_ARCHIVE_MEMBERS:
        raise RollbackVerificationError("runtime_archive_too_many_members")
    normalized_names: set[str] = set()
    total = 0
    validated: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    for info in infos:
        path = _safe_zip_name(info.filename)
        normalized_name = path.as_posix()
        if normalized_name in normalized_names:
            raise RollbackVerificationError("runtime_archive_duplicate_member")
        normalized_names.add(normalized_name)

        mode = info.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if stat.S_ISLNK(mode):
            raise RollbackVerificationError("runtime_archive_symlink")
        allowed_types = {0, stat.S_IFDIR if info.is_dir() else stat.S_IFREG}
        if file_type not in allowed_types:
            raise RollbackVerificationError("runtime_archive_special_member")
        if info.flag_bits & 0x1:
            raise RollbackVerificationError("runtime_archive_encrypted")
        if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
            raise RollbackVerificationError("runtime_archive_member_too_large")
        if info.file_size:
            ratio = info.file_size / max(1, info.compress_size)
            if ratio > MAX_ARCHIVE_COMPRESSION_RATIO:
                raise RollbackVerificationError("runtime_archive_compression_ratio")
        total += info.file_size
        if total > MAX_ARCHIVE_BYTES:
            raise RollbackVerificationError("runtime_archive_too_large")
        validated.append((info, path))
    return validated


def _extract_validated_archive(
    archive: zipfile.ZipFile,
    members: Sequence[tuple[zipfile.ZipInfo, PurePosixPath]],
    staging: Path,
) -> list[str]:
    """把已验证成员写入隔离 staging，并返回普通文件 manifest。"""

    manifest: list[str] = []
    for info, path in members:
        destination = staging.joinpath(*path.parts)
        if info.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, destination.open("xb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
        manifest.append(path.as_posix())
    return sorted(manifest)


def install_runtime(archive_path: Path, plugin_store: Path) -> tuple[Path, list[str]]:
    """把完整 runtime ZIP 安全安装到空插件根并返回 manifest。"""

    archive_path = Path(archive_path)
    if archive_path.is_symlink():
        raise RollbackVerificationError("runtime_archive_symlink_path")
    archive_path = archive_path.resolve()
    if not archive_path.is_file():
        raise RollbackVerificationError("runtime_archive_missing")
    plugin_store = Path(plugin_store)
    if plugin_store.is_symlink():
        raise RollbackVerificationError("runtime_install_root_symlink")
    if plugin_store.exists() and not plugin_store.is_dir():
        raise RollbackVerificationError("runtime_install_root_invalid")
    if plugin_store.exists() and any(plugin_store.iterdir()):
        raise RollbackVerificationError("runtime_install_root_not_empty")
    plugin_store.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{plugin_store.name}.extract-",
            dir=plugin_store.parent,
        )
    )
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = _validate_archive_members(archive.infolist())
            try:
                failed_member = archive.testzip()
            except (OSError, RuntimeError, zipfile.BadZipFile, zlib.error) as exc:
                raise RollbackVerificationError("runtime_archive_crc_failed") from exc
            if failed_member is not None:
                raise RollbackVerificationError("runtime_archive_crc_failed")
            manifest = _extract_validated_archive(archive, members, staging)
        staged_plugin_root = staging / PLUGIN_DIR_NAME
        required = ("main.py", "metadata.yaml")
        if any(not (staged_plugin_root / name).is_file() for name in required):
            raise RollbackVerificationError("runtime_archive_incomplete")
        if plugin_store.exists():
            plugin_store.rmdir()
        os.replace(staging, plugin_store)
    except RollbackVerificationError:
        raise
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise RollbackVerificationError("runtime_archive_invalid") from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    plugin_root = plugin_store / PLUGIN_DIR_NAME
    return plugin_root, manifest


def _validate_seed_sql(sql: str) -> None:
    """拒绝 fixture SQL 访问隔离数据库之外的资源。"""

    normalized = " ".join(sql.upper().split())
    forbidden = ("ATTACH ", "DETACH ", "VACUUM INTO", "LOAD_EXTENSION")
    if any(token in normalized for token in forbidden):
        raise RollbackVerificationError("fixture_seed_sql_unsafe")


def seed_data_dir(data_dir: Path, fixture: Mapping[str, Any]) -> Path:
    """从文本 fixture 创建匿名 canonical SQLite 与静态指纹文件。"""

    root = Path(fixture["root"])
    database = data_dir / str(fixture["database"])
    sql = (root / str(fixture["seed_sql"])).read_text(encoding="utf-8")
    _validate_seed_sql(sql)
    try:
        with sqlite3.connect(database) as connection:
            connection.executescript(sql)
            connection.commit()
    except sqlite3.Error as exc:
        raise RollbackVerificationError("fixture_seed_failed") from exc
    for name in fixture["fingerprint_files"]:
        shutil.copy2(root / name, data_dir / name)
    return database


def _read_derived_hash(connection: sqlite3.Connection, sql_path: Path) -> str:
    """执行单条只读派生投影并返回稳定行哈希。"""

    sql = sql_path.read_text(encoding="utf-8").strip()
    statement = sql[:-1].rstrip() if sql.endswith(";") else sql
    first_token = statement.lstrip().split(None, 1)[0].upper() if statement else ""
    if first_token != "SELECT" or ";" in statement:
        raise RollbackVerificationError("fixture_derived_sql_invalid")
    try:
        rows = connection.execute(sql).fetchall()
    except sqlite3.Error as exc:
        raise RollbackVerificationError("derived_rebuild_failed") from exc
    return _sha256_bytes(_canonical_json(rows))


def fingerprint_data(data_dir: Path, fixture: Mapping[str, Any]) -> dict[str, Any]:
    """计算 schema、canonical、配置及事务内契约探针指纹。"""

    root = Path(fixture["root"])
    database = data_dir / str(fixture["database"])
    uri = f"file:{database.as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            quick_check_rows = connection.execute("PRAGMA quick_check").fetchall()
            quick_check = [str(row[0]) for row in quick_check_rows]
            schema_rows = connection.execute(
                "SELECT type,name,COALESCE(sql,'') FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            ).fetchall()
            version_row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM db_version"
            ).fetchone()
            rows = connection.execute(
                "SELECT id,doc_id,text,metadata,created_at,updated_at "
                "FROM documents ORDER BY id"
            ).fetchall()
            canonical_projection_hash = _read_derived_hash(
                connection, root / str(fixture["derived_sql"])
            )
    except sqlite3.Error as exc:
        raise RollbackVerificationError("sqlite_fingerprint_failed") from exc
    identities: list[tuple[Any, Any]] = []
    revisions: list[Any] = []
    canonical_rows: list[Any] = []
    for row in rows:
        try:
            metadata = json.loads(row[3]) if row[3] else {}
        except json.JSONDecodeError as exc:
            raise RollbackVerificationError("canonical_metadata_invalid") from exc
        revision = metadata.get("revision_token") or row[5] or row[4]
        identities.append((row[0], row[1]))
        revisions.append(revision)
        canonical_rows.append(row)
    file_hashes = {
        name: _sha256_file(data_dir / name) for name in fixture["fingerprint_files"]
    }
    schema_contract = inspect_schema_contract(database, fixture)
    return {
        "quick_check": quick_check,
        "schema_version": int(version_row[0]) if version_row else 0,
        "canonical_count": len(rows),
        "schema_hash": _sha256_bytes(_canonical_json(schema_rows)),
        "canonical_hash": _sha256_bytes(_canonical_json(canonical_rows)),
        "canonical_id_hash": _sha256_bytes(_canonical_json(identities)),
        "canonical_revision_hash": _sha256_bytes(_canonical_json(revisions)),
        "canonical_projection_hash": canonical_projection_hash,
        "schema_contract": schema_contract,
        "file_hashes": file_hashes,
    }


def validate_fixture_baseline(
    fingerprint: Mapping[str, Any], fixture: Mapping[str, Any]
) -> None:
    """确认 fixture 初始数量、版本和 SQLite 完整性。"""

    if fingerprint.get("quick_check") != ["ok"]:
        raise RollbackVerificationError("sqlite_quick_check_failed")
    if fingerprint.get("canonical_count") != fixture["canonical_count"]:
        raise RollbackVerificationError("fixture_canonical_count_mismatch")
    if fingerprint.get("schema_version") != fixture["schema_version"]:
        raise RollbackVerificationError("fixture_schema_version_mismatch")
    contract = fingerprint.get("schema_contract")
    if not isinstance(contract, Mapping) or contract.get("status") != "closed":
        raise RollbackVerificationError("fixture_schema_contract_incomplete")


def compare_manifests(
    old: Sequence[str],
    new: Sequence[str],
    allowed_patterns: Sequence[str],
    temporary: Path,
) -> dict[str, Any]:
    """按 diff 0/1/>1 语义比较 runtime manifest。"""

    old_path, new_path = temporary / "old.manifest", temporary / "new.manifest"
    old_path.write_text("\n".join(old) + "\n", encoding="utf-8")
    new_path.write_text("\n".join(new) + "\n", encoding="utf-8")
    try:
        completed = subprocess.run(
            ["diff", "-u", str(old_path), str(new_path)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise RollbackVerificationError("manifest_diff_command_failed") from exc
    if completed.returncode > 1:
        raise RollbackVerificationError("manifest_diff_command_failed")
    changed = sorted(set(old).symmetric_difference(new))
    unexpected = [
        name
        for name in changed
        if not any(fnmatch.fnmatchcase(name, pattern) for pattern in allowed_patterns)
    ]
    return {
        "exit_code": completed.returncode,
        "review_required": completed.returncode == 1,
        "difference_count": len(changed),
        "unexpected_count": len(unexpected),
        "diff_hash": _sha256_bytes(completed.stdout),
        "old_manifest_hash": _sha256_bytes(_canonical_json(list(old))),
        "new_manifest_hash": _sha256_bytes(_canonical_json(list(new))),
    }


def detect_astrbot_version() -> str:
    """返回锁定环境当前 AstrBot 版本。"""

    import astrbot

    version = getattr(astrbot, "__version__", None)
    if not isinstance(version, str):
        try:
            from astrbot.core.config.default import VERSION
        except ImportError as exc:
            raise RollbackVerificationError("astrbot_version_unavailable") from exc
        version = str(VERSION)
    return version


def detect_astrbot_source_version(source: Path) -> tuple[Path, str]:
    """静态校验 AstrBot 源码根并读取项目版本，禁止回退到 wheel。"""

    import tomllib

    source = Path(source)
    if source.is_symlink():
        raise RollbackVerificationError("astrbot_source_symlink_path")
    source = source.resolve()
    metadata = source / "pyproject.toml"
    package = source / "astrbot"
    if not source.is_dir() or not package.is_dir() or not metadata.is_file():
        raise RollbackVerificationError("astrbot_source_missing")
    try:
        payload = tomllib.loads(metadata.read_text(encoding="utf-8"))
        version = payload.get("project", {}).get("version")
    except (OSError, UnicodeError, ValueError) as exc:
        raise RollbackVerificationError("astrbot_source_metadata_invalid") from exc
    if not isinstance(version, str) or not version.strip():
        raise RollbackVerificationError("astrbot_source_version_missing")
    return source, version.strip()


def _run_phase(
    *,
    name: str,
    version: str,
    source: Path | None,
    plugin_root: Path,
    data_dir: Path,
    temporary: Path,
    cycles: int,
    inject_failure: bool = False,
) -> dict[str, Any]:
    """执行一个隔离 load/reload/terminate 阶段。"""

    result = run_worker_subprocess(
        version=version,
        astrbot_source=source,
        plugin_root=plugin_root,
        data_dir=data_dir,
        report=temporary / f"{name}.json",
        cycles=cycles,
        inject_initialization_failure=inject_failure,
        scenario_mode="namespace",
    )
    return {
        "name": name,
        "status": result.get("status"),
        "worker_exit_code": result.get("worker_exit_code"),
        "reason_code": result.get("reason_code"),
        "error_type": result.get("error_type"),
        "namespace": result.get("namespace", {}),
    }


def run_verification(args: argparse.Namespace) -> dict[str, Any]:
    """执行真实更新器回退、派生重建及旧→新→旧生命周期流程。"""

    fixture = load_fixture(args.fixture)
    data_dir = prepare_empty_data_root(args.data_dir)
    database = seed_data_dir(data_dir, fixture)
    if not database.is_file():
        raise RollbackVerificationError("fixture_database_missing")
    if args.astrbot_source is not None:
        source, version = detect_astrbot_source_version(args.astrbot_source)
        astrbot_evidence = {"kind": "source_tree", "source_verified": True}
    else:
        source = None
        version = detect_astrbot_version()
        astrbot_evidence = {
            "kind": "locked_runtime_dependency",
            "source_verified": False,
        }
    old_archive_hash = _sha256_file(args.old_runtime.resolve())
    new_archive_hash = _sha256_file(args.new_runtime.resolve())
    phases: list[dict[str, Any]] = []
    fingerprints: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix=".memora-rollback-") as temporary_text:
        temporary = Path(temporary_text)
        old_store = temporary / "old-runtime" / "data" / "plugins"
        new_store = temporary / "new-runtime" / "data" / "plugins"
        old_root, old_manifest = install_runtime(args.old_runtime, old_store)
        new_root, new_manifest = install_runtime(args.new_runtime, new_store)
        manifest = compare_manifests(
            old_manifest,
            new_manifest,
            fixture["allowed_manifest_changes"],
            temporary,
        )
        runtime_update_rollback = exercise_runtime_update_rollback(
            old_root,
            args.old_runtime.resolve(),
            args.new_runtime.resolve(),
            temporary / "runtime-update-evidence",
        )
        phases.append(
            _run_phase(
                name="old_initial_load",
                version=version,
                source=source,
                plugin_root=old_root,
                data_dir=data_dir,
                temporary=temporary,
                cycles=1,
            )
        )
        baseline = fingerprint_data(data_dir, fixture)
        validate_fixture_baseline(baseline, fixture)
        fingerprints.append(baseline)
        derived_rebuild = build_derived_rebuild_evidence(
            database,
            temporary / "derived-rebuild-evidence",
            fixture,
        )
        phases.append(
            _run_phase(
                name="new_load_reload_twice_terminate",
                version=version,
                source=source,
                plugin_root=new_root,
                data_dir=data_dir,
                temporary=temporary,
                cycles=3,
            )
        )
        fingerprints.append(fingerprint_data(data_dir, fixture))
        phases.append(
            _run_phase(
                name="old_reinstall_load_terminate",
                version=version,
                source=source,
                plugin_root=old_root,
                data_dir=data_dir,
                temporary=temporary,
                cycles=1,
            )
        )
        fingerprints.append(fingerprint_data(data_dir, fixture))
        phases.append(
            _run_phase(
                name="new_initialization_failure",
                version=version,
                source=source,
                plugin_root=new_root,
                data_dir=data_dir,
                temporary=temporary,
                cycles=1,
                inject_failure=True,
            )
        )
        fingerprints.append(fingerprint_data(data_dir, fixture))

    fingerprints_unchanged = all(item == fingerprints[0] for item in fingerprints[1:])
    phase_error = any(item["status"] == "error" for item in phases)
    phases_passed = all(
        item["status"] == "passed" and item["worker_exit_code"] == 0 for item in phases
    )
    evidence_closed = (
        derived_rebuild.get("status") == "closed"
        and runtime_update_rollback.get("status") == "closed"
    )
    if phase_error:
        status = "error"
    elif not evidence_closed:
        status = "remaining"
    elif manifest["review_required"]:
        status = "review_required"
    elif phases_passed and fingerprints_unchanged and manifest["unexpected_count"] == 0:
        status = "passed"
    else:
        status = "failed"
    return {
        "schema": REPORT_SCHEMA,
        "status": status,
        "astrbot_version": version,
        "astrbot_evidence": astrbot_evidence,
        "runtime_archives": {
            "old_sha256": old_archive_hash,
            "new_sha256": new_archive_hash,
        },
        "manifest_diff": manifest,
        "runtime_update_rollback": runtime_update_rollback,
        "derived_rebuild": derived_rebuild,
        "phases": phases,
        "fingerprints": fingerprints,
        "fingerprints_unchanged": fingerprints_unchanged,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """运行命令并以 0/1/2 区分通过、待审/违约和工具错误。"""

    args = parse_args(argv)
    try:
        payload = run_verification(args)
        exit_code = (
            2
            if payload["status"] == "error"
            else (0 if payload["status"] == "passed" else 1)
        )
    except (RollbackVerificationError, LifecycleVerificationError) as exc:
        payload = {
            "schema": REPORT_SCHEMA,
            "status": "error",
            "reason_code": str(exc),
        }
        exit_code = 2
    except Exception as exc:
        payload = {
            "schema": REPORT_SCHEMA,
            "status": "error",
            "reason_code": "unhandled_tool_error",
            "error_type": type(exc).__name__,
        }
        exit_code = 2
    atomic_write_json(args.report, payload)
    print(json.dumps({"status": payload["status"]}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
