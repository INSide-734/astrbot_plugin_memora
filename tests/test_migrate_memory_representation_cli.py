"""canonical 表示迁移 CLI：只读 dry-run、显式计划与 fail-closed 授权。

共享 fixture（临时 SQLite、canonical 行）位于
``tests/representation_migration_support.py``。
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from core.features.memory.application.canonical_representation_migration import (
    PLAN_SCHEMA,
    REASON_DERIVED_REBUILD_UNAVAILABLE,
    TARGET_REPRESENTATION_VERSION,
)
from tests.representation_migration_support import (
    _create_schema,
    _current_row,
    _legacy_row,
    _seed,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "migrate_memory_representation.py"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli_module():
    """导入 CLI 脚本模块。"""

    import scripts.migrate_memory_representation as cli

    return cli


def test_cli_help_works_without_astrbot_runtime(tmp_path) -> None:
    """``--help`` 不导入 AstrBot 运行时：屏蔽 astrbot 后仍返回 0。"""

    harness = (
        "import runpy, sys; "
        "sys.modules['astrbot'] = None; "
        f"sys.path.insert(0, {str(PROJECT_ROOT)!r}); "
        "sys.argv = ['migrate_memory_representation', '--help']; "
        f"runpy.run_path({str(SCRIPT_PATH)!r}, run_name='__main__')"
    )

    result = subprocess.run(
        [sys.executable, "-c", harness],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        timeout=120,
    )

    assert result.returncode == 0
    assert "usage" in result.stdout.lower()
    assert "dry-run" in result.stdout


def test_cli_dry_run_is_read_only_and_plan_needs_confirmation(tmp_path, capsys) -> None:
    """默认 dry-run 不写库、不写计划；写计划必须显式给出确认令牌。"""

    db_path = str(tmp_path / "canonical.db")
    report_path = tmp_path / "report.json"
    plan_path = tmp_path / "plan.json"
    asyncio.run(_create_schema(db_path))
    asyncio.run(
        _seed(
            db_path,
            [
                _legacy_row(1, updated_at="rev-1"),
                _current_row(2, updated_at="rev-2"),
            ],
        )
    )
    before = Path(db_path).read_bytes()
    cli = _cli_module()

    assert cli.main(["--db", db_path, "--report", str(report_path)]) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["mode"] == "dry_run"
    assert report["changed_count"] == 1
    assert report["plan_written"] is False
    assert not plan_path.exists()
    assert Path(db_path).read_bytes() == before

    assert (
        cli.main(
            [
                "--db",
                db_path,
                "--write-plan",
                str(plan_path),
                "--confirm",
                "token-1",
            ]
        )
        == 0
    )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["plan_schema"] == PLAN_SCHEMA
    assert plan["target_representation_version"] == TARGET_REPRESENTATION_VERSION
    assert plan["operator_confirmation"] == "token-1"
    assert plan["items"] == [
        {
            "memory_id": 1,
            "action": "representation_rewrite",
            "expected_revision": "rev-1",
        }
    ]
    assert Path(db_path).read_bytes() == before

    missing_confirm = tmp_path / "plan-missing.json"
    assert (
        cli.main(
            ["--db", db_path, "--write-plan", str(missing_confirm), "--confirm", ""]
        )
        == 1
    )
    assert "error: confirmation_required" in capsys.readouterr().err
    assert not missing_confirm.exists()


def test_cli_apply_fails_closed_without_authorized_factory(tmp_path, capsys) -> None:
    """apply 缺少授权适配器时拒绝执行且不触碰 canonical。"""

    db_path = str(tmp_path / "canonical.db")
    plan_path = tmp_path / "plan.json"
    asyncio.run(_create_schema(db_path))
    asyncio.run(_seed(db_path, [_legacy_row(1, updated_at="rev-1")]))
    plan_path.write_text(
        json.dumps(
            {
                "plan_schema": PLAN_SCHEMA,
                "target_representation_version": TARGET_REPRESENTATION_VERSION,
                "operator_confirmation": "token-1",
                "items": [
                    {
                        "memory_id": 1,
                        "action": "representation_rewrite",
                        "expected_revision": "rev-1",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    before = Path(db_path).read_bytes()
    cli = _cli_module()

    assert (
        cli.main(
            [
                "--db",
                db_path,
                "--apply-plan",
                str(plan_path),
                "--confirm",
                "token-1",
            ]
        )
        == 1
    )
    assert "error: engine_unavailable" in capsys.readouterr().err
    assert Path(db_path).read_bytes() == before


def test_cli_rejects_plan_carrying_content_and_missing_db(tmp_path, capsys) -> None:
    """计划额外字段（正文 canary）与缺失数据库路径都 fail-closed。"""

    db_path = str(tmp_path / "canonical.db")
    bad_plan = tmp_path / "bad-plan.json"
    asyncio.run(_create_schema(db_path))
    asyncio.run(_seed(db_path, [_legacy_row(1, updated_at="rev-1")]))
    bad_plan.write_text(
        json.dumps(
            {
                "plan_schema": PLAN_SCHEMA,
                "target_representation_version": TARGET_REPRESENTATION_VERSION,
                "operator_confirmation": "token-1",
                "items": [
                    {
                        "memory_id": 1,
                        "action": "representation_rewrite",
                        "expected_revision": "rev-1",
                        "content": "用户喜欢咖啡",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    before = Path(db_path).read_bytes()
    cli = _cli_module()

    assert (
        cli.main(
            [
                "--db",
                db_path,
                "--apply-plan",
                str(bad_plan),
                "--confirm",
                "token-1",
                "--engine-factory",
                "missing_module:factory",
            ]
        )
        == 1
    )
    assert "error: plan_invalid" in capsys.readouterr().err
    assert Path(db_path).read_bytes() == before

    assert cli.main(["--db", str(tmp_path / "absent.db")]) == 1
    assert "error: db_unavailable" in capsys.readouterr().err
    assert not (tmp_path / "absent.db").exists()


def test_cli_apply_runs_through_authorized_factory(
    tmp_path, capsys, monkeypatch
) -> None:
    """授权适配器存在时 apply 走完整 CLI 路径，并以 degraded 退出码上报派生缺失。"""

    db_path = str(tmp_path / "canonical.db")
    plan_path = tmp_path / "plan.json"
    asyncio.run(_create_schema(db_path))
    asyncio.run(_seed(db_path, [_legacy_row(1, updated_at="rev-1")]))
    plan_path.write_text(
        json.dumps(
            {
                "plan_schema": PLAN_SCHEMA,
                "target_representation_version": TARGET_REPRESENTATION_VERSION,
                "operator_confirmation": "token-1",
                "items": [
                    {
                        "memory_id": 1,
                        "action": "representation_rewrite",
                        "expected_revision": "rev-1",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "fake_instance.py").write_text(
        "\n".join(
            [
                "from core.features.memory.application."
                "canonical_representation_migration import (",
                "    CanonicalRepresentationMigrationService,",
                ")",
                "",
                "async def _update_memory(memory_id, updates, expected_revision):",
                "    return True",
                "",
                "def migration_service(*, db_path, db_connection):",
                "    return CanonicalRepresentationMigrationService(",
                "        db_connection=db_connection,",
                "        update_memory=_update_memory,",
                "    )",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    cli = _cli_module()

    exit_code = cli.main(
        [
            "--db",
            db_path,
            "--apply-plan",
            str(plan_path),
            "--confirm",
            "token-1",
            "--engine-factory",
            "fake_instance:migration_service",
        ]
    )

    assert exit_code == cli.EXIT_DEGRADED
    captured = capsys.readouterr()
    assert "error:" not in captured.err
    report = json.loads(captured.out.strip().splitlines()[-1])
    assert report["mode"] == "apply"
    assert report["applied_count"] == 1
    assert report["derived"]["reason_code"] == REASON_DERIVED_REBUILD_UNAVAILABLE
