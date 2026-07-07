import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  apiGet,
  apiPost,
  apiRequest,
  normalizeImportance,
  unwrapApiData,
} from "./bridge";

interface BridgeMock {
  apiGet: ReturnType<typeof vi.fn>;
  apiPost: ReturnType<typeof vi.fn>;
}

describe("bridge", () => {
  let bridge: BridgeMock;

  beforeEach(() => {
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

  it("throws on error envelopes", () => {
    expect(() =>
      unwrapApiData({
        status: "error",
        message: "boom",
      })
    ).toThrow("boom");
  });

  it("throws on non-object responses", () => {
    expect(() => unwrapApiData("bad" as unknown as ApiResponse)).toThrow(
      "[bridge] Unexpected API response type: string"
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
