"""离线 topic 候选证据门 benchmark：K 网格 × 匿名合成记录。"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.features.evaluation.application.topic_candidate_evidence import (  # noqa: E402
    TOP_K_VALUES,
    CandidateReplayRecord,
    evaluate_evidence_gate,
)
from core.features.evaluation.application.topic_candidate_replay import (  # noqa: E402
    ReplayConfig,
    batch_replay,
    create_synthetic_windows,
    create_temp_catalog,
)
from core.features.memory.infrastructure.topic_catalog_store import (  # noqa: E402
    TopicCatalogStore,
)
from core.features.recall.processors.conversation_formatter import (  # noqa: E402
    ConversationFormatter,
)
from core.features.recall.processors.text_processor import TextProcessor  # noqa: E402
from core.features.reflection.application.topic_candidate_selector import (  # noqa: E402
    TopicCandidateSelector,
)

_BUCKET_TOPIC_COUNTS = {
    "tiny": 4,
    "small": 16,
    "medium": 64,
    "large": 128,
    "xlarge": 512,
    "huge": 1024,
}
_CHAT_TYPES = ("private", "group")


@dataclass(frozen=True)
class BenchmarkConfig:
    """离线 benchmark 的受控 catalog 与证据门配置。"""

    oracle_max_topics: int
    oracle_max_prompt_tokens: int
    misreuse_max_by_bucket: dict[str, float]


def _parse_misreuse_buckets(raw: str) -> dict[str, float]:
    """解析并校验以百分点表示的各规模桶误复用上限。"""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("misreuse-max-by-bucket 必须是 JSON 对象") from exc
    if not isinstance(data, dict):
        raise ValueError("misreuse-max-by-bucket 必须是 JSON 对象")

    allowed = set(_BUCKET_TOPIC_COUNTS)
    parsed: dict[str, float] = {}
    for bucket, raw_limit in data.items():
        if bucket not in allowed:
            raise ValueError(f"未知规模桶: {bucket}")
        if (
            isinstance(raw_limit, bool)
            or not isinstance(raw_limit, (int, float))
            or not 0.0 <= float(raw_limit) <= 100.0
        ):
            raise ValueError(f"{bucket} 的误复用上限必须是 0 到 100 的百分点")
        parsed[bucket] = float(raw_limit)
    return parsed


def _validate_args(args: argparse.Namespace) -> None:
    """校验必填参数；缺失时输出并退出 2。"""
    missing = []
    if not args.output:
        missing.append("--output")
    if args.oracle_max_topics is None:
        missing.append("--oracle-max-topics")
    if args.oracle_max_prompt_tokens is None:
        missing.append("--oracle-max-prompt-tokens")
    if not args.misreuse_max_by_bucket:
        missing.append("--misreuse-max-by-bucket")

    if missing:
        print(
            f"error: missing required arguments: {', '.join(missing)}", file=sys.stderr
        )
        sys.exit(2)

    # strict full 的查询本身无上限，但受控合成 catalog 至少需要一个 topic。
    assert args.oracle_max_topics is not None
    assert args.oracle_max_prompt_tokens is not None
    if args.oracle_max_topics < 1:
        print("error: --oracle-max-topics must be >= 1", file=sys.stderr)
        sys.exit(2)
    if args.oracle_max_prompt_tokens < 1:
        print("error: --oracle-max-prompt-tokens must be >= 1", file=sys.stderr)
        sys.exit(2)
    if args.windows_per_bucket < 1:
        print("error: --windows-per-bucket must be >= 1", file=sys.stderr)
        sys.exit(2)
    if args.negative_windows_per_bucket < 0:
        print("error: --negative-windows-per-bucket must be >= 0", file=sys.stderr)
        sys.exit(2)

    for name, val in [
        ("min-quality-n", args.min_quality_n),
        ("min-token-n", args.min_token_n),
        ("min-latency-n", args.min_latency_n),
    ]:
        if val < 1:
            print(f"error: --{name} must be >= 1", file=sys.stderr)
            sys.exit(2)


def _topic_label(bucket: str, chat_type: str, index: int) -> str:
    """为受控合成 catalog 生成稳定且不含用户数据的话题标签。"""
    return f"topic {bucket} {chat_type} {index}"


def _scope_key(bucket: str, chat_type: str) -> str:
    """生成只在临时 catalog 中使用的合成 scope 标识。"""
    return f"benchmark-{bucket}-{chat_type}"


def _bucket_topic_count(bucket: str, oracle_max_topics: int) -> int:
    """在 CLI 给定的合成 catalog 上限内返回预注册规模桶的 topic 数。"""
    return min(_BUCKET_TOPIC_COUNTS[bucket], oracle_max_topics)


def _build_synthetic_documents(config: BenchmarkConfig) -> list[dict[str, Any]]:
    """构造受控临时 catalog 的 canonical 文档，覆盖所有桶和 chat type。"""
    documents: list[dict[str, Any]] = []
    document_id = 1
    for bucket in _BUCKET_TOPIC_COUNTS:
        for chat_type in _CHAT_TYPES:
            privacy_level = "shared" if chat_type == "private" else "public"
            scope_key = _scope_key(bucket, chat_type)
            for index in range(_bucket_topic_count(bucket, config.oracle_max_topics)):
                label = _topic_label(bucket, chat_type, index)
                documents.append(
                    {
                        "id": document_id,
                        "text": f"受控合成历史: {label}",
                        "metadata": {
                            "topics": [label],
                            "scope_key": scope_key,
                            "chat_type": chat_type,
                            "privacy_level": privacy_level,
                            "resolver_revision": "benchmark-v1",
                            "source_provenance_complete": True,
                        },
                        "updated_at": f"benchmark-r{document_id}",
                    }
                )
                document_id += 1
            documents.append(
                {
                    "id": document_id,
                    "text": "不完整来源的合成负向文档",
                    "metadata": {
                        "topics": [f"blocked {bucket} {chat_type}"],
                        "scope_key": scope_key,
                        "chat_type": chat_type,
                        "privacy_level": privacy_level,
                        "resolver_revision": "benchmark-v1",
                        "source_provenance_complete": False,
                    },
                }
            )
            document_id += 1
    return documents


def _build_synthetic_scenarios(
    config: BenchmarkConfig,
    windows_per_bucket: int,
    negative_windows_per_bucket: int,
) -> list[dict[str, Any]]:
    """构造同一 catalog 上的正常和安全负向回放窗口。"""
    scenarios: list[dict[str, Any]] = []
    for bucket in _BUCKET_TOPIC_COUNTS:
        for chat_type in _CHAT_TYPES:
            privacy_level = "shared" if chat_type == "private" else "public"
            label = _topic_label(bucket, chat_type, 0)
            common = {
                "bucket": TopicCandidateSelector._map_topic_count_to_bucket(
                    _bucket_topic_count(bucket, config.oracle_max_topics)
                ),
                "chat_type": chat_type,
                "scope_key": _scope_key(bucket, chat_type),
                "privacy_level": privacy_level,
                "resolver_revision": "benchmark-v1",
                "semantic_clusters": [label],
                "nonsemantic_occurrences": [],
                "fragmentation_count": 0,
                "messages": [{"role": "user", "content": f"回顾 {label}"}],
            }
            for index in range(windows_per_bucket):
                scenarios.append(
                    {
                        **common,
                        "negative_topics": [],
                        "messages": [
                            {"role": "user", "content": f"窗口 {index}: 回顾 {label}"}
                        ],
                    }
                )
            for index in range(negative_windows_per_bucket):
                scenarios.append(
                    {
                        **common,
                        "negative_topics": [f"blocked {bucket} {chat_type}"],
                        "messages": [
                            {
                                "role": "user",
                                "content": f"负向窗口 {index}: 回顾 {label} blocked {bucket} {chat_type}",
                            }
                        ],
                    }
                )
    return scenarios


def _make_replay_config(
    mode: Literal["off", "observe", "full", "top_k"],
    fixed_k: int,
    *,
    max_full_topics: int,
    max_full_prompt_tokens: int,
) -> ReplayConfig:
    """生成一个由离线 replay adapter 消费的固定模式配置。"""
    return ReplayConfig(
        mode=mode,
        activation_threshold=3,
        fixed_k=fixed_k,
        max_full_topics=max_full_topics,
        max_full_prompt_tokens=max_full_prompt_tokens,
        max_query_chars=2000,
        overfetch_factor=3,
        metrics_retention_days=30,
    )


def _build_replay_configs(config: BenchmarkConfig) -> dict[str, ReplayConfig]:
    """生成 off、严格 full oracle 和全部预注册 K 的回放配置。"""
    max_full_topics = max(min(config.oracle_max_topics, 50), max(TOP_K_VALUES))
    max_full_prompt_tokens = min(max(config.oracle_max_prompt_tokens, 50), 2000)
    configs = {
        "off": _make_replay_config(
            "off",
            TOP_K_VALUES[0],
            max_full_topics=max_full_topics,
            max_full_prompt_tokens=max_full_prompt_tokens,
        ),
        "strict_full": _make_replay_config(
            "full",
            TOP_K_VALUES[0],
            max_full_topics=max_full_topics,
            max_full_prompt_tokens=max_full_prompt_tokens,
        ),
    }
    configs.update(
        {
            f"top_k_{k}": _make_replay_config(
                "top_k",
                k,
                max_full_topics=max_full_topics,
                max_full_prompt_tokens=max_full_prompt_tokens,
            )
            for k in TOP_K_VALUES
        }
    )
    return configs


async def _run_paired_replay(
    config: BenchmarkConfig,
    windows_per_bucket: int,
    negative_windows_per_bucket: int,
) -> tuple[list[CandidateReplayRecord], int, dict[str, int], dict[str, dict[str, int]]]:
    """在临时 catalog 中实际执行 selector 回放并汇总可配对的匿名记录。"""
    catalog_path, db = await create_temp_catalog(_build_synthetic_documents(config))
    configs = _build_replay_configs(config)
    try:
        store = TopicCatalogStore(db)
        selector = TopicCandidateSelector(
            store, TextProcessor(), ConversationFormatter()
        )
        cases = await batch_replay(
            create_synthetic_windows(
                _build_synthetic_scenarios(
                    config, windows_per_bucket, negative_windows_per_bucket
                )
            ),
            selector,
            configs,
            catalog_store=store,
        )
        records = [case.record for case in cases if case.record is not None]
        variants_by_case: dict[str, set[str]] = {}
        variant_record_counts = {variant: 0 for variant in configs}
        variant_status_counts = {variant: {} for variant in configs}
        for case in cases:
            statuses = variant_status_counts[case.mode]
            statuses[case.execution_status] = statuses.get(case.execution_status, 0) + 1
            if case.record is None:
                continue
            if case.execution_status == "success":
                variants_by_case.setdefault(case.case_hash, set()).add(case.mode)
            variant_record_counts[case.mode] += 1
        required_variants = {"strict_full"} | {f"top_k_{k}" for k in TOP_K_VALUES}
        paired_case_count = sum(
            required_variants.issubset(variants)
            for variants in variants_by_case.values()
        )
        return records, paired_case_count, variant_record_counts, variant_status_counts
    finally:
        await db.close()
        shutil.rmtree(catalog_path.parent, ignore_errors=True)


def _run_benchmark(
    records: list[CandidateReplayRecord],
    config: BenchmarkConfig,
    min_quality_n: int,
    min_token_n: int,
    min_latency_n: int,
    paired_case_count: int,
    variant_record_counts: dict[str, int],
    variant_status_counts: dict[str, dict[str, int]],
) -> dict[str, Any]:
    """运行证据门并输出不含标签、scope 或窗口正文的聚合报告。"""
    report = evaluate_evidence_gate(
        records=records,
        misreuse_max_by_bucket=config.misreuse_max_by_bucket,
        min_quality_n=min_quality_n,
        min_token_n=min_token_n,
        min_latency_n=min_latency_n,
    )
    return {
        **asdict(report),
        "config": {
            "oracle_max_topics": config.oracle_max_topics,
            "oracle_max_prompt_tokens": config.oracle_max_prompt_tokens,
            "misreuse_max_by_bucket": config.misreuse_max_by_bucket,
        },
        "total_records": len(records),
        "replay_execution": "actual_selector_replay",
        "paired_case_count": paired_case_count,
        "variant_record_counts": variant_record_counts,
        "variant_status_counts": variant_status_counts,
        "selector_limits": asdict(_build_replay_configs(config)["top_k_24"]),
        # 合成输入验证真实回放管道，仍不能替代人工标注的发布证据。
        "pipeline_validation_only": True,
    }


def main() -> int:
    """CLI 入口；返回 0 成功、2 参数错误。"""
    parser = argparse.ArgumentParser(
        description="离线 topic 候选证据门 benchmark：K 网格 × 匿名合成记录",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法：
  python scripts/benchmark_topic_candidates.py \\
    --output reports/topic-candidates.json \\
    --oracle-max-topics 50 \\
    --oracle-max-prompt-tokens 8000 \\
    --misreuse-max-by-bucket '{"tiny":5,"small":10,"medium":15,"large":20,"huge":25}'

注意：
  - 该 benchmark 使用临时 SQLite，不读取生产数据库
  - 不写入 ConfigManager，不修改生产配置
  - --misreuse-max-by-bucket 必须是 JSON 对象，键为 scale bucket 名
""",
    )

    parser.add_argument(
        "--output",
        type=str,
        required=True,
        help="JSON 报告输出路径（必填）",
    )
    parser.add_argument(
        "--oracle-max-topics",
        type=int,
        required=True,
        help="受控合成 catalog 的单 scope topic 上限（必填）",
    )
    parser.add_argument(
        "--oracle-max-prompt-tokens",
        type=int,
        required=True,
        help="非 strict_full 回放的 prompt token 上限（必填）",
    )
    parser.add_argument(
        "--misreuse-max-by-bucket",
        type=str,
        required=True,
        help='误复用上限 JSON 对象，如 {"tiny":5,"small":10}（必填）',
    )
    parser.add_argument(
        "--windows-per-bucket",
        type=int,
        default=200,
        help="每个 bucket × chat type 的正常窗口数（默认 200）",
    )
    parser.add_argument(
        "--negative-windows-per-bucket",
        type=int,
        default=100,
        help="每个 bucket × chat type 的安全负向窗口数（默认 100）",
    )
    parser.add_argument(
        "--min-quality-n",
        type=int,
        default=200,
        help="质量指标最小样本数（默认 200）",
    )
    parser.add_argument(
        "--min-token-n",
        type=int,
        default=200,
        help="token 指标最小样本数（默认 200）",
    )
    parser.add_argument(
        "--min-latency-n",
        type=int,
        default=200,
        help="延迟指标最小样本数（默认 200）",
    )

    args = parser.parse_args()
    _validate_args(args)

    try:
        misreuse_buckets = _parse_misreuse_buckets(args.misreuse_max_by_bucket)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    config = BenchmarkConfig(
        oracle_max_topics=args.oracle_max_topics,
        oracle_max_prompt_tokens=args.oracle_max_prompt_tokens,
        misreuse_max_by_bucket=misreuse_buckets,
    )

    records, paired_case_count, variant_record_counts, variant_status_counts = (
        asyncio.run(
            _run_paired_replay(
                config,
                args.windows_per_bucket,
                args.negative_windows_per_bucket,
            )
        )
    )
    result = _run_benchmark(
        records=records,
        config=config,
        min_quality_n=args.min_quality_n,
        min_token_n=args.min_token_n,
        min_latency_n=args.min_latency_n,
        paired_case_count=paired_case_count,
        variant_record_counts=variant_record_counts,
        variant_status_counts=variant_status_counts,
    )

    output_path = Path(args.output)
    output_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    # 护栏失败以退出码 1 表达（scripts/AGENTS：1 一般校验/阈值失败；
    # 0 仅表示管道执行完成，不代表证据门通过）
    if result.get("recommended_k") is None:
        print(
            "Benchmark complete. Report written to "
            f"{output_path} (gate rejected: recommended_k is None)",
        )
        return 1
    print(f"Benchmark complete. Report written to {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
