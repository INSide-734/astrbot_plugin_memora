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
  instance_id: string;
}

export interface ConfigApplyRequest {
  base_revision: string;
  changes: Record<string, ConfigValue>;
}

export type ConfigErrorCode =
  | "config_conflict"
  | "validation_failed"
  | "persist_failed"
  | "invalid_request"
  | "schema_unavailable";

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
