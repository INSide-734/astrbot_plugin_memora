"""Topic 候选指标的 HMAC key 供应、轮换与安全读写端口。

指标隐私契约：
- ``scope_key`` 与窗口去重键只允许以 HMAC-SHA-256 摘要进入数据库和日志；
- key 为安装级 32 字节随机值，保存在数据目录 sidecar 文件（0o600）；
- sidecar 带单调递增版本；轮换后旧版本只保留历史数据，不再参与聚合。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import json
import math
import os
import secrets
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from astrbot.api import logger

from ...reflection.domain.summary_models import CandidateMetrics, ClaimedJob

_KEY_BYTES = 32
_KEY_VERSION = 1
_KEY_FILENAME = "topic_metrics.hmac.key"
_ALLOWED_MODES = frozenset({"off", "observe", "full", "top_k"})


@dataclass(frozen=True, slots=True)
class TopicMetricsKeyState:
    """安装级指标 HMAC key 及其不可回退版本。"""

    key: bytes
    version: int

    def __post_init__(self) -> None:
        """校验随机密钥长度与正整数版本，拒绝布尔伪装。"""
        if not isinstance(self.key, bytes) or len(self.key) != _KEY_BYTES:
            raise ValueError("topic_metrics_key_invalid")
        if (
            isinstance(self.version, bool)
            or not isinstance(self.version, int)
            or self.version <= 0
        ):
            raise ValueError("topic_metrics_key_version_invalid")


def _validate_key_path(path: Path) -> None:
    """校验 sidecar 是权限正确的普通文件。"""

    try:
        metadata = path.lstat()
    except OSError as error:
        raise RuntimeError("topic_metrics_key_invalid") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError("topic_metrics_key_invalid")
    if os.name != "nt" and stat.S_IMODE(metadata.st_mode) != 0o600:
        raise RuntimeError("topic_metrics_key_invalid")


def _write_descriptor(descriptor: int, payload: bytes) -> None:
    """完整写入并同步 sidecar 内容。"""

    written = 0
    while written < len(payload):
        written += os.write(descriptor, payload[written:])
    os.fsync(descriptor)


def _write_key_state(
    path: Path, state: TopicMetricsKeyState, *, replace: bool = False
) -> None:
    """以临时文件加原子替换或独占创建写入 key state。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"key": state.key.hex(), "version": state.version},
        separators=(",", ":"),
    ).encode("ascii")
    if not replace:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            _write_descriptor(descriptor, payload)
        finally:
            os.close(descriptor)
        return

    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        _write_descriptor(descriptor, payload)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def load_topic_metrics_key_state(
    base_dir: str | Path, *, create: bool = False
) -> TopicMetricsKeyState:
    """读取版本化 sidecar；仅显式 ``create`` 时允许首次生成。"""

    path = Path(base_dir) / _KEY_FILENAME
    try:
        _validate_key_path(path)
    except RuntimeError:
        if path.exists():
            raise
        if not create:
            raise RuntimeError("topic_metrics_key_missing") from None
        state = TopicMetricsKeyState(secrets.token_bytes(_KEY_BYTES), _KEY_VERSION)
        try:
            _write_key_state(path, state)
        except FileExistsError:
            return load_topic_metrics_key_state(base_dir, create=False)
        return state

    try:
        raw = path.read_text(encoding="ascii").strip()
        if raw.startswith("{"):
            value = json.loads(raw)
            if not isinstance(value, dict) or set(value) != {"key", "version"}:
                raise ValueError("invalid key envelope")
            key = bytes.fromhex(value["key"])
            version = value["version"]
        else:
            # 兼容早期无 envelope 的 v1 sidecar；后续轮换始终写入 envelope。
            key = bytes.fromhex(raw)
            version = _KEY_VERSION
        return TopicMetricsKeyState(key, version)
    except (
        OSError,
        TypeError,
        ValueError,
        UnicodeError,
        json.JSONDecodeError,
    ) as error:
        raise RuntimeError("topic_metrics_key_invalid") from error


