"""总结 schema 的窄 CHECK 约束重建迁移。

SQLite 不能就地修改 CHECK 约束；新增固定枚举值时必须按「重命名 → 重建 →
复制 → 删除 → 外键自检」的顺序重建表，并保留全部旧行。本模块只承载这类
重建，迁移顺序与数据校验仍由 ``summary_schema.migrate_conversation_schema``
统一编排。
"""

from __future__ import annotations

from typing import Any

_SUMMARY_JOB_COLUMNS = (
    "job_id,session_id,session_epoch,start_seq,end_seq,expected_count,"
    "source_digest,persona_id,chat_type,group_id,scope_id,gate_revision,"
    "gate_snapshot_json,triggered_by,status,attempt_count,next_attempt_at,"
    "claim_token,lease_until,worker_generation,failed_stage,reason_code,"
    "exception_type,canonical_count,quarantine_count,discard_count,"
    "mark_write_count,failed_count,skipped_count,created_at,updated_at,"
    "operator_action,scope_key,privacy_level,resolver_revision,"
    "scope_provenance_complete,merged_count,facts_rejected_count"
)
_CANDIDATE_COLUMNS = (
    "job_id,slot,slot_key,content_digest,idempotency_key,disposition,"
    "status,canonical_id,updated_at"
)


async def ensure_reason_code_constraint(connection: Any) -> None:
    """重建旧任务表，使新增固定 reason code 可持久化且数据不变。

    ``summary_jobs`` 与 ``summary_job_candidates`` 必须在同一事务内成对重建：
    SQLite 重命名父表时会改写子表的外键，单独重建任一张都会留下悬空引用。
    """

    row = await (
        await connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type='table' AND name='summary_jobs'"
        )
    ).fetchone()
    schema_sql = str(row[0] or "") if row is not None else ""
    if "CHECK(reason_code IN" not in schema_sql or all(
        f"'{code}'" in schema_sql
        for code in ("no_facts", "summary_invalid", "scope_unavailable")
    ):
        return
    await connection.execute("PRAGMA defer_foreign_keys=ON")
    await connection.execute(
        "ALTER TABLE summary_job_candidates RENAME TO summary_job_candidates_reason_v4"
    )
    await connection.execute(
        "ALTER TABLE summary_jobs RENAME TO summary_jobs_reason_v4"
    )
    await _ensure_summary_tables(connection)
    await connection.execute(
        f"INSERT INTO summary_jobs({_SUMMARY_JOB_COLUMNS}) "
        f"SELECT {_SUMMARY_JOB_COLUMNS} FROM summary_jobs_reason_v4"
    )
    await connection.execute(
        f"INSERT INTO summary_job_candidates({_CANDIDATE_COLUMNS}) "
        f"SELECT {_CANDIDATE_COLUMNS} FROM summary_job_candidates_reason_v4"
    )
    await connection.execute("DROP TABLE summary_job_candidates_reason_v4")
    await connection.execute("DROP TABLE summary_jobs_reason_v4")
    await _require_consistent_foreign_keys(connection)


async def ensure_candidate_disposition_constraint(connection: Any) -> None:
    """重建候选 ledger 表，使 ``merged`` 处置可持久化且旧行不变。"""

    row = await (
        await connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type='table' "
            "AND name='summary_job_candidates'"
        )
    ).fetchone()
    schema_sql = str(row[0] or "") if row is not None else ""
    if "CHECK(disposition IN" not in schema_sql or "'merged'" in schema_sql:
        return
    await connection.execute("PRAGMA defer_foreign_keys=ON")
    await connection.execute(
        "ALTER TABLE summary_job_candidates RENAME TO summary_job_candidates_merged_v6"
    )
    await _ensure_summary_tables(connection)
    await connection.execute(
        f"INSERT INTO summary_job_candidates({_CANDIDATE_COLUMNS}) "
        f"SELECT {_CANDIDATE_COLUMNS} FROM summary_job_candidates_merged_v6"
    )
    await connection.execute("DROP TABLE summary_job_candidates_merged_v6")
    await _require_consistent_foreign_keys(connection)


async def _ensure_summary_tables(connection: Any) -> None:
    """延迟导入建表入口，避免与 ``summary_schema`` 形成模块级循环依赖。"""

    from .summary_schema import _ensure_summary_tables as _ensure

    await _ensure(connection)


async def _require_consistent_foreign_keys(connection: Any) -> None:
    """外键悬空时拒绝发布半迁移库。"""

    violations = await (await connection.execute("PRAGMA foreign_key_check")).fetchone()
    if violations is not None:
        raise RuntimeError("summary_constraint_migration_failed")


__all__ = [
    "ensure_candidate_disposition_constraint",
    "ensure_reason_code_constraint",
]
