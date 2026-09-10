"""话题候选灰度控制面的直接回归测试。"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from core.platform.config.audit import (
    ConfigAuditEntry,
    read_audit_log,
    write_audit_entry,
)
from core.platform.config.manager import (
    ConfigApplyResult,
    ConfigConflictError,
    ConfigManager,
)
from scripts import gradual_rollout
from tests.config_contract_support import SavingConfig


class _FakeConfigManager:
    """记录控制面调用的最小异步 ConfigManager 替身。"""

    def __init__(self, snapshot: dict[str, Any], revision: str = "rev-1") -> None:
        """使用隔离快照和稳定修订号初始化替身。"""
        self.snapshot = copy.deepcopy(snapshot)
        self.revision = revision
        self.calls: list[dict[str, Any]] = []

    async def get_config_snapshot_async(self) -> tuple[dict[str, Any], str]:
        """返回与生产接口一致的隔离快照和修订号。"""
        return copy.deepcopy(self.snapshot), self.revision

    async def apply_config_changes(
        self,
        changes: dict[str, Any],
        *,
        expected_revision: str | None = None,
        persist: bool = True,
    ) -> ConfigApplyResult:
        """执行最小点路径写入，并验证调用方提供的 CAS 修订号。"""
        if expected_revision != self.revision:
            raise ConfigConflictError(expected_revision or "", self.revision)
        self.calls.append(
            {
                "changes": copy.deepcopy(changes),
                "expected_revision": expected_revision,
                "persist": persist,
            }
        )
        for path, value in changes.items():
            current = self.snapshot
            parts = path.split(".")
            for part in parts[:-1]:
                current = current.setdefault(part, {})
            current[parts[-1]] = copy.deepcopy(value)
        self.revision = f"rev-{len(self.calls) + 1}"
        return ConfigApplyResult(self.revision, tuple(sorted(changes)))


def _base_snapshot() -> dict[str, Any]:
    """构造只含 candidate-reuse 所需字段的有效配置快照。"""
    return {
        "topic_segmentation": {
            "candidate_reuse": {
                "mode": "observe",
                "activation_threshold": 32,
                "bucket_overrides": {},
            }
        }
    }


def _result(
    bucket: str,
    chat_type: str,
    fixed_k: int,
    decision: str = "accept",
) -> dict[str, Any]:
    """构造脱敏的单个 evidence bucket 结果。"""
    return {
        "scale_bucket": bucket,
        "chat_type": chat_type,
        "k": fixed_k,
        "decision": decision,
        "quality_sample_count": 200,
        "token_sample_count": 200,
        "latency_sample_count": 200,
        "negative_sample_count": 100,
    }


def _small_bucket_report() -> dict[str, Any]:
    """构造两种 chat type 都接受 4 和 8 的可操作报告。"""
    return {
        "bucket_results": [
            _result("small", "private", 4),
            _result("small", "private", 8),
            _result("small", "group", 4),
            _result("small", "group", 8),
        ]
    }


def _apply_args(report: Path, config: Path, audit: Path) -> argparse.Namespace:
    """构造无需交互确认的 apply 命令参数。"""
    return argparse.Namespace(
        report=str(report),
        config_path=str(config),
        audit_log=str(audit),
        auto=True,
        fallback_mode="observe",
    )


def test_aggregate_recommendations_chooses_smallest_accepted_k() -> None:
    """P1-17：同一桶和 chat type 必须选择最小已接受 K。"""
    results = gradual_rollout._extract_bucket_results(_small_bucket_report())

    recommendations = gradual_rollout._aggregate_recommendations(results)

    assert recommendations[("small", "private")] == 4
    assert recommendations[("small", "group")] == 4


def test_rollout_recommendation_uses_common_k_and_lowest_threshold() -> None:
    """P1-24：建议只使用双 chat type 共有 K，并记录最低有效阈值。"""
    report = _small_bucket_report()
    report["bucket_results"].extend(
        [_result("large", "private", 8), _result("large", "group", 8)]
    )

    recommendation = gradual_rollout._build_rollout_recommendation(
        gradual_rollout._extract_bucket_results(report)
    )

    assert recommendation is not None
    assert recommendation.activation_threshold == 10
    assert [(item.bucket, item.fixed_k) for item in recommendation.buckets] == [
        ("small", 4),
        ("large", 8),
    ]
    assert recommendation.sample_counts == {
        "quality": 800,
        "token": 800,
        "latency": 800,
        "negative": 400,
    }


def test_rollout_recommendation_rejects_incomplete_chat_type_evidence() -> None:
    """缺失任一 chat type 时不得产生可写入的桶建议。"""
    result = gradual_rollout._build_rollout_recommendation(
        gradual_rollout._extract_bucket_results(
            {"bucket_results": [_result("small", "private", 4)]}
        )
    )

    assert result is None


def test_extract_rejects_pipeline_validation_only_report() -> None:
    """合成 benchmark 标记不得作为生产控制面证据。"""
    with pytest.raises(
        gradual_rollout.RolloutError,
        match="report_pipeline_validation_only",
    ):
        gradual_rollout._extract_bucket_results(
            {
                "pipeline_validation_only": True,
                "bucket_results": [_result("small", "private", 4)],
            }
        )


def test_apply_uses_one_cas_update_and_canonical_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-21/22：apply 必须一次写配置，并追加平台审计字段。"""
    report = tmp_path / "evidence.json"
    report_payload = json.dumps(_small_bucket_report()).encode("utf-8")
    report.write_bytes(report_payload)
    config = tmp_path / "memora_config.json"
    config.write_text("{}", encoding="utf-8")
    audit = tmp_path / "audit.jsonl"
    manager = _FakeConfigManager(_base_snapshot())
    monkeypatch.setattr(
        gradual_rollout,
        "_load_config_manager",
        lambda _config_path: manager,
    )

    result = gradual_rollout.cmd_apply(_apply_args(report, config, audit))

    assert result == 0
    assert len(manager.calls) == 1
    call = manager.calls[0]
    assert call["expected_revision"] == "rev-1"
    assert call["persist"] is True
    assert (
        call["changes"]["topic_segmentation.candidate_reuse.activation_threshold"] == 10
    )
    overrides = call["changes"]["topic_segmentation.candidate_reuse.bucket_overrides"]
    assert overrides["small"] == {"mode": "top_k", "fixed_k": 4}
    assert overrides["tiny"]["mode"] == "observe"

    entries = read_audit_log(audit)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.reason == "auto_rollout"
    assert entry.report_sha256 == hashlib.sha256(report_payload).hexdigest()
    assert entry.sample_counts == {
        "quality": 400,
        "token": 400,
        "latency": 400,
        "negative": 200,
    }
    assert entry.config_revision_before == "rev-1"
    assert entry.config_revision_after == "rev-2"
    assert entry.after["activation_threshold"] == 10


