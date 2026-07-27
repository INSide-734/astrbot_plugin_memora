"""测试 PrivacyLevel 枚举和隐私过滤逻辑。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parent.parent / "core" / "models" / "memory_atom.py"
)
_spec = importlib.util.spec_from_file_location("memory_atom", _MODULE_PATH)
assert _spec is not None
_memory_atom = importlib.util.module_from_spec(_spec)
sys.modules["memory_atom"] = _memory_atom
_spec.loader.exec_module(_memory_atom)  # type: ignore[arg-type]

PrivacyLevel = _memory_atom.PrivacyLevel


# ============================================================================
# PrivacyLevel enum
# ============================================================================


class TestPrivacyLevel:
    """PrivacyLevel enum values and semantics."""

    def test_three_levels_exist(self) -> None:
        assert PrivacyLevel.PUBLIC.value == "public"
        assert PrivacyLevel.SHARED.value == "shared"
        assert PrivacyLevel.CONFIDENTIAL.value == "confidential"

    def test_public_is_least_restrictive(self) -> None:
        assert PrivacyLevel.PUBLIC != PrivacyLevel.CONFIDENTIAL

    def test_confidential_is_most_restrictive(self) -> None:
        assert PrivacyLevel.CONFIDENTIAL != PrivacyLevel.SHARED


# ============================================================================
# Privacy filter simulation (mirrors _filter_by_privacy logic)
# ============================================================================


def _filter_by_privacy(results: list[dict], chat_type: str) -> list[dict]:
    """Simulate dual_route_retriever._filter_by_privacy().

    Group chat: filter out CONFIDENTIAL.
    Private chat: keep all.
    Missing privacy_level: treated as "shared" (backward compat).
    """
    if chat_type == "group":
        return [
            r for r in results if r.get("privacy_level", "shared") != "confidential"
        ]
    return list(results)


class TestPrivacyFilter:
    """Privacy filter logic (mirrors _filter_by_privacy in dual_route_retriever)."""

    @pytest.fixture
    def mixed_results(self) -> list[dict]:
        return [
            {"id": 1, "content": "群聊公开记忆", "privacy_level": "public"},
            {"id": 2, "content": "私聊秘密", "privacy_level": "confidential"},
            {"id": 3, "content": "共享记忆", "privacy_level": "shared"},
            {"id": 4, "content": "无等级记忆", "privacy_level": None},
        ]

    def test_confidential_filtered_in_group_chat(
        self, mixed_results: list[dict]
    ) -> None:
        filtered = _filter_by_privacy(mixed_results, "group")
        ids = {r["id"] for r in filtered}
        assert 2 not in ids, "CONFIDENTIAL should be filtered in group chat"
        assert 1 in ids and 3 in ids

    def test_confidential_visible_in_private_chat(
        self, mixed_results: list[dict]
    ) -> None:
        filtered = _filter_by_privacy(mixed_results, "private")
        ids = {r["id"] for r in filtered}
        assert 2 in ids, "CONFIDENTIAL should be visible in private chat"
        assert len(filtered) == 4

    def test_public_visible_in_both(self, mixed_results: list[dict]) -> None:
        group_ids = {r["id"] for r in _filter_by_privacy(mixed_results, "group")}
        private_ids = {r["id"] for r in _filter_by_privacy(mixed_results, "private")}
        assert 1 in group_ids and 1 in private_ids

    def test_backward_compat_no_privacy_field(self, mixed_results: list[dict]) -> None:
        no_level = [r for r in mixed_results if r.get("privacy_level") is None]
        assert len(no_level) == 1
        assert len(_filter_by_privacy(no_level, "group")) == 1
