import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

export type Theme = "light" | "dark";

const THEME_STORAGE_KEY = "memora_theme";
const THEME_OVERRIDE_KEY = "memora_theme_override";
const THEME_TRANSITION_CLASS = "theme-transitioning";
const THEME_TRANSITION_FALLBACK_MS = 220;
const THEME_TRANSITION_SENTINELS = ["--background", "--sidebar"] as const;

function isTheme(value: unknown): value is Theme {
  return value === "light" || value === "dark";
}

function readStoredTheme(): Theme | null {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    return isTheme(stored) ? stored : null;
  } catch {
    return null;
  }
}

function hasManualOverride(): boolean {
  try {
    return localStorage.getItem(THEME_OVERRIDE_KEY) === "1";
  } catch {
    return false;
  }
}

function readBridgeTheme(): Theme | null {
  try {
    const context = window.AstrBotPluginPage?.getContext?.();
    return typeof context?.isDark === "boolean"
      ? context.isDark ? "dark" : "light"
      : null;
  } catch {
    return null;
  }
}

function readTheme(): Theme {
  const storedTheme = readStoredTheme();
  if (hasManualOverride() && storedTheme) return storedTheme;

  if (typeof document !== "undefined") {
    const htmlTheme = document.documentElement.getAttribute("data-theme");
    if (isTheme(htmlTheme)) return htmlTheme;
    if (document.documentElement.classList.contains("dark")) return "dark";
  }

  return readBridgeTheme() ?? storedTheme ?? "light";
}

function applyDocumentTheme(theme: Theme) {
  const root = document.documentElement;
  if (root.getAttribute("data-theme") !== theme) {
    root.setAttribute("data-theme", theme);
  }
  root.classList.toggle("dark", theme === "dark");
}

function persistTheme(theme: Theme, manual: boolean) {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
    if (manual) localStorage.setItem(THEME_OVERRIDE_KEY, "1");
  } catch {
    // Theme switching remains usable when storage is unavailable.
  }
}

function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  } catch {
    return false;
  }
}

function themeFromMutation(records: MutationRecord[]): Theme | null {
  for (let index = records.length - 1; index >= 0; index -= 1) {
    const attributeName = records[index].attributeName;
    if (attributeName === "data-theme") {
      const value = document.documentElement.getAttribute("data-theme");
      if (isTheme(value)) return value;
    }
    if (attributeName === "class") {
      return document.documentElement.classList.contains("dark") ? "dark" : "light";
    }
  }
  return null;
}

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(readTheme);
  const committedThemeRef = useRef(theme);
  const pendingThemeRef = useRef<Theme | null>(null);
  const manualOverrideRef = useRef(hasManualOverride());
  const animationFrameRef = useRef<number | null>(null);
  const cleanupTimerRef = useRef<number | null>(null);
  const transitionListenerCleanupRef = useRef<(() => void) | null>(null);

  const commitTheme = useCallback((nextTheme: Theme, manual: boolean) => {
    committedThemeRef.current = nextTheme;
    pendingThemeRef.current = null;
    if (manual) manualOverrideRef.current = true;
    applyDocumentTheme(nextTheme);
    persistTheme(nextTheme, manual);
    setThemeState((currentTheme) => (
      currentTheme === nextTheme ? currentTheme : nextTheme
    ));
  }, []);

  const cancelScheduledTransition = useCallback((removeClass: boolean) => {
    if (animationFrameRef.current !== null) {
      window.cancelAnimationFrame(animationFrameRef.current);
      animationFrameRef.current = null;
    }
    if (cleanupTimerRef.current !== null) {
      window.clearTimeout(cleanupTimerRef.current);
      cleanupTimerRef.current = null;
    }
    transitionListenerCleanupRef.current?.();
    transitionListenerCleanupRef.current = null;
    if (removeClass) {
      document.documentElement.classList.remove(THEME_TRANSITION_CLASS);
    }
  }, []);

  const armTransitionCleanup = useCallback(() => {
    const root = document.documentElement;
    const pendingProperties = new Set<string>(THEME_TRANSITION_SENTINELS);

    const detachListener = () => {
      root.removeEventListener("transitionend", handleTransitionEnd);
    };
    const finishTransition = () => {
      if (cleanupTimerRef.current !== null) {
        window.clearTimeout(cleanupTimerRef.current);
        cleanupTimerRef.current = null;
      }
      transitionListenerCleanupRef.current?.();
      transitionListenerCleanupRef.current = null;
      animationFrameRef.current = window.requestAnimationFrame(() => {
        animationFrameRef.current = null;
        root.classList.remove(THEME_TRANSITION_CLASS);
      });
    };
    function handleTransitionEnd(event: Event) {
      if (
        event.target !== root
        || !pendingProperties.delete((event as TransitionEvent).propertyName)
      ) {
        return;
      }
      if (pendingProperties.size === 0) finishTransition();
    }

    root.addEventListener("transitionend", handleTransitionEnd);
    transitionListenerCleanupRef.current = detachListener;
    cleanupTimerRef.current = window.setTimeout(
      finishTransition,
      THEME_TRANSITION_FALLBACK_MS,
    );
  }, []);

  useLayoutEffect(() => {
    applyDocumentTheme(committedThemeRef.current);
    persistTheme(committedThemeRef.current, false);
  }, []);

  useEffect(() => {
    const observer = new MutationObserver((records) => {
      if (manualOverrideRef.current) {
        applyDocumentTheme(committedThemeRef.current);
        return;
      }

      const observedTheme = themeFromMutation(records);
      if (observedTheme && observedTheme !== committedThemeRef.current) {
        commitTheme(observedTheme, false);
      }
    });

    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme", "class"],
    });

    return () => observer.disconnect();
  }, [commitTheme]);

  useEffect(() => {
    const bridge = window.AstrBotPluginPage;
    if (!bridge || typeof bridge.onContext !== "function") return;

    const handler = (context: { isDark?: boolean }) => {
      if (manualOverrideRef.current || typeof context?.isDark !== "boolean") return;
      commitTheme(context.isDark ? "dark" : "light", false);
    };

    let unsubscribe: (() => void) | undefined;
    try {
      unsubscribe = bridge.onContext(handler);
    } catch {
      return;
    }

    return () => {
      try {
        unsubscribe?.();
      } catch {
        // 桥接清理失败不得阻塞 Dashboard 卸载。
      }
    };
  }, [commitTheme]);

  useEffect(() => () => {
    cancelScheduledTransition(true);
  }, [cancelScheduledTransition]);

  const toggleTheme = useCallback(() => {
    const baseTheme = pendingThemeRef.current ?? committedThemeRef.current;
    const nextTheme: Theme = baseTheme === "light" ? "dark" : "light";
    pendingThemeRef.current = nextTheme;
    manualOverrideRef.current = true;
    cancelScheduledTransition(false);

    if (prefersReducedMotion()) {
      document.documentElement.classList.remove(THEME_TRANSITION_CLASS);
      commitTheme(nextTheme, true);
      return;
    }

    document.documentElement.classList.add(THEME_TRANSITION_CLASS);
    animationFrameRef.current = window.requestAnimationFrame(() => {
      animationFrameRef.current = null;
      commitTheme(nextTheme, true);
      animationFrameRef.current = window.requestAnimationFrame(() => {
        animationFrameRef.current = null;
        armTransitionCleanup();
      });
    });
  }, [armTransitionCleanup, cancelScheduledTransition, commitTheme]);

  return { theme, toggleTheme };
}
