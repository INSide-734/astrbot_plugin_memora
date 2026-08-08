"""M1 gate 报告模块：原子发布 + 受信 current.json consumer。

发布侧：受信空目录中逐份写入五份报告并 fsync，O_EXCL 创建 COMMITTED，
最后原子写顶层 current.json（绑定 generation、provenance、exit code 与
五文件 SHA-256）。消费侧：no-follow/fd 验证（拒绝 symlink 与 TOCTOU）
后从已验证字节快照发布，run-id/attempt 绑定 staging，全量复验后才原子
rename；只有 consumer 成功（rc=0）才上传 artifact。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat as statmod
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

try:  # 直接从仓库根执行脚本时，scripts 目录在 sys.path 首位。
    from m1_gate_core import (
        COMMIT_MARKER,
        REPORT_NAMES,
        SCHEMA_VERSION,
        M1GateError,
        _canonical_change,
        change_manifest_sha256,
    )
except ImportError:  # pytest 以包模块导入时使用完整路径。
    from scripts.m1_gate_core import (  # type: ignore[no-redef]
        COMMIT_MARKER,
        REPORT_NAMES,
        SCHEMA_VERSION,
        M1GateError,
        _canonical_change,
        change_manifest_sha256,
    )


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    """同目录 temp + flush/fsync + os.replace 原子写 JSON。"""
    directory = path.parent
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(directory))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except OSError:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _ensure_empty_report_dir(report_dir: Path) -> None:
    """以受信 run_id/run_attempt 创建唯一空目录；非空/非目录即失败闭合。"""
    if report_dir.exists():
        if not report_dir.is_dir():
            raise M1GateError(f"报告目录必须是目录: {report_dir}")
        if any(report_dir.iterdir()):
            raise M1GateError(
                f"报告目录必须为空（受信 run_id/run_attempt 唯一）: {report_dir}"
            )
    else:
        report_dir.mkdir(parents=True)


def _sha256_file(path: Path) -> str:
    """文件内容 SHA-256（manifest/consumer 校验用）。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _publish_reports(
    report_dir: Path,
    payloads: Sequence[tuple[str, Mapping[str, Any]]],
    *,
    exit_code: int,
    run_id: str | None,
    run_attempt: str | None,
    base_commit: str,
    pr_head_commit: str,
    candidate_commit: str,
) -> None:
    """受信空目录中原子发布五份报告 + COMMITTED + current manifest。"""

    _ensure_empty_report_dir(report_dir)
    written: dict[str, str] = {}
    for name, payload in payloads:
        target = report_dir / name
        with target.open("w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        written[name] = _sha256_file(target)
    try:
        fd = os.open(
            str(report_dir / COMMIT_MARKER),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            os.write(fd, b"committed\n")
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as exc:
        raise M1GateError(f"报告 COMMITTED marker 发布失败: {exc}") from exc
    try:
        _atomic_write(
            report_dir / "current.json",
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "run_attempt": run_attempt,
                "generation": report_dir.name,
                "base_commit": base_commit,
                "pr_head_commit": pr_head_commit,
                "candidate_commit": candidate_commit,
                "exit_code": exit_code,
                "committed": True,
                "files": written,
            },
        )
    except OSError as exc:
        raise M1GateError(f"报告 current manifest 发布失败: {exc}") from exc


def _write_error_envelope(
    report_dir: Path,
    error: str,
    exit_code: int,
    *,
    run_id: str | None = None,
    run_attempt: str | None = None,
) -> None:
    """exit 2 时写出五份 error envelope 并发布 current manifest。

    绝不抛异常：目录不可用/envelope 失败只打印，保留 exit 2 语义。
    """
    extras: dict[str, dict[str, Any]] = {
        "provenance.json": {"verified": False, "reason": error},
        "diff.json": {"changes": [], "change_manifest_sha256": None},
        "base-analysis.json": {"analysis": None},
        "candidate-analysis.json": {"analysis": None},
    }
    payloads = [
        (
            name,
            {
                "schema_version": SCHEMA_VERSION,
                "error": error,
                "exit_code": exit_code,
                **extras.get(name, {}),
            },
        )
        for name in REPORT_NAMES
    ]
    try:
        _publish_reports(
            report_dir,
            payloads,
            exit_code=exit_code,
            run_id=run_id,
            run_attempt=run_attempt,
            base_commit="",
            pr_head_commit="",
            candidate_commit="",
        )
    except Exception as exc:  # 兜底：envelope 自身失败也不外泄、保持 exit 2
        print(
            f"M1 gate error（错误 envelope 落盘失败仍 exit 2）: {error}: {exc}",
            file=sys.stderr,
        )


def _gate_fail(
    report_dir: Path, error: str, *, run_id: str | None, run_attempt: str | None
) -> int:
    """写 error envelope（不抛异常）并返回 exit 2。"""
    _write_error_envelope(report_dir, error, 2, run_id=run_id, run_attempt=run_attempt)
    print(f"M1 gate error: {error}", file=sys.stderr)
    return 2


def _emit(
    report_dir: Path,
    provenance: Mapping[str, Any],
    changes: Sequence[Mapping[str, Any]],
    base_facts: Mapping[str, Any],
    candidate_facts: Mapping[str, Any],
    *,
    status: str,
    exit_code: int,
    reasons: Sequence[str],
    invariants: Mapping[str, Any],
    meta: Mapping[str, Any],
    **extra: Any,
) -> None:
    """统一形态：五报告（diff/provenance/base/candidate/decision）+ current manifest。"""

    decision = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "exit_code": exit_code,
        "reasons": list(reasons),
        "invariants": dict(invariants),
        **extra,
    }
    _publish_reports(
        report_dir,
        (
            (
                "diff.json",
                {
                    "schema_version": SCHEMA_VERSION,
                    "changes": [_canonical_change(item) for item in changes],
                    "change_manifest_sha256": change_manifest_sha256(
                        [_canonical_change(item) for item in changes]
                    ),
                },
            ),
            ("provenance.json", provenance),
            ("base-analysis.json", base_facts),
            ("candidate-analysis.json", candidate_facts),
            ("decision.json", decision),
        ),
        exit_code=exit_code,
        run_id=meta["run_id"],
        run_attempt=meta["run_attempt"],
        base_commit=str(provenance.get("base_commit", "")),
        pr_head_commit=str(provenance.get("pr_head_commit", "")),
        candidate_commit=str(provenance.get("candidate_commit", "")),
    )


