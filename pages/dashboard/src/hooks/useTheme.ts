import { useState, useEffect, useCallback } from "react";

type Theme = "light" | "dark";

function readTheme(): Theme {
  // 1. Prioritize documentElement data-theme or class (AstrBot host set theme directly on iframe element)
  if (typeof document !== "undefined") {
    const htmlAttr = document.documentElement.getAttribute("data-theme");
    if (htmlAttr === "dark" || htmlAttr === "light") return htmlAttr;
    if (document.documentElement.classList.contains("dark")) return "dark";
  }
  // 2. Try bridge context
  try {
    const bridge = window.AstrBotPluginPage;
    if (bridge) {
      const ctx = bridge.getContext();
      if (ctx && typeof ctx.isDark === "boolean") return ctx.isDark ? "dark" : "light";
    }
  } catch { /* bridge not ready */ }
  // 3. Try localStorage fallback
  try {
    const stored = localStorage.getItem("lmem_theme");
    if (stored === "dark" || stored === "light") return stored;
  } catch { /* localStorage unavailable */ }
  return "light";
}

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(readTheme);

  // Sync state changes to DOM and localStorage
  useEffect(() => {
    const currentAttr = document.documentElement.getAttribute("data-theme");
    if (currentAttr !== theme) {
      document.documentElement.setAttribute("data-theme", theme);
    }
    if (theme === "dark") {
      document.documentElement.classList.add("dark");
    } else {
      document.documentElement.classList.remove("dark");
    }
    try { localStorage.setItem("lmem_theme", theme); } catch { /* ignore */ }
  }, [theme]);

  // Listen to DOM attribute changes (AstrBot host changing data-theme/class directly on html)
  useEffect(() => {
    const observer = new MutationObserver(() => {
      const currentAttr = document.documentElement.getAttribute("data-theme");
      const hasDarkClass = document.documentElement.classList.contains("dark");
      
      let targetTheme: Theme = "light";
      if (currentAttr === "dark" || hasDarkClass) {
        targetTheme = "dark";
      } else if (currentAttr === "light") {
        targetTheme = "light";
      } else {
        // Fallback to localStorage
        try {
          const stored = localStorage.getItem("lmem_theme");
          if (stored === "dark" || stored === "light") {
            targetTheme = stored;
          }
        } catch {}
      }
      setThemeState((prev) => (prev !== targetTheme ? targetTheme : prev));
    });

    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme", "class"],
    });

    return () => observer.disconnect();
  }, []);

  // Listen to bridge context changes
  useEffect(() => {
    const bridge = window.AstrBotPluginPage;
    if (!bridge || typeof bridge.onContextChange !== "function") return;
    const handler = (ctx: { isDark?: boolean }) => {
      if (typeof ctx?.isDark === "boolean") {
        setThemeState(ctx.isDark ? "dark" : "light");
      }
    };
    bridge.onContextChange(handler);
    return () => { bridge.offContextChange(handler); };
  }, []);

  const toggleTheme = useCallback(() => {
    setThemeState((prev) => (prev === "light" ? "dark" : "light"));
  }, []);

  return { theme, toggleTheme };
}

