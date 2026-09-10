/**
 * Dashboard 开发环境的插件更新模拟接口。
 *
 * 默认返回一个可用的 runtime 更新，便于运行 `npm run dev` 时直接检查
 * 系统概览中的更新卡片。状态只存在于当前页面进程，不会访问网络或修改
 * 本地文件；这与生产环境的 Page API 响应形状保持一致。
 */

export type UpdateMockResponse = {
  status: string;
  data?: unknown;
  message?: string;
  code?: string;
};

const CURRENT_VERSION = "1.0.0";
const UPDATE_VERSION = "1.1.0";
const UPDATE_TAG = `v${UPDATE_VERSION}`;
const UPDATE_FILENAME = `astrbot_plugin_memora-${UPDATE_VERSION}-runtime.zip`;

const ok = (data: unknown): UpdateMockResponse => ({
  status: "ok",
  data: structuredClone(data),
});

const err = (message: string, code: string): UpdateMockResponse => ({
  status: "error",
  message,
  code,
});

const release = () => ({
  version: UPDATE_VERSION,
  tag: UPDATE_TAG,
  published_at: "2026-07-28T00:00:00Z",
  notes:
    "修复 runtime 更新流程，补充镜像回退、SHA-256 校验和失败自动回滚。",
  runtime_filename: UPDATE_FILENAME,
  source: "mirror",
});

let currentVersion = CURRENT_VERSION;
let ignoredVersion: string | null = null;
let operationId = "";
let operationVersion = "";

/** 将更新模拟状态恢复为开发环境的初始状态。 */
export function resetUpdateMockState(): void {
  currentVersion = CURRENT_VERSION;
  ignoredVersion = null;
  operationId = "";
  operationVersion = "";
}

/** 处理 update/check 与 update/status GET 请求。 */
export function handleUpdateGet(
  path: string,
  params: Record<string, string> = {},
): UpdateMockResponse | null {
  if (path === "update/check") {
    const ignored = ignoredVersion === UPDATE_VERSION;
    const available = currentVersion !== UPDATE_VERSION && !ignored;
    return ok({
      enabled: true,
      current_version: currentVersion,
      capabilities: { auto_apply: true, reason_code: null },
      available,
      ignored,
      ignored_version: ignoredVersion,
      release: available || ignored ? release() : null,
    });
  }

  if (path === "update/status") {
    if (!operationId || params.operation_id !== operationId) {
      return err("更新操作不存在", "update_operation_not_found");
    }
    return ok({
      operation_id: operationId,
      version: operationVersion,
      status: "succeeded",
      rollback_performed: false,
      requires_manual_restart: false,
    });
  }

  return null;
}

/** 处理 update/ignore、update/download 与 update/apply POST 请求。 */
export function handleUpdatePost(
  path: string,
  body: Record<string, unknown>,
): UpdateMockResponse | null {
  if (path === "update/ignore") {
    const version = typeof body.version === "string" ? body.version.trim() : "";
    if (version !== UPDATE_VERSION) {
      return err("version 必须是当前模拟发布版本", "invalid_request");
    }
    ignoredVersion = version;
    return ok({ ignored_version: ignoredVersion });
  }

  if (path === "update/download") {
    return ok({
      version: UPDATE_VERSION,
      size: 12 * 1024 * 1024,
      sha256: "a".repeat(64),
      source: "mirror",
      runtime_filename: UPDATE_FILENAME,
      staged: true,
    });
  }

  if (path === "update/apply") {
    operationId = `${Date.now().toString(16)}${"0".repeat(32)}`.slice(-32);
    operationVersion = UPDATE_VERSION;
    currentVersion = UPDATE_VERSION;
    ignoredVersion = null;
    return ok({
      operation_id: operationId,
      version: operationVersion,
      status: "reload_scheduled",
    });
  }

  return null;
}
