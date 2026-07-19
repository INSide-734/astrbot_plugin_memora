from datetime import datetime, timezone

from core.managers.memory_evolution_gate import MemoryEvolutionGate
from core.models.memory_evolution import EvolutionSignal


def signal(
    *,
    memory_id: int = 17,
    revision_token: str = "r1",
    importance: float = 0.8,
    pending_jobs: int = 0,
) -> EvolutionSignal:
    return EvolutionSignal(
        memory_id=memory_id,
        revision_token=revision_token,
        importance=importance,
        scope_key="private:user-a",
        topic_keys=("episode",),
        entity_keys=("user-a",),
        occurred_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
        pending_jobs=pending_jobs,
    )


def test_disabled_gate_never_enqueues() -> None:
    gate = MemoryEvolutionGate({"mode": "disabled", "trigger_threshold": 0.1})

    decision = gate.consider(signal(importance=1.0))

    assert decision.should_enqueue is False
    assert decision.reason_code == "mode_disabled"


def test_importance_below_threshold_is_skipped() -> None:
    gate = MemoryEvolutionGate({"mode": "shadow", "trigger_threshold": 0.8})

    decision = gate.consider(signal(importance=0.79))

    assert decision.should_enqueue is False
    assert decision.reason_code == "below_threshold"


def test_pending_cap_is_deterministic() -> None:
    gate = MemoryEvolutionGate({"mode": "shadow", "max_pending_jobs": 2})

    decision = gate.consider(signal(importance=0.9, pending_jobs=2))

    assert decision.should_enqueue is False
    assert decision.reason_code == "pending_cap"


def test_same_signal_produces_same_bucket_and_idempotency_key() -> None:
    gate = MemoryEvolutionGate({"mode": "shadow", "trigger_threshold": 0.5})

    first = gate.consider(signal(memory_id=17, revision_token="r4", importance=0.8))
    second = gate.consider(signal(memory_id=17, revision_token="r4", importance=0.8))

    assert first.should_enqueue is True
    assert first.bucket_key == second.bucket_key
    assert first.idempotency_key == second.idempotency_key


def test_eligible_signal_returns_enqueue_decision() -> None:
    gate = MemoryEvolutionGate({"mode": "readonly", "trigger_threshold": 0.5})

    decision = gate.consider(signal(importance=0.8))

    assert decision.should_enqueue is True
    assert decision.reason_code == "eligible"
    assert decision.bucket_key
    assert decision.idempotency_key


def test_unknown_mode_fails_closed() -> None:
    gate = MemoryEvolutionGate({"mode": "future-mode", "trigger_threshold": 0.1})

    decision = gate.consider(signal(importance=1.0))

    assert decision.should_enqueue is False
    assert decision.reason_code == "mode_disabled"

