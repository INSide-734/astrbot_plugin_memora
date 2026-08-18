"""反思窗口使用的门禁快照解析。"""

from __future__ import annotations

from typing import Any

from ...quality.application.gate_runtime import default_gate_snapshot
from ...quality.domain.gate_config import GateProfile


def resolve_reflection_gate(
    gate_runtime: Any,
    *,
    is_group_chat: bool,
    group_id: str | None,
    persona_id: str | None,
) -> tuple[GateProfile, bool]:
    """读取一次反思窗口门禁快照并解析 profile。

    Args:
        gate_runtime: 可选的门禁运行时；无运行时则使用默认快照。
        is_group_chat: 当前批次是否为群聊。
        group_id: 当前群聊 ID。
        persona_id: 当前人格 ID。

    Returns:
        ``(profile, enabled)``，两项都来自同一不可变快照。
    """

    snapshot = (
        gate_runtime.snapshot() if gate_runtime is not None else default_gate_snapshot()
    )
    profile = snapshot.resolve_profile(
        "group" if is_group_chat else "private", group_id, persona_id
    )
    return profile, snapshot.enabled
