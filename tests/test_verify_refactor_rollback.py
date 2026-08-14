"""同数据目录 rollback evidence harness 的独立定向测试。"""

from __future__ import annotations

import json
import sqlite3
import stat
import zipfile
from pathlib import Path

import pytest

from scripts import verify_refactor_rollback as rollback

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "refactor_rollback-v1"


def _runtime_files(
    version: str = "1.0.0",
    marker: str = "old-runtime",
) -> dict[str, str]:
    """返回可由真实 AstrBot PluginManager 加载的最小 runtime 内容。"""

    return {
        "astrbot_plugin_memora/main.py": f"""from astrbot.api.star import Star, register

@register("Memora", "fixture", "fixture", "{version}")
class MemoraPlugin(Star):
    def __init__(self, context, config=None):
        super().__init__(context)
        self.config = config

    async def terminate(self):
        self.stopped = True
""",
        "astrbot_plugin_memora/metadata.yaml": f"""name: astrbot_plugin_memora
display_name: Memora Fixture
desc: isolated rollback fixture
version: {version}
author: fixture
repo: https://example.invalid/memora
astrbot_version: ">=4.24.2"
""",
        "astrbot_plugin_memora/_conf_schema.json": "{}",
        "astrbot_plugin_memora/requirements.txt": "",
        "astrbot_plugin_memora/LICENSE": "fixture license",
        "astrbot_plugin_memora/README.md": "fixture readme",
        "astrbot_plugin_memora/README_EN.md": "fixture readme",
        "astrbot_plugin_memora/README_RU.md": "fixture readme",
        "astrbot_plugin_memora/logo.png": "fixture png",
        "astrbot_plugin_memora/core/__init__.py": "",
        "astrbot_plugin_memora/core/runtime_marker.txt": marker,
        "astrbot_plugin_memora/static/__init__.py": "",
        "astrbot_plugin_memora/pages/dashboard/index.html": "<html></html>",
        "astrbot_plugin_memora/pages/dashboard/assets/index.js": "void 0;",
        "astrbot_plugin_memora/.astrbot-plugin/i18n/zh-CN.json": "{}",
        "astrbot_plugin_memora/.astrbot-plugin/i18n/en-US.json": "{}",
        "astrbot_plugin_memora/.astrbot-plugin/i18n/ru-RU.json": "{}",
    }


def _write_runtime_zip(path: Path, files: dict[str, str] | None = None) -> None:
    """写入确定顺序的测试 runtime ZIP。"""

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted((files or _runtime_files()).items()):
            archive.writestr(name, content)


def _seed_fingerprint(tmp_path: Path) -> tuple[dict, dict, Path]:
    """创建 fixture 数据并返回定义、指纹和数据目录。"""

    fixture = rollback.load_fixture(FIXTURE_ROOT)
    data_dir = rollback.prepare_empty_data_root(tmp_path / "data")
    rollback.seed_data_dir(data_dir, fixture)
    fingerprint = rollback.fingerprint_data(data_dir, fixture)
    return fixture, fingerprint, data_dir


def test_install_runtime_rejects_path_traversal(tmp_path: Path) -> None:
    """ZIP 路径穿越必须在写文件前被拒绝。"""

    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("astrbot_plugin_memora/main.py", "")
        bundle.writestr("astrbot_plugin_memora/../../escaped", "canary")

    with pytest.raises(rollback.RollbackVerificationError, match="unsafe"):
        rollback.install_runtime(archive, tmp_path / "plugins")
    assert not (tmp_path / "escaped").exists()


def test_install_runtime_rejects_member_count_before_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成员数超过门限时不得创建半安装 runtime。"""

    archive = tmp_path / "too-many.zip"
    _write_runtime_zip(
        archive,
        {
            **_runtime_files(),
            "astrbot_plugin_memora/extra.py": "pass\n",
        },
    )
    monkeypatch.setattr(rollback, "MAX_ARCHIVE_MEMBERS", 2, raising=False)

    store = tmp_path / "plugins"
    with pytest.raises(
        rollback.RollbackVerificationError,
        match="runtime_archive_too_many_members",
    ):
        rollback.install_runtime(archive, store)
    assert not store.exists()


def test_install_runtime_rejects_oversized_member_before_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单成员超过门限时必须 fail-closed。"""

    archive = tmp_path / "member-too-large.zip"
    _write_runtime_zip(archive)
    monkeypatch.setattr(rollback, "MAX_ARCHIVE_MEMBER_BYTES", 8, raising=False)

    store = tmp_path / "plugins"
    with pytest.raises(
        rollback.RollbackVerificationError,
        match="runtime_archive_member_too_large",
    ):
        rollback.install_runtime(archive, store)
    assert not store.exists()


