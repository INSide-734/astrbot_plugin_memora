#!/usr/bin/env python3
"""运行合成效果门并写出脱敏 JSON 报告。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# 仓库根加入后导入运行时组件。
from effect_gate_synthetic import build_synthetic_scenario  # noqa: E402

from core.features.evaluation.application.effect_gate_runner import (  # noqa: E402
    run_effect_gate,
)
from core.features.evaluation.domain.effect_gate import EffectGatePolicy  # noqa: E402
from core.features.evaluation.infrastructure.evaluation_service import (  # noqa: E402
    EvaluationService,
)
from core.features.evaluation.infrastructure.report_store import (  # noqa: E402
    EvaluationReportStore,
)
from core.features.injection.application.router import (  # noqa: E402
    InjectionRoutingConfig,
    InjectionStrategyRouter,
)
from core.features.injection.application.selection import (  # noqa: E402
    metadata_has_user_evidence,
    select_candidates,
)
from core.features.injection.domain.models import (  # noqa: E402
    PresetName,
    RequestSignals,
    RoutingMode,
)

_DEFAULT_OUTPUT_DIR = REPO_ROOT / "artifacts" / "effect_gate"


def main() -> int:
    return asyncio.run(_main_async())


async def _main_async() -> int:
    args = _parse_args()
    output = Path(args.output) if args.output else _default_output()
    output.parent.mkdir(parents=True, exist_ok=True)
    freeze_dir = Path(args.freeze_dir) if args.freeze_dir else None
    scenario = await build_synthetic_scenario(frozen_dir=freeze_dir)
    try:
        engine, cases = scenario.engine, scenario.cases
        decision = _decision()
        policy = EffectGatePolicy(
            bootstrap_iterations=args.bootstrap_iterations,
            min_cases_per_category=args.min_cases_per_category,
            min_total_cases=args.min_total_cases,
            latency_margin_ms=args.latency_margin_ms,
            latency_margin_ratio=args.latency_margin_ratio,
        )
        manifest = _manifest(
            engine,
            cases,
            k=args.k,
            policy=policy,
            db_snapshot_hash=scenario.db_snapshot_hash,
            secret_dir=freeze_dir or output.parent,
            frozen_reused=scenario.reused_frozen_db,
        )
        placeholder_canary = {"checked_values": len(cases) * 2, "violations": []}

        result = await run_effect_gate(
            engine,
            cases,
            decision=decision,
            select_candidates=select_candidates,
            source_eligible=metadata_has_user_evidence,
            k=args.k,
            policy=policy,
            manifest=manifest,
            privacy_canary=placeholder_canary,
        )
        privacy_canary = _privacy_canary(cases, result.gate_report)
        if privacy_canary["violations"]:
            print(
                "privacy canary failed: "
                f"{len(privacy_canary['violations'])} value(s) leaked",
                file=sys.stderr,
            )
            return 1
        result.gate_report["privacy_canary"] = privacy_canary
        payload = {
            "schema_version": "effect-gate-v1",
            "data_source": "synthetic_real_sqlite_faiss",
            "manifest": manifest,
            "effect_gate": result.gate_report,
            "baseline": _variant_payload(
                result.baseline.to_evidence(), result.baseline.report
            ),
            "variants": {
                factor: _variant_payload(run.to_evidence(), run.report)
                for factor, run in result.variants.items()
            },
        }
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

        _print_summary(result.gate_report, output)
        return 0
    finally:
        await scenario.close()


def _decision() -> Any:
    config = InjectionRoutingConfig(
        mode=RoutingMode.MANUAL,
        manual_preset=PresetName.BALANCED,
    )
    return InjectionStrategyRouter().route_final(config, RequestSignals())


def _manifest(
    engine: Any,
    cases: list[Any],
    *,
    k: int,
    policy: EffectGatePolicy,
    db_snapshot_hash: str,
    secret_dir: Path,
    frozen_reused: bool,
) -> dict[str, Any]:
    fixture_payload = json.dumps(
        [
            {
                "case_id": case.case_id,
                "query": case.query,
                "relevant_doc_ids": sorted(case.relevant_doc_ids),
                "metadata": case.metadata,
            }
            for case in cases
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    dataset_hash = hashlib.sha256(fixture_payload).hexdigest()
    return {
        "data_source": "synthetic_real_sqlite_faiss",
        "code_revision": _code_revision(),
        "config_hash": _hash_json(engine.config),
        "schema_hash": _schema_hash(),
        "fixture_hmac": _fixture_hmac(fixture_payload, secret_dir),
        "dataset_hash": dataset_hash,
        "db_snapshot_hash": db_snapshot_hash,
        "frozen_db_reused": frozen_reused,
        "seed": 0,
        "k": k,
        "case_count": len(cases),
        "injection_config_hash": _hash_json({"mode": "manual", "preset": "balanced"}),
        "gate_policy_version": policy.policy_version,
        "gate_policy": policy.as_payload(),
    }


def _fixture_hmac(payload: bytes, secret_dir: Path) -> str:
    secret_dir.mkdir(parents=True, exist_ok=True)
    secret_path = secret_dir / ".effect_gate_hmac_secret"
    if secret_path.exists():
        secret = secret_path.read_bytes()
    else:
        secret = secrets.token_bytes(32)
        secret_path.write_bytes(secret)
        try:
            os.chmod(secret_path, 0o600)
        except OSError:
            pass
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def _privacy_canary(
    cases: list[Any],
    gate_report: dict[str, Any],
) -> dict[str, Any]:
    text = json.dumps(gate_report, ensure_ascii=False, sort_keys=True)
    checked = [value for case in cases for value in (case.case_id, case.query)]
    violations = [value for value in checked if value and value in text]
    return {"checked_values": len(checked), "violations": violations}


def _variant_payload(evidence: Any, report: Any | None) -> dict[str, Any]:
    payload = {
        "variant": evidence.variant,
        "status": evidence.status,
        "reason_code": evidence.reason_code,
        "execution": {
            "target_calls": evidence.target_calls,
            "target_applied": evidence.target_applied,
        },
    }
    if report is None:
        payload["summary"] = None
        payload["cases"] = []
        return payload
    payload["summary"] = EvaluationService._report_summary(report)
    payload["cases"] = [
        EvaluationReportStore.safe_case_payload(case) for case in report.cases
    ]
    return payload


def _print_summary(gate_report: dict[str, Any], output: Path) -> None:
    print(f"report: {output}")
    readiness = gate_report.get("readiness", {})
    print(
        "readiness:",
        "ready" if readiness.get("can_evaluate") else "insufficient",
        readiness.get("present_counts"),
    )
    for factor, payload in gate_report.get("factors", {}).items():
        print(
            f"{factor}: {payload.get('status')} -> "
            f"{payload.get('recommendation')} "
            f"({', '.join(payload.get('reason_codes', []))})"
        )
    print("recommended_config:", gate_report.get("recommended_config", {}))


def _hash_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _schema_hash() -> str:
    path = REPO_ROOT / "_conf_schema.json"
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return hashlib.sha256(b"schema-unavailable").hexdigest()


def _code_revision() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return completed.stdout.strip() or "unknown"


def _default_output() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return _DEFAULT_OUTPUT_DIR / f"effect-gate-{stamp}.json"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="输出 JSON 路径")
    parser.add_argument(
        "--freeze-dir",
        default=str(_DEFAULT_OUTPUT_DIR / "frozen"),
        help="冻结 DB/FAISS 复用目录；空字符串表示每次重新生成",
    )
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--bootstrap-iterations", type=int, default=2000)
    parser.add_argument("--min-cases-per-category", type=int, default=10)
    parser.add_argument("--min-total-cases", type=int, default=60)
    parser.add_argument("--latency-margin-ms", type=float, default=250.0)
    parser.add_argument("--latency-margin-ratio", type=float, default=1.0)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
