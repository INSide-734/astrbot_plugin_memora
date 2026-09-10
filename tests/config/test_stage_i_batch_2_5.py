"""测试 ConfigManager 扩展、CLI 工具和集成契约。"""

import subprocess
import sys
from pathlib import Path

import pytest

from core.features.reflection.domain.config import (
    BucketOverride,
    CandidateReuseConfig,
)
from core.platform.config.manager import (
    ConfigApplyResult,
    ConfigConflictError,
    ConfigManager,
    ConfigValidationError,
)


class TestConfigManagerUpdateBucket:
    """测试 ConfigManager.update_candidate_reuse_bucket 方法。"""

    @pytest.fixture
    def mock_config_source(self):
        """模拟 AstrBot 配置源。"""
        return {
            "topic_segmentation": {
                "candidate_reuse": {
                    "mode": "observe",
                    "fixed_k": 8,
                    "bucket_overrides": {},
                }
            }
        }

    @pytest.fixture
    def manager(self, mock_config_source):
        """创建 ConfigManager 实例。"""
        return ConfigManager(mock_config_source)

    @pytest.mark.asyncio
    async def test_update_bucket_writes_correct_config(self, manager):
        """测试更新桶配置写入正确的字段。"""
        result = await manager.update_candidate_reuse_bucket(
            bucket="tiny",
            mode="top_k",
            fixed_k=3,
            persist=False,
        )

        assert isinstance(result, ConfigApplyResult)
        assert len(result.changed_paths) == 2
        assert (
            "topic_segmentation.candidate_reuse.bucket_overrides.tiny.mode"
            in result.changed_paths
        )
        assert (
            "topic_segmentation.candidate_reuse.bucket_overrides.tiny.fixed_k"
            in result.changed_paths
        )

        config, _ = manager.get_config_snapshot()
        override = config["topic_segmentation"]["candidate_reuse"]["bucket_overrides"][
            "tiny"
        ]
        assert override["mode"] == "top_k"
        assert override["fixed_k"] == 3

    @pytest.mark.asyncio
    async def test_update_bucket_with_none_k(self, manager):
        """测试桶配置 K 为 None 时继承全局配置。"""
        await manager.update_candidate_reuse_bucket(
            bucket="small",
            mode="observe",
            fixed_k=None,
            persist=False,
        )

        config, _ = manager.get_config_snapshot()
        override = config["topic_segmentation"]["candidate_reuse"]["bucket_overrides"][
            "small"
        ]
        assert override["mode"] == "observe"
        assert override["fixed_k"] is None

    @pytest.mark.asyncio
    async def test_update_bucket_version_conflict(self, manager):
        """测试并发更新检测版本冲突。"""
        _, rev1 = manager.get_config_snapshot()

        await manager.update_candidate_reuse_bucket(
            bucket="medium", mode="top_k", fixed_k=7, persist=False
        )

        with pytest.raises(ConfigConflictError):
            await manager.update_candidate_reuse_bucket(
                bucket="large",
                mode="top_k",
                fixed_k=10,
                expected_revision=rev1,
                persist=False,
            )

    @pytest.mark.asyncio
    async def test_update_bucket_invalid_mode(self, manager):
        """测试无效模式触发验证错误。"""
        with pytest.raises(ConfigValidationError):
            await manager.update_candidate_reuse_bucket(
                bucket="huge", mode="invalid_mode", fixed_k=5, persist=False
            )

    @pytest.mark.asyncio
    async def test_update_bucket_invalid_k_range(self, manager):
        """测试 K 值超限触发验证错误。"""
        with pytest.raises(ConfigValidationError):
            await manager.update_candidate_reuse_bucket(
                bucket="tiny", mode="top_k", fixed_k=0, persist=False
            )

        with pytest.raises(ConfigValidationError):
            await manager.update_candidate_reuse_bucket(
                bucket="tiny", mode="top_k", fixed_k=25, persist=False
            )


class TestSelectorBucketMapping:
    """测试 Selector 的桶映射和配置读取。"""

    def test_get_bucket_config_with_override(self):
        """测试桶覆盖优先于全局配置。"""
        config = CandidateReuseConfig(
            mode="off",
            fixed_k=8,
            bucket_overrides={
                "tiny": BucketOverride(mode="top_k", fixed_k=3),
                "small": BucketOverride(mode="observe", fixed_k=None),
            },
        )

        mode, k = config.get_bucket_config("tiny")
        assert mode == "top_k"
        assert k == 3

        mode, k = config.get_bucket_config("small")
        assert mode == "observe"
        assert k == 8  # None 回落全局

    def test_get_bucket_config_fallback_to_global(self):
        """测试无覆盖时回落全局配置。"""
        config = CandidateReuseConfig(
            mode="top_k",
            fixed_k=8,
            bucket_overrides={},
        )

        mode, k = config.get_bucket_config("medium")
        assert mode == "top_k"
        assert k == 8

        mode, k = config.get_bucket_config("nonexistent")
        assert mode == "top_k"
        assert k == 8


class TestCLIIntegration:
    """测试 CLI 集成（当前 CLI 为 stub 实现）。"""

    # 显式定位仓库根，去除 cwd=仓库根的隐含假设（P2-26）
    REPO_ROOT = Path(__file__).resolve().parents[2]

    def test_cli_analyze_with_valid_report(self):
        """测试 analyze 子命令。"""
        result = subprocess.run(
            [
                sys.executable,
                str(self.REPO_ROOT / "scripts" / "gradual_rollout.py"),
                "analyze",
                "/tmp/fake.json",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=self.REPO_ROOT,
        )
        # CLI 当前是 stub，返回 1（文件不存在）
        assert result.returncode == 1

    def test_cli_apply_stub(self):
        """测试 apply 子命令 stub。"""
        result = subprocess.run(
            [
                sys.executable,
                str(self.REPO_ROOT / "scripts" / "gradual_rollout.py"),
                "apply",
                "/tmp/fake.json",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=self.REPO_ROOT,
        )
        # CLI 当前是 stub，返回 1
        assert result.returncode == 1

    def test_cli_rollback_stub(self):
        """测试 rollback 子命令 stub。"""
        result = subprocess.run(
            [
                sys.executable,
                str(self.REPO_ROOT / "scripts" / "gradual_rollout.py"),
                "rollback",
                "fake-id",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=self.REPO_ROOT,
        )
        # CLI 当前是 stub，返回 1
        assert result.returncode == 1

    def test_cli_audit_stub(self):
        """测试 audit 子命令 stub。"""
        result = subprocess.run(
            [
                sys.executable,
                str(self.REPO_ROOT / "scripts" / "gradual_rollout.py"),
                "audit",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=self.REPO_ROOT,
        )
        # CLI audit 返回 0（无条目）
        assert result.returncode == 0


class TestBoundaryConditions:
    """测试边界条件和错误处理。"""

    def test_bucket_not_exists(self):
        """测试访问不存在的桶使用全局配置。"""
        config = CandidateReuseConfig(mode="top_k", fixed_k=5, bucket_overrides={})
        mode, k = config.get_bucket_config("nonexistent_bucket")
        assert mode == "top_k"
        assert k == 5

    def test_k_exceeds_max(self):
        """测试 K 超过上限触发验证错误。"""
        with pytest.raises(Exception):
            BucketOverride(mode="top_k", fixed_k=25)

    def test_k_below_min(self):
        """测试 K 低于下限触发验证错误。"""
        with pytest.raises(Exception):
            BucketOverride(mode="top_k", fixed_k=0)