def test_rollback_restores_audited_snapshot_and_links_audit_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-21：rollback 使用审计后的 revision 并记录关联条目。"""
    report = tmp_path / "evidence.json"
    report.write_text(json.dumps(_small_bucket_report()), encoding="utf-8")
    config = tmp_path / "memora_config.json"
    config.write_text("{}", encoding="utf-8")
    audit = tmp_path / "audit.jsonl"
    manager = _FakeConfigManager(_base_snapshot())
    monkeypatch.setattr(
        gradual_rollout,
        "_load_config_manager",
        lambda _config_path: manager,
    )
    assert gradual_rollout.cmd_apply(_apply_args(report, config, audit)) == 0
    entry = read_audit_log(audit)[0]

    rollback_args = argparse.Namespace(
        entry_id=entry.entry_id,
        config_path=str(config),
        audit_log=str(audit),
        auto=True,
    )
    assert gradual_rollout.cmd_rollback(rollback_args) == 0

    assert len(manager.calls) == 2
    assert manager.calls[1]["expected_revision"] == entry.config_revision_after
    restored = manager.snapshot["topic_segmentation"]["candidate_reuse"]
    assert restored["activation_threshold"] == 32
    assert restored["bucket_overrides"]["small"]["mode"] == "observe"
    entries = read_audit_log(audit)
    assert len(entries) == 2
    assert entries[0].reason == "rollback"
    assert entries[0].rollback_of == entry.entry_id
    assert entries[0].config_revision_before == entry.config_revision_after


def test_rollback_rejects_stale_config_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """新配置已写入时，rollback 不能覆盖它。"""
    audit = tmp_path / "audit.jsonl"
    before = gradual_rollout._control_snapshot(_base_snapshot())
    after = copy.deepcopy(before)
    after["activation_threshold"] = 10
    entry = ConfigAuditEntry.create(
        operator="cli_auto",
        evidence_source="topic_candidate_evidence",
        reason="auto_rollout",
        before=before,
        after=after,
        config_revision_before="rev-1",
        config_revision_after="rev-2",
        changed_buckets=["small"],
    )
    write_audit_entry(entry, audit)
    config = tmp_path / "memora_config.json"
    config.write_text("{}", encoding="utf-8")
    manager = _FakeConfigManager(_base_snapshot(), revision="rev-newer")
    monkeypatch.setattr(
        gradual_rollout,
        "_load_config_manager",
        lambda _config_path: manager,
    )

    result = gradual_rollout.cmd_rollback(
        argparse.Namespace(
            entry_id=entry.entry_id,
            config_path=str(config),
            audit_log=str(audit),
            auto=True,
        )
    )

    assert result == 1
    assert manager.calls == []


def test_platform_audit_reads_legacy_row_without_rollout_metadata(
    tmp_path: Path,
) -> None:
    """P1-22：已有平台 JSONL 行缺少新增字段时仍可读取。"""
    path = tmp_path / "audit.jsonl"
    path.write_text(
        json.dumps(
            {
                "timestamp": "2026-09-07T00:00:00+00:00",
                "entry_id": "legacy-entry",
                "operator": "cli",
                "evidence_source": "legacy",
                "reason": "manual_approval",
                "before": {},
                "after": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    entry = read_audit_log(path)[0]

    assert entry.changed_buckets == []
    assert entry.config_revision_before is None
    assert entry.report_sha256 is None
    assert entry.sample_counts == {}


@pytest.mark.asyncio
async def test_schema_backed_composite_bucket_update_persists() -> None:
    """结构化桶映射通过现有 ConfigManager 持久化边界，而非脚本直写。"""
    source = SavingConfig(
        {
            "topic_segmentation": {
                "candidate_reuse": {
                    "mode": "observe",
                    "activation_threshold": 32,
                    "fixed_k": 8,
                    "max_full_topics": 32,
                    "bucket_overrides": {},
                }
            }
        }
    )
    manager = ConfigManager(source)
    _, revision = manager.get_config_snapshot()

    result = await manager.apply_config_changes(
        {
            "topic_segmentation.candidate_reuse.bucket_overrides": {
                "small": {"mode": "top_k", "fixed_k": 4}
            }
        },
        expected_revision=revision,
        persist=True,
    )

    assert result.changed_paths == (
        "topic_segmentation.candidate_reuse.bucket_overrides",
    )
    assert source.saved_snapshots
    assert source["topic_segmentation"]["candidate_reuse"]["bucket_overrides"] == {
        "small": {"mode": "top_k", "fixed_k": 4}
    }
