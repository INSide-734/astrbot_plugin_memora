import type {
  GateRuleAction,
  GateRuleData,
  GateRulePredicate,
  GateThresholds,
} from "@/types/config";

// 前端校验辅助：与后端 core/features/quality/domain/gate_config.py 的
// Pydantic 约束同构；复合值最终仍由后端兜底校验。

/** profile 名：1-32 位小写字母、数字、- 或 _。 */
export const GATE_PROFILE_NAME_RE = /^[a-z0-9_-]{1,32}$/;

/** 规则 id：1-64 位小写字母、数字、- 或 _。 */
export const GATE_RULE_ID_RE = /^[a-z0-9_-]{1,64}$/;

/** Judge 模板长度上限。 */
export const GATE_JUDGE_TEMPLATE_MAX = 2000;

/** 词表项上限（每项字符数与总条数）。 */
export const GATE_WORD_LIST_ITEM_MAX = 32;
export const GATE_WORD_LIST_MAX_ITEMS = 50;

/** 同义对上限与单侧字符上限。 */
export const GATE_SYNONYM_PAIR_MAX = 20;
export const GATE_SYNONYM_ITEM_MAX = 16;

/** 原因码映射条数上限。 */
export const GATE_OVERRIDES_MAX = 20;

/** 规则正则长度上限。 */
export const GATE_RULE_PATTERN_MAX = 500;

/** 规则描述长度上限。 */
export const GATE_RULE_DESCRIPTION_MAX = 200;

/** AND/OR 分组最大嵌套层数（根分组计第 1 层）。 */
export const GATE_AND_OR_MAX_DEPTH = 2;

/** 内置原因码（与后端 BUILTIN_GATE_REASON_CODES 同构）。 */
export const GATE_BUILTIN_REASON_CODES: readonly string[] = [
  "grounding_claim_missing",
  "grounding_source_missing",
  "grounding_reference_invalid",
  "grounding_source_evidence_missing",
  "grounding_source_evidence_invalid",
  "grounding_source_changed",
  "grounding_subject_ambiguous",
  "grounding_subject_mismatch",
  "grounding_numeric_conflict",
  "grounding_negation_conflict",
  "grounding_claim_unsupported",
  "grounding_needs_judge",
  "grounding_judge_supported",
  "grounding_judge_rejected",
  "grounding_judge_unavailable",
  "grounding_not_verified",
  "summary_quality_low",
];

const GATE_PLACEHOLDER_CLAIM = "{claim_text}";
const GATE_PLACEHOLDER_SOURCE = "{source_text}";
const GATE_PLACEHOLDER_RE = /\{([a-z0-9_]+)\}/g;
const GATE_ALLOWED_PLACEHOLDERS: Record<string, true> = {
  claim_text: true,
  source_text: true,
};

/** 会话类型选项文案 key（复用既有三语言登记）。 */
export const GATE_CHAT_TYPE_LABEL_KEYS: Record<string, string> = {
  private: "intelligence.trace.chatType.private",
  group: "intelligence.trace.chatType.group",
};

export type JudgeTemplateIssue =
  | { code: "missing_placeholders" }
  | { code: "unknown_placeholders"; placeholders: string[] }
  | { code: "too_long" };

/** 校验 Judge 模板（空模板 = 内置，恒通过）。 */
export function validateJudgeTemplate(template: string): JudgeTemplateIssue | null {
  if (!template) return null;
  if (template.length > GATE_JUDGE_TEMPLATE_MAX) {
    return { code: "too_long" };
  }
  if (
    !template.includes(GATE_PLACEHOLDER_CLAIM) ||
    !template.includes(GATE_PLACEHOLDER_SOURCE)
  ) {
    return { code: "missing_placeholders" };
  }
  const found = new Set<string>();
  for (const match of template.matchAll(GATE_PLACEHOLDER_RE)) {
    found.add(match[1]);
  }
  const unknown = Array.from(found)
    .filter((name) => !(name in GATE_ALLOWED_PLACEHOLDERS))
    .sort();
  if (unknown.length > 0) {
    return { code: "unknown_placeholders", placeholders: unknown };
  }
  return null;
}
/** Judge 支持分不得大于确定性通过分。 */
export function validateThresholdCross(thresholds: GateThresholds): boolean {
  return thresholds.min_judge_score > thresholds.min_deterministic_score;
}


export type ProfileNameIssue = "format" | "duplicate";

/** 校验 profile 名：格式 + 唯一性。 */
export function validateProfileName(
  name: string,
  existingNames: readonly string[],
): ProfileNameIssue | null {
  if (!GATE_PROFILE_NAME_RE.test(name)) return "format";
  if (existingNames.includes(name)) return "duplicate";
  return null;
}

export type RuleIdIssue = "format" | "duplicate";

/** 校验规则 id：格式 + 唯一性（existingIds 不含正在编辑的规则）。 */
export function validateRuleId(
  id: string,
  existingIds: readonly string[],
): RuleIdIssue | null {
  if (!GATE_RULE_ID_RE.test(id)) return "format";
  if (existingIds.includes(id)) return "duplicate";
  return null;
}