def test_install_runtime_rejects_excessive_compression_ratio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """高压缩比成员不得绕过累计解压大小门限。"""

    archive = tmp_path / "compression-bomb.zip"
    _write_runtime_zip(
        archive,
        {
            **_runtime_files(),
            "astrbot_plugin_memora/repeated.bin": "0" * 4096,
        },
    )
    monkeypatch.setattr(rollback, "MAX_ARCHIVE_COMPRESSION_RATIO", 2.0, raising=False)

    store = tmp_path / "plugins"
    with pytest.raises(
        rollback.RollbackVerificationError,
        match="runtime_archive_compression_ratio",
    ):
        rollback.install_runtime(archive, store)
    assert not store.exists()


def test_install_runtime_rejects_fifo_member(tmp_path: Path) -> None:
    """FIFO 等特殊成员不得被当作普通文件解压。"""

    archive = tmp_path / "fifo.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for name, content in _runtime_files().items():
            bundle.writestr(name, content)
        fifo = zipfile.ZipInfo("astrbot_plugin_memora/runtime.pipe")
        fifo.create_system = 3
        fifo.external_attr = (stat.S_IFIFO | 0o600) << 16
        bundle.writestr(fifo, b"")

    store = tmp_path / "plugins"
    with pytest.raises(
        rollback.RollbackVerificationError,
        match="runtime_archive_special_member",
    ):
        rollback.install_runtime(archive, store)
    assert not store.exists()


def test_install_runtime_rejects_normalized_duplicate_member(tmp_path: Path) -> None:
    """语法不同但规范化后相同的成员名必须拒绝。"""

    archive = tmp_path / "normalized-duplicate.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for name, content in _runtime_files().items():
            bundle.writestr(name, content)
        bundle.writestr("astrbot_plugin_memora/core/./same.py", "first")
        bundle.writestr("astrbot_plugin_memora/core/same.py", "second")

    store = tmp_path / "plugins"
    with pytest.raises(
        rollback.RollbackVerificationError,
        match="runtime_archive_duplicate_member",
    ):
        rollback.install_runtime(archive, store)
    assert not store.exists()


def test_install_runtime_rejects_archive_symlink_before_resolve(tmp_path: Path) -> None:
    """归档入口本身为 symlink 时不得在 resolve 后误判为普通文件。"""

    target = tmp_path / "runtime.zip"
    link = tmp_path / "runtime-link.zip"
    _write_runtime_zip(target)
    link.symlink_to(target.name)

    with pytest.raises(
        rollback.RollbackVerificationError,
        match="runtime_archive_symlink_path",
    ):
        rollback.install_runtime(link, tmp_path / "plugins")
    assert not (tmp_path / "plugins").exists()


def test_install_runtime_crc_failure_leaves_no_partial_files(tmp_path: Path) -> None:
    """CRC 失败即使发生在后续成员，也不得留下先前已解压文件。"""

    archive = tmp_path / "crc-failure.zip"
    files = {
        **_runtime_files(),
        "astrbot_plugin_memora/z-corrupt.bin": "CRC-CANARY-1234",
    }
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
        for name, content in sorted(files.items()):
            bundle.writestr(name, content)
    contents = archive.read_bytes()
    assert contents.count(b"CRC-CANARY-1234") == 1
    archive.write_bytes(contents.replace(b"CRC-CANARY-1234", b"CRC-CORRUPT-123", 1))

    store = tmp_path / "plugins"
    with pytest.raises(
        rollback.RollbackVerificationError,
        match="runtime_archive_crc_failed",
    ):
        rollback.install_runtime(archive, store)
    assert not store.exists()


