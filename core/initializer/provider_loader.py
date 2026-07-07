"""Provider 加载器"""

from astrbot.api import logger
from astrbot.core.provider.provider import EmbeddingProvider, Provider


class ProviderLoader:
    """初始化 Embedding 和 LLM Provider"""

    def __init__(self, context, config_manager):
        self.context = context
        self.config_manager = config_manager

    def initialize_providers(
        self, embedding_provider, llm_provider, silent: bool = False
    ):
        emb = embedding_provider
        llm = llm_provider

        # --- Embedding Provider ---
        emb_id = self.config_manager.get("provider_settings.embedding_provider_id")
        if emb_id:
            provider = self.get_provider_by_id(emb_id, silent=silent)
            if provider and isinstance(provider, EmbeddingProvider):
                emb = provider
                if not silent:
                    logger.info(f"成功从配置加载 Embedding Provider: {emb_id}")
            elif provider and not silent:
                logger.warning(f"Provider {emb_id} 不是 EmbeddingProvider 类型")

        if not emb:
            embedding_providers = self.context.get_all_embedding_providers()
            if embedding_providers:
                emb = embedding_providers[0]
                if not silent:
                    provider_id = getattr(
                        emb.provider_config,
                        "id",
                        emb.provider_config.get("id", "unknown"),
                    )
                    logger.info(f"未指定 Embedding Provider，使用默认的: {provider_id}")
            else:
                emb = None
                if not silent:
                    logger.debug("没有可用的 Embedding Provider")

        # --- LLM Provider ---
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
            except (ValueError, Exception) as e:
                if not silent:
                    logger.debug(f"获取默认 LLM Provider 失败: {e}")
                llm = None

        return emb, llm

    def get_provider_by_id(self, provider_id: str, *, silent: bool):
        if not provider_id:
            return None
        if not silent:
            return self.context.get_provider_by_id(provider_id)
        provider_manager = getattr(self.context, "provider_manager", None)
        inst_map = getattr(provider_manager, "inst_map", None)
        if isinstance(inst_map, dict):
            return inst_map.get(provider_id)
        return None
