"""质量闭环重放 manifest 的领域契约与校验。

冻结一次质量闭环运行的全部外部输入标识（代码/配置/schema/fixture、模型、
Embedding、tokenizer、随机种子与数据库快照哈希），并以顺序无关的内容哈希
绑定实际用例。manifest 只保存低敏标识（短 ASCII code 与 SHA-256 哈希），
不进入 query、正文、身份、scope/privacy、revision 或 canonical ID。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

QUALITY_LOOP_MANIFEST_SCHEMA_VERSION = "quality-loop-replay-v1"
QUALITY_LOOP_EVALUATOR_VERSION = "quality-loop-evaluator-v1"


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    """按排序 JSON 计算域分离 SHA-256；与 learning evidence 同一口径。"""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _short_code(value: Any) -> bool:
    """仅接受短 ASCII 标识，阻止任意原文进入 manifest。"""
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and value.isascii()
        and all(char.isalnum() or char in "-_.:" for char in value)
    )


def _sha256_text(value: Any) -> bool:
    """判断值是否为规范小写 SHA-256 十六进制文本。"""
    return (
        isinstance(value, str)
        and len(value) == 64
        and value == value.lower()
        and all(char in "0123456789abcdef" for char in value)
    )


def _optional_short_code(value: Any) -> bool:
    """可选短标识：显式 ``None`` 表示未配置，不允许空字符串。"""
    return value is None or _short_code(value)


def sanitize_quality_loop_manifest_summary(value: Any) -> dict[str, Any] | None:
    """读取旧报告时校验 manifest 摘要的闭集格式与低敏标识。"""
    if not isinstance(value, Mapping):
        return None
    if value.get("schema_version") != QUALITY_LOOP_MANIFEST_SCHEMA_VERSION:
        return None
    if value.get("evaluator_version") != QUALITY_LOOP_EVALUATOR_VERSION:
        return None

    code_revision = value.get("code_revision")
    hash_keys = ("config_hash", "schema_hash", "fixture_hash", "manifest_hash")
    if not _short_code(code_revision) or not all(
        _sha256_text(value.get(key)) for key in hash_keys
    ):
        return None

    seed = value.get("seed")
    pair_count = value.get("pair_count")
    k = value.get("k")
    if (
        isinstance(seed, bool)
        or not isinstance(seed, int)
        or seed < 0
        or isinstance(pair_count, bool)
        or not isinstance(pair_count, int)
        or pair_count < 0
        or isinstance(k, bool)
        or not isinstance(k, int)
        or not 1 <= k <= 20
    ):
        return None
    db_snapshot_hash = value.get("db_snapshot_hash")
    if db_snapshot_hash is not None and not _sha256_text(db_snapshot_hash):
        return None
    optional_ids = {
        key: value.get(key) for key in ("model_id", "embedding_id", "tokenizer_id")
    }
    if not all(_optional_short_code(item) for item in optional_ids.values()):
        return None
    return {
        "schema_version": QUALITY_LOOP_MANIFEST_SCHEMA_VERSION,
        "evaluator_version": QUALITY_LOOP_EVALUATOR_VERSION,
        "code_revision": code_revision,
        **{key: value.get(key) for key in hash_keys},
        **optional_ids,
        "seed": seed,
        "k": k,
        "db_snapshot_hash": db_snapshot_hash,
        "pair_count": pair_count,
    }


@dataclass(frozen=True, slots=True)
class QualityLoopReplayManifest:
    """一次质量闭环运行的冻结输入标识与配对指纹。"""

    schema_version: str
    evaluator_version: str
    code_revision: str
    config_hash: str
    schema_hash: str
    fixture_hash: str
    model_id: str | None
    embedding_id: str | None
    tokenizer_id: str | None
    seed: int
    k: int
    db_snapshot_hash: str | None
    pair_fingerprints: tuple[tuple[str, str, str], ...]


def quality_loop_context_key_hash(context_key: str, *, secret: bytes) -> str:
    """使用安装级 HMAC 隐藏配对上下文键，报告不保存原文键。"""
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise ValueError("quality_loop_context_key_secret_invalid")
    payload = f"quality-loop-context-v1\0{str(context_key).strip()}".encode("utf-8")
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def quality_loop_case_hash(
    case_id: str,
    *,
    query: str,
    relevant: Sequence[Any],
    metadata: Mapping[str, Any],
    secret: bytes,
) -> str:
    """使用安装密钥生成带域分离的用例 HMAC 指纹。"""
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise ValueError("quality_loop_context_key_secret_invalid")
    payload = json.dumps(
        {
            "case_id": str(case_id),
            "query": str(query),
            "relevant_doc_ids": sorted(str(item) for item in relevant),
            "metadata": dict(metadata),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(
        secret,
        b"quality-loop-case-v1\0" + payload,
        hashlib.sha256,
    ).hexdigest()


def build_quality_loop_replay_manifest(
    *,
    code_revision: str,
    config_hash: str,
    schema_hash: str,
    seed: int,
    pair_fingerprints: Sequence[tuple[str, str, str]],
    k: int = 5,
    model_id: str | None = None,
    embedding_id: str | None = None,
    tokenizer_id: str | None = None,
    db_snapshot_hash: str | None = None,
) -> QualityLoopReplayManifest:
    """构建冻结 manifest；非法标识或指纹结构直接拒绝。"""

    if not _short_code(code_revision):
        raise ValueError("quality_loop_code_revision_invalid")
    if not _sha256_text(config_hash) or not _sha256_text(schema_hash):
        raise ValueError("quality_loop_hash_invalid")
    if not _optional_short_code(model_id) or not _optional_short_code(embedding_id):
        raise ValueError("quality_loop_model_identifier_invalid")
    if not _optional_short_code(tokenizer_id):
        raise ValueError("quality_loop_model_identifier_invalid")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("quality_loop_seed_invalid")
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 20:
        raise ValueError("quality_loop_k_invalid")
    if db_snapshot_hash is not None and not _sha256_text(db_snapshot_hash):
        raise ValueError("quality_loop_snapshot_hash_invalid")
    fingerprints = tuple(
        (str(item[0]), str(item[1]), str(item[2])) for item in pair_fingerprints
    )
    if not fingerprints or not all(
        len(item) == 3 and all(_sha256_text(part) for part in item)
        for item in fingerprints
    ):
        raise ValueError("quality_loop_pair_fingerprints_invalid")
    return QualityLoopReplayManifest(
        schema_version=QUALITY_LOOP_MANIFEST_SCHEMA_VERSION,
        evaluator_version=QUALITY_LOOP_EVALUATOR_VERSION,
        code_revision=code_revision,
        config_hash=config_hash,
        schema_hash=schema_hash,
        fixture_hash=quality_loop_fixture_hash(fingerprints),
        model_id=model_id,
        embedding_id=embedding_id,
        tokenizer_id=tokenizer_id,
        seed=seed,
        k=k,
        db_snapshot_hash=db_snapshot_hash,
        pair_fingerprints=fingerprints,
    )


def quality_loop_fixture_hash(
    fingerprints: Sequence[tuple[str, str, str]],
) -> str:
    """按配对指纹计算顺序无关 fixture 内容哈希。"""
    ordered = sorted(tuple(item) for item in fingerprints)
    return _canonical_hash({"namespace": "quality_loop_fixture", "pairs": ordered})


def quality_loop_manifest_hash(manifest: QualityLoopReplayManifest) -> str:
    """对完整 manifest 计算内容哈希；pair_fingerprints 排序后参与。"""
    payload = asdict(manifest)
    payload["pair_fingerprints"] = sorted(payload["pair_fingerprints"])
    return _canonical_hash(payload)


def validate_quality_loop_manifest(
    manifest: object,
    *,
    expected_pairs: Sequence[tuple[str, str, str]],
    expected_code_revision: str,
    expected_config_hash: str,
    expected_schema_hash: str,
    expected_seed: int,
    expected_k: int,
    expected_model_id: str | None,
    expected_embedding_id: str | None,
    expected_tokenizer_id: str | None,
    expected_db_snapshot_hash: str | None,
) -> bool:
    """校验 manifest 结构，并与实际配对指纹精确比对。"""

    if not isinstance(manifest, QualityLoopReplayManifest):
        return False
    if (
        manifest.schema_version != QUALITY_LOOP_MANIFEST_SCHEMA_VERSION
        or manifest.evaluator_version != QUALITY_LOOP_EVALUATOR_VERSION
    ):
        return False
    if not _short_code(manifest.code_revision):
        return False
    if not _sha256_text(manifest.config_hash) or not _sha256_text(manifest.schema_hash):
        return False
    if not _optional_short_code(manifest.model_id):
        return False
    if not _optional_short_code(manifest.embedding_id) or not _optional_short_code(
        manifest.tokenizer_id
    ):
        return False
    if isinstance(manifest.seed, bool) or not isinstance(manifest.seed, int):
        return False
    if (
        isinstance(manifest.k, bool)
        or not isinstance(manifest.k, int)
        or not 1 <= manifest.k <= 20
    ):
        return False
    if manifest.db_snapshot_hash is not None and not _sha256_text(
        manifest.db_snapshot_hash
    ):
        return False
    try:
        expected = build_quality_loop_replay_manifest(
            code_revision=expected_code_revision,
            config_hash=expected_config_hash,
            schema_hash=expected_schema_hash,
            seed=expected_seed,
            pair_fingerprints=expected_pairs,
            k=expected_k,
            model_id=expected_model_id,
            embedding_id=expected_embedding_id,
            tokenizer_id=expected_tokenizer_id,
            db_snapshot_hash=expected_db_snapshot_hash,
        )
    except ValueError:
        return False
    return manifest == expected


def manifest_public_summary(
    manifest: QualityLoopReplayManifest,
) -> dict[str, Any]:
    """返回报告/API 可安全携带的 manifest 摘要（仅哈希与短标识）。"""
    return {
        "schema_version": manifest.schema_version,
        "evaluator_version": manifest.evaluator_version,
        "code_revision": manifest.code_revision,
        "config_hash": manifest.config_hash,
        "schema_hash": manifest.schema_hash,
        "fixture_hash": manifest.fixture_hash,
        "manifest_hash": quality_loop_manifest_hash(manifest),
        "model_id": manifest.model_id,
        "embedding_id": manifest.embedding_id,
        "tokenizer_id": manifest.tokenizer_id,
        "seed": manifest.seed,
        "k": manifest.k,
        "db_snapshot_hash": manifest.db_snapshot_hash,
        "pair_count": len(manifest.pair_fingerprints),
    }


__all__ = [
    "QUALITY_LOOP_EVALUATOR_VERSION",
    "QUALITY_LOOP_MANIFEST_SCHEMA_VERSION",
    "QualityLoopReplayManifest",
    "build_quality_loop_replay_manifest",
    "manifest_public_summary",
    "sanitize_quality_loop_manifest_summary",
    "quality_loop_case_hash",
    "quality_loop_context_key_hash",
    "quality_loop_fixture_hash",
    "quality_loop_manifest_hash",
    "validate_quality_loop_manifest",
]
