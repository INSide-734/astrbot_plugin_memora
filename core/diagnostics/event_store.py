from __future__ import annotations

import json
import math
import re
import sqlite3
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

_EVENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_SAFE_EXCEPTION_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9.]{0,127}$")
_DOMAINS = frozenset(
    {
        "diagnostics",
        "evolution",
        "index",
        "injection",
        "provider",
        "recall",
        "scheduler",
        "storage",
        "unknown",
        "write",
    }
)
_SEVERITIES = frozenset({"info", "warning", "error", "critical"})
_SOURCES = frozenset(
    {
        "diagnostics",
        "health_scorer",
        "index_validator",
        "memory_evolution",
        "provider_waiter",
        "recall",
        "scheduler",
        "storage",
        "test",
        "unknown",
    }
)
_TEXT_PAYLOAD_CHOICES = {
    "action": frozenset(
        {
            "clear_completed_events",
            "rebuild_index",
            "refresh_metrics",
            "restart_backfill",
        }
    ),
    "capability": frozenset(
        {
            "database",
            "index_validator",
            "injection_recorder",
            "memory_engine",
            "memory_evolution_manager",
            "provider_waiting",
        }
    ),
    "component": frozenset(
        {
            "diagnostics",
            "evolution",
            "index",
            "injection",
            "provider",
            "recall",
            "scheduler",
            "storage",
            "write",
        }
    ),
    "delivery": frozenset(
        {
            "auto",
            "extra_user_content",
            "fake_tool_call",
            "none",
            "user_message_after",
            "user_message_before",
        }
    ),
    "mode": frozenset(
        {"active", "auto", "disabled", "hybrid", "manual", "readonly", "shadow"}
    ),
    "outcome": frozenset(
        {"blocked", "cancelled", "completed", "degraded", "failed", "skipped"}
    ),
    "reason_code": frozenset(
        {
            "FORMAT_FAILED",
            "MUTATION_FAILED",
            "PROTECTION_FAILED",
            "PROTECTION_SCOPE_FAILED",
            "diagnostic_event",
            "diagnostics_action_failed",
            "diagnostics_event_failed",
            "diagnostics_events_failed",
            "diagnostics_health_failed",
            "provider_unavailable",
            "recall_error",
            "task_error",
        }
    ),
    "route": frozenset(
        {"auto", "balanced", "hybrid", "low_cost", "manual", "quality", "tool_first"}
    ),
    "stage": frozenset(
        {
            "diagnostics",
            "evolution",
            "index",
            "injection",
            "maintenance",
            "provider",
            "recall",
            "retrieval",
            "retry",
            "scheduler",
            "storage",
            "write",
        }
    ),
    "status": frozenset(
        {
            "cancelled",
            "completed",
            "degraded",
            "failed",
            "info",
            "ready",
            "running",
            "skipped",
            "waiting",
        }
    ),
    "task_type": frozenset(
        {"cleanup", "evolution", "index", "maintenance", "storage", "summary"}
    ),
}
_NUMERIC_PAYLOAD_FIELDS = frozenset(
    {
        "actual_chars",
        "attempt_count",
        "batch_count",
        "budget_chars",
        "candidate_count",
        "configured_budget_chars",
        "count",
        "duration_ms",
        "effective_budget_chars",
        "failed_count",
        "filtered_count",
        "injected_count",
        "latency_ms",
        "message_count",
        "payload_chars",
        "provider_calls",
        "queue_depth",
        "retry_count",
        "selected_count",
        "skipped_count",
        "success_count",
        "token_cost",
    }
)
_BOOLEAN_PAYLOAD_FIELDS = frozenset(
    {"available", "degraded", "retry_active", "success"}
)
_MAX_NUMBER = 10**12


