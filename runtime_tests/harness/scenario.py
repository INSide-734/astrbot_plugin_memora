"""逐测试场景的 AstrBot 根目录与资源释放证据。"""

from __future__ import annotations

import json
import secrets
import shutil
import socket
import uuid
from pathlib import Path

from .client import AstrBotClient
from .config import configure_dashboard, read_command_config, write_command_config
from .process import AstrBotProcess, reserve_loopback_port
from .template import run_astrbot_cli

_DASHBOARD_PASSWORD_ENV = "ASTRBOT_DASHBOARD_INITIAL_PASSWORD"


class AstrBotScenario:
    """封装一个完全隔离的真实 AstrBot 根目录与进程。"""

    def __init__(self, root: Path, process: AstrBotProcess) -> None:
        """保存已准备的场景根目录及其尚未启动的进程管理器。"""
        self.root = root.resolve()
        self._process = process

    @classmethod
    def prepare(cls, template_root: Path, root: Path) -> AstrBotScenario:
        """复制无秘密模板、预约端口并通过官方 init 生成场景鉴权配置。"""
        template_root = template_root.resolve()
        root = root.resolve()
        shutil.copytree(template_root, root)
        port, reservation = reserve_loopback_port()
        password = f"Memora-{secrets.token_hex(16)}-A9"
        test_token = secrets.token_urlsafe(32)
        try:
            run_astrbot_cli(
                ["init"],
                root,
                extra_env={_DASHBOARD_PASSWORD_ENV: password},
            )
            cls._write_official_configs(root, port)
            process = AstrBotProcess(
                root=root,
                port=port,
                reservation=reservation,
                password=password,
                test_token=test_token,
            )
        except Exception:
            reservation.close()
            raise
        return cls(root=root, process=process)

    @property
    def client(self) -> AstrBotClient:
        """返回当前场景绑定的 Dashboard HTTP 客户端。"""
        return self._process.client

    def start(self) -> None:
        """启动当前场景的真实 AstrBot 进程并等待 Dashboard 可响应。"""
        self._process.start()

    def stop(self) -> None:
        """请求当前场景干净关停并等待进程退出。"""
        self._process.stop()

    def close(self) -> None:
        """幂等释放当前场景全部父侧资源。"""
        self._process.close()

    def assert_resources_released(self) -> None:
        """独立验证退出码、关停方式、端口重绑和目录原子重命名。"""
        reasons: list[str] = []
        returncode = self._process.returncode
        if returncode != 0:
            reasons.append(f"AstrBot 退出码不是 0：{returncode!r}")
        if self._process.forced_shutdown:
            reasons.append("AstrBot 使用了强制关停")

        for port in self._process.release_check_ports:
            reason = self._port_release_failure(port)
            if reason:
                reasons.append(reason)

        rename_reason = self._directory_release_failure()
        if rename_reason:
            reasons.append(rename_reason)
        if reasons:
            raise AssertionError("场景资源未完全释放：\n- " + "\n- ".join(reasons))

    @staticmethod
    def _write_official_configs(root: Path, port: int) -> None:
        """写入 AstrBot 主配置和 Memora 官方插件配置文件。"""
        config = read_command_config(root)
        configure_dashboard(config, port, password_change_required=False)
        config["provider"] = [
            {
                "id": "memora-test-chat",
                "type": "memora_test_chat",
                "provider_type": "chat_completion",
                "enable": True,
                "model": "memora-test-chat-model",
                "key": [],
            },
            {
                "id": "memora-test-embedding",
                "type": "memora_test_embedding",
                "provider_type": "embedding",
                "enable": True,
                "model": "memora-test-embedding-model",
            },
        ]
        config["provider_settings"]["default_provider_id"] = "memora-test-chat"
        config["platform"] = [
            {
                "id": "memora-test-platform",
                "type": "memora_test_platform",
                "enable": True,
            }
        ]
        write_command_config(root, config)

        plugin_config_path = (
            root / "data" / "config" / "astrbot_plugin_memora_config.json"
        )
        plugin_config_path.write_text(
            json.dumps(
                {
                    "provider_settings": {
                        "embedding_provider_id": "memora-test-embedding",
                        "llm_provider_id": "memora-test-chat",
                    }
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _port_release_failure(port: int) -> str | None:
        """尝试重新绑定已使用端口，并返回独立的失败原因。"""
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            return f"端口 {port} 不能重新绑定：{exc}"
        finally:
            probe.close()
        return None

    def _directory_release_failure(self) -> str | None:
        """在同一父目录原子重命名场景根目录并恢复，以验证无遗留句柄。"""
        probe = self.root.with_name(
            f"{self.root.name}.release-check-{uuid.uuid4().hex}"
        )
        moved = False
        try:
            self.root.rename(probe)
            moved = True
            probe.rename(self.root)
            moved = False
        except OSError as exc:
            return f"场景目录不能原子重命名并恢复：{exc}"
        finally:
            if moved and probe.exists() and not self.root.exists():
                try:
                    probe.rename(self.root)
                except OSError:
                    pass
        return None
