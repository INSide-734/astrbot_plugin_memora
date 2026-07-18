from datetime import datetime, timezone

from core.managers.memory_evolution_gate import MemoryEvolutionGate
from core.models.memory_evolution import EvolutionSignal


UTC = timezone.utc


def signal(**overrides) -> EvolutionSignal:
    values = {
        "memory_id": 17,
        "revision_token": "r1",
        "importance": 0.8,
        "scope_key": "private:user-a",
        "topic_keys": ("episode",),
        "entity_keys": ("user-a",),
        "occurred_at": datetime(2026, 7, 18, tzinfo=UTC),
        "pending_jobs": 0,
        "privacy_level": "shared",
    }
    values.update(overrides)
    return EvolutionSignal(**values)


def test_disabled_and_invalid_modes_never_enqueue() -> None:
    disabled = MemoryEvolutionGate({"enabled": False, "mode": "active"})
    unknown = MemoryEvolutionGate({"enabled": True, "mode": "running"})
    assert disabled.consider(signal()).reason_code == "mode_disabled"
    assert unknown.consider(signal()).reason_code == "mode_disabled"


def test_threshold_and_pending_cap_are_deterministic() -> None:
    gate = MemoryEvolutionGate(
        {
            "enabled": True,
            "mode": "shadow",
            "trigger_threshold": 0.9,
            "max_pending_jobs": 2,
        }
    )
    assert gate.consider(signal(importance=0.8)).reason_code == "below_threshold"
    assert gate.consider(signal(importance=0.95, pending_jobs=2)).reason_code == "pending_cap"


def test_missing_topic_or_time_is_rejected() -> None:
    gate = MemoryEvolutionGate({"enabled": True, "mode": "shadow"})
    assert gate.consider(signal(topic_keys=(), entity_keys=())).reason_code == "invalid_signal"
    assert gate.consider(signal(occurred_at=None)).reason_code == "invalid_signal"


def test_same_signal_produces_stable_keys() -> None:
    gate = MemoryEvolutionGate({"enabled": True, "mode": "shadow"})
    first = gate.consider(signal())
    second = gate.consider(signal())
    assert first.should_enqueue is True
    assert first.bucket_key == second.bucket_key
    assert first.idempotency_key == second.idempotency_key


def test_revision_and_time_bucket_change_idempotency_key() -> None:
    gate = MemoryEvolutionGate(
        {
            "enabled": True,
            "mode": "shadow",
            "consolidation_debounce_seconds": 60,
        }
    )
    first = gate.consider(signal())
    changed_revision = gate.consider(signal(revision_token="r2"))
    changed_bucket = gate.consider(
        signal(occurred_at=datetime(2026, 7, 18, 0, 1, 1, tzinfo=UTC))
    )
    assert first.idempotency_key != changed_revision.idempotency_key
    assert first.idempotency_key != changed_bucket.idempotency_key
