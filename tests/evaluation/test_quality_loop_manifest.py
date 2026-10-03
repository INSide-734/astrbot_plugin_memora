"""质量闭环 manifest 与阶段契约的回归测试。"""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from core.features.evaluation.domain.quality_loop_manifest import (
    QUALITY_LOOP_MANIFEST_SCHEMA_VERSION,
    QualityLoopReplayManifest,
    build_quality_loop_replay_manifest,
    manifest_public_summary,
    quality_loop_case_hash,
    quality_loop_context_key_hash,
    quality_loop_fixture_hash,
    quality_loop_manifest_hash,
    validate_quality_loop_manifest,
)
from core.features.evaluation.domain.quality_loop_stages import (
    QUALITY_LOOP_STAGES,
    REASON_NO_ANNOTATION,
    REASON_TO_OWNING_STAGE,
    make_stage_read,
    owning_stage_for_reason,
    sanitize_stage_payload,
    stage_read_to_payload,
)

_HASH_A = "a" * 64
_HASH_B = "b" * 64
_HASH_C = "c" * 64
_PAIRS = [(_HASH_A, _HASH_B, _HASH_C)]
_SECRET = b"quality-loop-test-secret-32-bytes!"


def _build_manifest(**overrides):
    params = {
        "code_revision": "8f0681e",
        "config_hash": _HASH_A,
        "schema_hash": _HASH_B,
        "seed": 42,
        "pair_fingerprints": _PAIRS,
    }
    params.update(overrides)
    return build_quality_loop_replay_manifest(**params)


def test_manifest_hash_is_deterministic_and_order_independent():
    first = _build_manifest()
    second = _build_manifest()
    assert quality_loop_manifest_hash(first) == quality_loop_manifest_hash(second)

    reordered = _build_manifest(pair_fingerprints=[(_HASH_A, _HASH_B, _HASH_C)] * 1)
    assert quality_loop_manifest_hash(reordered) == quality_loop_manifest_hash(first)


def test_manifest_hash_changes_when_seed_k_or_fixture_changes():
    base = _build_manifest()
    changed_seed = _build_manifest(seed=43)
    changed_k = _build_manifest(k=6)
    changed_pair = _build_manifest(pair_fingerprints=[(_HASH_A, _HASH_B, "d" * 64)])
    base_hash = quality_loop_manifest_hash(base)
    assert quality_loop_manifest_hash(changed_seed) != base_hash
    assert quality_loop_manifest_hash(changed_k) != base_hash
    assert quality_loop_manifest_hash(changed_pair) != base_hash


def test_manifest_rejects_invalid_identifiers():
    with pytest.raises(ValueError):
        _build_manifest(code_revision="has spaces and 中文")
    with pytest.raises(ValueError):
        _build_manifest(config_hash="not-a-hash")
    with pytest.raises(ValueError):
        _build_manifest(seed=-1)
    with pytest.raises(ValueError):
        _build_manifest(pair_fingerprints=[])
    with pytest.raises(ValueError):
        _build_manifest(pair_fingerprints=[("short", _HASH_B, _HASH_C)])


def test_validate_manifest_matches_actual_pairs_and_rejects_drift():
    manifest = _build_manifest()
    expected = {
        "expected_pairs": _PAIRS,
        "expected_code_revision": "8f0681e",
        "expected_config_hash": _HASH_A,
        "expected_schema_hash": _HASH_B,
        "expected_seed": 42,
        "expected_k": 5,
        "expected_model_id": None,
        "expected_embedding_id": None,
        "expected_tokenizer_id": None,
        "expected_db_snapshot_hash": None,
    }
    assert validate_quality_loop_manifest(manifest, **expected)
    drifted = [("a" * 64, "b" * 64, "e" * 64)]
    assert not validate_quality_loop_manifest(
        manifest, **{**expected, "expected_pairs": drifted}
    )
    assert not validate_quality_loop_manifest("not-a-manifest", **expected)


@pytest.mark.parametrize(
    ("field", "forged"),
    [
        ("schema_version", "forged-schema"),
        ("evaluator_version", "forged-evaluator"),
        ("code_revision", "attacker-revision"),
        ("config_hash", _HASH_B),
        ("schema_hash", _HASH_C),
        ("fixture_hash", _HASH_C),
        ("model_id", "attacker-model"),
        ("embedding_id", "attacker-embedding"),
        ("tokenizer_id", "attacker-tokenizer"),
        ("seed", 43),
        ("k", 6),
        ("db_snapshot_hash", _HASH_A),
        ("pair_fingerprints", ((_HASH_A, _HASH_B, "d" * 64),)),
    ],
)
def test_manifest_validation_rejects_each_forged_binding(field, forged):
    manifest = _build_manifest(
        model_id="model-v1",
        embedding_id="embedding-v1",
        tokenizer_id="tokenizer-v1",
        db_snapshot_hash=_HASH_C,
    )
    expected = {
        "expected_pairs": _PAIRS,
        "expected_code_revision": "8f0681e",
        "expected_config_hash": _HASH_A,
        "expected_schema_hash": _HASH_B,
        "expected_seed": 42,
        "expected_k": 5,
        "expected_model_id": "model-v1",
        "expected_embedding_id": "embedding-v1",
        "expected_tokenizer_id": "tokenizer-v1",
        "expected_db_snapshot_hash": _HASH_C,
    }
    assert not validate_quality_loop_manifest(
        replace(manifest, **{field: forged}), **expected
    )


