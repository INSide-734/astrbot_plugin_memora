import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useRealtimeStream } from "./useRealtimeStream";

interface HookSnapshot {
  connected: boolean;
  unreadCount: number;
  eventCount: number;
  lastEventType: string | null;
}

interface BridgeMock {
  subscribeSSE: ReturnType<typeof vi.fn>;
  unsubscribeSSE: ReturnType<typeof vi.fn>;
}

function Harness() {
  const state = useRealtimeStream();
  const snapshot: HookSnapshot = {
    connected: state.connected,
    unreadCount: state.unreadCount,
    eventCount: state.events.length,
    lastEventType: state.lastEvent?.event ? String(state.lastEvent.event) : null,
  };

  return <pre data-testid="state">{JSON.stringify(snapshot)}</pre>;
}

function readState(): HookSnapshot {
  return JSON.parse(screen.getByTestId("state").textContent ?? "{}") as HookSnapshot;
}

describe("useRealtimeStream", () => {
  let bridge: BridgeMock;

  beforeEach(() => {
    vi.useFakeTimers();

    bridge = {
      subscribeSSE: vi.fn(),
      unsubscribeSSE: vi.fn(),
    };

    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: bridge,
    });
  });

  afterEach(() => {
    cleanup();
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    vi.restoreAllMocks();
    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: undefined,
    });
  });

  it("subscribes to realtime SSE and updates state on messages", async () => {
    let handlers:
      | {
          onMessage?: (data: string) => void;
          onError?: (error: Error) => void;
          onClose?: () => void;
        }
      | undefined;

    bridge.subscribeSSE.mockImplementation((endpoint, incomingHandlers) => {
      handlers = incomingHandlers;
      expect(endpoint).toBe("realtime/stream");
      return "sub-1";
    });

    render(<Harness />);

    await act(async () => {
      vi.advanceTimersByTime(500);
    });

    expect(bridge.subscribeSSE).toHaveBeenCalledTimes(1);
    expect(readState()).toEqual({
      connected: true,
      unreadCount: 0,
      eventCount: 0,
      lastEventType: null,
    });

    await act(async () => {
      handlers?.onMessage?.(
        JSON.stringify({
          event: "memory_added",
          data: { id: 1 },
          ts: 123,
        })
      );
    });

    expect(readState()).toEqual({
      connected: true,
      unreadCount: 1,
      eventCount: 1,
      lastEventType: "memory_added",
    });
  });

  it("unsubscribes on cleanup", async () => {
    bridge.subscribeSSE.mockReturnValue("sub-cleanup");

    const view = render(<Harness />);

    await act(async () => {
      vi.advanceTimersByTime(500);
    });

    view.unmount();

    expect(bridge.unsubscribeSSE).toHaveBeenCalledWith("sub-cleanup");
  });

  it("reconnects after SSE errors", async () => {
    let handlers:
      | {
          onMessage?: (data: string) => void;
          onError?: (error: Error) => void;
          onClose?: () => void;
        }
      | undefined;

    bridge.subscribeSSE
      .mockImplementationOnce((_, incomingHandlers) => {
        handlers = incomingHandlers;
        return "sub-1";
      })
      .mockImplementationOnce(() => "sub-2");

    render(<Harness />);

    await act(async () => {
      vi.advanceTimersByTime(500);
    });

    await act(async () => {
      handlers?.onError?.(new Error("broken"));
    });

    expect(bridge.unsubscribeSSE).toHaveBeenCalledWith("sub-1");
    expect(readState().connected).toBe(false);

    await act(async () => {
      vi.advanceTimersByTime(5000);
    });

    expect(bridge.subscribeSSE).toHaveBeenCalledTimes(2);
  });
});
