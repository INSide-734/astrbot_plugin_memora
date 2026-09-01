"""反思窗口使用的门禁快照解析。"""

from __future__ import annotations

from typing import Any

from ...quality.application.gate_runtime import (
    GateSnapshot,
    default_gate_snapshot,
    gate_snapshot_from_json,
)
from ...quality.domain.gate_config import GateProfile


def resolve_reflection_gate(
    gate_runtime: Any,
    *,
    is_group_chat: bool,
    group_id: str | None,
    persona_id: str | None,
    gate_snapshot: GateSnapshot | None = None,
    gate_snapshot_json: str | None = None,
) -> tuple[GateProfile, bool]:
    """读取反思窗口固定或当前门禁快照并解析 profile。

    Args:
        gate_runtime: 可选的门禁运行时；无运行时则使用默认快照。
        is_group_chat: 当前批次是否为群聊。
        group_id: 当前群聊 ID。
        persona_id: 当前人格 ID。
        gate_snapshot: 已恢复的固定快照，优先于运行时。
        gate_snapshot_json: 固定快照 JSON；非法值返回默认快照。

    Returns:
        ``(profile, enabled)``，两项都来自同一不可变快照。
    """
    snapshot = gate_snapshot
    if snapshot is None and gate_snapshot_json:
        snapshot = gate_snapshot_from_json(gate_snapshot_json)
    if snapshot is None:
        snapshot = (
            gate_runtime.snapshot()
            if gate_runtime is not None
            else default_gate_snapshot()
        )
    profile = snapshot.resolve_profile(
        "group" if is_group_chat else "private", group_id, persona_id
    )
    return profile, snapshot.enabled
