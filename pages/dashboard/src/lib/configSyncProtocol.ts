import {
  cloneConfig,
  diffConfigLeafPaths,
  rebaseConfig,
} from "@/lib/config";
import type {
  ConfigApiError,
  ConfigApiResponse,
  ConfigObject,
  ConfigSyncError,
} from "@/types/config";

/** 表示配置 Page API 返回了可识别的协议错误。 */
export class ConfigProtocolError extends Error {
  /** 保存服务端错误 envelope，供冲突和字段错误分支使用。 */
  constructor(readonly response: ConfigApiError) {
    super(response.message);
  }
}

/**
 * 从宿主原始响应或标准成功 envelope 中提取配置数据。
 *
 * @param response AstrBot bridge 返回的原始响应。
 * @returns 标准化后的成功数据。
 * @throws ConfigProtocolError 响应为错误 envelope 或形状非法时抛出。
 */
export function configSuccessData<T>(response: ApiResponse): T {
  const configResponse = response as ConfigApiResponse<T>;
  if (configResponse.status === "error") {
    throw new ConfigProtocolError(configResponse);
  }
  if (configResponse.status === "ok" && "data" in configResponse) {
    return configResponse.data;
  }
  // AstrBot 的宿主 Page bridge 会剥离成功 envelope，仅把 data 传给 iframe。
  if (
    response !== null &&
    typeof response === "object" &&
    !Array.isArray(response) &&
    !Object.prototype.hasOwnProperty.call(response, "status")
  ) {
    return response as unknown as T;
  }
  throw new ConfigProtocolError({
    status: "error",
    code: "invalid_request",
    message: "Unexpected configuration response",
  });
}

/**
 * 把未知异常转换为配置同步状态可公开的稳定错误。
 *
 * @param error 捕获到的协议或传输异常。
 * @returns 不包含内部堆栈的同步错误。
 */
export function configSyncError(error: unknown): ConfigSyncError {
  if (error instanceof ConfigProtocolError) {
    return {
      kind: "protocol",
      code: error.response.code,
      message: error.response.message,
      ...(error.response.data ? { data: error.response.data } : {}),
    };
  }
  return {
    kind: "transport",
    message: error instanceof Error ? error.message : String(error),
  };
}

/**
 * 将已持久化草稿设为新基线，同时保留提交期间产生的新编辑。
 *
 * @param persistedConfig 服务端确认持久化的配置。
 * @param submittedDraft 本次提交时的草稿快照。
 * @param latestDraft 请求完成时用户正在编辑的最新草稿。
 * @returns 新基线、重放后的草稿以及是否仍有待提交变更。
 */
export function acknowledgeConfigDraft(
  persistedConfig: ConfigObject,
  submittedDraft: ConfigObject,
  latestDraft: ConfigObject | null
) {
  const baseConfig = cloneConfig(persistedConfig);
  const pendingPaths = latestDraft
    ? diffConfigLeafPaths(submittedDraft, latestDraft)
    : [];
  const draft = latestDraft
    ? rebaseConfig(baseConfig, latestDraft, pendingPaths)
    : cloneConfig(baseConfig);
  return {
    baseConfig,
    draft,
    hasPendingChanges: diffConfigLeafPaths(baseConfig, draft).length > 0,
  };
}
