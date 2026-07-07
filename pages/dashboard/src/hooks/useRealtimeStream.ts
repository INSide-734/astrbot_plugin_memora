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

export function useRealtimeStream() {
  const [state, setState] = useState<RealtimeState>({
    connected: false,
    events: [],
    unreadCount: 0,
    lastEvent: null,
  });
  const subIdRef = useRef<string | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  const connect = useCallback(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const bridge = (window as any).AstrBotPluginPage as
      | {
          subscribeSSE?: (
            endpoint: string,
            handlers: { onMessage?: (data: string) => void; onError?: (e: Error) => void; onClose?: () => void },
            params?: Record<string, string>
          ) => string;
          unsubscribeSSE?: (id: string) => void;
        }
      | undefined;

    if (!bridge?.subscribeSSE) {
      // Bridge not available — retry after delay
      if (mountedRef.current) {
        reconnectTimerRef.current = setTimeout(connect, RECONNECT_DELAY_MS);
      }
      return;
    }

    // Unsubscribe any existing subscription
    if (subIdRef.current && bridge.unsubscribeSSE) {
      bridge.unsubscribeSSE(subIdRef.current);
      subIdRef.current = null;
    }

    try {
      subIdRef.current = bridge.subscribeSSE(
        "realtime/stream",  // relative endpoint — bridge handles proxying
        {
          onMessage: (rawData: string) => {
            if (!mountedRef.current) return;
            try {
              const payload = JSON.parse(rawData) as StreamEvent;
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
            } catch {
              // ignore parse errors (heartbeat comments are not JSON)
            }
          },
          onError: () => {
            if (!mountedRef.current) return;
            setState((prev) => ({ ...prev, connected: false }));
            // Clean up and schedule reconnect
            if (subIdRef.current && bridge.unsubscribeSSE) {
              bridge.unsubscribeSSE(subIdRef.current);
              subIdRef.current = null;
            }
            clearReconnectTimer();
            reconnectTimerRef.current = setTimeout(connect, RECONNECT_DELAY_MS);
          },
          onClose: () => {
            if (!mountedRef.current) return;
            setState((prev) => ({ ...prev, connected: false }));
          },
        }
      );

      // Mark as connected once subscription is created
      setState((prev) => ({ ...prev, connected: true }));
    } catch {
      // Subscription failed — retry
      if (mountedRef.current) {
        reconnectTimerRef.current = setTimeout(connect, RECONNECT_DELAY_MS);
      }
    }
  }, [clearReconnectTimer]);

  useEffect(() => {
    mountedRef.current = true;
    // Delay initial connection to let bridge initialize
    const initTimer = setTimeout(connect, 500);

    return () => {
      mountedRef.current = false;
      clearTimeout(initTimer);
      clearReconnectTimer();
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const bridge = (window as any).AstrBotPluginPage as
        | { unsubscribeSSE?: (id: string) => void }
        | undefined;
      if (subIdRef.current && bridge?.unsubscribeSSE) {
        bridge.unsubscribeSSE(subIdRef.current);
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
