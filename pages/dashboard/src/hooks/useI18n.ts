import { useState, useEffect, useCallback } from "react";
import { LANG_MAPS, I18N_MAP } from "@/mock";

// 全局版本计数器：每次 languagechange / context change 时递增
let langVersion = 0;
const listeners = new Set<() => void>();

if (typeof window !== "undefined") {
  window.addEventListener("languagechange", () => {
    syncDocumentLanguage(getEffectiveLanguageOverride() ?? getCurrentLocale());
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

function getStoredLanguage(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const stored = window.localStorage.getItem("lmem_lang");
    return stored === "zh" || stored === "en" || stored === "ru" ? stored : null;
  } catch {
    return null;
  }
}

/** 返回仍然有效的本地语言覆盖；外部清理或修改存储后重新跟随当前状态。 */
function getEffectiveLanguageOverride(): string | null {
  const stored = getStoredLanguage();
  if (stored === langOverride) return langOverride;
  langOverride = stored;
  return stored;
}

// 记录本地语言覆盖值（null 表示跟随 AstrBot）
let langOverride: string | null = getStoredLanguage();
syncDocumentLanguage(langOverride ?? getCurrentLocale());

export function useI18n() {
  const [version, setVersion] = useState(langVersion);

  useEffect(() => {
    const fn = () => setVersion(langVersion);
    let removeBridgeListener: (() => void) | undefined;
    listeners.add(fn);
    // 同时监听 AstrBot 上下文变更
    try {
      const bridge = window.AstrBotPluginPage;
      if (bridge && typeof bridge.onContextChange === "function") {
        const handler = () => {
          syncDocumentLanguage(getEffectiveLanguageOverride() ?? getCurrentLocale());
          langVersion++;
          listeners.forEach((l) => l());
        };
        bridge.onContextChange(handler);
        if (typeof bridge.offContextChange === "function") {
          removeBridgeListener = () => bridge.offContextChange(handler);
        }
      }
    } catch { /* */ }
    return () => {
      listeners.delete(fn);
      removeBridgeListener?.();
    };
  }, []);

  const t = useCallback((key: string, ...args: string[]): string => {
    const dashboardKey = `dashboard.${key}`;
    const languageOverride = getEffectiveLanguageOverride();
    // 辅助函数：替换 {0}、{1} 等占位符
    const replaceArgs = (val: string): string => {
      if (!args.length) return val;
      let result = val;
      args.forEach((arg, i) => {
        result = result.replace(new RegExp(`\\{${i}\\}`, "g"), () => arg);
      });
      return result;
    };

    // 显式选择的 Dashboard 语言优先于嵌入模式中可能未变化的宿主语言。
    if (languageOverride) {
      const langKey = languageOverride === "en" ? "en" : languageOverride === "ru" ? "ru" : "zh";
      const value = (LANG_MAPS[langKey] ?? I18N_MAP)[key];
      if (value) return replaceArgs(value);
    }

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
        const locale = languageOverride ?? bridge.getLocale?.() ?? "zh";
        let map = i18nCache[locale];
        if (!map) {
          map = bridge.getI18n() as Record<string, unknown>;
          if (map) i18nCache[locale] = map;
        }
        if (languageOverride && locale !== bridge.getLocale?.()) {
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
      const currentLocale = languageOverride ?? getCurrentLocale();
      const langKey = currentLocale === "en" ? "en" : currentLocale === "ru" ? "ru" : "zh";
      const map = LANG_MAPS[langKey] ?? I18N_MAP;
      const val = map[key];
      if (val) {
        return replaceArgs(val);
      }
    } catch { /* */ }

    return key;
  }, [version]);

  const currentLang = useCallback((): string => {
    return getEffectiveLanguageOverride() ?? getCurrentLocale();
  }, [version]);

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

function syncDocumentLanguage(language: string): void {
  if (typeof document === "undefined") return;
  const normalized = language.slice(0, 2).toLowerCase();
  document.documentElement.lang = normalized === "en"
    ? "en-US"
    : normalized === "ru"
      ? "ru-RU"
      : "zh-CN";
}

/** 切换语言并通知所有 useI18n 实例重新渲染。 */
export function toggleLanguage(): string {
  const current = getEffectiveLanguageOverride() ?? getCurrentLocale();
  const next = current === "zh" ? "en" : current === "en" ? "ru" : "zh";
  langOverride = next;
  syncDocumentLanguage(next);

  try {
    window.localStorage.setItem("lmem_lang", next);
  } catch { /* */ }

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
