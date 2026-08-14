"""GateSnapshot/GateRuntime 契约：解析、替换与不可变。"""

import pytest

from core.features.quality.application.gate_runtime import (
    GateRuntime,
    build_gate_snapshot,
    default_gate_snapshot,
)
from core.features.quality.domain.gate_config import (
    GateBinding,
    GateConfig,
    GateProfile,
)


def test_default_snapshot_matches_legacy_two_profiles():
    snap = default_gate_snapshot()
    assert snap.enabled is True
    assert snap.resolve_profile("private", None, None).name == "private"
    assert snap.resolve_profile("group", None, None).name == "group"


def test_binding_order_first_match_wins():
    cfg = GateConfig(
        default_profile="a",
        profiles=(GateProfile(name="a"), GateProfile(name="b")),
        bindings=(
            GateBinding(profile="a", chat_type="group"),
            GateBinding(profile="b", group_id="g1"),
        ),
    )
    assert build_gate_snapshot(cfg).resolve_profile("group", "g1", None).name == "a"


def test_fallback_to_default_profile():
    cfg = GateConfig(
        default_profile="a",
        profiles=(GateProfile(name="a"), GateProfile(name="b")),
        bindings=(GateBinding(profile="b", group_id="g1"),),
    )
    assert build_gate_snapshot(cfg).resolve_profile("group", "g2", None).name == "a"


def test_exact_match_persona():
    cfg = GateConfig(
        default_profile="a",
        profiles=(GateProfile(name="a"), GateProfile(name="b")),
        bindings=(GateBinding(profile="b", persona_id="p1"),),
    )
    snap = build_gate_snapshot(cfg)
    assert snap.resolve_profile("private", None, "p1").name == "b"
    assert snap.resolve_profile("private", None, "p2").name == "a"


def test_reload_replaces_snapshot():
    rt = GateRuntime(default_gate_snapshot())
    cfg = GateConfig(
        default_profile="b", profiles=(GateProfile(name="b"),), bindings=()
    )
    rt.reload(build_gate_snapshot(cfg))
    assert rt.snapshot().default_profile == "b"
    assert rt.resolve_profile("group", None, None).name == "b"


def test_snapshot_is_immutable():
    snap = default_gate_snapshot()
    with pytest.raises(Exception):
        snap.default_profile = "x"  # type: ignore[misc]
