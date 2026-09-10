import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { CandidateReuseConfig } from "./CandidateReuseConfig";

const mockShowToast = vi.fn();

/** GET config/topic-segmentation 的 candidate_reuse 状态分支（后端真实形状）。 */
const mockStatusResponse = {
  candidate_reuse: {
    catalog_status: "ready",
    dirty_count: 3,
    scope_buckets: { small: 125, medium: 48, large: 12 },
    aggregated_metrics: { p95_latency_ms: 42.5, p95_candidates: 8.0, p95_tokens: 340.0 },
  },
};

/** GET config/state 的快照配置分支（真实嵌套路径）。 */
const mockStateResponse = {
  revision: "rev-1",
  config: {
    topic_segmentation: {
      candidate_reuse: {
        mode: "observe",
        fixed_k: 8,
        activation_threshold: 32,
        max_full_topics: 32,
        max_full_prompt_tokens: 256,
      },
    },
  },
};

interface BridgeMock {
  apiGet: Mock;
  apiPost: Mock;
}

let bridge: BridgeMock;

/** 按端点路由桥接响应；未覆盖端点直接拒绝，暴露意外请求。 */
function mockBridgeEndpoints(
  statusData: unknown = mockStatusResponse,
  stateData: unknown = mockStateResponse
) {
  bridge.apiGet.mockImplementation((endpoint: string) => {
    if (endpoint === "page/config/topic-segmentation") {
      return Promise.resolve({ status: "ok", data: statusData });
    }
    if (endpoint === "page/config/state") {
      return Promise.resolve({ status: "ok", data: stateData });
    }
    return Promise.reject(new Error(`Unexpected GET endpoint: ${endpoint}`));
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  bridge = {
    apiGet: vi.fn(),
    apiPost: vi.fn().mockResolvedValue({ status: "ok", data: { updated: [], revision: "rev-2" } }),
  };
  mockBridgeEndpoints();
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

describe("CandidateReuseConfig", () => {
  it("renders loading state initially", () => {
    render(<CandidateReuseConfig showToast={mockShowToast} />);
    expect(screen.getByText(/正在加载候选重用配置|Loading candidate reuse config/i)).toBeDefined();
  });

  it("fetches status and settings from real endpoints", async () => {
    render(<CandidateReuseConfig showToast={mockShowToast} />);

    await waitFor(() => {
      expect(screen.queryByText(/正在加载|loading/i)).toBeNull();
    });

    expect(bridge.apiGet).toHaveBeenCalledWith("page/config/topic-segmentation", {});
    expect(bridge.apiGet).toHaveBeenCalledWith("page/config/state", {});
    expect(
      screen.getAllByText(/Topic 候选重用配置|Topic Candidate Reuse Config/i)[0]
    ).toBeDefined();
  });

  it("shows degraded warning when catalog is not ready", async () => {
    mockBridgeEndpoints({
      candidate_reuse: { ...mockStatusResponse.candidate_reuse, catalog_status: "degraded" },
    });

    render(<CandidateReuseConfig showToast={mockShowToast} />);

    await waitFor(() => {
      expect(screen.getByText(/目录处于降级状态|Catalog is in degraded state/i)).toBeDefined();
    });
  });

  it("disables mode selector when catalog is degraded", async () => {
    mockBridgeEndpoints({
      candidate_reuse: { ...mockStatusResponse.candidate_reuse, catalog_status: "degraded" },
    });

    const { container } = render(<CandidateReuseConfig showToast={mockShowToast} />);

    await waitFor(() => {
      expect(screen.getByText(/目录处于降级状态|Catalog is in degraded state/i)).toBeDefined();
    });

    const modeSelect = container.querySelector('button[role="combobox"]');
    expect(modeSelect?.classList.contains("cursor-not-allowed")).toBe(true);
  });

  it("offers off rollback option in mode selector", async () => {
    const { container } = render(<CandidateReuseConfig showToast={mockShowToast} />);

    await waitFor(() => {
      expect(screen.queryByText(/正在加载|loading/i)).toBeNull();
    });

    fireEvent.click(container.querySelector('button[role="combobox"]')!);
    await waitFor(() => {
      expect(screen.getByRole("option", { name: /关闭|Off/i })).toBeDefined();
    });
  });

  it("validates fixed_k does not exceed max_full_topics", async () => {
    const topKState = {
      ...mockStateResponse,
      config: {
        topic_segmentation: {
          candidate_reuse: {
            ...mockStateResponse.config.topic_segmentation.candidate_reuse,
            mode: "top_k",
            fixed_k: 3,
          },
        },
      },
    };
    mockBridgeEndpoints(mockStatusResponse, topKState);

    const { container } = render(<CandidateReuseConfig showToast={mockShowToast} />);

    await waitFor(() => {
      expect(screen.queryByText(/正在加载|loading/i)).toBeNull();
    });

    const inputs = container.querySelectorAll('input[type="number"]');
    const fixedKInput = inputs[0] as HTMLInputElement;
    fireEvent.change(fixedKInput, { target: { value: "40" } });

    const saveButton = screen.getByRole("button", { name: /Save Config|保存配置/i });
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(mockShowToast).toHaveBeenCalledWith(
        expect.stringMatching(/cannot exceed|不能超过/i),
        true
      );
    });
  });

  it("validates activation_threshold does not exceed max_full_topics", async () => {
    const { container } = render(<CandidateReuseConfig showToast={mockShowToast} />);

    await waitFor(() => {
      expect(screen.queryByText(/正在加载|loading/i)).toBeNull();
    });

    const inputs = container.querySelectorAll('input[type="number"]');
    const thresholdInput = inputs[0] as HTMLInputElement;
    fireEvent.change(thresholdInput, { target: { value: "40" } });

    const saveButton = screen.getByRole("button", { name: /Save Config|保存配置/i });
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(mockShowToast).toHaveBeenCalledWith(
        expect.stringMatching(/cannot exceed|不能超过/i),
        true
      );
    });
  });

  it("saves with flat topic_segmentation.candidate_reuse prefix keys", async () => {
    const { container } = render(<CandidateReuseConfig showToast={mockShowToast} />);

    await waitFor(() => {
      expect(screen.queryByText(/正在加载|loading/i)).toBeNull();
    });

    const inputs = container.querySelectorAll('input[type="number"]');
    const thresholdInput = inputs[0] as HTMLInputElement;
    fireEvent.change(thresholdInput, { target: { value: "16" } });

    const saveButton = screen.getByRole("button", { name: /Save Config|保存配置/i });
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/config/topic-segmentation", {
        base_revision: "rev-1",
        "topic_segmentation.candidate_reuse.activation_threshold": 16,
      });
    });
  });

  it("saves new numeric leaves with flat prefix keys", async () => {
    const { container } = render(<CandidateReuseConfig showToast={mockShowToast} />);

    await waitFor(() => {
      expect(screen.queryByText(/正在加载|loading/i)).toBeNull();
    });

    // observe 模式输入顺序：activation_threshold, max_full_topics, max_full_prompt_tokens,
    // max_query_chars, metrics_retention_days, observe_max_candidates, observe_max_rows,
    // observe_max_duration_ms（索引 7），最后是只读 overfetch_factor
    const inputs = container.querySelectorAll('input[type="number"]');
    const durationInput = inputs[7] as HTMLInputElement;
    fireEvent.change(durationInput, { target: { value: "500" } });

    const saveButton = screen.getByRole("button", { name: /Save Config|保存配置/i });
    fireEvent.click(saveButton);
    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/config/topic-segmentation", {
        base_revision: "rev-1",
        "topic_segmentation.candidate_reuse.observe_max_duration_ms": 500,
      });
    });
  });

  it("renders overfetch_factor as a read-only fixed value", async () => {
    const { container } = render(<CandidateReuseConfig showToast={mockShowToast} />);

    await waitFor(() => {
      expect(screen.queryByText(/正在加载|loading/i)).toBeNull();
    });

    const inputs = container.querySelectorAll('input[type="number"]');
    const overfetchInput = inputs[inputs.length - 1] as HTMLInputElement;
    expect(overfetchInput.value).toBe("3");
    expect(overfetchInput.readOnly).toBe(true);

    // 修改只读框不应进入草稿：直接保存时不含 overfetch_factor 键
    fireEvent.change(overfetchInput, { target: { value: "5" } });
    const saveButtons = screen.queryByRole("button", { name: /Save Config|保存配置/i });
    expect(saveButtons).toBeNull();
  });

  it("shows save and discard buttons when there are changes", async () => {
    const { container } = render(<CandidateReuseConfig showToast={mockShowToast} />);

    await waitFor(() => {
      expect(screen.queryByText(/正在加载|loading/i)).toBeNull();
    });

    expect(screen.queryByRole("button", { name: /Save Config|保存配置/i })).toBeNull();

    const inputs = container.querySelectorAll('input[type="number"]');
    const thresholdInput = inputs[0] as HTMLInputElement;
    fireEvent.change(thresholdInput, { target: { value: "16" } });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Save Config|保存配置/i })).toBeDefined();
      expect(screen.getByRole("button", { name: /Discard|放弃修改/i })).toBeDefined();
    });
  });

  it("displays size buckets distribution", async () => {
    render(<CandidateReuseConfig showToast={mockShowToast} />);

    await waitFor(() => {
      expect(screen.getByText("small")).toBeDefined();
    });
    expect(screen.getByText("125")).toBeDefined();
    expect(screen.getByText("medium")).toBeDefined();
  });

  it("displays aggregated p95 metrics", async () => {
    render(<CandidateReuseConfig showToast={mockShowToast} />);

    await waitFor(() => {
      expect(screen.getByText("42.5")).toBeDefined();
    });
  });

  it("surfaces save errors via toast without crashing", async () => {
    bridge.apiPost.mockRejectedValue(new Error("config conflict"));

    const { container } = render(<CandidateReuseConfig showToast={mockShowToast} />);

    await waitFor(() => {
      expect(screen.queryByText(/正在加载|loading/i)).toBeNull();
    });

    const inputs = container.querySelectorAll('input[type="number"]');
    fireEvent.change(inputs[0] as HTMLInputElement, { target: { value: "16" } });

    fireEvent.click(screen.getByRole("button", { name: /Save Config|保存配置/i }));

    await waitFor(() => {
      expect(mockShowToast).toHaveBeenCalledWith("config conflict", true);
    });
  });

  it("polls status every 30 seconds", async () => {
    vi.useFakeTimers();

    render(<CandidateReuseConfig showToast={mockShowToast} />);

    // 初始：status + state 各一次
    await vi.waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledTimes(2);
    });

    await vi.advanceTimersByTimeAsync(30000);

    await vi.waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledTimes(3);
    });

    vi.useRealTimers();
  });
});
