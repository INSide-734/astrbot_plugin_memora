import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RecallPage } from "./RecallPage";

interface BridgeMock {
  apiGet: ReturnType<typeof vi.fn>;
  apiPost: ReturnType<typeof vi.fn>;
  getLocale?: ReturnType<typeof vi.fn>;
  getI18n?: ReturnType<typeof vi.fn>;
  t?: ReturnType<typeof vi.fn>;
}

describe("RecallPage", () => {
  let bridge: BridgeMock;
  let showToast: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    bridge = {
      apiGet: vi.fn(),
      apiPost: vi.fn(),
      getLocale: vi.fn().mockReturnValue("en-US"),
      getI18n: vi.fn().mockReturnValue({}),
      t: vi.fn((key: string) => key),
    };
    showToast = vi.fn();

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

  it("does not send a recall request for blank queries", async () => {
    render(<RecallPage showToast={showToast} />);

    fireEvent.click(screen.getByRole("button", { name: /run recall/i }));

    expect(bridge.apiPost).not.toHaveBeenCalled();
    expect(showToast).not.toHaveBeenCalled();
  });

  it("submits recall requests and renders returned results", async () => {
    bridge.apiPost.mockResolvedValue({
      status: "ok",
      data: {
        results: [
          {
            id: "1",
            content: "Remember the Python async discussion",
            type: "fact",
            importance: 0.8,
            score: 0.9123,
            created_at: "2026-06-28T12:00:00Z",
            doc_kw_score: 0.7,
            doc_vec_score: 0.8,
          },
        ],
      },
    });

    render(<RecallPage showToast={showToast} />);

    fireEvent.change(screen.getByPlaceholderText("Enter query text to test retrieval..."), {
      target: { value: "python async" },
    });
    fireEvent.change(screen.getByPlaceholderText("Filter by session..."), {
      target: { value: "session-42" },
    });
    fireEvent.click(screen.getByRole("button", { name: /run recall/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/recall/test", {
        query: "python async",
        k: 5,
        session_id: "session-42",
      });
    });

    expect(await screen.findByText("Remember the Python async discussion")).toBeTruthy();
    expect(screen.getByText(/1 results/)).toBeTruthy();
    expect(screen.getByText(/Importance:/)).toBeTruthy();
    expect(screen.getByText("0.912")).toBeTruthy();
    expect(screen.getByText("Doc-KW: 0.700")).toBeTruthy();
    expect(screen.getByText("Doc-Vec: 0.800")).toBeTruthy();
  });

  it("shows an error toast when recall requests fail", async () => {
    bridge.apiPost.mockResolvedValue({
      status: "error",
      message: "recall exploded",
    });

    render(<RecallPage showToast={showToast} />);

    fireEvent.change(screen.getByPlaceholderText("Enter query text to test retrieval..."), {
      target: { value: "broken request" },
    });
    fireEvent.click(screen.getByRole("button", { name: /run recall/i }));

    await waitFor(() => {
      expect(showToast).toHaveBeenCalledWith("Error: recall exploded", true);
    });
  });
});
