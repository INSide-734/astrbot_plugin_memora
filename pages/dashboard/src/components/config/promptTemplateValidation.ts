// Config 页 prompt_templates 模板前端校验：与后端
// core/platform/config/runtime_feature_config.py PromptTemplatesConfig 同构。

/** 抽取模板 7 占位符白名单（conversation 必含）。 */
export const PROMPT_TEMPLATE_ALLOWED_PLACEHOLDERS = [
  "conversation",
  "current_date",
  "chat_type",
  "continuity_topics",
  "interests",
  "emotion_tags",
  "emotional_intensity",
] as const;

const PROMPT_TEMPLATE_PLACEHOLDER_RE = /\{([a-z0-9_]+)\}/g;

export type PromptTemplateIssue =
  | { code: "missing_conversation" }
  | { code: "unknown_placeholders"; placeholders: string[] }
  | { code: "unclosed_brace" };

/** 校验抽取模板（空模板 = 文件默认，恒通过）。 */
export function validatePromptTemplate(
  template: string,
): PromptTemplateIssue | null {
  if (!template) return null;
  if (!template.includes("{conversation}")) {
    return { code: "missing_conversation" };
  }
  const found = new Set<string>();
  for (const match of template.matchAll(PROMPT_TEMPLATE_PLACEHOLDER_RE)) {
    found.add(match[1]);
  }
  const unknown = Array.from(found)
    .filter(
      (name) =>
        !(
          PROMPT_TEMPLATE_ALLOWED_PLACEHOLDERS as readonly string[]
        ).includes(name),
    )
    .sort();
  if (unknown.length > 0) {
    return { code: "unknown_placeholders", placeholders: unknown };
  }
  const residual = template.replace(PROMPT_TEMPLATE_PLACEHOLDER_RE, "");
  if (residual.includes("{") || residual.includes("}")) {
    return { code: "unclosed_brace" };
  }
  return null;
}
