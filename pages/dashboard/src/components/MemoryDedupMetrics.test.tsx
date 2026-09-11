import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { MemoryDedupMetrics } from "./MemoryDedupMetrics";

/** 与后端白名单一致的成功样本（含 observe/enforce 分列）。 */
const SUMMARY = {
  window: "24h",
  checked: 10,
  hit: 7,
  merged: 3,
  fact_mismatch: 1,
  conflict: 1,
  failed: 1,
  hit_rate: 0.7,
  guard_rate: 0.1,
  failure_rate: 0.2,
  by_mode: {
    observe: {
      checked: 4,
      hit: 3,
      merged: 0,
      fact_mismatch: 1,
      conflict: 0,
      failed: 0,
    },
    enforce: {
      checked: 6,
      hit: 4,
      merged: 3,
      fact_mismatch: 0,
      conflict: 1,
      failed: 1,
    },
  },
  trend: [
    {
      bucket_ms: 1_750_000_000_000,
      checked: 10,
      hit: 7,
      merged: 3,
      fact_mismatch: 1,
      conflict: 1,
      failed: 1,
    },
  ],
};

const EMPTY_SUMMARY = {
  ...SUMMARY,
  checked: 0,
  hit: 0,
  merged: 0,
  fact_mismatch: 0,
  conflict: 0,
  failed: 0,
  hit_rate: 0,
  guard_rate: 0,
  failure_rate: 0,
  by_mode: {
    observe: {
      checked: 0,
      hit: 0,
      merged: 0,
      fact_mismatch: 0,
      conflict: 0,
      failed: 0,
    },
    enforce: {
      checked: 0,
      hit: 0,
      merged: 0,
      fact_mismatch: 0,
      conflict: 0,
      failed: 0,
    },
  },
  trend: [],
};

interface BridgeMock {
  apiGet: Mock;
  apiPost: Mock;
}

let bridge: BridgeMock;

function respondWith(data: unknown) {
  bridge.apiGet.mockImplementation((endpoint: string) => {
    if (endpoint === "page/memory-dedup/metrics") {
      return Promise.resolve({ status: "ok", data });
    }
    return Promise.reject(new Error(`Unexpected GET endpoint: ${endpoint}`));
  });
}

beforeEach(() => {
  bridge = {
    apiGet: vi.fn(),
    apiPost: vi.fn(),
  };
  respondWith(SUMMARY);
  Object.defineProperty(window, "AstrBotPluginPage", {
    configurable: true,
    value: bridge,
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  Object.defineProperty(window, "AstrBotPluginPage", {
    configurable: true,
    value: undefined,
  });
});

describe("MemoryDedupMetrics", () => {
  it("renders the loading state while the window summary is pending", () => {
    render(<MemoryDedupMetrics />);

    expect(screen.getByText(/正在加载去重指标|Loading dedup metrics/i)).toBeDefined();
  });

  it("requests the default 24h window and renders hand-computed totals", async () => {
    render(<MemoryDedupMetrics />);

    await waitFor(() => {
      expect(screen.queryByText(/正在加载去重指标|Loading dedup metrics/i)).toBeNull();
    });

    expect(bridge.apiGet).toHaveBeenCalledWith("page/memory-dedup/metrics", {
      window: "24h",
    });
    expect(screen.getByText(/跨窗口去重指标|Cross-window Dedup Metrics/i)).toBeDefined();
    // 命中率 7/10、护栏率 1/10、失败率 (1+1)/10。
    expect(screen.getByText("70%")).toBeDefined();
    expect(screen.getByText("10%")).toBeDefined();
    expect(screen.getByText("20%")).toBeDefined();
    expect(screen.getByText(/按模式分列|By Mode/i)).toBeDefined();
    expect(screen.getByText(/观察模式|Observe/i)).toBeDefined();
    expect(screen.getByText(/执行模式|Enforce/i)).toBeDefined();
  });

  it("renders the empty contract when no candidate was checked", async () => {
    respondWith(EMPTY_SUMMARY);
    render(<MemoryDedupMetrics />);

    await waitFor(() => {
      expect(
        screen.getByText(/当前窗口没有去重指标|No dedup metrics in this window/i)
      ).toBeDefined();
    });
    expect(
      screen.getByText(/mode=off 时不检测也不记录/i)
    ).toBeDefined();
  });

  it("renders the error state and retries on demand", async () => {
    bridge.apiGet.mockRejectedValue(new Error("panel offline"));
    render(<MemoryDedupMetrics />);

    await waitFor(() => {
      expect(screen.getByText(/无法加载去重指标|Could not load dedup metrics/i)).toBeDefined();
    });

    respondWith(SUMMARY);
    fireEvent.click(screen.getByRole("button", { name: /重试|Retry/i }));

    await waitFor(() => {
      expect(screen.getByText("70%")).toBeDefined();
    });
  });

  it("requests the selected window from the window selector", async () => {
    render(<MemoryDedupMetrics />);
    await waitFor(() => expect(screen.getByText("70%")).toBeDefined());

    fireEvent.click(screen.getByLabelText(/统计窗口|Window/i));
    const option = await screen.findByRole("option", {
      name: /最近 7 天|Last 7 days/i,
    });
    fireEvent.pointerDown(option, { pointerType: "mouse" });
    fireEvent.click(option);

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/memory-dedup/metrics", {
        window: "7d",
      });
    });
  });
});
