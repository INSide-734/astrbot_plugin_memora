"""共享安全策略的运行时配置模型。"""

from pydantic import BaseModel, Field


class SecurityConfig(BaseModel):
    """Prompt 防护与 LLM 输出护栏配置。"""

    prompt_protection_enabled: bool = Field(
        default=True, description="是否对注入的记忆上下文启用提示词保护包装"
    )
    sanitize_llm_response: bool = Field(
        default=True, description="是否在助手回复落库前清理泄露的内部提示词片段"
    )
    guardrails_enabled: bool = Field(
        default=True, description="是否启用记忆抽取输出的结构化护栏校验"
    )
    double_check_enabled: bool = Field(
        default=True, description="是否启用提示词保护与回复清洗的二次校验"
    )
    wrapper_template_index: int = Field(
        default=0, ge=0, le=10, description="提示词保护包装模板索引"
    )
    strict_mode: bool = Field(
        default=False,
        description="严格模式下安全组件失败会跳过注入或落库，而不是降级放行",
    )


__all__ = ["SecurityConfig"]
