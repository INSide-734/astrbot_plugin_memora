"""Provider 选择与能力校验的组合根实现。"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger
from astrbot.api.provider import Provider

from ...shared.adapter_capabilities import UnsupportedAdapterCapability
from ..provider import EmbeddingProviderAdapter


def _supports_embedding(provider: Any) -> bool:
    """判断候选对象是否公开可用稳定的 Embedding 调用入口。

    参数:
        provider: AstrBot 返回的 Provider 候选对象。

    返回:
        能够构造冻结 Embedding adapter 时返回 ``True``，否则返回 ``False``。
    """

    try:
        EmbeddingProviderAdapter.from_provider(provider)
    except UnsupportedAdapterCapability:
        return False
    return True


class ProviderLoader:
    """按配置优先级选择 Embedding 与聊天 Provider。"""

    def __init__(self, context: Any, config_manager: Any) -> None:
        """保存 AstrBot 上下文与已校验的配置读取器。

        参数:
            context: AstrBot 插件运行时上下文。
            config_manager: 提供点号路径读取的配置管理器。
        """

        self.context = context
        self.config_manager = config_manager

    def initialize_providers(
        self,
        embedding_provider: Any,
        llm_provider: Provider | None,
        silent: bool = False,
    ) -> tuple[Any | None, Provider | None]:
        """按配置 ID 和 AstrBot 默认顺序选择两类 Provider。

        参数:
            embedding_provider: 上一次检查保留的 Embedding Provider。
            llm_provider: 上一次检查保留的聊天 Provider；当前契约会重新选择。
            silent: 是否抑制等待轮询期间的普通日志。

        返回:
            ``(embedding_provider, llm_provider)`` 二元组；缺失项为 ``None``。
        """

        emb = embedding_provider
        llm = llm_provider

        # 配置项优先；Embedding 类型未公开，因此只依赖冻结能力探针。
        emb_id = self.config_manager.get("provider_settings.embedding_provider_id")
        if emb_id:
            provider = self.get_provider_by_id(emb_id, silent=silent)
            if provider and _supports_embedding(provider):
                emb = provider
                if not silent:
                    logger.info(f"成功从配置加载 Embedding Provider: {emb_id}")
            elif provider and not silent:
                logger.warning(f"Provider {emb_id} 不具备 Embedding 能力")

        if not emb:
            embedding_providers = self.context.get_all_embedding_providers()
            emb = next(
                (
                    provider
                    for provider in embedding_providers
                    if _supports_embedding(provider)
                ),
                None,
            )
            if emb is not None:
                if not silent:
                    provider_id = getattr(
                        emb.provider_config,
                        "id",
                        emb.provider_config.get("id", "unknown"),
                    )
                    logger.info(f"未指定 Embedding Provider，使用默认的: {provider_id}")
            elif not silent:
                logger.debug("没有可用的 Embedding Provider")

        # 保持既有契约：聊天 Provider 每次都从配置或 AstrBot 默认项重新选择。
        llm = None
        llm_id = self.config_manager.get("provider_settings.llm_provider_id")
        if llm_id:
            provider = self.get_provider_by_id(llm_id, silent=silent)
            if provider and isinstance(provider, Provider):
                llm = provider
                if not silent:
                    logger.info(f"成功从配置加载 LLM Provider: {llm_id}")
            elif provider and not silent:
                logger.warning(
                    f"Provider {llm_id} 不是聊天 Provider 类型，已忽略该配置。"
                )

        if not llm:
            try:
                if silent and not self.context.get_all_providers():
                    llm = None
                    return emb, llm
                default_provider = self.context.get_using_provider()
                if default_provider and not isinstance(default_provider, Provider):
                    if not silent:
                        logger.warning(
                            "AstrBot 默认 Provider 类型不正确，期望聊天 Provider。"
                        )
                    llm = None
                else:
                    llm = default_provider
                if not silent and llm:
                    logger.info("使用 AstrBot 当前默认的 LLM Provider。")
            except (ValueError, Exception) as exc:
                if not silent:
                    logger.debug(f"获取默认 LLM Provider 失败: {exc}")
                llm = None

        return emb, llm

    def get_provider_by_id(self, provider_id: str, *, silent: bool) -> Any | None:
        """按 ID 查询 Provider，并在静默轮询时避免触发框架告警。

        参数:
            provider_id: AstrBot Provider 的配置 ID。
            silent: 是否只读取 Provider manager 的当前实例映射。

        返回:
            已注册 Provider；ID 为空或当前未注册时返回 ``None``。
        """

        if not provider_id:
            return None
        if not silent:
            return self.context.get_provider_by_id(provider_id)
        provider_manager = getattr(self.context, "provider_manager", None)
        inst_map = getattr(provider_manager, "inst_map", None)
        if isinstance(inst_map, dict):
            return inst_map.get(provider_id)
        return None


__all__ = ["ProviderLoader"]