def load_or_create_topic_metrics_key(base_dir: str | Path) -> bytes:
    """加载或一次性生成指标 HMAC key。"""

    return load_topic_metrics_key_state(base_dir, create=True).key


def rotate_topic_metrics_key(base_dir: str | Path) -> TopicMetricsKeyState:
    """显式轮换指标 key；版本递增，旧版本聚合不会被重算。"""

    current = load_topic_metrics_key_state(base_dir, create=False)
    next_version = current.version + 1
    if next_version <= current.version:
        raise RuntimeError("topic_metrics_key_version_invalid")
    state = TopicMetricsKeyState(secrets.token_bytes(_KEY_BYTES), next_version)
    _write_key_state(Path(base_dir) / _KEY_FILENAME, state, replace=True)
    return state


def hmac_key_digest(key: bytes, message: str) -> str:
    """返回 message 的 HMAC-SHA-256 小写十六进制摘要。"""

    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).hexdigest()


async def read_topic_metrics_summary(
    catalog_store: Any,
    base_dir: str | Path,
    *,
    retention_days: int = 7,
    now: float | None = None,
) -> dict[str, float | None] | None:
    """按当前 key 版本从 canonical Store 读取安全指标摘要。

    key 缺失、权限错误、格式错误、版本无效或 Store 读失败都返回空值；
    绝不创建 key，也不跨版本拼接历史摘要。
    """

    if (
        isinstance(retention_days, bool)
        or not isinstance(retention_days, int)
        or retention_days <= 0
    ):
        return None
    try:
        state = load_topic_metrics_key_state(base_dir, create=False)
        current_time = time.time() if now is None else now
        if (
            isinstance(current_time, bool)
            or not isinstance(current_time, (int, float))
            or not math.isfinite(current_time)
            or current_time < 0
        ):
            return None
        reader = getattr(catalog_store, "read_metric_summary", None)
        if not callable(reader):
            return None
        result = reader(
            hash_key_version=state.version,
            since=max(0.0, float(current_time) - retention_days * 86400.0),
            until=float(current_time),
        )
        if inspect.isawaitable(result):
            result = await result
        active = load_topic_metrics_key_state(base_dir, create=False)
        if active.version != state.version or not hmac.compare_digest(
            active.key, state.key
        ):
            return None
        return result if isinstance(result, dict) else None
    except asyncio.CancelledError:
        raise
    except Exception:
        return None


