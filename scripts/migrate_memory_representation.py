"""canonical 记忆表示迁移 CLI：默认只读 dry-run，apply 必须显式授权。

用法：

    # 1) 只读 dry-run：不写库，输出脱敏聚合报告
    python scripts/migrate_memory_representation.py --db <memora.db>

    # 2) 生成显式计划文件（含 canonical 整数 ID 与读取时的 revision，仅本地使用）
    python scripts/migrate_memory_representation.py --db <memora.db> \
        --write-plan plan.json --confirm <操作者令牌>

    # 3) 按计划执行（需要把授权实例的迁移服务适配器交给 CLI）
    python scripts/migrate_memory_representation.py --db <memora.db> \
        --apply-plan plan.json --confirm <操作者令牌> \
        --engine-factory <模块:可调用>

安全边界：

- 默认命令只读扫描，不产生任何数据库写入；报告只含白名单计数/状态/原因码，
  不含正文、ID、scope、revision 或来源映射。
- ``--write-plan`` 输出的计划文件包含 canonical 整数 ID 与 revision，属于本地
  敏感运维产物：只用于人工复核后的 apply，不要提交、粘贴或写入日志。
- apply 需要 ``plan_schema``、``target_representation_version``、
  ``operator_confirmation`` 与每条记录的 ``expected_revision``；缺少授权运行时
  适配器时 fail-closed，不触碰 canonical。
- ``--engine-factory`` 由已授权的运行实例提供，签名为
  ``factory(db_path: str, db_connection) -> CanonicalRepresentationMigrationService``；
  本脚本自身不读取生产配置、不创建引擎、不新增第二套 canonical 入口。

退出码：0 = completed；1 = 输入/计划/授权失败（未写 canonical）；2 = 已执行但
degraded（存在 CAS 冲突、不可迁移项、失败项或派生重建降级）。
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
from pathlib import Path
from typing import Any, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

EXIT_COMPLETED = 0
EXIT_FAILED = 1
EXIT_DEGRADED = 2
# CLI 入口的稳定失败原因码；只在标准错误输出，不进入任何报告字段。
REASON_DB_UNAVAILABLE = "db_unavailable"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """解析公开迁移命令；不导入任何运行时代码。"""

    parser = argparse.ArgumentParser(
        description=(
            "canonical 记忆表示迁移（默认只读 dry-run，apply 需要显式计划与授权）"
        )
    )
    parser.add_argument("--db", required=True, help="canonical memora.db 路径")
    parser.add_argument(
        "--report",
        help="可选：把脱敏报告写入该 JSON 文件（父目录必须存在）",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="以缩进 JSON 输出报告",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="keyset 扫描与 checkpoint 的批大小（默认 200）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="dry-run 最多扫描的行数，0 表示全量（默认 0）",
    )
    parser.add_argument(
        "--write-plan",
        help="dry-run 时把显式计划写入该 JSON 文件（需同时提供 --confirm）",
    )
    parser.add_argument("--apply-plan", help="apply 模式：读取该版本化计划文件")
    parser.add_argument(
        "--confirm",
        default="",
        help="操作者确认令牌；必须与计划中的 operator_confirmation 完全一致",
    )
    parser.add_argument(
        "--engine-factory",
        default="",
        help=(
            "apply 模式必需的授权适配器，格式 module:callable；"
            "签名为 factory(db_path, db_connection) -> migration service"
        ),
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="忽略已有 checkpoint，从头按计划执行",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> int:
    """执行 CLI；稳定失败只输出 ``error: <reason_code>``。"""

    args = parse_args(argv)
    from core.features.memory.application import (
        canonical_representation_migration as migration,
    )

    try:
        report = asyncio.run(_run(args, migration))
    except migration.RepresentationMigrationError as error:
        return _fail(error.reason)
    except OSError:
        return _fail("input_unavailable")
    if args.report and not _write_json(args.report, report, pretty=args.pretty):
        return _fail("report_write_failed")
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            sort_keys=True,
        )
    )
    if report.get("status") == "degraded":
        return EXIT_DEGRADED
    return EXIT_COMPLETED


async def _run(args: argparse.Namespace, migration: Any) -> dict[str, Any]:
    """打开 canonical 连接并执行 dry-run 或 apply。"""

    import aiosqlite

    if not Path(args.db).exists():
        raise migration.RepresentationMigrationError(REASON_DB_UNAVAILABLE)
    connection = await aiosqlite.connect(args.db)
    try:
        if args.apply_plan:
            plan = migration.MigrationPlan.parse(
                _read_plan_text(args.apply_plan, migration)
            )
            service = _build_apply_service(args, connection, migration)
            return await service.apply(
                plan,
                confirmation=args.confirm,
                resume=not args.no_resume,
            )
        service = migration.CanonicalRepresentationMigrationService(
            db_connection=connection,
            batch_size=args.batch_size,
        )
        outcome = await service.dry_run(limit=args.limit)
        report = dict(outcome.report)
        if args.write_plan and outcome.plan_items:
            plan = migration.build_migration_plan(
                outcome.plan_items, operator_confirmation=args.confirm
            )
            if not _write_json(args.write_plan, plan.to_payload(), pretty=True):
                raise migration.PlanValidationError("plan_write_failed")
            report["plan_written"] = True
            report["plan_items_count"] = len(plan.items)
        return report
    finally:
        await connection.close()


def _build_apply_service(
    args: argparse.Namespace, connection: Any, migration: Any
) -> Any:
    """加载授权适配器并构造迁移服务；缺失或无效时 fail-closed。"""

    reason = migration.REASON_ENGINE_UNAVAILABLE
    module_name, separator, attribute = args.engine_factory.partition(":")
    if not separator or not module_name or not attribute:
        raise migration.RepresentationMigrationError(reason)
    try:
        module = importlib.import_module(module_name)
        factory = getattr(module, attribute)
    except (ImportError, AttributeError):
        raise migration.RepresentationMigrationError(reason) from None
    service = factory(db_path=args.db, db_connection=connection)
    if not callable(getattr(service, "apply", None)):
        raise migration.RepresentationMigrationError(reason)
    return service


def _read_plan_text(path: str, migration: Any) -> str:
    """读取计划文件；读取失败转换为稳定原因码。"""

    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as error:
        raise migration.PlanValidationError(migration.REASON_PLAN_UNREADABLE) from error


def _write_json(path: str, payload: Any, *, pretty: bool) -> bool:
    """写出 JSON 文件；父目录缺失或写入失败返回 ``False``。"""

    text = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2 if pretty else None,
        sort_keys=True,
    )
    try:
        Path(path).write_text(f"{text}\n", encoding="utf-8")
    except OSError:
        return False
    return True


def _fail(reason: str) -> int:
    """向标准错误输出稳定失败原因并返回失败码。"""

    print(f"error: {reason}", file=sys.stderr)
    return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