class DiagnosticEventStore:
    """保存经过固定标量允许列表过滤的诊断事件历史。"""

    def __init__(self, db_path: str | Path) -> None:
        """初始化 Store，但不创建数据库文件。"""
        self.db_path = Path(db_path)

    async def initialize(self) -> None:
        """创建诊断事件表和稳定排序索引。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS diagnostic_events (
                    event_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    resolved_at TEXT
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_diagnostic_events_list
                ON diagnostic_events (created_at DESC, event_id DESC)
                """
            )
            await db.commit()

    async def add_event(self, event: dict[str, Any] | None) -> dict[str, Any]:
        """保存一条脱敏事件，并返回与持久化内容一致的副本。"""
        event_data = event if isinstance(event, dict) else {}
        event_id = self._safe_event_id(event_data.get("event_id"))
        created_at = self._safe_timestamp(event_data.get("created_at")) or self._now()
        domain = self._safe_choice(event_data.get("domain"), _DOMAINS, "unknown")
        severity = self._safe_choice(event_data.get("severity"), _SEVERITIES, "info")
        payload = self._sanitize_payload(event_data.get("payload"))
        reason_code = str(payload.get("reason_code") or "diagnostic_event")
        title = reason_code
        message = reason_code
        source = self._safe_choice(event_data.get("source"), _SOURCES, "unknown")
        resolved_at_text = self._safe_timestamp(event_data.get("resolved_at"))

        payload_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO diagnostic_events (
                    event_id, created_at, domain, severity, title, message,
                    source, payload, resolved_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    created_at,
                    domain,
                    severity,
                    title,
                    message,
                    source,
                    payload_text,
                    resolved_at_text,
                ),
            )
            await db.commit()

        return {
            "event_id": event_id,
            "created_at": created_at,
            "domain": domain,
            "severity": severity,
            "title": title,
            "message": message,
            "source": source,
            "payload": deepcopy(payload),
            "resolved_at": resolved_at_text,
        }

    async def list_events(
        self,
        limit: int = 50,
        domain: str | None = None,
        severity: str | None = None,
        include_resolved: bool = True,
    ) -> list[dict[str, Any]]:
        """按可选领域、严重度和解决状态列出最新事件。"""
        safe_limit = self._safe_limit(limit)
        query = """
            SELECT event_id, created_at, domain, severity, title, message,
                   source, payload, resolved_at
            FROM diagnostic_events
            WHERE (:domain IS NULL OR domain = :domain)
              AND (:severity IS NULL OR severity = :severity)
              AND (:include_resolved = 1 OR resolved_at IS NULL)
            ORDER BY created_at DESC, rowid DESC
            LIMIT :limit
        """
        params = {
            "domain": domain,
            "severity": severity,
            "include_resolved": int(include_resolved),
            "limit": safe_limit,
        }

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            await cursor.close()
        return [self._row_to_event(row) for row in rows]

    async def get_event(self, event_id: str) -> dict[str, Any] | None:
        """按诊断关联码读取单条事件。"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT event_id, created_at, domain, severity, title, message,
                       source, payload, resolved_at
                FROM diagnostic_events
                WHERE event_id = ?
                """,
                (event_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return self._row_to_event(row) if row is not None else None

    async def resolve_event(self, event_id: str) -> dict[str, Any] | None:
        """幂等标记事件已解决，并返回脱敏后的最新记录。"""
        resolved_at = self._now()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE diagnostic_events SET resolved_at = ? WHERE event_id = ?",
                (resolved_at, event_id),
            )
            await db.commit()
        return await self.get_event(event_id)

    @classmethod
    def _row_to_event(cls, row: sqlite3.Row | aiosqlite.Row) -> dict[str, Any]:
        """把新旧数据库行转换成当前安全 DTO。"""
        payload = cls._loads_payload(row["payload"])
        reason_code = str(payload.get("reason_code") or "diagnostic_event")
        return {
            "event_id": cls._safe_event_id(row["event_id"], generate=False),
            "created_at": cls._safe_timestamp(row["created_at"]) or cls._now(),
            "domain": cls._safe_choice(row["domain"], _DOMAINS, "unknown"),
            "severity": cls._safe_choice(row["severity"], _SEVERITIES, "info"),
            "title": reason_code,
            "message": reason_code,
            "source": cls._safe_choice(row["source"], _SOURCES, "unknown"),
            "payload": payload,
            "resolved_at": cls._safe_timestamp(row["resolved_at"]),
        }

    @classmethod
    def _loads_payload(cls, payload_text: str) -> dict[str, Any]:
        """解析历史 payload，并重新执行标量允许列表过滤。"""
        try:
            return cls._sanitize_payload(json.loads(payload_text))
        except (TypeError, json.JSONDecodeError):
            return {}

    @classmethod
    def _sanitize_payload(cls, value: Any) -> dict[str, Any]:
        """仅保留允许字段和安全的标量值。"""
        if not isinstance(value, dict):
            return {}
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if key in _TEXT_PAYLOAD_CHOICES:
                selected = str(item or "").strip()
                if selected in _TEXT_PAYLOAD_CHOICES[key]:
                    sanitized[key] = selected
            elif key == "error_type":
                selected = str(item or "").strip()
                if _SAFE_EXCEPTION_PATTERN.fullmatch(selected):
                    sanitized[key] = selected
            elif key in _NUMERIC_PAYLOAD_FIELDS and cls._valid_number(item):
                sanitized[key] = item
            elif key in _BOOLEAN_PAYLOAD_FIELDS and isinstance(item, bool):
                sanitized[key] = item
        return sanitized

    @staticmethod
    def _safe_event_id(value: Any, *, generate: bool = True) -> str:
        """规范化诊断关联码；新增事件缺失时生成随机码。"""
        text = str(value or "").strip()
        if _EVENT_ID_PATTERN.fullmatch(text):
            return text
        return uuid.uuid4().hex if generate else "invalid-event"

    @staticmethod
    def _safe_choice(value: Any, choices: frozenset[str], default: str) -> str:
        """从固定枚举中选择值，非法输入回退默认值。"""
        text = str(value or "").strip().lower()
        return text if text in choices else default

    @staticmethod
    def _valid_number(value: Any) -> bool:
        """检查观测数值为非负、有限且有界的真实数字。"""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if isinstance(value, float) and not math.isfinite(value):
            return False
        return 0 <= value <= _MAX_NUMBER

    @staticmethod
    def _safe_timestamp(value: Any) -> str | None:
        """只接受可解析的 ISO 时间字符串。"""
        if value is None:
            return None
        text = str(value).strip()
        try:
            datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        return text

    @staticmethod
    def _safe_limit(value: Any) -> int:
        """把列表上限钳制到 0～500。"""
        try:
            return max(0, min(int(value), 500))
        except (TypeError, ValueError):
            return 50

    @staticmethod
    def _now() -> str:
        """返回当前 UTC ISO 时间。"""
        return datetime.now(timezone.utc).isoformat()
