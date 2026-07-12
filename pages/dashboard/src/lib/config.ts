import type { ConfigObject, ConfigValue, JsonValue } from "@/types/config";

function isConfigObject(value: unknown): value is ConfigObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const DANGEROUS_CONFIG_KEYS = new Set([
  "__proto__",
  "prototype",
  "constructor",
]);

function assertSafeConfigKey(key: string): void {
  if (DANGEROUS_CONFIG_KEYS.has(key)) {
    throw new Error(`Unsafe config key: ${key}`);
  }
}

function configObjectKeys(value: ConfigObject): string[] {
  const keys = Object.keys(value);
  keys.forEach(assertSafeConfigKey);
  return keys;
}

function assertSafeConfigValue(value: unknown): void {
  if (Array.isArray(value)) {
    value.forEach(assertSafeConfigValue);
    return;
  }
  if (!isConfigObject(value)) return;
  for (const key of configObjectKeys(value)) {
    assertSafeConfigValue(value[key]);
  }
}

function pathSegments(path: string): string[] {
  const segments = path.split(".");
  if (!path || segments.some((segment) => segment.length === 0)) {
    throw new Error(`Invalid config path: ${path}`);
  }
  segments.forEach(assertSafeConfigKey);
  return segments;
}

function hasOwn(value: ConfigObject, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(value, key);
}

export function getConfigValue(config: unknown, path: string): ConfigValue {
  let current: unknown = config;
  for (const segment of pathSegments(path)) {
    if (!isConfigObject(current) || !hasOwn(current, segment)) {
      return undefined;
    }
    current = current[segment];
  }
  return current as ConfigValue;
}

export function setConfigValue<T extends ConfigObject>(
  config: T,
  path: string,
  value: ConfigValue
): T {
  const segments = pathSegments(path);
  assertSafeConfigValue(config);
  assertSafeConfigValue(value);

  const setAt = (current: ConfigValue, index: number): ConfigObject => {
    const base = isConfigObject(current) ? current : {};
    const next: ConfigObject = { ...base };
    const segment = segments[index];
    const currentValue = hasOwn(base, segment) ? base[segment] : undefined;

    next[segment] =
      index === segments.length - 1
        ? value
        : setAt(currentValue, index + 1);
    return next;
  };

  return setAt(config, 0) as T;
}

function configValuesEqual(left: unknown, right: unknown): boolean {
  if (left === right) return true;

  if (Array.isArray(left) || Array.isArray(right)) {
    if (!Array.isArray(left) || !Array.isArray(right)) return false;
    return (
      left.length === right.length &&
      left.every((item, index) => configValuesEqual(item, right[index]))
    );
  }

  if (!isConfigObject(left) || !isConfigObject(right)) return false;

  const leftKeys = configObjectKeys(left);
  const rightKeys = configObjectKeys(right);
  if (leftKeys.length !== rightKeys.length) return false;

  return leftKeys.every(
    (key) => hasOwn(right, key) && configValuesEqual(left[key], right[key])
  );
}

export function configValueEquals(left: unknown, right: unknown): boolean {
  assertSafeConfigValue(left);
  assertSafeConfigValue(right);
  return configValuesEqual(left, right);
}

function cloneConfigValue<T>(value: T): T {
  if (Array.isArray(value)) {
    return value.map((item) => cloneConfigValue(item)) as T;
  }
  if (isConfigObject(value)) {
    const cloned: ConfigObject = {};
    for (const key of configObjectKeys(value)) {
      cloned[key] = cloneConfigValue(value[key]);
    }
    return cloned as T;
  }
  return value;
}

export function cloneConfig<T>(value: T): T {
  assertSafeConfigValue(value);
  return cloneConfigValue(value);
}

export function diffConfigLeafPaths(
  before: ConfigObject,
  after: ConfigObject
): string[] {
  assertSafeConfigValue(before);
  assertSafeConfigValue(after);
  const changed: string[] = [];

  const visit = (
    beforeValue: ConfigValue,
    afterValue: ConfigValue,
    path: string,
    beforeExists: boolean,
    afterExists: boolean
  ) => {
    if (
      beforeExists &&
      afterExists &&
      configValuesEqual(beforeValue, afterValue)
    ) {
      return;
    }

    const beforeObject = beforeExists && isConfigObject(beforeValue);
    const afterObject = afterExists && isConfigObject(afterValue);
    const canDescend =
      (beforeObject && afterObject) ||
      (beforeObject && !afterExists) ||
      (!beforeExists && afterObject);

    if (canDescend) {
      const beforeRecord = beforeObject ? beforeValue : {};
      const afterRecord = afterObject ? afterValue : {};
      const keys = Array.from(
        new Set([
          ...configObjectKeys(beforeRecord),
          ...configObjectKeys(afterRecord),
        ])
      ).sort();

      if (keys.length === 0 && path) {
        changed.push(path);
        return;
      }

      for (const key of keys) {
        visit(
          beforeRecord[key],
          afterRecord[key],
          path ? `${path}.${key}` : key,
          hasOwn(beforeRecord, key),
          hasOwn(afterRecord, key)
        );
      }
      return;
    }

    if (path) changed.push(path);
  };

  visit(before, after, "", true, true);
  return changed.sort();
}

export function buildConfigChanges(
  draft: ConfigObject,
  dirtyPaths: readonly string[]
): Record<string, ConfigValue> {
  assertSafeConfigValue(draft);
  const changes: Record<string, ConfigValue> = {};
  for (const path of Array.from(new Set(dirtyPaths)).sort()) {
    changes[path] = cloneConfig(getConfigValue(draft, path));
  }
  return changes;
}

function toJsonConfigValue(value: unknown, path: string): JsonValue {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean"
  ) {
    return value;
  }
  if (typeof value === "number") {
    if (Number.isFinite(value)) return value;
    throw new Error(`Invalid JSON config value at ${path}: non-finite number`);
  }
  if (Array.isArray(value)) {
    return value.map((item, index) =>
      toJsonConfigValue(item, `${path}[${index}]`)
    );
  }
  if (isConfigObject(value)) {
    const converted: Record<string, JsonValue> = {};
    for (const key of configObjectKeys(value)) {
      converted[key] = toJsonConfigValue(value[key], `${path}.${key}`);
    }
    return converted;
  }
  throw new Error(`Invalid JSON config value at ${path}`);
}

export function toJsonConfigChanges(
  changes: Readonly<Record<string, unknown>>
): Record<string, JsonValue> {
  const converted: Record<string, JsonValue> = {};
  for (const path of Object.keys(changes).sort()) {
    pathSegments(path);
    converted[path] = toJsonConfigValue(changes[path], path);
  }
  return converted;
}

export function applyConfigChanges<T extends ConfigObject>(
  remote: T,
  changes: Readonly<Record<string, ConfigValue>>
): T {
  return Object.keys(changes)
    .sort()
    .reduce(
      (result, path) =>
        setConfigValue(result, path, cloneConfig(changes[path])),
      remote
    );
}

export function rebaseConfig<T extends ConfigObject>(
  remote: T,
  draft: ConfigObject,
  dirtyPaths: readonly string[]
): T {
  return applyConfigChanges(remote, buildConfigChanges(draft, dirtyPaths));
}
