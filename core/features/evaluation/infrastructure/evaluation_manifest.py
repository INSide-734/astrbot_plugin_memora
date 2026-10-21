"""评测 manifest 的共享哈希、脱敏与代码绑定。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ...backup.infrastructure.snapshot import snapshot_sqlite
from ..domain.effect_gate import EffectGatePolicy

QUALITY_LOOP_CODE_PATHS: tuple[Path, ...] = (
    Path("core/features/evaluation/application/quality_loop.py"),
    Path("core/features/evaluation/domain/quality_loop_manifest.py"),
    Path("core/features/evaluation/domain/quality_loop_stages.py"),
    Path("core/features/evaluation/infrastructure/evaluation_service.py"),
)
EFFECT_GATE_CODE_PATHS: tuple[Path, ...] = (
    Path("core/features/evaluation/domain/effect_gate.py"),
    Path("core/features/evaluation/application/effect_gate_runner.py"),
    Path("core/features/evaluation/infrastructure/effect_gate_evaluation.py"),
    Path("core/features/evaluation/infrastructure/effect_gate_payloads.py"),
    Path("core/features/evaluation/infrastructure/evaluation_manifest.py"),
)
_EFFECT_GATE_HMAC_DOMAIN = b"effect-gate-fixtures-v1"


def _repository_root() -> Path:
    """返回仓库根目录；调用方只依赖源码文件的相对位置。"""

    return Path(__file__).resolve().parents[4]


def redact_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """递归隐藏配置中的密钥类字段。"""

    redacted: dict[str, Any] = {}
    for key, value in config.items():
        key_text = str(key)
        if any(
            token in key_text.lower()
            for token in ("key", "token", "secret", "password")
        ):
            redacted[key_text] = "<已隐藏>"
        elif isinstance(value, Mapping):
            redacted[key_text] = redact_config(value)
        else:
            redacted[key_text] = value
    return redacted


def configuration_hash(engine: Any) -> str:
    """计算匿名配置摘要，不将配置原文写入评测报告。"""

    config = getattr(engine, "config", {})
    if not isinstance(config, Mapping):
        config = {"config_type": type(config).__name__}
    encoded = json.dumps(
        redact_config(dict(config)),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def schema_hash() -> str:
    """读取插件 schema 的哈希，不把 schema 正文写入报告。"""

    schema_path = _repository_root() / "_conf_schema.json"
    try:
        return hashlib.sha256(schema_path.read_bytes()).hexdigest()
    except (OSError, ValueError):
        return hashlib.sha256(b"quality-loop-schema-unavailable").hexdigest()


def database_snapshot_hash_sync(engine: Any) -> str | None:
    """同步执行 SQLite snapshot；调用方不得在事件循环直接调用。"""

    try:
        database_path = getattr(engine, "db_path", None)
        if not isinstance(database_path, (str, Path)):
            return None
        with tempfile.TemporaryDirectory(prefix="memora-eval-") as directory:
            result = snapshot_sqlite(
                Path(database_path), Path(directory) / "canonical.db"
            )
            return result.sha256
    except Exception:
        return None


async def database_snapshot_hash(engine: Any) -> str | None:
    """在线程池中对 canonical SQLite 做临时 backup snapshot 并返回哈希。"""

    return await asyncio.to_thread(database_snapshot_hash_sync, engine)


def evaluator_code_revision(paths: Sequence[Path]) -> str:
    """按给定源码路径计算稳定的低敏代码绑定。"""

    root = _repository_root()
    digest = hashlib.sha256()
    for relative_path in paths:
        path = root / relative_path
        digest.update(path.name.encode("ascii"))
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"evaluation-source-unavailable")
    return digest.hexdigest()


def hash_json(value: Any) -> str:
    """返回稳定 JSON 的 SHA-256；不把原值写入报告。"""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def fixture_hmac(payload: bytes, secret: bytes) -> str:
    """对夹具 payload 做带 domain prefix 的 HMAC-SHA256。"""

    return hmac.new(
        secret,
        _EFFECT_GATE_HMAC_DOMAIN + payload,
        hashlib.sha256,
    ).hexdigest()


def canonical_fixture_payload(cases: Sequence[Any]) -> bytes:
    """构造与 CLI 同字段的夹具 canonical JSON。"""

    encoded = json.dumps(
        [
            {
                "case_id": str(getattr(case, "case_id", "")),
                "query": str(getattr(case, "query", "")),
                "relevant_doc_ids": sorted(
                    str(item) for item in (getattr(case, "relevant_doc_ids", ()) or ())
                ),
                "metadata": getattr(case, "metadata", {}) or {},
            }
            for case in cases
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return encoded


def build_effect_gate_manifest(
    engine: Any,
    cases: Sequence[Any],
    *,
    k: int,
    policy: EffectGatePolicy,
    fixture_secret: bytes,
    db_snapshot_hash: str | None,
) -> dict[str, Any]:
    """构造服务端绑定、可复核且不含原始夹具的 gate manifest。"""

    payload = canonical_fixture_payload(cases)
    return {
        "data_source": "plugin_evaluation_datasets",
        "code_revision": evaluator_code_revision(EFFECT_GATE_CODE_PATHS),
        "config_hash": configuration_hash(engine),
        "schema_hash": schema_hash(),
        "fixture_hmac": fixture_hmac(payload, fixture_secret),
        "db_snapshot_hash": db_snapshot_hash,
        "seed": 0,
        "k": k,
        "case_count": len(cases),
        "injection_config_hash": hash_json({"mode": "manual", "preset": "balanced"}),
        "gate_policy_version": policy.policy_version,
        "gate_policy": policy.as_payload(),
    }


__all__ = [
    "EFFECT_GATE_CODE_PATHS",
    "QUALITY_LOOP_CODE_PATHS",
    "build_effect_gate_manifest",
    "canonical_fixture_payload",
    "configuration_hash",
    "database_snapshot_hash",
    "database_snapshot_hash_sync",
    "evaluator_code_revision",
    "fixture_hmac",
    "hash_json",
    "redact_config",
    "schema_hash",
]
