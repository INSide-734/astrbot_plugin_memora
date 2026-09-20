"""存储格式构建器"""

from typing import Any


class StorageBuilder:
    """构建 (content, metadata) 的标准化存储格式"""

    @staticmethod
    def build_storage_format(
        fallback_excerpt: str,
        structured_data: dict[str, Any],
        is_group_chat: bool,
    ) -> tuple[str, dict[str, Any]]:
        """构建标准化存储格式。

        Args:
            fallback_excerpt: 回退文本摘要
            structured_data: LLM 结构化输出
            is_group_chat: 是否群聊
        """
        summary = str(structured_data.get("summary", "") or "")
        # key_facts 来自不可信的 LLM 结构化输出：先收敛类型，只接受非空字符串。
        # 否则该键为 None 会抛 TypeError、为字符串会被逐字符拆开、元素为标量时
        # 会把 "None" 之类的字面量写进唯一可检索正文。
        raw_key_facts = structured_data.get("key_facts")
        key_facts = (
            [
                item.strip()
                for item in raw_key_facts
                if isinstance(item, str) and item.strip()
            ]
            if isinstance(raw_key_facts, list)
            else []
        )

        # 正文只保留一份事实，避免摘要改述与事实列表重复；叙述另存 persona_summary。
        # 保留全部准入事实，不能把原补充片段的五条上限沿用到唯一可检索正文。
        canonical_summary = "；".join(key_facts) if key_facts else summary

        content = canonical_summary or fallback_excerpt

        # 隐私记忆 — 群聊 PUBLIC，私聊 CONFIDENTIAL
        privacy_level = "public" if is_group_chat else "confidential"

        metadata = {
            "topics": structured_data.get("topics", []),
            "key_facts": key_facts,
            "fact_source_evidence": structured_data.get("fact_source_evidence", []),
            "sentiment": structured_data.get("sentiment", "neutral"),
            "interaction_type": "group_chat" if is_group_chat else "private_chat",
            "privacy_level": privacy_level,
            "canonical_summary": canonical_summary,
            "persona_summary": summary,
            "summary_schema_version": "v2",
        }

        if is_group_chat and "participants" in structured_data:
            metadata["participants"] = structured_data["participants"]

        return content, metadata
