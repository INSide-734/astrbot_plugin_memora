export type JsonPrimitive = string | number | boolean | null;

export type JsonValue = JsonPrimitive | JsonObject | JsonValue[];

export interface JsonObject {
  [key: string]: JsonValue;
}

// Config helpers retain explicit undefined values so comparisons can distinguish
// them from absent keys before data is sent across the JSON bridge.
export type ConfigValue = JsonValue | ConfigObject | ConfigValue[] | undefined;

export interface ConfigObject {
  [key: string]: ConfigValue;
}

export type ConfigLeafType = "bool" | "string" | "text" | "int" | "float";

interface ConfigSchemaMetadata {
  description?: string;
  hint?: string;
  default?: ConfigValue;
  options?: Array<string | number>;
  invisible?: boolean;
  _special?: "select_provider";
}

export interface ConfigObjectSchemaNode extends ConfigSchemaMetadata {
  type: "object";
  items: Record<string, ConfigSchemaNode>;
}

export interface ConfigLeafSchemaNode extends ConfigSchemaMetadata {
  type: ConfigLeafType;
  min?: number;
  max?: number;
  step?: number;
}

export type ConfigSchemaNode = ConfigObjectSchemaNode | ConfigLeafSchemaNode;

export interface ConfigProviderOption {
  id: string;
  label: string;
}

export interface ConfigProviderOptions {
  llm: ConfigProviderOption[];
  embedding: ConfigProviderOption[];
}

export interface ConfigCapabilities {
  hot_reload: boolean;
}

export interface ConfigSchemaData {
  plugin_name: string;
  schema: Record<string, ConfigSchemaNode>;
  provider_options: ConfigProviderOptions;
  capabilities: ConfigCapabilities;
}

interface ConfigStateBase {
  revision: string;
  instance_id: string;
}

export interface ChangedConfigStateData extends ConfigStateBase {
  changed: true;
  config: ConfigObject;
}

export interface UnchangedConfigStateData extends ConfigStateBase {
  changed: false;
  config?: never;
}

export type ConfigStateData =
  | ChangedConfigStateData
  | UnchangedConfigStateData;

export interface ConfigApplyData {
  revision: string;
  changed_paths: string[];
  reload_scheduled: boolean;
  restart_required: boolean;
  rebuild_required: boolean;
  instance_id: string;
}

export interface ConfigRuntimeEffects {
  manualRestartRequired: boolean;
  rebuildRequired: boolean;
}

export interface ConfigApplyRequest {
  base_revision: string;
  changes: Record<string, JsonValue>;
}

export type ConfigErrorCode =
  | "config_conflict"
  | "validation_failed"
  | "persist_failed"
  | "invalid_request"
  | "schema_unavailable"
  | "state_unavailable";

export interface ConfigErrorData {
  current_revision?: string;
  field_errors?: Record<string, string>;
}

export interface ConfigApiError {
  status: "error";
  code: ConfigErrorCode;
  message: string;
  data?: ConfigErrorData;
}

export interface ConfigApiSuccess<T> {
  status: "ok";
  data: T;
}

export type ConfigApiResponse<T> = ConfigApiSuccess<T> | ConfigApiError;

export type ConfigSyncStatus =
  | "loading"
  | "synced"
  | "dirty"
  | "applying"
  | "reloading"
  | "conflict"
  | "offline"
  | "error";

export interface ConfigSyncError {
  kind: "transport" | "protocol";
  message: string;
  code?: ConfigErrorCode;
  data?: ConfigErrorData;
}

export interface ConfigRemoteSnapshot {
  config: ConfigObject;
  revision: string;
  instanceId: string;
}

export interface ConfigSyncOptions {
  pollIntervalMs?: number;
  reloadTimeoutMs?: number;
}

/** 配置同步 hook 向页面公开的完整状态与操作契约。 */
export interface ConfigSyncResult {
  schemaData: ConfigSchemaData | null;
  baseConfig: ConfigObject | null;
  draft: ConfigObject | null;
  revision: string | null;
  instanceId: string | null;
  remoteConfig: ConfigObject | null;
  remoteRevision: string | null;
  remoteInstanceId: string | null;
  dirtyPaths: string[];
  localPaths: string[];
  remotePaths: string[];
  overlapPaths: string[];
  fieldErrors: Record<string, string>;
  status: ConfigSyncStatus;
  error: ConfigSyncError | null;
  runtimeEffects: ConfigRuntimeEffects | null;
  changeField: (path: string, value: ConfigValue) => void;
  refresh: () => Promise<void>;
  apply: () => Promise<void>;
  discardLocal: () => void;
  acceptRemote: () => void;
  rebaseRemote: () => void;
}