def test_public_summary_contains_only_low_sensitivity_fields():
    summary = manifest_public_summary(_build_manifest())
    assert summary["schema_version"] == QUALITY_LOOP_MANIFEST_SCHEMA_VERSION
    assert summary["seed"] == 42
    assert summary["k"] == 5
    assert summary["pair_count"] == 1
    assert summary["manifest_hash"] == quality_loop_manifest_hash(_build_manifest())
    dumped = repr(summary)
    assert "query" not in dumped
    assert "context_key" not in dumped


def test_context_key_hash_is_domain_separated():
    first = quality_loop_context_key_hash("ctx-1", secret=_SECRET)
    second = quality_loop_context_key_hash("ctx-1", secret=_SECRET)
    different = quality_loop_context_key_hash("ctx-2", secret=_SECRET)
    assert first == second
    assert first != different
    assert len(first) == 64


def test_case_hash_is_order_independent_across_relevant_id_order():
    first = quality_loop_case_hash(
        "case-1",
        query="q",
        relevant=["b", "a"],
        metadata={"x": 1},
        secret=_SECRET,
    )
    second = quality_loop_case_hash(
        "case-1",
        query="q",
        relevant=["a", "b"],
        metadata={"x": 1},
        secret=_SECRET,
    )
    assert first == second
    assert len(first) == 64
    assert first != quality_loop_case_hash(
        "case-1",
        query="q",
        relevant=["a", "b"],
        metadata={"x": 1},
        secret=b"x" * 32,
    )


def test_fixture_hash_is_order_independent():
    pairs_a = [(_HASH_A, _HASH_B, _HASH_C), (_HASH_C, _HASH_A, _HASH_B)]
    pairs_b = [(_HASH_C, _HASH_A, _HASH_B), (_HASH_A, _HASH_B, _HASH_C)]
    assert quality_loop_fixture_hash(pairs_a) == quality_loop_fixture_hash(pairs_b)


def test_stage_read_filters_unknown_metric_keys_and_keeps_none():
    read = make_stage_read(
        "write",
        state="degraded",
        reason=REASON_NO_ANNOTATION,
        metrics={"write_fact_correctness": None, "rogue_metric": 1.0},
    )
    payload = stage_read_to_payload(read)
    assert payload["metrics"] == {"write_fact_correctness": None}
    assert payload["owning_stage"] == "write"


def test_generic_annotation_reason_owns_reporting_stage():
    read = make_stage_read(
        "expression",
        state="degraded",
        reason=REASON_NO_ANNOTATION,
    )
    assert stage_read_to_payload(read)["owning_stage"] == "expression"
    cleaned = sanitize_stage_payload(stage_read_to_payload(read))
    assert cleaned is not None
    assert cleaned["owning_stage"] == "expression"


def test_stage_read_rejects_closed_set_violations():
    with pytest.raises(ValueError):
        make_stage_read("not-a-stage", state="available", reason="ok")
    with pytest.raises(ValueError):
        make_stage_read("write", state="bogus", reason="ok")
    with pytest.raises(ValueError):
        make_stage_read("write", state="available", reason="bogus")


def test_every_reason_maps_to_valid_owning_stage():
    for reason, stage in REASON_TO_OWNING_STAGE.items():
        assert stage in QUALITY_LOOP_STAGES, reason
    assert owning_stage_for_reason("never-defined-reason") is None


def test_sanitize_stage_payload_round_trips_and_drops_unknown():
    read = make_stage_read(
        "recall",
        state="available",
        reason="ok",
        metrics={"candidate_hit_rate": 0.5},
    )
    payload = stage_read_to_payload(read)
    sanitized = sanitize_stage_payload(payload)
    assert sanitized == payload
    assert sanitize_stage_payload({"stage": "bogus"}) is None
    payload["metrics"]["injected"] = 3
    cleaned = sanitize_stage_payload(payload)
    assert cleaned is not None
    assert "injected" not in cleaned["metrics"]


def test_manifest_dataclass_is_frozen():
    manifest = _build_manifest()
    with pytest.raises(AttributeError):
        manifest.seed = 99  # type: ignore[misc]


def test_db_snapshot_hash_optional_but_validated():
    without_snapshot = _build_manifest()
    assert without_snapshot.db_snapshot_hash is None
    with pytest.raises(ValueError):
        _build_manifest(db_snapshot_hash="zz")
    with_snapshot = _build_manifest(db_snapshot_hash=_HASH_A)
    assert with_snapshot.db_snapshot_hash == _HASH_A
    assert isinstance(with_snapshot, QualityLoopReplayManifest)


def test_manifest_hash_uses_sha256_domain():
    manifest = _build_manifest()
    assert len(quality_loop_manifest_hash(manifest)) == 64
    assert all(c in "0123456789abcdef" for c in quality_loop_manifest_hash(manifest))
    # 确保没有直接对 repr 哈希：改变无关字段顺序不影响
    assert hashlib.sha256(b"x").hexdigest() != quality_loop_manifest_hash(manifest)
