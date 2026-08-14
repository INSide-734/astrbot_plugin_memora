"""门禁运行时快照与热重载入口。"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.gate_config import GateBinding, GateConfig, GateProfile


@dataclass(frozen=True, slots=True)
class GateSnapshot:
    """不可变门禁评估快照；窗口内评估始终引用同一实例。"""

    enabled: bool
    default_profile: str
    profiles: tuple[GateProfile, ...]
    bindings: tuple[GateBinding, ...]

    def profile_by_name(self, name: str) -> GateProfile | None:
        for profile in self.profiles:
            if profile.name == name:
                return profile
        return None

    def resolve_profile(
        self,
        chat_type: str | None,
        group_id: str | None,
        persona_id: str | None,
    ) -> GateProfile:
        """按绑定顺序精确匹配，未命中回退 default_profile。"""
        for binding in self.bindings:
            if binding.chat_type is not None and binding.chat_type != chat_type:
                continue
            if binding.group_id is not None and binding.group_id != group_id:
                continue
            if binding.persona_id is not None and binding.persona_id != persona_id:
                continue
            matched = self.profile_by_name(binding.profile)
            if matched is not None:
                return matched
        fallback = self.profile_by_name(self.default_profile)
        if fallback is None:
            raise RuntimeError("gate_default_profile_missing")
        return fallback


def build_gate_snapshot(config: GateConfig) -> GateSnapshot:
    """把已校验的 GateConfig 投影为不可变快照。"""
    return GateSnapshot(
        enabled=config.enabled,
        default_profile=config.default_profile,
        profiles=tuple(config.profiles),
        bindings=tuple(config.bindings),
    )


def default_gate_snapshot() -> GateSnapshot:
    """内置默认快照 = 当前硬编码行为。"""
    return build_gate_snapshot(GateConfig())


class GateRuntime:
    """持有当前门禁快照；reload 为原子替换。"""

    def __init__(self, snapshot: GateSnapshot) -> None:
        self._snapshot = snapshot

    def snapshot(self) -> GateSnapshot:
        return self._snapshot

    def reload(self, snapshot: GateSnapshot) -> None:
        self._snapshot = snapshot

    def resolve_profile(
        self, chat_type: str | None, group_id: str | None, persona_id: str | None
    ) -> GateProfile:
        return self._snapshot.resolve_profile(chat_type, group_id, persona_id)
