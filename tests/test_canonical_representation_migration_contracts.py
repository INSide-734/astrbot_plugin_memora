"""表示迁移的纯契约：目标表示计算、报告投影 canary 与计划 fail-closed 解析。

本文件不访问数据库；临时 SQLite / 引擎 fixture 位于
``tests/representation_migration_support.py``。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from core.features.memory.application.canonical_representation_migration import (
    PLAN_SCHEMA,
    REASON_CONTENT_PRESERVATION_UNPROVEN,
    REASON_CONTENT_UNAVAILABLE,
    REASON_EVIDENCE_MAPPING_MISMATCH,
    REASON_METADATA_INVALID,
    REASON_NOT_RECALLABLE,
    REASON_PLAN_INVALID,
    REASON_PLAN_TARGET_UNSUPPORTED,
    REPORT_SCHEMA,
    TARGET_REPRESENTATION_VERSION,
    MigrationPlan,
    PlanValidationError,
    ReportCanaryError,
    assert_report_is_privacy_safe,
    compute_representation_target,
)
from tests.representation_migration_support import _legacy_content, _legacy_metadata

# ---------------------------------------------------------------------------
# 纯函数契约
# ---------------------------------------------------------------------------


def test_target_representation_normalizes_legacy_row() -> None:
    """历史行改写为事实连接正文，并把旧正文保留为叙述。"""

    target = compute_representation_target(_legacy_content(), _legacy_metadata())

    assert target.write_kind == "content"
    assert target.changed is True
    assert target.reason_code is None
    assert target.content == "喜欢咖啡；在上海工作"
    assert target.metadata_updates == {
        "persona_summary": _legacy_content(),
        "canonical_summary": "喜欢咖啡；在上海工作",
        "summary_schema_version": TARGET_REPRESENTATION_VERSION,
    }


def test_target_representation_leaves_current_format_unchanged() -> None:
    """当前格式行既不改写正文，也不产生 metadata 增量。"""

    content = "喜欢咖啡；在上海工作"
    metadata = {
        "key_facts": ["喜欢咖啡", "在上海工作"],
        "fact_source_evidence": [[{"role": "user"}], [{"role": "user"}]],
        "canonical_summary": content,
        "persona_summary": "用户喜欢咖啡，并在上海工作。",
        "summary_schema_version": TARGET_REPRESENTATION_VERSION,
    }

    target = compute_representation_target(content, metadata)

    assert target.changed is False
    assert target.write_kind == "none"
    assert target.metadata_updates == {}


def test_target_representation_without_facts_is_metadata_only() -> None:
    """没有准入事实时不改写正文，只补齐 canonical_summary 与版本标记。"""

    target = compute_representation_target("叙述正文", {"persona_summary": "叙述正文"})

    assert target.write_kind == "metadata"
    assert target.content == "叙述正文"
    assert target.metadata_updates == {
        "canonical_summary": "叙述正文",
        "summary_schema_version": TARGET_REPRESENTATION_VERSION,
    }


@pytest.mark.parametrize(
    ("content", "metadata", "reason_code"),
    [
        ("正文", "not-json", REASON_METADATA_INVALID),
        (
            "正文",
            {"memory_status": "archived", "key_facts": ["a"]},
            REASON_NOT_RECALLABLE,
        ),
        (
            "正文",
            {"key_facts": ["a", "b"], "fact_source_evidence": [{"role": "user"}]},
            REASON_EVIDENCE_MAPPING_MISMATCH,
        ),
        ("", {"key_facts": []}, REASON_CONTENT_UNAVAILABLE),
        (
            "旧正文",
            {
                "key_facts": ["a"],
                "persona_summary": "另一段叙述",
                "fact_source_evidence": [[{"role": "user"}]],
            },
            REASON_CONTENT_PRESERVATION_UNPROVEN,
        ),
    ],
)
def test_target_representation_flags_unprovable_rows(
    content: str, metadata: Any, reason_code: str
) -> None:
    """无法证明可迁移时只返回固定原因码，不猜测也不丢弃文本。"""

    target = compute_representation_target(content, metadata)

    assert target.changed is False
    assert target.reason_code == reason_code
    assert target.content == ""


def test_report_projection_rejects_canary_material() -> None:
    """报告白名单 fail-closed：额外字段与疑似正文值都被拒绝。"""

    report: dict[str, Any] = {
        "report_schema": REPORT_SCHEMA,
        "mode": "dry_run",
        "target_representation_version": TARGET_REPRESENTATION_VERSION,
        "status": "completed",
        "scanned_count": 0,
        "eligible_count": 0,
        "changed_count": 0,
        "unchanged_count": 0,
        "unavailable_count": 0,
        "conflict_count": 0,
        "plan_items_count": 0,
        "plan_written": False,
        "exhausted": True,
        "action_counts": {"content": 0, "metadata": 0, "none": 0, "unavailable": 0},
        "reason_counts": {},
        "derived": {
            "status": "not_applicable",
            "stage": None,
            "reason_code": "dry_run",
        },
        "checkpoint": {"status": "not_required", "reason_code": "dry_run"},
    }
    assert_report_is_privacy_safe(report)

    with pytest.raises(ReportCanaryError):
        assert_report_is_privacy_safe({**report, "memory_id": 7})
    with pytest.raises(ReportCanaryError):
        assert_report_is_privacy_safe({**report, "status": "用户喜欢咖啡"})
    with pytest.raises(ReportCanaryError):
        assert_report_is_privacy_safe({**report, "reason_counts": {"用户喜欢咖啡": 1}})
    with pytest.raises(ReportCanaryError):
        assert_report_is_privacy_safe(
            {**report, "reason_counts": {"revision_conflict": 1, "unknown_code": 1}}
        )
    with pytest.raises(ReportCanaryError):
        assert_report_is_privacy_safe(
            {**report, "derived": {"status": "available", "stage": "用户喜欢咖啡"}}
        )
    with pytest.raises(ReportCanaryError):
        assert_report_is_privacy_safe({**report, "exhausted": "yes"})


def _plan_payload(**overrides: Any) -> dict[str, Any]:
    """构造合法计划载荷，便于逐项破坏。"""

    payload: dict[str, Any] = {
        "plan_schema": PLAN_SCHEMA,
        "target_representation_version": TARGET_REPRESENTATION_VERSION,
        "operator_confirmation": "token-1",
        "items": [
            {
                "memory_id": 3,
                "action": "representation_rewrite",
                "expected_revision": "rev-3",
            }
        ],
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    "payload",
    [
        _plan_payload(plan_schema="memora-memory-representation-plan-v9"),
        _plan_payload(target_representation_version="v9"),
        _plan_payload(operator_confirmation=""),
        _plan_payload(items=[]),
        _plan_payload(items=[{"memory_id": 3, "action": "representation_rewrite"}]),
        _plan_payload(
            items=[
                {"memory_id": 3, "action": "delete_row", "expected_revision": "rev-3"}
            ]
        ),
        _plan_payload(
            items=[
                {
                    "memory_id": 3,
                    "action": "representation_rewrite",
                    "expected_revision": "rev-3",
                    "content": "用户喜欢咖啡",
                }
            ]
        ),
        _plan_payload(
            items=[
                {
                    "memory_id": 0,
                    "action": "representation_rewrite",
                    "expected_revision": "r",
                }
            ]
        ),
        _plan_payload(
            items=[
                {
                    "memory_id": 3,
                    "action": "representation_rewrite",
                    "expected_revision": "rev-3",
                },
                {
                    "memory_id": 3,
                    "action": "representation_rewrite",
                    "expected_revision": "rev-3b",
                },
            ]
        ),
        _plan_payload(
            items=[
                {
                    "memory_id": 3,
                    "action": "owner_reinforce",
                    "expected_revision": "rev-3",
                }
            ]
        ),
        _plan_payload(note="用户喜欢咖啡"),
        {"items": []},
    ],
)
def test_plan_parse_rejects_malformed_or_privacy_carrying_payloads(
    payload: dict[str, Any],
) -> None:
    """计划解析 fail-closed：结构、动作、确认与额外字段都在白名单内。"""

    with pytest.raises(PlanValidationError):
        MigrationPlan.parse(payload)


def test_plan_parse_rejects_extra_item_fields_with_stable_reason() -> None:
    """计划条目携带正文等额外字段时返回稳定原因码 plan_invalid。"""

    with pytest.raises(PlanValidationError) as error:
        MigrationPlan.parse(
            _plan_payload(
                items=[
                    {
                        "memory_id": 3,
                        "action": "representation_rewrite",
                        "expected_revision": "rev-3",
                        "text": "用户喜欢咖啡",
                    }
                ]
            )
        )

    assert error.value.reason == REASON_PLAN_INVALID


def test_plan_parse_rejects_unreadable_json() -> None:
    """非 JSON 文本与 JSON 非对象都返回稳定原因码。"""

    with pytest.raises(PlanValidationError) as invalid_json:
        MigrationPlan.parse("{not json")
    assert invalid_json.value.reason == "plan_json_invalid"

    with pytest.raises(PlanValidationError):
        MigrationPlan.parse("[1, 2, 3]")


def test_plan_parse_reports_unsupported_target_version() -> None:
    """目标表示版本不受支持时不做任何兼容猜测。"""

    with pytest.raises(PlanValidationError) as error:
        MigrationPlan.parse(_plan_payload(target_representation_version="v9"))

    assert error.value.reason == REASON_PLAN_TARGET_UNSUPPORTED


def test_plan_parse_orders_items_and_keeps_stable_fingerprint() -> None:
    """计划按 ID 排序，指纹稳定且不受 JSON 格式影响。"""

    payload = _plan_payload(
        items=[
            {
                "memory_id": 9,
                "action": "representation_rewrite",
                "expected_revision": "rev-9",
            },
            {
                "memory_id": 2,
                "action": "representation_rewrite",
                "expected_revision": "rev-2",
            },
        ]
    )

    from_mapping = MigrationPlan.parse(payload)
    from_text = MigrationPlan.parse(json.dumps(payload))

    assert [item.memory_id for item in from_mapping.items] == [2, 9]
    assert from_mapping.fingerprint == from_text.fingerprint
    assert from_mapping.operator_confirmation == "token-1"
