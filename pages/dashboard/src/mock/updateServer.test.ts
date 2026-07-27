import { beforeEach, describe, expect, it } from "vitest";

import {
  handleUpdateGet,
  handleUpdatePost,
  resetUpdateMockState,
} from "./updateServer";

function dataOf(response: { data?: unknown }): Record<string, any> {
  return response.data as Record<string, any>;
}

describe("开发环境更新模拟接口", () => {
  beforeEach(() => resetUpdateMockState());

  it("默认返回可展示的新版本", () => {
    const response = handleUpdateGet("update/check");
    const data = dataOf(response!);

    expect(response?.status).toBe("ok");
    expect(data).toMatchObject({
      enabled: true,
      current_version: "1.0.0",
      available: true,
      capabilities: { auto_apply: true },
      release: {
        version: "1.1.0",
        source: "mirror",
      },
    });
  });

  it("支持忽略版本并在安装后收敛到最新版本", () => {
    handleUpdatePost("update/ignore", { version: "1.1.0" });
    expect(dataOf(handleUpdateGet("update/check")!)).toMatchObject({
      available: false,
      ignored: true,
    });

    resetUpdateMockState();
    const apply = dataOf(handleUpdatePost("update/apply", {})!);
    const status = dataOf(
      handleUpdateGet("update/status", { operation_id: apply.operation_id })!,
    );
    expect(status).toMatchObject({ status: "succeeded", version: "1.1.0" });
    expect(dataOf(handleUpdateGet("update/check")!)).toMatchObject({
      current_version: "1.1.0",
      available: false,
    });
  });

  it("返回可供旧宿主展示的下载结果", () => {
    const response = handleUpdatePost("update/download", {});
    expect(dataOf(response!)).toMatchObject({
      version: "1.1.0",
      source: "mirror",
      staged: true,
    });
  });
});