def _reject_symlink_components(path: Path) -> Path:
    """拒绝路径任一组件为 symlink（含目录本身），消除目录重定向。"""

    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor) if absolute.anchor else Path("/")
    for part in absolute.parts[1:]:
        current = current / part
        try:
            if os.path.islink(current):
                raise M1GateError(f"路径含 symlink 组件: {current}")
        except OSError as exc:
            raise M1GateError(f"路径不可检查: {current}: {exc}") from exc
    return absolute


def _open_dir_fd(report_dir: Path) -> int:
    """以 O_DIRECTORY|O_NOFOLLOW 持有目录 FD（拒绝目录 symlink 与替换竞态）。

    目录 FD 一旦持有，后续 openat/fstat/fd 列表都绑定该目录 inode——
    即使外部把路径 rename 换走，本 FD 仍指向已验证目录。
    """

    report_dir = _reject_symlink_components(report_dir)
    try:
        return os.open(
            str(report_dir),
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise M1GateError(f"无法持有报告目录 FD: {report_dir}: {exc}") from exc


def _open_verified_file_at(dir_fd: int, name: str) -> Any:
    """openat(dir_fd, name, O_NOFOLLOW) + fstat regular 校验（无 TOCTOU）。"""

    try:
        fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=dir_fd)
    except OSError as exc:
        raise M1GateError(f"无法打开（no-follow）: {name}: {exc}") from exc
    try:
        stat = os.fstat(fd)
        if not statmod.S_ISREG(stat.st_mode):
            raise M1GateError(f"非 regular 文件（拒绝 symlink/特殊文件）: {name}")
        return fd
    except M1GateError:
        os.close(fd)
        raise
    except OSError as exc:
        os.close(fd)
        raise M1GateError(f"fstat 失败: {name}: {exc}") from exc


def _read_verified_at(dir_fd: int, name: str) -> bytes:
    """从 openat fd 读取完整内容（fd 与校验同源）。"""

    fd = _open_verified_file_at(dir_fd, name)
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError as exc:
        raise M1GateError(f"读取失败: {name}: {exc}") from exc
    finally:
        os.close(fd)


def _sha256_bytes(payload: bytes) -> str:
    """字节内容的 SHA-256。"""
    return hashlib.sha256(payload).hexdigest()


