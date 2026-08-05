import { useState, useEffect, useRef, useCallback } from "react";

interface StreamEvent {
  event: string;
  data: Record<string, unknown>;
  ts: number;
}

interface RealtimeState {
  connected: boolean;
  events: StreamEvent[];
  unreadCount: number;
  lastEvent: StreamEvent | null;
}

const MAX_EVENTS = 50;
const RECONNECT_DELAY_MS = 5000;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function decodeJson(value: unknown): unknown {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function parseStreamEvent(input: unknown): StreamEvent | null {
  let payload = input;
  let rawFallback: unknown;
  if (isRecord(input) && ("parsed" in input || "raw" in input)) {
    payload = input.parsed;
    rawFallback = input.raw;
  }

  payload = decodeJson(payload);
  if (!isRecord(payload) && rawFallback !== undefined) {
    payload = decodeJson(rawFallback);
  }
  if (!isRecord(payload)) return null;

  const { event, data, ts } = payload;
  if (
    typeof event !== "string"
    || !event
    || !isRecord(data)
    || typeof ts !== "number"
    || !Number.isFinite(ts)
  ) {
    return null;
  }
  return { event, data, ts };
}

async function unsubscribeSafely(
  bridge: AstrBotPluginPageBridge,
  subscriptionId: string,
): Promise<void> {
  try {
    await bridge.unsubscribeSSE(subscriptionId);
  } catch {
    // 已失效的宿主订阅不应阻塞重连或页面卸载。
  }
}

export function useRealtimeStream() {
  const [state, setState] = useState<RealtimeState>({
    connected: false,
    events: [],
    unreadCount: 0,
    lastEvent: null,
  });
  const subIdRef = useRef<string | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(false);
  const attemptRef = useRef(0);

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current !== null) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  const connect = useCallback(async () => {
    clearReconnectTimer();
    const attempt = ++attemptRef.current;
    const bridge = window.AstrBotPluginPage as AstrBotPluginPageBridge | undefined;

    const scheduleReconnect = () => {
      clearReconnectTimer();
      if (mountedRef.current) {
        reconnectTimerRef.current = setTimeout(() => {
          void connect();
        }, RECONNECT_DELAY_MS);
      }
    };

    if (!bridge?.subscribeSSE) {
      scheduleReconnect();
      return;
    }

    if (subIdRef.current) {
      const previousId = subIdRef.current;
      subIdRef.current = null;
      await unsubscribeSafely(bridge, previousId);
      if (!mountedRef.current || attemptRef.current !== attempt) return;
    }

    const handleFailure = () => {
      if (!mountedRef.current || attemptRef.current !== attempt) return;
      attemptRef.current += 1;
      setState((prev) => ({ ...prev, connected: false }));
      const subscriptionId = subIdRef.current;
      subIdRef.current = null;
      if (subscriptionId) {
        void unsubscribeSafely(bridge, subscriptionId);
      }
      scheduleReconnect();
    };

    try {
      let subscriptionReady = false;
      const subscriptionId = await bridge.subscribeSSE(
        "realtime/stream",
        {
          onOpen: () => {
            if (!subscriptionReady) return;
            if (!mountedRef.current || attemptRef.current !== attempt) return;
            setState((prev) => ({ ...prev, connected: true }));
          },
          onMessage: (event: SseEvent) => {
            if (!mountedRef.current || attemptRef.current !== attempt) return;
            const payload = parseStreamEvent(event);
            if (!payload) return;
            setState((prev) => {
              const events = [payload, ...prev.events].slice(0, MAX_EVENTS);
              return {
                ...prev,
                connected: true,
                events,
                lastEvent: payload,
                unreadCount: prev.unreadCount + 1,
              };
            });
          },
          onError: handleFailure,
        },
      );

      if (!mountedRef.current || attemptRef.current !== attempt) {
        await unsubscribeSafely(bridge, subscriptionId);
        return;
      }
      if (typeof subscriptionId !== "string" || !subscriptionId) {
        throw new Error("AstrBot bridge returned an invalid SSE subscription ID");
      }
      subIdRef.current = subscriptionId;
      subscriptionReady = true;
      setState((prev) => ({ ...prev, connected: true }));
    } catch {
      if (!mountedRef.current || attemptRef.current !== attempt) return;
      setState((prev) => ({ ...prev, connected: false }));
      scheduleReconnect();
    }
  }, [clearReconnectTimer]);

  useEffect(() => {
    mountedRef.current = true;
    // 给宿主桥接留出初始化时间。
    const initTimer = setTimeout(() => {
      void connect();
    }, 500);

    return () => {
      mountedRef.current = false;
      attemptRef.current += 1;
      clearTimeout(initTimer);
      clearReconnectTimer();
      const bridge = window.AstrBotPluginPage as AstrBotPluginPageBridge | undefined;
      if (subIdRef.current && bridge) {
        void unsubscribeSafely(bridge, subIdRef.current);
        subIdRef.current = null;
      }
    };
  }, [connect, clearReconnectTimer]);

  const markSeen = useCallback(() => {
    setState((prev) => ({ ...prev, unreadCount: 0 }));
  }, []);

  return {
    connected: state.connected,
    events: state.events,
    unreadCount: state.unreadCount,
    lastEvent: state.lastEvent,
    markSeen,
  };
}
