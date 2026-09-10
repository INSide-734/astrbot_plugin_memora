"""测试配置模型扩展和审计日志。"""

import tempfile
from pathlib import Path

import pytest

from core.features.reflection.domain.config import BucketOverride, CandidateReuseConfig
from core.platform.config.audit import (
    ConfigAuditEntry,
    get_entry_by_id,
    read_audit_log,
    write_audit_entry,
)


class TestBucketOverride:
    """测试 BucketOverride 模型。"""

    def test_bucket_override_with_explicit_k(self):
        """测试显式指定 K 值的桶覆盖。"""
        override = BucketOverride(mode="top_k", fixed_k=8)
        assert override.mode == "top_k"
        assert override.fixed_k == 8

    def test_bucket_override_with_none_k(self):
        """测试 K 值为 None（使用全局配置）的桶覆盖。"""
        override = BucketOverride(mode="observe", fixed_k=None)
        assert override.mode == "observe"
        assert override.fixed_k is None

    def test_bucket_override_frozen(self):
        """测试 BucketOverride 是不可变的。"""
        override = BucketOverride(mode="top_k", fixed_k=8)
        with pytest.raises(Exception):  # Pydantic frozen 会抛出 ValidationError
            override.mode = "off"  # type: ignore


class TestCandidateReuseConfigBucketOverrides:
    """测试 CandidateReuseConfig 的 bucket_overrides 功能。"""

    def test_config_with_bucket_overrides(self):
        """测试带桶覆盖的配置能正确解析。"""
        config = CandidateReuseConfig(
            mode="observe",
            fixed_k=5,
            bucket_overrides={
                "tiny": BucketOverride(mode="top_k", fixed_k=4),
                "small": BucketOverride(mode="top_k", fixed_k=8),
                "medium": BucketOverride(mode="observe", fixed_k=None),
            },
        )
        assert config.mode == "observe"
        assert config.fixed_k == 5
        assert len(config.bucket_overrides) == 3
        assert config.bucket_overrides["tiny"].mode == "top_k"
        assert config.bucket_overrides["tiny"].fixed_k == 4

    def test_get_bucket_config_with_override(self):
        """测试 get_bucket_config 返回桶覆盖配置。"""
        config = CandidateReuseConfig(
            mode="observe",
            fixed_k=5,
            bucket_overrides={
                "tiny": BucketOverride(mode="top_k", fixed_k=4),
            },
        )
        mode, k = config.get_bucket_config("tiny")
        assert mode == "top_k"
        assert k == 4

    def test_get_bucket_config_fallback_to_global(self):
        """测试 get_bucket_config 在无覆盖时回落全局配置。"""
        config = CandidateReuseConfig(
            mode="observe",
            fixed_k=5,
            bucket_overrides={},
        )
        mode, k = config.get_bucket_config("tiny")
        assert mode == "observe"
        assert k == 5

    def test_get_bucket_config_override_k_none_uses_global(self):
        """测试桶覆盖 K 为 None 时使用全局 K。"""
        config = CandidateReuseConfig(
            mode="off",
            fixed_k=10,
            bucket_overrides={
                "medium": BucketOverride(mode="observe", fixed_k=None),
            },
        )
        mode, k = config.get_bucket_config("medium")
        assert mode == "observe"
        assert k == 10  # 使用全局 fixed_k

    def test_bucket_override_respects_fixed_k_validation(self):
        """测试桶覆盖的 K 值也受范围约束。"""
        with pytest.raises(Exception):  # Pydantic validation error
            BucketOverride(mode="top_k", fixed_k=25)  # 超过 le=20

        with pytest.raises(Exception):
            BucketOverride(mode="top_k", fixed_k=0)  # 低于 ge=1


