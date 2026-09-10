import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  apiGet,
  apiPost,
  apiRequest,
  normalizeImportance,
  unwrapApiData,
} from "./bridge";
import { ApiRequestError } from "@/types/editing";

interface BridgeMock {
  apiGet: ReturnType<typeof vi.fn>;
  apiPost: ReturnType<typeof vi.fn>;
}

describe("bridge", () => {
  let bridge: BridgeMock;

  beforeEach(() => {
    window.t = vi.fn((key: string, ...args: string[]) => {
      const translations: Record<string, string> = {
        "error.bridgeUnavailable": "桥接不可用",
        "error.requestFailed": "请求失败",
        "error.unexpectedResponseType": "响应类型异常：{0}",
      };
      let value = translations[key] ?? key;
      args.forEach((arg, index) => {
        value = value.replace(`{${index}}`, arg);
      });
      return value;
    });
    bridge = {
      apiGet: vi.fn(),
      apiPost: vi.fn(),
    };

    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: bridge,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: undefined,
    });
    Reflect.deleteProperty(window, "t");
  });

  it("localizes the bridge-unavailable fallback", async () => {
    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: undefined,
    });

    await expect(apiGet("stats")).rejects.toThrow("桥接不可用");
  });

  it("prefixes page/ for GET requests", async () => {
    bridge.apiGet.mockResolvedValue({ status: "ok", data: { ok: true } });

    await apiGet("stats", { limit: "5" });

    expect(bridge.apiGet).toHaveBeenCalledWith("page/stats", { limit: "5" });
  });

  it("prefixes page/ for POST requests", async () => {
    bridge.apiPost.mockResolvedValue({ status: "ok", data: { ok: true } });

    await apiPost("/backup/create", { force: true });

    expect(bridge.apiPost).toHaveBeenCalledWith("page/backup/create", {
      force: true,
    });
  });

  it("splits GET query strings into params", async () => {
    bridge.apiGet.mockResolvedValue({ status: "ok", data: { ok: true } });

    await apiRequest("quality/recent?limit=10&group_id=test-group");

    expect(bridge.apiGet).toHaveBeenCalledWith("page/quality/recent", {
      limit: "10",
      group_id: "test-group",
    });
  });

  it("uses POST bridge calls for non-GET methods", async () => {
    bridge.apiPost.mockResolvedValue({ status: "ok", data: { ok: true } });

    await apiRequest("quality/reset", {
      method: "POST",
      body: { confirm: true },
    });

    expect(bridge.apiPost).toHaveBeenCalledWith("page/quality/reset", {
      confirm: true,
    });
  });

  it("unwraps ok envelopes", () => {
    expect(unwrapApiData<{ total: number }>({
      status: "ok",
      data: { total: 3 },
    })).toEqual({ total: 3 });
  });

  it("unwraps error envelopes as structured request errors", () => {
    let error: unknown;
    try {
      unwrapApiData({
        status: "error",
        message: "boom",
        code: "validation_failed",
        field_errors: { name: "名称不能为空" },
        data: { request_id: "req-1" },
      });
    } catch (caught) {
      error = caught;
    }

    expect(error).toBeInstanceOf(ApiRequestError);
    expect(error).toMatchObject({
      message: "boom",
      code: "validation_failed",
      fieldErrors: { name: "名称不能为空" },
      data: { request_id: "req-1" },
    });
    expect(String(error)).toBe("Error: boom");
  });

  it("reads structured field errors from error data", () => {
    expect(() =>
      unwrapApiData({
        status: "error",
        code: "validation_failed",
        data: { field_errors: { tags: "标签不合法" } },
      })
    ).toThrowError(
      expect.objectContaining({
        code: "validation_failed",
        fieldErrors: { tags: "标签不合法" },
        data: { field_errors: { tags: "标签不合法" } },
      })
    );
  });

  it("drops malformed top-level field error values", () => {
    let error: unknown;
    try {
      unwrapApiData({
        status: "error",
        field_errors: {
          name: "名称不能为空",
          nested: { message: "无效" },
          entries: ["无效"],
          count: 3,
        },
      } as unknown as ApiResponse);
    } catch (caught) {
      error = caught;
    }

    expect(error).toBeInstanceOf(ApiRequestError);
    expect((error as ApiRequestError).fieldErrors).toEqual({
      name: "名称不能为空",
    });
  });

  it("drops malformed data field error values", () => {
    let error: unknown;
    try {
      unwrapApiData({
        status: "error",
        data: {
          field_errors: {
            tags: "标签不合法",
            nested: { message: "无效" },
            entries: ["无效"],
          },
        },
      });
    } catch (caught) {
      error = caught;
    }

    expect(error).toBeInstanceOf(ApiRequestError);
    expect((error as ApiRequestError).fieldErrors).toEqual({
      tags: "标签不合法",
    });
  });

  it("throws on non-object responses", () => {
    expect(() => unwrapApiData("bad" as unknown as ApiResponse)).toThrow(
      "响应类型异常：string"
    );
  });

  it("normalizes importance to 0-10 scale", () => {
    expect(normalizeImportance(0.7)).toBe(7);
    expect(normalizeImportance(8)).toBe(8);
    expect(normalizeImportance(Number.NaN)).toBe(5);
    expect(normalizeImportance(999)).toBe(10);
    expect(normalizeImportance(-3)).toBe(0);
  });
});
