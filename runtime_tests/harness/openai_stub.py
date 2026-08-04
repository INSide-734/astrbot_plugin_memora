"""普通 PR 使用的确定性 OpenAI-compatible 回环服务。"""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

CONTRACT_CANARY = "MEMORA_CONTRACT_CANARY"
CONTRACT_MEMORY = "MEMORA_CONTRACT_MEMORY"
CONTRACT_REPLY = "MEMORA_CONTRACT_REPLY"
_MAX_REQUEST_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class OpenAIRequestObservation:
    """只保留契约断言需要的低敏请求事实。"""

    purpose: str
    model: str
    authorization_valid: bool
    contains_canary: bool


class _ContractHTTPServer(ThreadingHTTPServer):
    """保存 stub 状态且由标准库线程服务器处理并发请求。"""

    daemon_threads = True

    def __init__(self, owner: OpenAIContractStub) -> None:
        """绑定随机回环端口并关联拥有其生命周期的 stub。"""
        super().__init__(("127.0.0.1", 0), _ContractRequestHandler)
        self.owner = owner


class _ContractRequestHandler(BaseHTTPRequestHandler):
    """实现模型列表与非流式 Chat Completions 两个最小端点。"""

    server: _ContractHTTPServer

    def do_GET(self) -> None:  # noqa: N802
        """返回唯一模型，兼容 AstrBot Provider 的可选模型探测。"""
        if self.path != "/v1/models":
            self._send_json(404, {"error": {"message": "not found"}})
            return
        self._send_json(
            200,
            {
                "object": "list",
                "data": [
                    {
                        "id": self.server.owner.model,
                        "object": "model",
                        "created": 0,
                        "owned_by": "memora-test",
                    }
                ],
            },
        )

    def do_POST(self) -> None:  # noqa: N802
        """验证鉴权与请求形状，并返回用途对应的确定性模型响应。"""
        if self.path != "/v1/chat/completions":
            self._send_json(404, {"error": {"message": "not found"}})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": {"message": "invalid length"}})
            return
        if content_length <= 0 or content_length > _MAX_REQUEST_BYTES:
            self._send_json(413, {"error": {"message": "invalid size"}})
            return
        try:
            payload = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": {"message": "invalid json"}})
            return

        authorization_valid = secrets.compare_digest(
            self.headers.get("Authorization", ""),
            f"Bearer {self.server.owner.api_key}",
        )
        serialized_messages = json.dumps(
            payload.get("messages", []),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        purpose = "memory" if "你正在总结对话记忆" in serialized_messages else "chat"
        observation = OpenAIRequestObservation(
            purpose=purpose,
            model=str(payload.get("model", "")),
            authorization_valid=authorization_valid,
            contains_canary=CONTRACT_CANARY in serialized_messages,
        )
        self.server.owner._record(observation)
        if not authorization_valid:
            self._send_json(401, {"error": {"message": "unauthorized"}})
            return
        if observation.model != self.server.owner.model:
            self._send_json(400, {"error": {"message": "unexpected model"}})
            return

        completion = (
            json.dumps(
                {
                    "memories": [
                        {
                            "content": CONTRACT_MEMORY,
                            "atom_type": "fact",
                            "importance": 0.9,
                            "entities": ["blackbox"],
                            "emotion_tags": [],
                            "confidence": 0.99,
                            "topics": ["contract"],
                            "key_facts": [CONTRACT_CANARY],
                            "participants": ["memora-test-user"],
                            "sentiment": "neutral",
                            "causal_relations": [],
                        }
                    ],
                    "confidence": 0.99,
                    "extraction_quality": "high",
                },
                ensure_ascii=False,
            )
            if purpose == "memory"
            else CONTRACT_REPLY
        )
        self._send_json(
            200,
            {
                "id": "chatcmpl-memora-contract",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": self.server.owner.model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": completion},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 4,
                    "total_tokens": 12,
                },
            },
        )

    def log_message(self, _format: str, *args: object) -> None:
        """禁止标准库把测试请求路径或载荷旁路写入 stderr。"""
        return None

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        """以 OpenAI 客户端可解析的 UTF-8 JSON 响应结束当前请求。"""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class OpenAIContractStub:
    """同步管理回环 OpenAI-compatible 服务及低敏请求观测。"""

    def __init__(self) -> None:
        """创建尚未启动且不包含外部秘密的 stub。"""
        self.api_key = "memora-contract-key-123456"
        self.model = "memora-contract-model"
        self._server: _ContractHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._observations: list[OpenAIRequestObservation] = []
        self._lock = threading.Lock()

    @property
    def api_base(self) -> str:
        """返回启动后仅指向回环地址的 OpenAI API Base。"""
        if self._server is None:
            raise RuntimeError("OpenAI contract stub 尚未启动")
        return f"http://127.0.0.1:{self._server.server_port}/v1"

    @property
    def observations(self) -> list[OpenAIRequestObservation]:
        """返回请求事实快照，避免测试修改服务内部列表。"""
        with self._lock:
            return list(self._observations)

    def start(self) -> None:
        """启动回环线程服务器；重复启动保持幂等。"""
        if self._server is not None:
            return
        self._server = _ContractHTTPServer(self)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="memora-openai-contract-stub",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        """同步停止服务器并等待线程退出，防止测试完成后遗留任务。"""
        server = self._server
        thread = self._thread
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=5)
            if thread.is_alive():
                raise RuntimeError("OpenAI contract stub 线程未能停止")
        self._server = None
        self._thread = None

    def provider_config(self) -> dict[str, object]:
        """生成强制 AstrBot 使用内置 OpenAI adapter 的测试 Provider 配置。"""
        return {
            "id": "memora-test-chat",
            "provider": "openai-compatible-contract",
            "type": "openai_chat_completion",
            "provider_type": "chat_completion",
            "enable": True,
            "model": self.model,
            "key": [self.api_key],
            "api_base": self.api_base,
            "timeout": 10,
            "proxy": "",
            "custom_headers": {},
        }

    def _record(self, observation: OpenAIRequestObservation) -> None:
        """在线程安全边界内追加一条不含正文的请求观测。"""
        with self._lock:
            self._observations.append(observation)
