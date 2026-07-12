import type { ConfigObject, ConfigValue } from "@/types/config";

function isConfigObject(value: unknown): value is ConfigObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function pathSegments(path: string): string[] {
  const segments = path.split(".");
  if (!path || segments.some((segment) => segment.length === 0)) {
    throw new Error(`Invalid config path: ${path}`);
  }
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

  const setAt = (current: ConfigValue, index: number): ConfigObject => {
    const base = isConfigObject(current) ? current : {};
    const next: ConfigObject = { ...base };
    const segment = segments[index];

    next[segment] =
      index === segments.length - 1
        ? value
        : setAt(base[segment], index + 1);
    return next;
  };

  return setAt(config, 0) as T;
}

export function configValueEquals(left: unknown, right: unknown): boolean {
  if (left === right) return true;

  if (Array.isArray(left) || Array.isArray(right)) {
    if (!Array.isArray(left) || !Array.isArray(right)) return false;
    return (
      left.length === right.length &&
      left.every((item, index) => configValueEquals(item, right[index]))
    );
  }

  if (!isConfigObject(left) || !isConfigObject(right)) return false;

  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  if (leftKeys.length !== rightKeys.length) return false;

  return leftKeys.every(
    (key) => hasOwn(right, key) && configValueEquals(left[key], right[key])
  );
}

export function cloneConfig<T>(value: T): T {
  if (Array.isArray(value)) {
    return value.map((item) => cloneConfig(item)) as T;
  }
  if (isConfigObject(value)) {
    const cloned: ConfigObject = {};
    for (const key of Object.keys(value)) {
      cloned[key] = cloneConfig(value[key]);
    }
    return cloned as T;
  }
  return value;
}

export function diffConfigLeafPaths(
  before: ConfigObject,
  after: ConfigObject
): string[] {
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
      configValueEquals(beforeValue, afterValue)
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
        new Set([...Object.keys(beforeRecord), ...Object.keys(afterRecord)])
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
  const changes: Record<string, ConfigValue> = {};
  for (const path of Array.from(new Set(dirtyPaths)).sort()) {
    changes[path] = cloneConfig(getConfigValue(draft, path));
  }
  return changes;
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