class TopicCandidateMetricsRecorder:
    """把窗口终态指标经 HMAC 摘要写入 TopicCatalogStore。

    只依赖 ``record_metric_window`` 端口（终态 CAS + 聚合）；不落任何
    scope 原文、正文或 label，失败只输出固定 reason code 与窗口摘要。
    """

    def __init__(
        self,
        catalog_store: Any,
        key: bytes | TopicMetricsKeyState,
        *,
        key_version: int | None = None,
        key_state_dir: str | Path | None = None,
        now: Any = None,
    ) -> None:
        """绑定 catalog store、版本化 key 与可注入墙钟。"""

        if isinstance(key, TopicMetricsKeyState):
            if key_version is not None and key_version != key.version:
                raise ValueError("topic_metrics_key_version_mismatch")
            key_version = key.version
            key = key.key
        if not isinstance(key, bytes) or len(key) != _KEY_BYTES:
            raise ValueError("topic_metrics_key_invalid")
        if key_version is None:
            key_version = _KEY_VERSION
        if (
            isinstance(key_version, bool)
            or not isinstance(key_version, int)
            or key_version <= 0
        ):
            raise ValueError("topic_metrics_key_version_invalid")
        self._store = catalog_store
        self._key = key
        self._key_version = key_version
        self._key_state_dir = Path(key_state_dir) if key_state_dir is not None else None
        self._now = now or time.time

    def _active_key_valid(self) -> bool:
        """确认运行中的 recorder 未继续使用已轮换或失效的 key。"""

        if self._key_state_dir is None:
            return True
        try:
            state = load_topic_metrics_key_state(self._key_state_dir, create=False)
        except Exception:
            return False
        return state.version == self._key_version and hmac.compare_digest(
            state.key, self._key
        )

    def _window_key_hash(self, claim: ClaimedJob) -> str:
        """由 job/epoch/seq 范围/source digest 构造窗口去重摘要。"""

        message = (
            f"{claim.job.job_id}:{claim.session_epoch}:"
            f"{claim.start_seq}:{claim.end_seq}:{claim.source_digest}"
        )
        return hmac_key_digest(self._key, message)

    async def record_success(
        self, claim: ClaimedJob, metrics: CandidateMetrics
    ) -> bool:
        """以 success 终态记录一次窗口候选指标（自身 fail-safe）。"""

        try:
            return await self._record_success_inner(claim, metrics)
        except asyncio.CancelledError:
            raise
        except Exception:
            # 存储失败不阻塞总结主链；只输出固定 reason code，不携带异常细节。
            logger.warning(
                "候选指标记录失败",
                extra={"reason_code": "metrics_record_failed"},
            )
            return False

    async def _record_success_inner(
        self, claim: ClaimedJob, metrics: CandidateMetrics
    ) -> bool:
        """执行实际的摘要构造与终态写入（不做异常兜底）。"""

        if not self._active_key_valid():
            return False
        scope_key = getattr(claim, "scope_key", None)
        if not isinstance(scope_key, str) or not scope_key.strip():
            logger.warning(
                "候选指标跳过：scope 缺失",
                extra={"reason_code": "metrics_scope_missing"},
            )
            return False
        mode = (
            metrics.mode.value if hasattr(metrics.mode, "value") else str(metrics.mode)
        )
        if mode not in _ALLOWED_MODES:
            logger.warning(
                "候选指标跳过：模式不合法",
                extra={"reason_code": "metrics_mode_invalid"},
            )
            return False
        current_time = self._now()
        if (
            isinstance(current_time, bool)
            or not isinstance(current_time, (int, float))
            or not math.isfinite(current_time)
            or current_time < 0
        ):
            return False
        prompt_tokens = getattr(metrics, "n_tokens", None)
        if prompt_tokens is not None and (
            isinstance(prompt_tokens, bool)
            or not isinstance(prompt_tokens, int)
            or prompt_tokens < 0
        ):
            return False
        window_key_hash = self._window_key_hash(claim)
        recorded = await self._store.record_metric_window(
            window_key_hash=window_key_hash,
            hash_key_version=self._key_version,
            terminal_state="success",
            token_source_available=prompt_tokens is not None,
            scope_key_hash=hmac_key_digest(self._key, scope_key.strip()),
            bucket_date=datetime.fromtimestamp(current_time, timezone.utc)
            .date()
            .isoformat(),
            mode=mode,
            topic_count_bucket="unknown",
            values={
                "candidate_count_sum": metrics.n_candidates,
                "selector_duration_ms": metrics.selector_latency_ms,
                "prompt_tokens": prompt_tokens,
            },
            now=current_time,
        )
        if not recorded:
            # CAS 拒绝（重试/重复终态）是预期幂等路径，不告警。
            return False
        return True


async def build_metrics_recorder(
    memory_engine: Any, base_dir: str | Path
) -> TopicCandidateMetricsRecorder | None:
    """创建安装级记录器；已有历史但 key 丢失/无效时停聚，不阻断总结启动。"""
    store = memory_engine.topic_catalog_store
    try:
        create = not await store.has_metric_history()
        state = load_topic_metrics_key_state(base_dir, create=create)
        return TopicCandidateMetricsRecorder(store, state, key_state_dir=base_dir)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("候选指标密钥不可用，已停止指标聚合")
        return None


__all__ = [
    "TopicCandidateMetricsRecorder",
    "TopicMetricsKeyState",
    "build_metrics_recorder",
    "hmac_key_digest",
    "load_or_create_topic_metrics_key",
    "load_topic_metrics_key_state",
    "read_topic_metrics_summary",
    "rotate_topic_metrics_key",
]
