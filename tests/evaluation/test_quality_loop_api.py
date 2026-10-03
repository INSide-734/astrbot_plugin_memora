"""Dedicated quality-loop key persistence and request boundary regressions."""

from __future__ import annotations

import os
import stat
from typing import Any

import pytest

from core.features.evaluation.infrastructure.quality_loop_secret import (
    load_or_create_quality_loop_secret,
)
from core.platform.transport.page_api.evaluation_api import EvaluationApiMixin


def test_quality_loop_key_is_persistent_dedicated_and_private(tmp_path):
    first = load_or_create_quality_loop_secret(tmp_path)
    second = load_or_create_quality_loop_secret(tmp_path)
    key_path = tmp_path / "evaluation_quality_loop.hmac.key"

    assert len(first) == 32
    assert second == first
    assert key_path.read_bytes() == first
    assert not (tmp_path / ".secret_key").exists()
    if os.name != "nt":
        assert stat.S_IMODE(key_path.stat().st_mode) == 0o600


def test_quality_loop_key_rejects_corrupt_existing_sidecar(tmp_path):
    key_path = tmp_path / "evaluation_quality_loop.hmac.key"
    key_path.write_bytes(b"too-short")
    if os.name != "nt":
        key_path.chmod(0o600)

    with pytest.raises(RuntimeError, match="quality_loop_hmac_secret_invalid"):
        load_or_create_quality_loop_secret(tmp_path)


def test_api_parser_discards_client_manifest_binding_claims():
    parsed = EvaluationApiMixin._parse_quality_loop_request(
        {
            "seed": 41,
            "schema_version": "client-schema",
            "evaluator_version": "client-evaluator",
            "code_revision": "client-revision",
            "config_hash": "client-config",
            "schema_hash": "client-schema-hash",
            "fixture_hash": "client-fixture",
            "model_id": "client-model",
            "embedding_id": "client-embedding",
            "tokenizer_id": "client-tokenizer",
            "db_snapshot_hash": "client-snapshot",
            "k": 20,
            "pair_fingerprints": ["client-pair"],
            "manifest_hash": "client-manifest",
        }
    )

    assert parsed == {}


@pytest.mark.asyncio
async def test_api_response_strips_unsafe_loop_fields_and_ignores_nested_seed_claim(
    tmp_path,
):
    from types import SimpleNamespace

    class _Service:
        def __init__(self):
            self.received_secret: bytes | None = None
            self.received_request: dict[str, Any] | None = None

        async def initialize(self):
            return None

        def list_datasets(self):
            return {"datasets": [{"name": "fixture"}]}

        async def run_evaluation(self, **kwargs):
            self.received_request = kwargs
            return {"quality_loop": unsafe_loop}

    unsafe_loop = {
        "manifest": None,
        "stages": [
            {
                "stage": stage,
                "state": "degraded",
                "reason": "no_annotation",
                "metrics": {},
            }
            for stage in (
                "write",
                "source",
                "recall",
                "injection",
                "lifecycle",
                "expression",
            )
        ],
        "pairs": {
            "total_pairs": 0,
            "should_use_hit_rate": None,
            "should_silence_correct_rate": None,
        },
        "pair_outcomes": [],
        "query": "API-QUERY-CANARY-82",
    }
    service = _Service()

    class _Api(EvaluationApiMixin):
        def __init__(self):
            self.plugin = SimpleNamespace(
                initializer=SimpleNamespace(data_dir=tmp_path)
            )

        def _get_evaluation_engine(self):
            return object()

        def _build_evaluation_service(
            self,
            *,
            engine: Any = None,
            quality_loop_secret: bytes | None = None,
            **_: Any,
        ) -> Any:
            service.received_secret = quality_loop_secret
            return service

        @staticmethod
        async def _load_current_memory_cases(engine: Any):
            return []

    response = await _Api().run_evaluation_payload(
        {
            "datasets": ["fixture"],
            "quality_loop": {"seed": 900, "model_id": "client-model-claim"},
            "quality_loop_seed": 2,
        }
    )

    serialized = str(response)
    assert response["status"] == "ok"
    assert service.received_request is not None
    assert "API-QUERY-CANARY-82" not in serialized
    assert service.received_request["quality_loop_seed"] == 2
    assert service.received_request["quality_loop"] == {}
    assert isinstance(service.received_secret, bytes)
    assert len(service.received_secret) == 32
