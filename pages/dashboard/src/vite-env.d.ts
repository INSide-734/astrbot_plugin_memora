/// <reference types="vite/client" />

declare module "virtual:memora-config-schema" {
  const schemaSource: string;
  export default schemaSource;
}

interface AstrBotPluginPageBridge {
  ready(): Promise<AstrBotContext>;
  getContext(): AstrBotContext;
  getLocale(): string;
  getI18n(): Record<string, unknown>;
  t(key: string, fallback?: string): string;
  onContext(callback: (ctx: AstrBotContext) => void): void;
  offContext(callback: (ctx: AstrBotContext) => void): void;
  onContextChange(callback: (ctx: AstrBotContext) => void): void;
  offContextChange(callback: (ctx: AstrBotContext) => void): void;
  apiGet(endpoint: string, params?: Record<string, string>): Promise<ApiResponse>;
  apiPost(endpoint: string, body?: unknown): Promise<ApiResponse>;
  upload(endpoint: string, file: File): Promise<ApiResponse>;
  download(endpoint: string, params: Record<string, string>, filename: string): Promise<void>;
  subscribeSSE(endpoint: string, handlers: SseHandlers, params?: Record<string, string>): string;
  unsubscribeSSE(subscriptionId: string): void;
}

interface SseHandlers {
  onMessage?: (data: string) => void;
  onError?: (error: Error) => void;
  onClose?: () => void;
}

interface AstrBotContext {
  pluginName: string;
  displayName: string;
  locale: string;
  isDark: boolean;
  pluginI18n?: Record<string, unknown>;
}

interface ApiResponse {
  status?: string;
  data?: unknown;
  message?: string;
  code?: string;
  field_errors?: Record<string, string>;
  total?: number;
  page?: number;
  page_size?: number;
  [key: string]: unknown;
}

interface Window {
  AstrBotPluginPage: AstrBotPluginPageBridge;
  t: (key: string, ...args: string[]) => string;
  setLanguage: (lang: string) => void;
  getLanguage: () => string;
  hideBootstrap: () => void;
}
