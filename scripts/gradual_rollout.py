"""话题候选灰度切换控制面 CLI。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.platform.config.audit import (  # noqa: E402
    ConfigAuditEntry,
    get_changed_buckets,
    get_entry_by_id,
    read_audit_log,
    write_audit_entry,
)
from core.platform.config.manager import (  # noqa: E402
    ConfigConflictError,
    ConfigManager,
    ConfigPersistenceError,
    ConfigValidationError,
)
from core.platform.resources import PluginResourceLocator  # noqa: E402

_CANDIDATE_REUSE_PATH = "topic_segmentation.candidate_reuse"
_CANONICAL_BUCKETS = ("tiny", "small", "medium", "large", "xlarge", "huge")
_BUCKET_THRESHOLDS = {
    "tiny": 3,
    "small": 10,
    "medium": 30,
    "large": 100,
    "xlarge": 100,
    "huge": 100,
}
_REQUIRED_CHAT_TYPES = frozenset({"private", "group"})
_SAMPLE_FIELDS = (
    "quality_sample_count",
    "token_sample_count",
    "latency_sample_count",
    "negative_sample_count",
)
_DEFAULT_AUDIT_LOG_PATH = Path("data/memora/audit/config_changes.jsonl")


class RolloutError(ValueError):
    """表示可安全展示给 CLI 操作者的稳定失败原因。"""

    def __init__(self, reason: str) -> None:
        """保存稳定原因码，避免输出输入文件或异常正文。"""
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class BucketRecommendation:
    """单个规模桶的固定 K 建议与已选证据样本量。"""

    bucket: str
    fixed_k: int
    sample_counts: dict[str, int]


@dataclass(frozen=True)
class RolloutRecommendation:
    """可一次性应用的按桶候选重用建议。"""

    activation_threshold: int
    buckets: tuple[BucketRecommendation, ...]
    sample_counts: dict[str, int]


def _fail(reason: str) -> int:
    """向标准错误输出稳定失败原因并返回通用失败码。"""
    print(f"error: {reason}", file=sys.stderr)
    return 1


def _load_report(report_path: str) -> tuple[dict[str, Any], str]:
    """读取证据报告并返回已解析对象及原始内容 SHA-256。"""
    try:
        payload = Path(report_path).read_bytes()
    except OSError as exc:
        raise RolloutError("report_unavailable") from exc
    try:
        report = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RolloutError("report_json_invalid") from exc
    if not isinstance(report, dict):
        raise RolloutError("report_shape_invalid")
    return report, hashlib.sha256(payload).hexdigest()


def _sample_count(row: Mapping[str, Any], field: str) -> int:
    """读取非负整数样本量，拒绝布尔值和缺失字段。"""
    value = row.get(field)
    if type(value) is not int or value < 0:
        raise RolloutError("report_result_invalid")
    return value


def _extract_bucket_results(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """校验并规范化可操作的按桶证据结果。

    合成 benchmark 会显式标记为管道验证，不得作为配置切换依据。
    每个 `(bucket, chat_type, k)` 只能出现一次，避免后写结果覆盖前写结果。
    """
    if report.get("pipeline_validation_only") is True:
        raise RolloutError("report_pipeline_validation_only")
    raw_results = report.get("bucket_results")
    if not isinstance(raw_results, list) or not raw_results:
        raise RolloutError("report_bucket_results_missing")

    keys: set[tuple[str, str, int]] = set()
    results: list[dict[str, Any]] = []
    for raw_result in raw_results:
        if not isinstance(raw_result, dict):
            raise RolloutError("report_result_invalid")
        bucket = raw_result.get("scale_bucket")
        chat_type = raw_result.get("chat_type")
        fixed_k = raw_result.get("k")
        decision = raw_result.get("decision")
        if (
            not isinstance(bucket, str)
            or bucket not in _CANONICAL_BUCKETS
            or not isinstance(chat_type, str)
            or chat_type not in _REQUIRED_CHAT_TYPES
            or type(fixed_k) is not int
            or not 1 <= fixed_k <= 20
            or decision not in {"accept", "reject"}
        ):
            raise RolloutError("report_result_invalid")
        key = (bucket, chat_type, fixed_k)
        if key in keys:
            raise RolloutError("report_result_duplicate")
        keys.add(key)
        result = {
            "scale_bucket": bucket,
            "chat_type": chat_type,
            "k": fixed_k,
            "decision": decision,
        }
        for field in _SAMPLE_FIELDS:
            result[field] = _sample_count(raw_result, field)
        results.append(result)
    return results


def _aggregate_recommendations(
    bucket_results: list[dict[str, Any]],
) -> dict[tuple[str, str], int | None]:
    """为每个 `(bucket, chat_type)` 选择最小的已接受固定 K。"""
    groups: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for result in bucket_results:
        key = (str(result["scale_bucket"]), str(result["chat_type"]))
        groups.setdefault(key, []).append((int(result["k"]), str(result["decision"])))

    recommendations: dict[tuple[str, str], int | None] = {}
    for key, decisions in groups.items():
        accepted = [fixed_k for fixed_k, decision in decisions if decision == "accept"]
        recommendations[key] = min(accepted) if accepted else None
    return recommendations


def _summarize_samples(rows: list[dict[str, Any]]) -> dict[str, int]:
    """汇总一个已选证据集合的 allowlist 样本量。"""
    return {
        "quality": sum(int(row["quality_sample_count"]) for row in rows),
        "token": sum(int(row["token_sample_count"]) for row in rows),
        "latency": sum(int(row["latency_sample_count"]) for row in rows),
        "negative": sum(int(row["negative_sample_count"]) for row in rows),
    }


def _build_rollout_recommendation(
    bucket_results: list[dict[str, Any]],
) -> RolloutRecommendation | None:
    """构建按桶建议，并要求每个桶的两种 chat type 共用同一最小 K。"""
    grouped: dict[str, dict[str, dict[int, dict[str, Any]]]] = {}
    for result in bucket_results:
        bucket = str(result["scale_bucket"])
        chat_type = str(result["chat_type"])
        fixed_k = int(result["k"])
        grouped.setdefault(bucket, {}).setdefault(chat_type, {})[fixed_k] = result

    recommendations: list[BucketRecommendation] = []
    selected_rows: list[dict[str, Any]] = []
    for bucket in _CANONICAL_BUCKETS:
        chat_results = grouped.get(bucket, {})
        if set(chat_results) != _REQUIRED_CHAT_TYPES:
            continue
        accepted = {
            chat_type: {
                fixed_k for fixed_k, row in rows.items() if row["decision"] == "accept"
            }
            for chat_type, rows in chat_results.items()
        }
        common_accepted = accepted["private"] & accepted["group"]
        if not common_accepted:
            continue
        fixed_k = min(common_accepted)
        rows = [
            chat_results[chat_type][fixed_k]
            for chat_type in sorted(_REQUIRED_CHAT_TYPES)
        ]
        sample_counts = _summarize_samples(rows)
        recommendations.append(
            BucketRecommendation(
                bucket=bucket,
                fixed_k=fixed_k,
                sample_counts=sample_counts,
            )
        )
        selected_rows.extend(rows)

    if not recommendations:
        return None
    activation_threshold = min(
        _BUCKET_THRESHOLDS[recommendation.bucket] for recommendation in recommendations
    )
    return RolloutRecommendation(
        activation_threshold=activation_threshold,
        buckets=tuple(recommendations),
        sample_counts=_summarize_samples(selected_rows),
    )


def _audit_log_path(args: argparse.Namespace) -> Path:
    """解析审计日志路径，优先命令行参数再兼容现有环境变量。"""
    raw_path = (
        getattr(args, "audit_log", None)
        or os.getenv("MEMORA_AUDIT_LOG_PATH")
        or _DEFAULT_AUDIT_LOG_PATH
    )
    return Path(raw_path).expanduser()


def _config_path(args: argparse.Namespace) -> Path:
    """解析并校验 AstrBot 插件专用 JSON 配置文件路径。"""
    raw_path = getattr(args, "config_path", None) or os.getenv("MEMORA_CONFIG_PATH")
    if not isinstance(raw_path, str) or not raw_path:
        raise RolloutError("config_path_required")
    path = Path(raw_path).expanduser()
    if not path.is_file():
        raise RolloutError("plugin_config_unavailable")
    return path


def _load_config_manager(config_path: Path) -> ConfigManager:
    """按 AstrBot 插件加载方式构造带 Schema 的 ConfigManager。"""
    try:
        from astrbot.core.config.astrbot_config import AstrBotConfig

        locator = PluginResourceLocator(PROJECT_ROOT)
        schema = locator.load_schema()
        if not isinstance(schema, Mapping):
            raise RolloutError("plugin_schema_unavailable")
        source = AstrBotConfig(config_path=str(config_path), schema=dict(schema))
        return ConfigManager(source, resource_locator=locator)
    except RolloutError:
        raise
    except Exception as exc:
        raise RolloutError("plugin_config_load_failed") from exc


def _control_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """提取可审计、可回滚的 candidate-reuse 配置子集。"""
    segmentation = snapshot.get("topic_segmentation")
    if not isinstance(segmentation, Mapping):
        raise RolloutError("candidate_reuse_snapshot_invalid")
    candidate_reuse = segmentation.get("candidate_reuse")
    if not isinstance(candidate_reuse, Mapping):
        raise RolloutError("candidate_reuse_snapshot_invalid")
    mode = candidate_reuse.get("mode")
    activation_threshold = candidate_reuse.get("activation_threshold")
    overrides = candidate_reuse.get("bucket_overrides", {})
    if (
        mode not in {"off", "observe", "full", "top_k"}
        or type(activation_threshold) is not int
        or not 3 <= activation_threshold <= 100
        or not isinstance(overrides, Mapping)
    ):
        raise RolloutError("candidate_reuse_snapshot_invalid")

    normalized_overrides: dict[str, dict[str, Any]] = {}
    for bucket in _CANONICAL_BUCKETS:
        raw_override = overrides.get(bucket, {})
        if not isinstance(raw_override, Mapping):
            raise RolloutError("candidate_reuse_snapshot_invalid")
        override_mode = raw_override.get("mode", "observe")
        fixed_k = raw_override.get("fixed_k")
        if override_mode not in {"off", "observe", "full", "top_k"} or (
            fixed_k is not None and (type(fixed_k) is not int or not 1 <= fixed_k <= 20)
        ):
            raise RolloutError("candidate_reuse_snapshot_invalid")
        normalized_overrides[bucket] = {
            "mode": override_mode,
            "fixed_k": fixed_k,
        }
    return {
        "mode": mode,
        "activation_threshold": activation_threshold,
        "bucket_overrides": normalized_overrides,
    }


def _rollout_snapshot(
    recommendation: RolloutRecommendation,
) -> dict[str, Any]:
    """构造保守全局模式和显式证据桶覆盖的目标快照。"""
    overrides = {
        bucket: {"mode": "observe", "fixed_k": None} for bucket in _CANONICAL_BUCKETS
    }
    for bucket in recommendation.buckets:
        overrides[bucket.bucket] = {"mode": "top_k", "fixed_k": bucket.fixed_k}
    return {
        "mode": "observe",
        "activation_threshold": recommendation.activation_threshold,
        "bucket_overrides": overrides,
    }


def _fallback_snapshot(
    snapshot: Mapping[str, Any], fallback_mode: str
) -> dict[str, Any]:
    """构造审计持久化失败后的保守 observe 或 off 快照。"""
    return {
        "mode": fallback_mode,
        "activation_threshold": snapshot["activation_threshold"],
        "bucket_overrides": {
            bucket: {"mode": fallback_mode, "fixed_k": None}
            for bucket in _CANONICAL_BUCKETS
        },
    }


def _control_changes(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """把 allowlist 控制快照转换为 ConfigManager 的点路径变更。"""
    return {
        f"{_CANDIDATE_REUSE_PATH}.mode": snapshot["mode"],
        f"{_CANDIDATE_REUSE_PATH}.activation_threshold": snapshot[
            "activation_threshold"
        ],
        f"{_CANDIDATE_REUSE_PATH}.bucket_overrides": snapshot["bucket_overrides"],
    }


async def _apply_recommendation(
    manager: ConfigManager,
    recommendation: RolloutRecommendation,
    report_sha256: str,
    audit_path: Path,
    fallback_mode: str,
    operator: str,
    reason: str,
) -> tuple[ConfigAuditEntry, str]:
    """执行一次 CAS 灰度切换并在成功后写入规范审计记录。"""
    config_snapshot, revision_before = await manager.get_config_snapshot_async()
    before = _control_snapshot(config_snapshot)
    after = _rollout_snapshot(recommendation)
    result = await manager.apply_config_changes(
        _control_changes(after),
        expected_revision=revision_before,
        persist=True,
    )
    entry = ConfigAuditEntry.create(
        operator=operator,
        evidence_source="topic_candidate_evidence",
        reason=reason,
        before=before,
        after=after,
        changed_buckets=get_changed_buckets(
            before["bucket_overrides"], after["bucket_overrides"]
        ),
        config_revision_before=revision_before,
        config_revision_after=result.revision,
        report_sha256=report_sha256,
        sample_counts=recommendation.sample_counts,
    )
    try:
        write_audit_entry(entry, audit_path)
    except OSError as exc:
        try:
            await manager.apply_config_changes(
                _control_changes(_fallback_snapshot(after, fallback_mode)),
                expected_revision=result.revision,
                persist=True,
            )
        except (
            ConfigConflictError,
            ConfigPersistenceError,
            ConfigValidationError,
        ) as error:
            raise RolloutError("audit_persistence_fallback_failed") from error
        raise RolloutError("audit_persistence_failed") from exc
    return entry, result.revision


async def _rollback_entry(
    manager: ConfigManager,
    entry: ConfigAuditEntry,
    audit_path: Path,
) -> tuple[ConfigAuditEntry, str]:
    """用原条目的后修订号做 CAS 并恢复其 allowlist 前态。"""
    if not entry.config_revision_after:
        raise RolloutError("rollback_revision_missing")
    target = _control_snapshot(
        {"topic_segmentation": {"candidate_reuse": entry.before}}
    )
    current_snapshot, current_revision = await manager.get_config_snapshot_async()
    current = _control_snapshot(current_snapshot)
    if current_revision != entry.config_revision_after:
        raise RolloutError("rollback_revision_conflict")
    result = await manager.apply_config_changes(
        _control_changes(target),
        expected_revision=entry.config_revision_after,
        persist=True,
    )
    rollback_entry = ConfigAuditEntry.create(
        operator="cli",
        evidence_source="topic_candidate_rollback",
        reason="rollback",
        before=current,
        after=target,
        changed_buckets=get_changed_buckets(
            current["bucket_overrides"], target["bucket_overrides"]
        ),
        config_revision_before=current_revision,
        config_revision_after=result.revision,
        report_sha256=entry.report_sha256,
        sample_counts=entry.sample_counts,
        rollback_of=entry.entry_id,
    )
    try:
        write_audit_entry(rollback_entry, audit_path)
    except OSError as exc:
        try:
            await manager.apply_config_changes(
                _control_changes(current),
                expected_revision=result.revision,
                persist=True,
            )
        except (
            ConfigConflictError,
            ConfigPersistenceError,
            ConfigValidationError,
        ) as error:
            raise RolloutError("rollback_audit_restore_failed") from error
        raise RolloutError("rollback_audit_persistence_failed") from exc
    return rollback_entry, result.revision


def cmd_analyze(args: argparse.Namespace) -> int:
    """分析报告并输出最小固定 K 和 activation threshold 建议。"""
    try:
        report, _ = _load_report(args.report)
        results = _extract_bucket_results(report)
        grouped = _aggregate_recommendations(results)
        recommendation = _build_rollout_recommendation(results)
    except RolloutError as error:
        return _fail(error.reason)

    print(f"{'Bucket':<10} | {'ChatType':<10} | {'MinimumAcceptedK':<16}")
    print("-" * 46)
    for (bucket, chat_type), fixed_k in sorted(grouped.items()):
        display_k = str(fixed_k) if fixed_k is not None else "N/A"
        print(f"{bucket:<10} | {chat_type:<10} | {display_k:<16}")
    if recommendation is None:
        return _fail("no_safe_recommendation")

    buckets = ",".join(bucket.bucket for bucket in recommendation.buckets)
    print()
    print(f"activation_threshold={recommendation.activation_threshold}")
    print(f"enabled_buckets={buckets}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    """将证据支持的建议一次性持久化，并在成功后追加审计记录。"""
    try:
        report, report_sha256 = _load_report(args.report)
        recommendation = _build_rollout_recommendation(_extract_bucket_results(report))
        if recommendation is None:
            raise RolloutError("no_safe_recommendation")
        config_path = _config_path(args)
        audit_path = _audit_log_path(args)
        if not args.auto:
            buckets = ",".join(bucket.bucket for bucket in recommendation.buckets)
            prompt = (
                "Apply evidence-backed rollout "
                f"(threshold={recommendation.activation_threshold}, "
                f"buckets={buckets})? [y/n]: "
            )
            try:
                response = input(prompt).strip().lower()
            except EOFError as exc:
                raise RolloutError("confirmation_unavailable") from exc
            if response != "y":
                print("cancelled")
                return 0
        manager = _load_config_manager(config_path)
        entry, revision = asyncio.run(
            _apply_recommendation(
                manager,
                recommendation,
                report_sha256,
                audit_path,
                args.fallback_mode,
                "cli_auto" if args.auto else "cli_manual",
                "auto_rollout" if args.auto else "manual_approval",
            )
        )
    except RolloutError as error:
        return _fail(error.reason)
    except ConfigConflictError:
        return _fail("config_conflict")
    except ConfigValidationError:
        return _fail("config_validation_failed")
    except ConfigPersistenceError:
        return _fail("config_persistence_failed")

    print(f"applied entry_id={entry.entry_id} revision={revision}")
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    """使用审计条目的后修订号 CAS 恢复其变更前的候选重用配置。"""
    audit_path = _audit_log_path(args)
    entry = get_entry_by_id(args.entry_id, audit_path)
    if entry is None:
        return _fail("audit_entry_not_found")
    try:
        config_path = _config_path(args)
        if not args.auto:
            try:
                response = input("Confirm rollback? [y/n]: ").strip().lower()
            except EOFError as exc:
                raise RolloutError("confirmation_unavailable") from exc
            if response != "y":
                print("cancelled")
                return 0
        manager = _load_config_manager(config_path)
        rollback_entry, revision = asyncio.run(
            _rollback_entry(manager, entry, audit_path)
        )
    except RolloutError as error:
        return _fail(error.reason)
    except ConfigConflictError:
        return _fail("config_conflict")
    except ConfigValidationError:
        return _fail("config_validation_failed")
    except ConfigPersistenceError:
        return _fail("config_persistence_failed")

    print(
        "rolled_back entry_id={} revision={}".format(
            rollback_entry.entry_id,
            revision,
        )
    )
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    """按倒序展示平台配置审计记录的安全摘要。"""
    if args.limit < 1:
        return _fail("audit_limit_invalid")
    entries = read_audit_log(_audit_log_path(args), limit=args.limit)
    if not entries:
        print("no_audit_entries")
        return 0

    print(
        f"{'Timestamp':<20} | {'EntryID':<10} | {'Buckets':<20} | "
        f"{'Reason':<16} | {'Revision':<12}"
    )
    print("-" * 96)
    for entry in entries:
        buckets = ",".join(entry.changed_buckets[:3])
        if len(entry.changed_buckets) > 3:
            buckets += "..."
        revision = (entry.config_revision_after or "-")[:12]
        print(
            f"{entry.timestamp[:19].replace('T', ' '):<20} | "
            f"{entry.entry_id[:8]:<10} | {buckets:<20} | "
            f"{entry.reason:<16} | {revision:<12}"
        )
    return 0


def _add_audit_path_argument(parser: argparse.ArgumentParser) -> None:
    """为需要审计日志的子命令添加一致的路径参数。"""
    parser.add_argument(
        "--audit-log",
        default=None,
        help="审计 JSONL 路径；默认 MEMORA_AUDIT_LOG_PATH 或 data/memora/audit/config_changes.jsonl",
    )


def _add_config_path_argument(parser: argparse.ArgumentParser) -> None:
    """为写配置的子命令添加 AstrBot 插件配置文件参数。"""
    parser.add_argument(
        "--config-path",
        default=None,
        help="AstrBot 为 Memora 创建的插件专用 JSON 配置文件路径",
    )


def main() -> int:
    """解析四个控制面子命令并返回稳定进程退出码。"""
    parser = argparse.ArgumentParser(description="话题候选灰度切换控制面")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze", help="分析证据报告")
    analyze_parser.add_argument("report", help="证据报告 JSON 路径")

    apply_parser = subparsers.add_parser("apply", help="应用获证的灰度配置")
    apply_parser.add_argument("report", help="证据报告 JSON 路径")
    _add_config_path_argument(apply_parser)
    _add_audit_path_argument(apply_parser)
    apply_parser.add_argument("--auto", action="store_true", help="跳过人工确认")
    apply_parser.add_argument(
        "--fallback-mode",
        choices=("observe", "off"),
        default="observe",
        help="审计写入失败后的保守回退模式",
    )

    rollback_parser = subparsers.add_parser("rollback", help="回滚审计配置变更")
    rollback_parser.add_argument("entry_id", help="要回滚的审计条目 ID")
    _add_config_path_argument(rollback_parser)
    _add_audit_path_argument(rollback_parser)
    rollback_parser.add_argument("--auto", action="store_true", help="跳过人工确认")

    audit_parser = subparsers.add_parser("audit", help="查看配置审计记录")
    _add_audit_path_argument(audit_parser)
    audit_parser.add_argument("--limit", type=int, default=10, help="显示记录数量")

    args = parser.parse_args()
    if args.command == "analyze":
        return cmd_analyze(args)
    if args.command == "apply":
        return cmd_apply(args)
    if args.command == "rollback":
        return cmd_rollback(args)
    return cmd_audit(args)


if __name__ == "__main__":
    sys.exit(main())
