"""门禁运行时快照与热重载入口。"""

import hashlib
import json
from dataclasses import dataclass

from ..domain.gate_config import GateBinding, GateConfig, GateProfile


@dataclass(frozen=True, slots=True)
class GateSnapshot:
    """不可变门禁评估快照；窗口内评估始终引用同一实例。"""

    enabled: bool
    default_profile: str
    profiles: tuple[GateProfile, ...]
    bindings: tuple[GateBinding, ...]
    revision: str = ""

    def profile_by_name(self, name: str) -> GateProfile | None:
        """按名称查找门禁 profile。"""
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


def build_gate_snapshot(
    config: GateConfig, revision: str | None = None
) -> GateSnapshot:
    """把已校验的 GateConfig 投影为带稳定 revision 的不可变快照。"""
    payload = config.model_dump(mode="json")
    stable_revision = (
        revision
        or hashlib.sha256(
            json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
    )
    return GateSnapshot(
        enabled=config.enabled,
        default_profile=config.default_profile,
        profiles=tuple(config.profiles),
        bindings=tuple(config.bindings),
        revision=stable_revision,
    )


def gate_snapshot_to_json(snapshot: GateSnapshot) -> str:
    """把门禁快照序列化为可持久化的配置白名单 JSON。"""
    payload = {
        "enabled": snapshot.enabled,
        "default_profile": snapshot.default_profile,
        "profiles": [profile.model_dump(mode="json") for profile in snapshot.profiles],
        "bindings": [binding.model_dump(mode="json") for binding in snapshot.bindings],
        "revision": snapshot.revision,
    }
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def gate_snapshot_from_json(value: str | None) -> GateSnapshot | None:
    """从受限 JSON 恢复门禁快照；输入非法时返回 None。"""
    if not value:
        return None
    try:
        payload = json.loads(value)
        if not isinstance(payload, dict):
            return None
        revision = payload.pop("revision", None)
        if revision is not None and not isinstance(revision, str):
            return None
        return build_gate_snapshot(
            GateConfig.model_validate(payload), revision=revision or None
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


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


def capture_gate_snapshot_json(gate_runtime: GateRuntime | None) -> str:
    """捕获当前门禁运行时快照；缺省或无效替身使用内置默认配置。"""
    snapshot = None
    if gate_runtime is not None:
        candidate = gate_runtime.snapshot()
        if isinstance(candidate, GateSnapshot):
            snapshot = candidate
    if snapshot is None:
        snapshot = default_gate_snapshot()
    return gate_snapshot_to_json(snapshot)