def test_fingerprint_detects_revision_change_without_exposing_rows(
    tmp_path: Path,
) -> None:
    """revision 变化必须改变哈希，报告不得包含 canonical 原值。"""

    fixture, before, data_dir = _seed_fingerprint(tmp_path)
    with sqlite3.connect(data_dir / "memora.db") as connection:
        connection.execute(
            "UPDATE documents SET metadata = ? WHERE id = ?",
            ('{"revision_token":"changed"}', 101),
        )
        connection.commit()
    after = rollback.fingerprint_data(data_dir, fixture)

    assert before["canonical_count"] == after["canonical_count"] == 2
    assert before["canonical_id_hash"] == after["canonical_id_hash"]
    assert before["canonical_revision_hash"] != after["canonical_revision_hash"]
    serialized = json.dumps(after, ensure_ascii=False)
    assert "revision-alpha" not in serialized
    assert "fixture-memory-alpha" not in serialized


def test_fixture_closes_complete_v9_schema_and_idempotency_contract(
    tmp_path: Path,
) -> None:
    """fixture 必须覆盖 v9 支撑对象并实际执行映射 trigger 探针。"""

    fixture, fingerprint, _data_dir = _seed_fingerprint(tmp_path)
    contract = fingerprint["schema_contract"]

    assert fixture["schema_version"] == 9
    assert contract["status"] == "closed"
    assert contract["missing_table_count"] == 0
    assert contract["missing_index_count"] == 0
    assert contract["missing_trigger_count"] == 0
    assert contract["idempotency_mapping_count"] == fixture["canonical_count"]
    assert contract["idempotency_trigger_probe"] == "closed"
    assert "derived_rebuildable" not in fingerprint


@pytest.mark.asyncio
async def test_fixture_is_current_for_production_schema_manager(tmp_path: Path) -> None:
    """生产 SchemaManager 必须把 fixture 判定为含写日志的当前 v9。"""

    import aiosqlite

    from core.features.memory.infrastructure.schema_manager import SchemaManager

    fixture, _fingerprint, data_dir = _seed_fingerprint(tmp_path)
    connection = await aiosqlite.connect(data_dir / str(fixture["database"]))
    manager = SchemaManager(connection)
    try:
        inspection = await manager.inspect_schema()
        plan = manager.build_migration_plan(
            inspection,
            require_write_journal=True,
        )
    finally:
        await connection.close()

    assert inspection.version == 9
    assert inspection.idempotency_mapping_valid is True
    assert plan is None


def test_derived_rebuild_evidence_uses_real_fts_faiss_graph_and_evolution(
    tmp_path: Path,
) -> None:
    """五类派生存储必须从 canonical 重建并产生可查询证据。"""

    fixture, before, data_dir = _seed_fingerprint(tmp_path)
    evidence = rollback.build_derived_rebuild_evidence(
        data_dir / str(fixture["database"]),
        tmp_path / "derived-rebuild",
        fixture,
    )
    after = rollback.fingerprint_data(data_dir, fixture)

    assert evidence["status"] == "closed"
    assert set(evidence["stages"]) == {
        "fts5",
        "faiss",
        "graph",
        "relation",
        "projection",
    }
    assert all(stage["status"] == "closed" for stage in evidence["stages"].values())
    assert evidence["stages"]["fts5"]["indexed_count"] == 2
    assert evidence["stages"]["faiss"]["indexed_count"] == 2
    assert evidence["stages"]["graph"]["entry_count"] >= 1
    assert evidence["stages"]["relation"]["active_count"] == 1
    assert evidence["stages"]["projection"]["active_count"] == 1
    assert before == after
    assert str(tmp_path) not in json.dumps(evidence, ensure_ascii=False)


def test_runtime_update_evidence_rejects_identical_archives(tmp_path: Path) -> None:
    """old/new 字节相同必须保持 remaining，不能伪造切换证据。"""

    archive = tmp_path / "runtime.zip"
    _write_runtime_zip(archive, _runtime_files())
    old_root, _manifest = rollback.install_runtime(archive, tmp_path / "plugins")

    evidence = rollback.exercise_runtime_update_rollback(
        old_root,
        archive,
        archive,
        tmp_path / "runtime-evidence",
    )

    assert evidence["status"] == "remaining"
    assert evidence["reason_code"] == "runtime_archives_identical"
    assert evidence["stages"]["archive_identity"]["status"] == "remaining"


