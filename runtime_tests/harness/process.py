"""真实 AstrBot 子进程的跨平台生命周期管理。"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import BinaryIO

import httpx
import psutil

from .client import AstrBotClient

_CLI_RUN = [sys.executable, "-m", "astrbot.cli.__main__", "run", "-p"]
_DASHBOARD_READY_TIMEOUT = 120.0
_POLL_INTERVAL_SECONDS = 0.5
_MAX_PORT_ATTEMPTS = 3
_BIND_CONFLICT_MARKERS = (
    "address already in use",
    "only one usage of each socket address",
    "winerror 10048",
    "error while attempting to bind",
    "端口 ",
    "已被占用",
)
_INHERITED_SCENARIO_ENV_KEYS = (
    "ASTRBOT_DASHBOARD_INITIAL_PASSWORD",
    "ASTRBOT_RESET_DASHBOARD_PASSWORD",
    "MEMORA_TEST_DRIVER_TOKEN",
)


def reserve_loopback_port() -> tuple[int, socket.socket]:
    """绑定回环地址的临时端口，并把预约套接字保持为打开状态。"""
    reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        reservation.bind(("127.0.0.1", 0))
        port = int(reservation.getsockname()[1])
        return port, reservation
    except Exception:
        reservation.close()
        raise


class AstrBotProcess:
    """管理单个场景的进程、日志、HTTP 客户端与端口预约。"""

    def __init__(
        self,
        root: Path,
        port: int,
        reservation: socket.socket,
        password: str,
        test_token: str,
    ) -> None:
        """记录场景边界并创建尚未连接的 Dashboard 客户端。"""
        self.root = root.resolve()
        self.port = port
        self.original_port = port
        self.password = password
        self.test_token = test_token
        self.forced_shutdown = False
        self.ports_used: list[int] = []
        self._reservation: socket.socket | None = reservation
        self._process: subprocess.Popen[bytes] | None = None
        self._log_path = self.root / "astrbot-runtime.log"
        self._log_handle: BinaryIO | None = None
        self._attempt_log_offset = 0
        self._closed = False
        self.client = AstrBotClient(port, password, test_token, self)

    @property
    def returncode(self) -> int | None:
        """返回当前子进程退出码；未启动或仍在运行时返回 ``None``。"""
        if self._process is None:
            return None
        return self._process.poll()

    def start(self) -> None:
        """启动真实 AstrBot，并仅对明确的早期端口冲突更换端口重试。"""
        if self._closed:
            raise RuntimeError("不能启动已关闭的 AstrBot 进程")
        if self._process is not None:
            if self._process.poll() is None:
                return
            raise RuntimeError("AstrBot 进程已退出，不能重复启动")

        deadline = time.monotonic() + _DASHBOARD_READY_TIMEOUT
        for attempt in range(_MAX_PORT_ATTEMPTS):
            self._launch_once()
            if self._wait_until_dashboard_accepts_requests(deadline):
                return

            logs = self.read_sanitized_log()
            attempt_logs = self._read_current_attempt_sanitized_log()
            if (
                not self._is_bind_conflict(attempt_logs)
                or attempt == _MAX_PORT_ATTEMPTS - 1
            ):
                self._close_log_handle()
                raise RuntimeError(
                    "AstrBot 在 Dashboard 就绪前退出，且不满足端口冲突重试条件。"
                    f"\n{logs}"
                )

            self._close_log_handle()
            self._process = None
            self.port, self._reservation = reserve_loopback_port()
            self._write_dashboard_port(self.port)
            self.client.retarget(self.port)

        raise RuntimeError("AstrBot 端口重试次数已耗尽")

    def stop(self) -> None:
        """先关闭 HTTP 客户端，再以平台信号请求干净关停并回收日志句柄。"""
        self.client.close()
        self._release_reservation()
        process = self._process
        if process is None:
            self._close_log_handle()
            return

        if process.poll() is None:
            process_tree = self._snapshot_process_tree(process)
            self._send_interrupt(process)
            if process_tree:
                _, alive = psutil.wait_procs(process_tree, timeout=15)
            else:
                try:
                    process.wait(timeout=15)
                    alive = []
                except subprocess.TimeoutExpired:
                    alive = [psutil.Process(process.pid)]
            if alive:
                self.forced_shutdown = True
                self._terminate_process_tree(process, alive)
            try:
                process.wait(timeout=0)
            except subprocess.TimeoutExpired:
                self.forced_shutdown = True
                self._terminate_process_tree(process, [psutil.Process(process.pid)])
        self._close_log_handle()

    def close(self) -> None:
        """幂等释放准备中、部分启动、运行中或已停止场景的所有父侧资源。"""
        if self._closed:
            return
        try:
            self.stop()
        finally:
            self._release_reservation()
            self._close_log_handle()
            self.client.close()
            self._closed = True

    def read_sanitized_log(self) -> str:
        """读取至多最后 200 行日志并替换密码、令牌和场景绝对路径。"""
        return self._read_sanitized_log(start_offset=0)

    def _read_current_attempt_sanitized_log(self) -> str:
        """仅读取当前启动尝试的脱敏日志，供端口冲突判定使用。"""
        return self._read_sanitized_log(start_offset=self._attempt_log_offset)

    def _read_sanitized_log(self, *, start_offset: int) -> str:
        """从指定字节偏移读取日志，脱敏后限制为最后 200 行。"""
        if self._log_handle is not None:
            self._log_handle.flush()
        if not self._log_path.exists():
            return ""
        with self._log_path.open("rb") as log_file:
            log_file.seek(start_offset)
            content = log_file.read().decode("utf-8", errors="replace")
        redactions = (
            (self.password, "[DASHBOARD_PASSWORD]"),
            (self.test_token, "[TEST_TOKEN]"),
            (str(self.root), "[SCENARIO_ROOT]"),
            (self.root.as_posix(), "[SCENARIO_ROOT]"),
        )
        for secret, replacement in redactions:
            if secret:
                content = content.replace(secret, replacement)
        return "\n".join(content.splitlines()[-200:])

    def _launch_once(self) -> None:
        """在释放当前端口预约后启动一次独立进程组。"""
        self._attempt_log_offset = (
            self._log_path.stat().st_size if self._log_path.exists() else 0
        )
        self._log_handle = self._log_path.open("ab")
        environment = os.environ.copy()
        for key in _INHERITED_SCENARIO_ENV_KEYS:
            environment.pop(key, None)
        environment["ASTRBOT_ROOT"] = str(self.root)
        environment["MEMORA_TEST_DRIVER_TOKEN"] = self.test_token
        arguments: dict[str, object] = {}
        if os.name == "nt":
            arguments["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            arguments["start_new_session"] = True

        self.ports_used.append(self.port)
        self._release_reservation()
        self._process = subprocess.Popen(
            [*_CLI_RUN, str(self.port)],
            cwd=self.root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            **arguments,
        )

    def _wait_until_dashboard_accepts_requests(self, deadline: float) -> bool:
        """条件轮询真实登录，成功后才确认 Dashboard 鉴权服务已经就绪。"""
        while True:
            process = self._process
            if process is None or process.poll() is not None:
                return False
            try:
                self.client.login()
                return True
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 401:
                    raise RuntimeError(
                        "AstrBot Dashboard 拒绝场景初始化密码。\n"
                        f"{self.read_sanitized_log()}"
                    ) from exc
            except httpx.RequestError:
                pass

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"等待 AstrBot Dashboard 就绪超时。\n{self.read_sanitized_log()}"
                )
            time.sleep(min(_POLL_INTERVAL_SECONDS, remaining))

    def _release_reservation(self) -> None:
        """关闭当前端口预约套接字，并确保重复调用安全。"""
        if self._reservation is None:
            return
        self._reservation.close()
        self._reservation = None

    def _close_log_handle(self) -> None:
        """在子进程退出后幂等关闭父进程持有的日志文件句柄。"""
        if self._log_handle is None:
            return
        self._log_handle.close()
        self._log_handle = None

    @staticmethod
    def _send_interrupt(process: subprocess.Popen[bytes]) -> None:
        """向完整 AstrBot 进程组发送平台对应的正常中断信号。"""
        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(process.pid, signal.SIGINT)
        except ProcessLookupError:
            return

    @staticmethod
    def _snapshot_process_tree(
        process: subprocess.Popen[bytes],
    ) -> list[psutil.Process]:
        """在发信号前快照包装进程及全部后代，供统一等待与兜底终止。"""
        try:
            parent = psutil.Process(process.pid)
            return [parent, *parent.children(recursive=True)]
        except psutil.Error:
            return []

    @staticmethod
    def _terminate_process_tree(
        process: subprocess.Popen[bytes],
        alive: list[psutil.Process],
    ) -> None:
        """仅在正常关停超时后强制终止完整子进程树。"""
        if os.name == "nt":
            for child in reversed(alive):
                subprocess.run(
                    ["taskkill", "/PID", str(child.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        psutil.wait_procs(alive, timeout=5)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait(timeout=5)

    @staticmethod
    def _is_bind_conflict(logs: str) -> bool:
        """仅依据已退出进程日志中的明确绑定冲突标记决定是否重试。"""
        lowered = logs.lower()
        if "端口 " in logs and "已被占用" in logs:
            return True
        return any(marker in lowered for marker in _BIND_CONFLICT_MARKERS[:4])

    def _write_dashboard_port(self, port: int) -> None:
        """把冲突重试的新端口写回场景的官方 Dashboard 配置。"""
        config_path = self.root / "data" / "cmd_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        config["dashboard"]["host"] = "127.0.0.1"
        config["dashboard"]["port"] = port
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding="utf-8-sig",
        )
