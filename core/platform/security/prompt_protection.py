"""将既有提示词保护实现暴露为共享运行时端口。"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

from .prompt_sanitizer import PromptProtectionService


class PromptProtectionAdapter:
    """包装唯一安全实现，并追踪运行时创建的请求作用域。

    功能模块只接收 ``PromptProtectionPort``；这个适配器保留既有服务作为
    唯一清洗实现，并在插件停止时释放本运行时登记的作用域。
    """

    def __init__(self, service: PromptProtectionService) -> None:
        """保存单例安全服务并初始化无敏感内容的作用域标识集合。

        参数:
            service: 已按安全配置创建的提示词保护服务。
        """

        self._service = service
        self._scope_ids: set[str] = set()
        self._lock = threading.RLock()
        self._closed = False

    def wrap_prompt(
        self,
        content: str,
        label: str = "memory_context",
        *,
        register_for_filter: bool = True,
        scope_id: str | None = None,
    ) -> str:
        """包装内容，并在成功登记后追踪关联的请求作用域。

        参数:
            content: 已经完成本地授权检查的待包装文本。
            label: 供底层服务保留的内容标签。
            register_for_filter: 是否登记内容供回复清洗使用。
            scope_id: 当前请求的唯一保护作用域。

        返回:
            底层服务产生的保护包装文本。

        异常:
            RuntimeError: 适配器已关闭时拒绝创建新的保护状态。
        """

        with self._lock:
            if self._closed:
                raise RuntimeError("prompt_protection_closed")
            wrapped = self._service.wrap_prompt(
                content,
                label=label,
                register_for_filter=register_for_filter,
                scope_id=scope_id,
            )
            if register_for_filter and isinstance(scope_id, str) and scope_id:
                self._scope_ids.add(scope_id)
            return wrapped

    def sanitize_response(
        self,
        response: str,
        *,
        enable_validation: bool | None = None,
        scope_id: str | None = None,
        consume_scope: bool = False,
    ) -> tuple[str, Mapping[str, Any]]:
        """清洗回复，并在消费作用域后同步移除本地生命周期登记。

        参数:
            response: 未信任的模型回复。
            enable_validation: 是否覆盖底层双重校验开关。
            scope_id: 与注入阶段对应的请求作用域。
            consume_scope: 是否在完成后消费该作用域。

        返回:
            清洗后的回复及底层服务生成的安全报告。
        """

        try:
            with self._lock:
                return self._service.sanitize_response(
                    response,
                    enable_validation=enable_validation,
                    scope_id=scope_id,
                    consume_scope=consume_scope,
                )
        finally:
            if consume_scope:
                self._forget_scope(scope_id)

    def has_scope(self, scope_id: str | None) -> bool:
        """返回指定作用域是否仍由底层安全服务持有。"""

        with self._lock:
            if self._closed:
                return False
            return self._service.has_scope(scope_id)

    def discard_scope(self, scope_id: str | None) -> None:
        """释放一个请求作用域，并移除适配器的生命周期登记。"""

        try:
            with self._lock:
                self._service.discard_scope(scope_id)
        finally:
            self._forget_scope(scope_id)

    def close(self) -> None:
        """释放本运行时登记的所有作用域，且保持幂等。"""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            scope_ids = tuple(self._scope_ids)
            self._scope_ids.clear()

        first_error: Exception | None = None
        for scope_id in scope_ids:
            try:
                self._service.discard_scope(scope_id)
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def _forget_scope(self, scope_id: str | None) -> None:
        """从无敏感内容的生命周期集合中移除一个作用域标识。"""

        if not isinstance(scope_id, str) or not scope_id:
            return
        with self._lock:
            self._scope_ids.discard(scope_id)


def build_prompt_protection_port(
    *,
    wrapper_template_index: int = 0,
    enable_double_check: bool = True,
) -> PromptProtectionAdapter:
    """创建唯一底层服务并将其封装为共享提示词保护端口。

    参数:
        wrapper_template_index: 元指令包装模板索引。
        enable_double_check: 是否启用回复泄露双重验证。

    返回:
        满足 ``PromptProtectionPort`` 的平台适配器。
    """

    return PromptProtectionAdapter(
        PromptProtectionService(
            wrapper_template_index=wrapper_template_index,
            enable_double_check=enable_double_check,
        )
    )


__all__ = ["PromptProtectionAdapter", "build_prompt_protection_port"]