def verify_report_set(
    report_dir: Path,
    *,
    run_id: str,
    run_attempt: str,
    expect_generation: str | None = None,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """受信 consumer：持有目录 FD，openat/fstat/fd 列表完成验证。

    - 目录 FD 绑定目录 inode（目录替换竞态闭合；路径 symlink 拒绝）
    - 目录项经 fd 列表（os.listdir(fd)）+ lstat 拒绝 symlink/非 regular
    - 目录精确等于五份报告 + COMMITTED + current.json
    - manifest 严格绑定受信 run_id/run_attempt 与 generation
    - COMMITTED 必须为固定内容 "committed\n"
    - 五份报告与 manifest 经 openat/fstat/同 fd 读取与哈希校验
    返回 (manifest, 字节快照) 供发布复用（发布绝不重新读取路径）。
    """

    report_dir = _reject_symlink_components(report_dir)
    if not report_dir.is_dir():
        raise M1GateError(f"报告目录不存在: {report_dir}")
    expected_entries = set(REPORT_NAMES) | {COMMIT_MARKER, "current.json"}
    dir_fd = _open_dir_fd(report_dir)
    try:
        try:
            entry_names = {entry for entry in os.listdir(dir_fd)}
        except OSError as exc:
            raise M1GateError(f"无法枚举报告目录: {exc}") from exc
        if entry_names != expected_entries:
            raise M1GateError(f"报告目录含未授权/缺失文件: {sorted(entry_names)}")
        for name in entry_names:
            try:
                stat = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                if not statmod.S_ISREG(stat.st_mode):
                    raise M1GateError(f"报告目录含 symlink/非 regular 项: {name}")
            except M1GateError:
                raise
            except OSError as exc:
                raise M1GateError(f"lstat 失败: {name}: {exc}") from exc
        try:
            manifest_bytes = _read_verified_at(dir_fd, "current.json")
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (M1GateError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise M1GateError(f"current.json 不可解析: {exc}") from exc
        if manifest.get("schema_version") != SCHEMA_VERSION:
            raise M1GateError("current.json schema_version 不受支持")
        if manifest.get("committed") is not True:
            raise M1GateError("current.json 未标记 committed")
        if (
            manifest.get("run_id") != run_id
            or manifest.get("run_attempt") != run_attempt
        ):
            raise M1GateError(
                f"manifest run_id/run_attempt 与受信值不符: "
                f"{manifest.get('run_id')}/{manifest.get('run_attempt')} "
                f"!= {run_id}/{run_attempt}"
            )
        expected_generation = (
            report_dir.name if expect_generation is None else expect_generation
        )
        if manifest.get("generation") != expected_generation:
            raise M1GateError(
                f"generation 不符: {manifest.get('generation')} != {expected_generation}"
            )
        committed = _read_verified_at(dir_fd, COMMIT_MARKER)
        if committed != b"committed\n":
            raise M1GateError("COMMITTED 内容与固定摘要不符")
        files = manifest.get("files")
        if not isinstance(files, dict) or set(files) != set(REPORT_NAMES):
            raise M1GateError(
                f"manifest files 必须是精确五份报告: {sorted(files or {})}"
            )
        snapshots: dict[str, bytes] = {
            COMMIT_MARKER: committed,
            "current.json": manifest_bytes,
        }
        for name, digest in files.items():
            if not isinstance(digest, str) or len(digest) != 64:
                raise M1GateError(f"manifest 报告 hash 无效: {name}")
            payload = _read_verified_at(dir_fd, name)
            if _sha256_bytes(payload) != digest:
                raise M1GateError(f"报告 hash 校验失败: {name}")
            snapshots[name] = payload
        return manifest, snapshots
    finally:
        os.close(dir_fd)


def publish_verified_set(
    source_dir: Path,
    dest_dir: Path,
    *,
    run_id: str,
    run_attempt: str,
) -> None:
    """受信 consumer：从已验证字节快照发布，run-id/attempt 绑定 staging。

    验证产生 (manifest, 快照)；快照写入以 run-id/attempt 命名的临时
    staging 并 fsync；对 staging 全量复验后才原子 rename 为 dest（目标
    已存在即拒绝）。发布绝不重新读取 source 路径（无 TOCTOU 窗口）。
    """

    manifest, snapshots = verify_report_set(
        source_dir, run_id=run_id, run_attempt=run_attempt
    )
    generation = manifest.get("generation")
    dest_dir = _reject_symlink_components(dest_dir)
    if dest_dir.exists():
        raise M1GateError(f"发布目标已存在（须为全新路径）: {dest_dir}")
    dest_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f"{dest_dir.name}.{run_id}-{run_attempt}.staging.",
            dir=str(dest_dir.parent),
        )
    )
    try:
        for name in sorted(snapshots):
            target = staging / name
            fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, snapshots[name])
                os.fsync(fd)
            finally:
                os.close(fd)
        verify_report_set(
            staging,
            run_id=run_id,
            run_attempt=run_attempt,
            expect_generation=generation,
        )  # 全量复验后才原子 rename
        os.replace(staging, dest_dir)
    except (M1GateError, OSError) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise M1GateError(f"原子发布失败: {exc}") from exc
    if manifest.get("exit_code") != 0:
        print(
            f"M1 gate 报告已发布（exit_code={manifest.get('exit_code')}）: {dest_dir}",
            file=sys.stderr,
        )


def _main(argv: Sequence[str] | None = None) -> int:
    """consumer CLI：--verify DIR / --publish SRC DST（0=已验证，2=不可信）。"""

    args = list(argv) if argv is not None else sys.argv[1:]
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    mode = args[0]
    try:
        if mode == "--verify" and len(args) == 6:
            verify_report_set(Path(args[1]), run_id=args[3], run_attempt=args[5])
            print("verified")
            return 0
        if mode == "--publish" and len(args) == 7:
            publish_verified_set(
                Path(args[1]),
                Path(args[2]),
                run_id=args[4],
                run_attempt=args[6],
            )
            print("published")
            return 0
        print(f"用法错误: {args}", file=sys.stderr)
        return 2
    except M1GateError as exc:
        print(f"M1 gate consumer error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # 顶层兜底：任何未预期异常都 exit 2
        print(
            f"M1 gate consumer error: 未预期错误: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