// ---- 记忆写入门禁（quality.gate）领域类型 ----
// 与后端 core/features/quality/domain/gate_config.py 的 Pydantic 模型同构：
// GateConfig { enabled, default_profile, bindings, profiles }，profile 内含
// checks/thresholds/scoring/references/quality/word_lists/judge/disposition/
// disposition_overrides/rules。复合分支由后端 Pydantic 兜底校验。

/** 处置策略：隔离 / 丢弃 / 标记写入。 */
export type GateDisposition = "quarantine" | "discard" | "mark_write";

/** 规则强制处置（比默认处置多一个 allow）。 */
export type GateRuleForce = GateDisposition | "allow";

/** 会话类型（绑定与 dry-run 上下文共用）。 */
export type GateChatType = "private" | "group";

/** 谓词运算符。 */
export type GatePredicateOp =
  | "regex"
  | "contains"
  | "exists"
  | "length_cmp"
  | "numeric_cmp"
  | "and"
  | "or"
  | "not";

/** 谓词可用字段（脱敏候选视图）。 */
export type GateRuleField =
  | "content"
  | "summary"
  | "key_facts"
  | "topics"
  | "participants"
  | "importance";

/** 比较运算符。 */
export type GateCompareOp = "gt" | "gte" | "lt" | "lte" | "eq";

/** 规则动作类型。 */
export type GateActionKind =
  | "force_disposition"
  | "importance_delta"
  | "set_importance"
  | "add_topics"
  | "set_privacy"
  | "drop_atoms";

/** 词表模式：追加（与内置并集）或替换（完全掌控）。 */
export type GateListMode = "append" | "replace";

/** 门禁检查开关。 */
export type GateChecks = {
  numeric_check: boolean;
  negation_check: boolean;
  group_subject_check: boolean;
  quality_low_check: boolean;
};

/** 判定阈值；后端约束 min_judge_score ≤ min_deterministic_score。 */
export type GateThresholds = {
  min_deterministic_score: number;
  min_judge_score: number;
  min_inference_score: number;
};

/** 支持分参数：token 分与 SequenceMatcher 序列分加权。 */
export type GateScoring = {
  token_weight: number;
  sequence_enabled: boolean;
  sequence_weight: number;
};

/** 引用上限。 */
export type GateReferences = {
  max_references: number;
};

/** 质量判定参数。 */
export type GateQualityParams = {
  min_summary_chars: number;
};

/** 可配置词表（模式 + 词项）。 */
export type GateWordListConfig = {
  mode: GateListMode;
  items: string[];
};

/** 同义替换对。 */
export type GateSynonymPair = {
  source: string;
  target: string;
};

/** 词表组。 */
export type GateWordLists = {
  negation_whitelist: string[];
  negation_markers: GateWordListConfig;
  generic_terms: GateWordListConfig;
  synonym_pairs: GateSynonymPair[];
};

/** Judge 开关与自定义模板（空模板 = 内置）。 */
export type GateJudge = {
  enabled: boolean;
  prompt_template: string;
};

/** 规则谓词树节点。 */
export type GateRulePredicate = {
  op: GatePredicateOp;
  field?: GateRuleField | null;
  pattern?: string | null;
  values?: string[] | null;
  cmp?: GateCompareOp | null;
  value?: number | null;
  children?: GateRulePredicate[] | null;
  child?: GateRulePredicate | null;
};

/** 规则动作（按 kind 互斥携带 payload）。 */
export type GateRuleAction = {
  kind: GateActionKind;
  value?: string | number | boolean | null;
  delta?: number | null;
  values?: string[] | null;
};

/** 门禁规则。 */
export type GateRuleData = {
  id: string;
  enabled: boolean;
  description: string;
  when: GateRulePredicate;
  action: GateRuleAction;
};

/** profile 绑定（按序首个匹配生效）。 */
export type GateBindingData = {
  profile: string;
  chat_type?: GateChatType | null;
  group_id?: string | null;
  persona_id?: string | null;
};

/** 门禁 profile。 */
export type GateProfileData = {
  name: string;
  checks: GateChecks;
  thresholds: GateThresholds;
  scoring: GateScoring;
  references: GateReferences;
  quality: GateQualityParams;
  word_lists: GateWordLists;
  judge: GateJudge;
  disposition: GateDisposition;
  disposition_overrides: Record<string, GateDisposition>;
  rules: GateRuleData[];
};

/** 门禁整体配置（config 对象 quality.gate 分支）。 */
export type GateConfigData = {
  enabled: boolean;
  default_profile: string;
  bindings: GateBindingData[];
  profiles: GateProfileData[];
};
