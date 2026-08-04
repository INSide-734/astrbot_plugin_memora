"""为真实 AstrBot 黑盒测试注册确定性 Provider。"""

from astrbot.core.provider.entities import LLMResponse, ProviderType
from astrbot.core.provider.provider import EmbeddingProvider, Provider
from astrbot.core.provider.register import register_provider_adapter

CHAT_PROVIDER_ID = "memora-test-chat"
EMBEDDING_PROVIDER_ID = "memora-test-embedding"


@register_provider_adapter("memora_test_chat", "Memora 测试聊天 Provider")
class MemoraTestChatProvider(Provider):
    """通过 AstrBot ProviderManager 实例化的最小确定性聊天 Provider。"""

    def get_current_key(self) -> str:
        """返回只用于本地测试协议的固定密钥标识。"""
        return "test-only"

    def set_key(self, key: str) -> None:
        """保存 AstrBot 轮换的测试密钥，但不执行任何网络操作。"""
        self._key = key

    async def get_models(self) -> list[str]:
        """返回唯一的确定性测试聊天模型。"""
        return ["memora-test-chat-model"]

    async def text_chat(self, **kwargs: object) -> LLMResponse:
        """忽略请求内容并返回固定最小响应。"""
        return LLMResponse(role="assistant", completion_text="测试响应")


@register_provider_adapter(
    "memora_test_embedding",
    "Memora 测试 Embedding Provider",
    provider_type=ProviderType.EMBEDDING,
)
class MemoraTestEmbeddingProvider(EmbeddingProvider):
    """返回跨平台稳定四维向量的真实 AstrBot Embedding Provider。"""

    def get_dim(self) -> int:
        """返回测试协议固定的四维向量长度。"""
        return 4

    async def get_embedding(self, text: str) -> list[float]:
        """为任意文本返回相同的四维确定性向量。"""
        return [1.0, 0.0, 0.0, 0.0]

    async def get_embeddings(self, text: list[str]) -> list[list[float]]:
        """按输入条目数量批量返回四维确定性向量。"""
        return [await self.get_embedding(item) for item in text]
