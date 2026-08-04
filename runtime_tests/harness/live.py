"""显式 live 黑盒档位的外部 Provider 配置与安全校验。"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

LIVE_API_BASE_ENV = "MEMORA_LIVE_API_BASE"
LIVE_API_KEY_ENV = "MEMORA_LIVE_API_KEY"
LIVE_MODEL_ENV = "MEMORA_LIVE_MODEL"
LIVE_ALLOWED_HOSTS_ENV = "MEMORA_LIVE_ALLOWED_HOSTS"
LIVE_ENV_KEYS = (
    LIVE_API_BASE_ENV,
    LIVE_API_KEY_ENV,
    LIVE_MODEL_ENV,
    LIVE_ALLOWED_HOSTS_ENV,
)


class _ProtectedEnvironment(Mapping[str, str]):
    """提供 live 变量只读视图，并阻止 traceback 展开任何变量值。"""

    def __init__(self) -> None:
        """只复制 live 档位需要的四个进程环境变量。"""
        self._values = {key: os.environ.get(key, "") for key in LIVE_ENV_KEYS}

    def __getitem__(self, key: str) -> str:
        """返回指定 live 变量值。"""
        return self._values[key]

    def __iter__(self):
        """按固定顺序迭代 live 变量名。"""
        return iter(LIVE_ENV_KEYS)

    def __len__(self) -> int:
        """返回固定 live 变量数量。"""
        return len(LIVE_ENV_KEYS)

    def __repr__(self) -> str:
        """仅报告受保护视图类型，不输出键值。"""
        return "<protected live environment>"


@dataclass(frozen=True, slots=True)
class LiveProviderSettings:
    """保存通过安全校验的 OpenAI-compatible live Provider 配置。"""

    api_base: str
    api_key: str = field(repr=False)
    model: str
    hostname: str

    @classmethod
    def from_process_environment(cls) -> LiveProviderSettings:
        """通过不可展开的受保护视图读取当前进程 live 配置。"""
        return cls.from_environment(_ProtectedEnvironment())

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str],
    ) -> LiveProviderSettings:
        """从显式环境读取 live 配置，并在任何联网或落盘前拒绝危险值。"""
        values = {key: str(environment.get(key, "")).strip() for key in LIVE_ENV_KEYS}
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise ValueError("live 黑盒档位缺少配置：" + ", ".join(missing))

        api_base = values[LIVE_API_BASE_ENV].rstrip("/")
        api_key = values[LIVE_API_KEY_ENV]
        model = values[LIVE_MODEL_ENV]
        allowed_hosts = {
            item.strip().lower()
            for item in values[LIVE_ALLOWED_HOSTS_ENV].split(",")
            if item.strip()
        }
        parsed = urlsplit(api_base)
        if parsed.scheme.lower() != "https":
            raise ValueError("live API Base 必须使用 HTTPS")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("live API Base 不得包含用户信息")
        if parsed.query or parsed.fragment:
            raise ValueError("live API Base 不得包含查询参数或片段")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("live API Base 端口无效") from exc
        if port not in (None, 443):
            raise ValueError("live API Base 只允许标准 HTTPS 端口")

        hostname = (parsed.hostname or "").rstrip(".").lower()
        if not hostname:
            raise ValueError("live API Base 缺少主机名")
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            raise ValueError("live API Base 不得使用 IP 地址")
        if hostname not in allowed_hosts:
            raise ValueError("live API Base 主机未进入白名单")
        if any(character.isspace() or ord(character) < 32 for character in model):
            raise ValueError("live 模型名包含非法字符")
        if len(api_key) < 8 or any(ord(character) < 32 for character in api_key):
            raise ValueError("live API Key 格式无效")

        return cls(
            api_base=api_base,
            api_key=api_key,
            model=model,
            hostname=hostname,
        )

    def provider_config(self) -> dict[str, object]:
        """生成 AstrBot 4.26.7 内置 OpenAI adapter 的最小 Provider 配置。"""
        return {
            "id": "memora-test-chat",
            "provider": "openai-compatible-live",
            "type": "openai_chat_completion",
            "provider_type": "chat_completion",
            "enable": True,
            "model": self.model,
            "key": [self.api_key],
            "api_base": self.api_base,
            "timeout": 30,
            "proxy": "",
            "custom_headers": {},
        }
