// ApiResponse is declared globally in vite-env.d.ts

const ENDPOINT_PREFIX = "page";

function localizedError(key: string, ...args: string[]): Error {
  try {
    if (typeof window !== "undefined" && typeof window.t === "function") {
      const translated = window.t(key, ...args);
      if (translated && translated !== key) return new Error(translated);
    }
  } catch { /* fall through to the stable key */ }
  return new Error(key);
}

function buildEndpoint(path: string): string {
  return `${ENDPOINT_PREFIX}/${path.replace(/^\/+/, "").replace(/\/+/g, "/")}`;
}

export async function apiGet(
  path: string,
  params?: Record<string, string>
): Promise<ApiResponse> {
  const bridge = window.AstrBotPluginPage;
  if (!bridge) throw localizedError("error.bridgeUnavailable");
  return bridge.apiGet(buildEndpoint(path), params ?? {});
}

export async function apiPost(
  path: string,
  body?: unknown
): Promise<ApiResponse> {
  const bridge = window.AstrBotPluginPage;
  if (!bridge) throw localizedError("error.bridgeUnavailable");
  return bridge.apiPost(buildEndpoint(path), body ?? {});
}

export async function apiRequest(
  path: string,
  options?: { method?: string; body?: unknown; retries?: number }
): Promise<ApiResponse> {
  const { method = "GET", body } = options ?? {};
  const retries = options?.retries ?? 2;

  let lastError: unknown;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      if (method === "GET") {
        const qi = path.indexOf("?");
        if (qi !== -1) {
          const base = path.substring(0, qi);
          const qs = path.substring(qi + 1);
          const params: Record<string, string> = {};
          new URLSearchParams(qs).forEach((v, k) => { params[k] = v; });
          return apiGet(base, params);
        }
        return apiGet(path);
      }
      return apiPost(path, body);
    } catch (e) {
      lastError = e;
      if (attempt === retries) throw e;
      await new Promise((r) => setTimeout(r, Math.min(1000 * Math.pow(2, attempt), 5000)));
    }
  }
  throw lastError ?? localizedError("error.requestFailed");
}

export function unwrapApiData<T = Record<string, unknown>>(response: ApiResponse): T {
  if (response?.status === "ok" && Object.prototype.hasOwnProperty.call(response, "data")) {
    return (response.data ?? {}) as T;
  }
  if (response?.status === "error") {
    throw response.message
      ? new Error(String(response.message))
      : localizedError("error.requestFailed");
  }
  // Guard: if the response doesn't match the expected envelope, throw instead
  // of silently casting — downstream components would receive wrong-shaped data.
  if (typeof response !== "object" || response === null) {
    throw localizedError("error.unexpectedResponseType", typeof response);
  }
  return response as unknown as T;
}

export function normalizeImportance(value: number): number {
  let n = value;
  if (!Number.isFinite(n)) n = 0.5;
  if (n <= 1) n *= 10;
  return Math.min(10, Math.max(0, n));
}
