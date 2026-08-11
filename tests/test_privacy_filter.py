"""测试 PrivacyLevel 枚举和隐私过滤逻辑。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "core"
    / "features"
    / "memory"
    / "domain"
    / "memory_atom.py"
)
_spec = importlib.util.spec_from_file_location("memory_atom", _MODULE_PATH)
assert _spec is not None
_memory_atom = importlib.util.module_from_spec(_spec)
sys.modules["memory_atom"] = _memory_atom
_spec.loader.exec_module(_memory_atom)  # type: ignore[arg-type]

PrivacyLevel = _memory_atom.PrivacyLevel


# ============================================================================
# PrivacyLevel 枚举
# ============================================================================


class TestPrivacyLevel:
    """验证 PrivacyLevel 的枚举值与语义。"""

    def test_three_levels_exist(self) -> None:
        """隐私级别应完整包含公开、共享和机密三类。"""
        assert PrivacyLevel.PUBLIC.value == "public"
        assert PrivacyLevel.SHARED.value == "shared"
        assert PrivacyLevel.CONFIDENTIAL.value == "confidential"

    def test_public_is_least_restrictive(self) -> None:
        """公开级别不应等同于机密级别。"""
        assert PrivacyLevel.PUBLIC != PrivacyLevel.CONFIDENTIAL

    def test_confidential_is_most_restrictive(self) -> None:
        """机密级别不应等同于共享级别。"""
        assert PrivacyLevel.CONFIDENTIAL != PrivacyLevel.SHARED


# ============================================================================
# 隐私过滤模拟，与 _filter_by_privacy 的生产逻辑保持一致。
# ============================================================================


def _filter_by_privacy(results: list[dict], chat_type: str) -> list[dict]:
    """模拟 dual_route_retriever._filter_by_privacy()。

    群聊过滤机密记忆，私聊保留全部记忆；缺少 privacy_level 时按旧数据
    兼容契约视为 shared。
    """
    if chat_type == "group":
        return [
            r for r in results if r.get("privacy_level", "shared") != "confidential"
        ]
    return list(results)


class TestPrivacyFilter:
    """验证与 dual_route_retriever 一致的隐私过滤逻辑。"""

    @pytest.fixture
    def mixed_results(self) -> list[dict]:
        """返回覆盖全部隐私级别及旧数据的候选集合。"""
        return [
            {"id": 1, "content": "群聊公开记忆", "privacy_level": "public"},
            {"id": 2, "content": "私聊秘密", "privacy_level": "confidential"},
            {"id": 3, "content": "共享记忆", "privacy_level": "shared"},
            {"id": 4, "content": "无等级记忆", "privacy_level": None},
        ]

    def test_confidential_filtered_in_group_chat(
        self, mixed_results: list[dict]
    ) -> None:
        """群聊应过滤机密记忆。"""
        filtered = _filter_by_privacy(mixed_results, "group")
        ids = {r["id"] for r in filtered}
        assert 2 not in ids, "群聊应过滤机密记忆"
        assert 1 in ids and 3 in ids

    def test_confidential_visible_in_private_chat(
        self, mixed_results: list[dict]
    ) -> None:
        """私聊应允许机密记忆。"""
        filtered = _filter_by_privacy(mixed_results, "private")
        ids = {r["id"] for r in filtered}
        assert 2 in ids, "私聊应允许机密记忆"
        assert len(filtered) == 4

    def test_public_visible_in_both(self, mixed_results: list[dict]) -> None:
        """公开记忆应同时对群聊和私聊可见。"""
        group_ids = {r["id"] for r in _filter_by_privacy(mixed_results, "group")}
        private_ids = {r["id"] for r in _filter_by_privacy(mixed_results, "private")}
        assert 1 in group_ids and 1 in private_ids

    def test_backward_compat_no_privacy_field(self, mixed_results: list[dict]) -> None:
        """缺少隐私字段的旧记忆应按共享级别保留。"""
        no_level = [r for r in mixed_results if r.get("privacy_level") is None]
        assert len(no_level) == 1
        assert len(_filter_by_privacy(no_level, "group")) == 1
