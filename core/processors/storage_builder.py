"""存储格式构建器"""

from typing import Any


class StorageBuilder:
    """构建 (content, metadata) 的标准化存储格式"""

    @staticmethod
    def build_storage_format(
            fallback_excerpt: str,
        structured_data: dict[str, Any],
        is_group_chat: bool,
        persona_interpretations: dict[str, str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """构建标准化存储格式。

        Args:
            fallback_excerpt: 回退文本摘要
            structured_data: LLM 结构化输出
            is_group_chat: 是否群聊
            persona_interpretations:— 多角色解读字典
                {persona_id: interpretation_text, ...}
                同一事实对不同角色有不同意义，存储时保留所有解读，
                检索时按当前 persona 匹配加权。
        """
        summary = structured_data.get("summary", "")
        key_facts = structured_data.get("key_facts", [])

        canonical_parts = [summary] if summary else []
        if key_facts:
            canonical_parts.append("；".join(str(f) for f in key_facts[:5]))
        canonical_summary = " | ".join(canonical_parts) if canonical_parts else ""

        content = canonical_summary or fallback_excerpt

        # 隐私记忆 — 群聊 PUBLIC，私聊 CONFIDENTIAL
        privacy_level = "public" if is_group_chat else "confidential"

        metadata = {
            "topics": structured_data.get("topics", []),
            "key_facts": key_facts,
            "sentiment": structured_data.get("sentiment", "neutral"),
            "interaction_type": "group_chat" if is_group_chat else "private_chat",
            "privacy_level": privacy_level,
            "canonical_summary": canonical_summary,
            "persona_summary": summary,
            "summary_schema_version": "v2",
        }

        # 人格感知记忆解读 — 多版本存储
        if persona_interpretations:
            metadata["persona_interpretations"] = {
                str(k): str(v) for k, v in persona_interpretations.items() if v
            }

        if is_group_chat and "participants" in structured_data:
            metadata["participants"] = structured_data["participants"]

        return content, metadata