/** 校验正则：编译失败时返回底层错误信息，成功返回 null。 */
export function validateRuleRegex(pattern: string): string | null {
  try {
    // eslint-disable-next-line no-new
    new RegExp(pattern);
    return null;
  } catch (error) {
    return error instanceof Error ? error.message : String(error);
  }
}

/** AND/OR 分组嵌套超过上限时返回 true（not 不计入分组层数）。 */
export function validatePredicateDepth(root: GateRulePredicate | null | undefined): boolean {
  if (!root) return false;
  let maxGroupDepth = 0;
  const walk = (node: GateRulePredicate, depth: number): void => {
    if (node.op === "and" || node.op === "or") {
      maxGroupDepth = Math.max(maxGroupDepth, depth);
      for (const child of node.children ?? []) {
        walk(child, depth + 1);
      }
    } else if (node.op === "not" && node.child) {
      walk(node.child, depth);
    }
  };
  walk(root, 1);
  return maxGroupDepth > GATE_AND_OR_MAX_DEPTH;
}

/** 叶谓词是否填写完整（field 必填 + op 专属 payload）。 */
export function predicateLeafComplete(node: GateRulePredicate): boolean {
  if (node.op === "and" || node.op === "or") {
    return (node.children ?? []).every(predicateLeafComplete);
  }
  if (node.op === "not") {
    return node.child ? predicateLeafComplete(node.child) : false;
  }
  if (!node.field) return false;
  switch (node.op) {
    case "regex":
      return Boolean(node.pattern && node.pattern.length > 0);
    case "contains":
      return Boolean(node.values && node.values.length > 0);
    case "exists":
      return true;
    case "length_cmp":
      return Boolean(
        node.cmp &&
          typeof node.value === "number" &&
          Number.isFinite(node.value),
      );
    case "numeric_cmp":
      return Boolean(
        node.cmp &&
          typeof node.value === "number" &&
          Number.isFinite(node.value),
      );
    default:
      return false;
  }
}

/** 谓词树内是否存在正则叶且其正则无法编译。 */
export function predicateRegexError(
  root: GateRulePredicate,
): string | null {
  if (root.op === "and" || root.op === "or") {
    for (const child of root.children ?? []) {
      const error = predicateRegexError(child);
      if (error) return error;
    }
    return null;
  }
  if (root.op === "not") {
    return root.child ? predicateRegexError(root.child) : null;
  }
  if (root.op === "regex" && root.pattern) {
    return validateRuleRegex(root.pattern);
  }
  return null;
}

/** 规则动作 payload 是否完整（按 kind 互斥校验，与后端同构）。 */
export function ruleActionComplete(action: GateRuleAction): boolean {
  switch (action.kind) {
    case "force_disposition":
      return (
        action.value === "quarantine" ||
        action.value === "discard" ||
        action.value === "mark_write" ||
        action.value === "allow"
      );
    case "importance_delta":
      return (
        typeof action.delta === "number" &&
        Number.isFinite(action.delta) &&
        action.delta >= -1 &&
        action.delta <= 1
      );
    case "set_importance":
      return (
        typeof action.value === "number" &&
        Number.isFinite(action.value) &&
        action.value >= 0 &&
        action.value <= 1
      );
    case "add_topics":
      return Boolean(
        action.values &&
          action.values.length >= 1 &&
          action.values.length <= 5 &&
          action.values.every(
            (topic) => topic.length > 0 && topic.length <= GATE_WORD_LIST_ITEM_MAX,
          ),
      );
    case "set_privacy":
      return action.value === "public" || action.value === "confidential";
    case "drop_atoms":
      return action.value === true;
    default:
      return false;
  }
}

export type RuleDraftIssue =
  | "id_format"
  | "id_duplicate"
  | "description_too_long"
  | "depth"
  | "regex_invalid"
  | "leaf_incomplete"
  | "action_incomplete";

export interface RuleDraftValidation {
  issues: RuleDraftIssue[];
  /** regex_invalid 时携带底层编译错误信息。 */
  regexError?: string;
}

/** 规则草稿整体校验（Sheet 保存前调用）。 */
export function validateRuleDraft(
  rule: GateRuleData,
  existingIds: readonly string[],
): RuleDraftValidation {
  const issues: RuleDraftIssue[] = [];
  let regexError: string | undefined;

  if (validateRuleId(rule.id, existingIds) === "format") {
    issues.push("id_format");
  } else if (validateRuleId(rule.id, existingIds) === "duplicate") {
    issues.push("id_duplicate");
  }
  if (rule.description.length > GATE_RULE_DESCRIPTION_MAX) {
    issues.push("description_too_long");
  }
  if (validatePredicateDepth(rule.when)) {
    issues.push("depth");
  }
  if (!predicateLeafComplete(rule.when)) {
    issues.push("leaf_incomplete");
  }
  const regexIssue = predicateRegexError(rule.when);
  if (regexIssue) {
    issues.push("regex_invalid");
    regexError = regexIssue;
  }
  if (!ruleActionComplete(rule.action)) {
    issues.push("action_incomplete");
  }

  return { issues, regexError };
}

/** 判断节点是否为 AND/OR 分组。 */
export function isGroupPredicate(
  node: GateRulePredicate,
): node is GateRulePredicate & { op: "and" | "or" } {
  return node.op === "and" || node.op === "or";
}
