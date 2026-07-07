import { useState, useEffect, useCallback } from "react";
import { LANG_MAPS, I18N_MAP } from "@/mock";

// 全局版本计数器：每次 languagechange / context change 时递增
let langVersion = 0;
const listeners = new Set<() => void>();

if (typeof window !== "undefined") {
  window.addEventListener("languagechange", () => {
    langVersion++;
    listeners.forEach((fn) => fn());
  });
}

/** 深层取值：`dashboard.nav.preview` → `i18n_obj["dashboard"]["nav"]["preview"]` */
function deepGet(obj: Record<string, unknown>, path: string): string | undefined {
  const parts = path.split(".");
  let current: unknown = obj;
  for (const p of parts) {
    if (current == null || typeof current !== "object") return undefined;
    current = (current as Record<string, unknown>)[p];
  }
  return typeof current === "string" ? current : undefined;
}

// 缓存：locale → i18n map（由 bridge 提供时填充）
const i18nCache: Record<string, Record<string, unknown>> = {};

/** 从 bridge 获取当前 i18n 映射，写入缓存后返回。 */
function getBridgeI18n(): Record<string, unknown> | null {
  try {
    const bridge = window.AstrBotPluginPage;
    if (!bridge || typeof bridge.getI18n !== "function") return null;
    const i18n = bridge.getI18n() as Record<string, unknown>;
    const locale = bridge.getLocale?.() ?? "zh";
    i18nCache[locale] = i18n;
    return i18n;
  } catch {
    return null;
  }
}

// 记录本地语言覆盖值（null 表示跟随 AstrBot）
let langOverride: string | null = null;

export function useI18n() {
  const [, setTick] = useState(langVersion);

  useEffect(() => {
    const fn = () => setTick(langVersion);
    listeners.add(fn);
    // 同时监听 AstrBot 上下文变更
    try {
      const bridge = window.AstrBotPluginPage;
      if (bridge && typeof bridge.onContextChange === "function") {
        bridge.onContextChange(() => {
          langVersion++;
          listeners.forEach((l) => l());
        });
      }
    } catch { /* */ }
    return () => {
      listeners.delete(fn);
    };
  }, []);

  const t = useCallback((key: string, ...args: string[]): string => {
    const dashboardKey = `dashboard.${key}`;
    // 辅助函数：替换 {0}、{1} 等占位符
    const replaceArgs = (val: string): string => {
      if (!args.length) return val;
      let result = val;
      args.forEach((arg, i) => {
        result = result.replace(new RegExp(`\\{${i}\\}`, "g"), arg);
      });
      return result;
    };

    // 1. 优先尝试带完整 dashboard. 前缀的 bridge.t()
    try {
      const bridge = window.AstrBotPluginPage;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      if (bridge && typeof (bridge as any).t === "function") {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const result = (bridge as any).t(dashboardKey);
        if (result && result !== dashboardKey) return replaceArgs(result);
      }
    } catch { /* */ }

    // 2. 尝试从 getI18n() 结果中做深层查找（带或不带 dashboard. 前缀）
    try {
      const bridge = window.AstrBotPluginPage;
      if (bridge && typeof bridge.getI18n === "function") {
        const locale = langOverride ?? bridge.getLocale?.() ?? "zh";
        let map = i18nCache[locale];
        if (!map) {
          map = bridge.getI18n() as Record<string, unknown>;
          if (map) i18nCache[locale] = map;
        }
        if (langOverride && locale !== bridge.getLocale?.()) {
          const primaryLocale = bridge.getLocale?.() ?? "zh";
          if (!i18nCache[primaryLocale]) {
            getBridgeI18n();
          }
        }
        if (map) {
          // 先尝试 dashboard. 前缀路径，再尝试平铺 key
          const val = deepGet(map, dashboardKey) ?? deepGet(map, key);
          if (val) return replaceArgs(val);
        }
      }
    } catch { /* */ }

    // 3. 回退：尝试无 dashboard. 前缀的 bridge.t()
    try {
      const bridge = window.AstrBotPluginPage;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      if (bridge && typeof (bridge as any).t === "function") {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const result = (bridge as any).t(key);
        if (result && result !== key) return replaceArgs(result);
      }
    } catch { /* */ }

    // 4. 最后手段：尝试全局 window.t（mock bridge 回退）
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      if (typeof (window as any).t === "function") {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const result = (window as any).t(key, ...args);
        if (result && result !== key) return result;
      }
    } catch { /* */ }

    // 5. 直接查询 LANG_MAPS，绕过可能被覆盖的 window.t
    try {
      const currentLocale = langOverride ?? getCurrentLocale();
      const langKey = currentLocale === "en" ? "en" : currentLocale === "ru" ? "ru" : "zh";
      const map = LANG_MAPS[langKey] ?? I18N_MAP;
      const val = map[key];
      if (val) {
        return replaceArgs(val);
      }
    } catch { /* */ }

    return key;
  }, []);

  const currentLang = useCallback((): string => {
    return langOverride ?? getCurrentLocale();
  }, []);

  return { t, currentLang };
}

function getCurrentLocale(): string {
  try {
    const bridge = window.AstrBotPluginPage;
    if (bridge && typeof bridge.getLocale === "function") {
      return bridge.getLocale().slice(0, 2);
    }
  } catch { /* */ }
  return "zh";
}

/** Toggle language and notify all useI18n hooks to re-render. */
export function toggleLanguage(): string {
  const current = langOverride ?? getCurrentLocale();
  const next = current === "zh" ? "en" : current === "en" ? "ru" : "zh";
  langOverride = next;

  // 同步到全局 window.setLanguage（mock bridge）
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    if (typeof (window as any).setLanguage === "function") {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (window as any).setLanguage(next);
    }
  } catch { /* */ }

  // 刷新新语言对应的 i18n 缓存
  try {
    const bridge = window.AstrBotPluginPage;
    if (bridge && typeof bridge.getI18n === "function") {
      // 使目标语言缓存失效，以便重新拉取
      delete i18nCache[next];
      getBridgeI18n(); // re-fetch for current bridge locale
    }
  } catch { /* */ }
  window.dispatchEvent(new Event("languagechange"));
  return next;
}

export { getCurrentLocale };