def test_astrbot_source_version_is_read_without_importing_wheel(
    tmp_path: Path,
) -> None:
    """显式源码必须由 pyproject 静态定版，且入口 symlink fail-closed。"""

    source = tmp_path / "AstrBot"
    (source / "astrbot").mkdir(parents=True)
    (source / "pyproject.toml").write_text(
        '[project]\nname = "astrbot"\nversion = "4.27.1"\n',
        encoding="utf-8",
    )

    resolved, version = rollback.detect_astrbot_source_version(source)

    assert resolved == source.resolve()
    assert version == "4.27.1"
    link = tmp_path / "AstrBot-link"
    link.symlink_to(source.name)
    with pytest.raises(
        rollback.RollbackVerificationError,
        match="astrbot_source_symlink_path",
    ):
        rollback.detect_astrbot_source_version(link)


def test_manifest_diff_uses_zero_one_semantics(tmp_path: Path) -> None:
    """manifest 相同返回 0，差异返回 1 并标记待审。"""

    same = rollback.compare_manifests(
        ["astrbot_plugin_memora/main.py"],
        ["astrbot_plugin_memora/main.py"],
        [],
        tmp_path,
    )
    changed = rollback.compare_manifests(
        ["astrbot_plugin_memora/main.py"],
        ["astrbot_plugin_memora/main.py", "astrbot_plugin_memora/core/new.py"],
        ["astrbot_plugin_memora/core/*"],
        tmp_path,
    )

    assert same["exit_code"] == 0
    assert same["review_required"] is False
    assert changed["exit_code"] == 1
    assert changed["review_required"] is True
    assert changed["unexpected_count"] == 0


def test_full_rollback_flow_preserves_same_data_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧→新→旧→失败注入必须共享数据并保持全部指纹。"""

    old_zip, new_zip = tmp_path / "old.zip", tmp_path / "new.zip"
    _write_runtime_zip(old_zip, _runtime_files("1.0.0", "old-runtime"))
    _write_runtime_zip(new_zip, _runtime_files("1.1.0", "new-runtime"))
    assert old_zip.read_bytes() != new_zip.read_bytes()
    installed_version = __import__("importlib.metadata").metadata.version("astrbot")
    monkeypatch.setattr(rollback, "detect_astrbot_version", lambda: installed_version)
    report = tmp_path / "rollback-report.json"

    exit_code = rollback.main(
        [
            "--old-runtime",
            str(old_zip),
            "--new-runtime",
            str(new_zip),
            "--data-dir",
            str(tmp_path / "same-data"),
            "--fixture",
            str(FIXTURE_ROOT),
            "--report",
            str(report),
        ]
    )

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["status"] == "passed"
    assert payload["fingerprints_unchanged"] is True
    assert (
        payload["runtime_archives"]["old_sha256"]
        != payload["runtime_archives"]["new_sha256"]
    )
    assert payload["derived_rebuild"]["status"] == "closed"
    assert payload["runtime_update_rollback"]["status"] == "closed"
    assert all(
        stage["status"] == "closed"
        for stage in payload["runtime_update_rollback"]["stages"].values()
    )
    assert payload["runtime_update_rollback"]["migration"]["partial_write_seen"]
    assert payload["runtime_update_rollback"]["migration"]["snapshot_restored"]
    assert payload["runtime_update_rollback"]["blocked"]["persisted"]
    assert payload["runtime_update_rollback"]["old_runtime_reactivated"] is True
    assert [item["name"] for item in payload["phases"]] == [
        "old_initial_load",
        "new_load_reload_twice_terminate",
        "old_reinstall_load_terminate",
        "new_initialization_failure",
    ]
    assert all(item["status"] == "passed" for item in payload["phases"])
    assert str(tmp_path) not in json.dumps(payload, ensure_ascii=False)