class TestConfigAuditEntry:
    """测试生产配置审计条目，不再使用 evaluation 双轨模型。"""

    def test_create_audit_entry(self):
        """创建条目写入固定 reason 与桶列表。"""
        entry = ConfigAuditEntry.create(
            operator="cli_user",
            evidence_source="topic_candidate_evidence",
            reason="manual_approval",
            before={"mode": "observe"},
            after={
                "mode": "top_k",
                "bucket_overrides": {"tiny": {"mode": "top_k", "fixed_k": 4}},
            },
            changed_buckets=["tiny"],
        )
        assert entry.operator == "cli_user"
        assert entry.reason == "manual_approval"
        assert entry.before == {"mode": "observe"}
        assert entry.after["mode"] == "top_k"
        assert entry.changed_buckets == ["tiny"]
        assert entry.evidence_source == "topic_candidate_evidence"
        assert entry.entry_id
        assert entry.timestamp

    def test_audit_entry_serialization(self):
        """JSON Lines 往返保持标识与 reason。"""
        original = ConfigAuditEntry.create(
            operator="auto_rollout",
            evidence_source="topic_candidate_evidence",
            reason="auto_rollout",
            before={},
            after={"bucket_overrides": {"small": {"mode": "top_k", "fixed_k": 8}}},
            changed_buckets=["small"],
        )
        restored = ConfigAuditEntry.from_json(original.to_json())
        assert restored.entry_id == original.entry_id
        assert restored.operator == original.operator
        assert restored.reason == original.reason
        assert restored.changed_buckets == original.changed_buckets


class TestConfigAuditLog:
    """测试生产审计 JSONL 读写。"""

    def test_append_and_read_audit_log(self):
        """追加后按时间倒序读取。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "audit.jsonl"
            entry1 = ConfigAuditEntry.create(
                operator="user1",
                evidence_source="topic_candidate_evidence",
                reason="manual_approval",
                before={},
                after={"mode": "top_k"},
                changed_buckets=["tiny"],
            )
            entry2 = ConfigAuditEntry.create(
                operator="user2",
                evidence_source="topic_candidate_rollback",
                reason="rollback",
                before={"mode": "top_k"},
                after={"mode": "observe"},
                changed_buckets=["tiny"],
            )
            write_audit_entry(entry1, log_path)
            write_audit_entry(entry2, log_path)
            all_entries = read_audit_log(log_path)
            assert len(all_entries) == 2
            assert all_entries[0].entry_id == entry2.entry_id
            assert all_entries[1].entry_id == entry1.entry_id

    def test_get_by_id(self):
        """按 ID 查找已写入记录。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "audit.jsonl"
            entry = ConfigAuditEntry.create(
                operator="cli",
                evidence_source="topic_candidate_evidence",
                reason="manual_approval",
                before={},
                after={"mode": "top_k"},
                changed_buckets=["small"],
            )
            write_audit_entry(entry, log_path)
            found = get_entry_by_id(entry.entry_id, log_path)
            assert found is not None
            assert found.entry_id == entry.entry_id
            assert found.operator == "cli"

    def test_get_by_id_not_found(self):
        """缺失 ID 返回 None。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "audit.jsonl"
            found = get_entry_by_id("non-existent-uuid", log_path)
            assert found is None

    def test_get_latest(self):
        """limit 返回最近 N 条，最新在前。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "audit.jsonl"
            entries = []
            for index in range(5):
                entry = ConfigAuditEntry.create(
                    operator=f"user{index}",
                    evidence_source="topic_candidate_evidence",
                    reason="manual_approval",
                    before={},
                    after={"mode": "top_k"},
                    changed_buckets=["tiny"],
                )
                write_audit_entry(entry, log_path)
                entries.append(entry)
            latest_3 = read_audit_log(log_path, limit=3)
            assert len(latest_3) == 3
            assert latest_3[0].entry_id == entries[4].entry_id
            assert latest_3[1].entry_id == entries[3].entry_id
            assert latest_3[2].entry_id == entries[2].entry_id

    def test_empty_log_returns_empty_list(self):
        """不存在的日志文件返回空列表。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "audit.jsonl"
            assert read_audit_log(log_path) == []
            assert read_audit_log(log_path, limit=10) == []

    def test_corrupted_lines_are_skipped(self):
        """损坏行跳过，不把读失败伪装成隐私或配置错误。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "audit.jsonl"
            log_path.write_text(
                '{"invalid": "json"\n{"also": "invalid"}\n', encoding="utf-8"
            )
            assert read_audit_log(log_path) == []
