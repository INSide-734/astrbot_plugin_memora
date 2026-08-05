import { handleApiGet, handleApiPost } from "./server";

interface MockBridgeDependencies {
  getContext: () => AstrBotContext;
  getTranslations: () => Record<string, string>;
}

interface MockSseSubscription {
  handlers: SseHandlers;
  interval: ReturnType<typeof setInterval>;
}

const contextHandlers = new Set<(context: AstrBotContext) => void>();

/** 将扁平翻译键转换成 AstrBot bridge 返回的 dashboard 命名空间。 */
function nestTranslations(flat: Record<string, string>): Record<string, unknown> {
  const nested: Record<string, unknown> = { dashboard: {} };
  const dashboard = nested.dashboard as Record<string, unknown>;
  for (const [key, value] of Object.entries(flat)) {
    const parts = key.split(".");
    let target = dashboard;
    for (let index = 0; index < parts.length - 1; index += 1) {
      if (!target[parts[index]]) target[parts[index]] = {};
      target = target[parts[index]] as Record<string, unknown>;
    }
    target[parts[parts.length - 1]] = value;
  }
  return nested;
}

/** 向当前模拟 bridge 的 context 订阅者广播语言或主题变化。 */
export function notifyMockContextChanged(context: AstrBotContext): void {
  contextHandlers.forEach((handler) => handler(context));
}

/**
 * 在宿主 bridge 缺失时安装 Dashboard 模拟 bridge。
 *
 * @param dependencies 读取当前 context 与翻译字典的回调。
 * @returns 安装成功时返回 true；已有宿主 bridge 时返回 false。
 */
export function installMockPluginPageBridge(
  dependencies: MockBridgeDependencies,
): boolean {
  if (window.AstrBotPluginPage) return false;

  console.log("[模拟桥接] 未找到 AstrBot bridge，开始安装模拟 API 服务");

  const sseSubscriptions: Record<string, MockSseSubscription> = {};
  let sseCounter = 0;

  const mockBridge: AstrBotPluginPageBridge = {
    apiGet: async (path: string, params: Record<string, string> = {}) => {
      const cleanPath = path.replace(/^\/+/, "");
      return handleApiGet(cleanPath, params);
    },
    apiPost: async (path: string, body: unknown) => {
      const cleanPath = path.replace(/^\/+/, "");
      return handleApiPost(cleanPath, body);
    },
    getLocale: () => dependencies.getContext().locale,
    getI18n: () => nestTranslations(dependencies.getTranslations()),
    t: (key: string, fallback?: string): string => {
      const translations = dependencies.getTranslations();
      if (translations[key]) return translations[key];
      if (key.startsWith("dashboard.")) {
        const stripped = key.slice("dashboard.".length);
        if (translations[stripped]) return translations[stripped];
      }
      return fallback ?? key;
    },
    ready: async () => dependencies.getContext(),
    getContext: () => dependencies.getContext(),
    onContext: (callback: (context: AstrBotContext) => void) => {
      contextHandlers.add(callback);
      callback(dependencies.getContext());
      return () => {
        contextHandlers.delete(callback);
      };
    },
    upload: async () => ({ status: "ok" }),
    download: async () => {},
    subscribeSSE: async (
      endpoint: string,
      handlers: SseHandlers,
      _params?: Record<string, string>,
    ): Promise<string> => {
      sseCounter += 1;
      const subscriptionId = `mock_sse_${sseCounter}`;
      const mockEventTypes = [
        { event: "memory_created", data: { memory_id: 100 + sseCounter, summary: "来自会话的新情景记忆", importance: 6.5 } },
        { event: "memory_recalled", data: { memory_id: 42, query: "最近的讨论", score: 0.87 } },
        { event: "atom_consolidated", data: { atom_type: "FACTUAL", count: 3 } },
        { event: "decay_applied", data: { total_decayed: 12, avg_importance_drop: 0.03 } },
      ];

      let eventIndex = 0;
      const interval = setInterval(() => {
        const mockEvent = mockEventTypes[eventIndex % mockEventTypes.length];
        eventIndex += 1;
        const parsed = {
          event: mockEvent.event,
          data: mockEvent.data,
          ts: Date.now() / 1000,
        };
        handlers.onMessage?.({
          raw: JSON.stringify(parsed),
          parsed,
          eventType: "message",
        });
      }, 8000 + Math.random() * 12000);

      sseSubscriptions[subscriptionId] = { handlers, interval };
      handlers.onOpen?.();
      console.log(`[模拟桥接] SSE 已订阅：${subscriptionId} -> ${endpoint}`);
      return subscriptionId;
    },
    unsubscribeSSE: async (subscriptionId: string): Promise<void> => {
      const subscription = sseSubscriptions[subscriptionId];
      if (subscription) {
        clearInterval(subscription.interval);
        delete sseSubscriptions[subscriptionId];
        console.log(`[模拟桥接] SSE 已取消订阅：${subscriptionId}`);
      }
    },
  };

  window.AstrBotPluginPage = mockBridge;
  window.dispatchEvent(new Event("languagechange"));
  console.log("[模拟桥接] 桥接已安装，API 与 i18n 已就绪");
  return true;
}
