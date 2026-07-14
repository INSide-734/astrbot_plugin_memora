import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  BookOpen,
  PanelsTopLeft,
  ScrollText,
  Search,
  Settings,
  StickyNote,
  X,
} from "lucide-react";
import { apiGet, unwrapApiData } from "@/lib/bridge";
import {
  buildConfigSearchEntries,
  buildPageSearchEntries,
  highlightSegments,
  searchLocalEntries,
  type LocalSearchEntry,
} from "@/lib/globalSearch";
import { useI18n } from "@/hooks/useI18n";
import type { PageId, PageNavigationIntent } from "@/types";
import type { ConfigSchemaData } from "@/types/config";
import { dashboardLocale, formatDashboardNumber, translateEnum } from "@/lib/i18n";
import { Button } from "@/components/ui/Button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";

interface SearchBarProps {
  onNavigate: (page: PageId, intent?: PageNavigationIntent) => void;
}

type SearchResultKind = "page" | "config" | "memories" | "knowledge" | "notes";

interface SearchResultItem {
  key: string;
  kind: SearchResultKind;
  id: string;
  title: string;
  subtitle?: string;
  page: PageId;
  configPath?: string;
}

interface SearchGroup {
  type: SearchResultKind;
  label: string;
  icon: React.ReactNode;
  results: SearchResultItem[];
  total: number;
}

interface RemoteGroup<T> {
  items: T[];
  total: number;
}

interface SearchData {
  memories: RemoteGroup<ApiMemoryItem>;
  knowledge: RemoteGroup<ApiKnowledgeItem>;
  notes: RemoteGroup<ApiNoteItem>;
}

type ConfigSchemaStatus = "idle" | "loading" | "ready" | "error";

interface ApiMemoryItem {
  id?: unknown;
  content?: string;
  summary?: string;
  importance?: number;
}

interface ApiKnowledgeItem {
  entry_id?: unknown;
  id?: unknown;
  title?: string;
  content?: string;
  category?: string;
}

interface ApiNoteItem {
  note_id?: unknown;
  id?: unknown;
  title?: string;
  content?: string;
  status?: string;
}

const EMPTY_SEARCH_DATA: SearchData = {
  memories: { items: [], total: 0 },
  knowledge: { items: [], total: 0 },
  notes: { items: [], total: 0 },
};

function toStr(v: unknown): string {
  return String(v ?? "");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function extractSearchGroup<T>(
  result: PromiseSettledResult<ApiResponse>,
  keys: readonly string[]
): RemoteGroup<T> {
  if (result.status !== "fulfilled") return { items: [], total: 0 };

  try {
    const data = unwrapApiData<unknown>(result.value);
    if (Array.isArray(data)) {
      const items = data.filter(isRecord) as T[];
      return { items, total: items.length };
    }
    if (!data || typeof data !== "object") return { items: [], total: 0 };

    const record = data as Record<string, unknown>;
    for (const key of keys) {
      if (Array.isArray(record[key])) {
        const items = record[key].filter(isRecord) as T[];
        const rawTotal = record.total;
        const total =
          typeof rawTotal === "number" ||
          (typeof rawTotal === "string" && rawTotal.trim() !== "")
            ? Number(rawTotal)
            : Number.NaN;
        return {
          items,
          total: Number.isFinite(total) && total >= 0 ? total : items.length,
        };
      }
    }
  } catch {
    return { items: [], total: 0 };
  }

  return { items: [], total: 0 };
}

function HighlightedText({ text, query }: { text: string; query: string }) {
  return <>{highlightSegments(text, query).map((segment, index) =>
    segment.matched ? (
      <mark
        key={`${segment.text}-${index}`}
        className="bg-muted text-foreground"
      >
        {segment.text}
      </mark>
    ) : (
      <span key={`${segment.text}-${index}`}>{segment.text}</span>
    )
  )}</>;
}

export function SearchBar({ onNavigate }: SearchBarProps) {
  const { t, currentLang } = useI18n();
  const language = currentLang();
  const locale = dashboardLocale(language);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [searchData, setSearchData] = useState<SearchData>(EMPTY_SEARCH_DATA);
  const [configEntries, setConfigEntries] = useState<LocalSearchEntry[]>([]);
  const [configSchemaStatus, setConfigSchemaStatus] =
    useState<ConfigSchemaStatus>("idle");
  const [activeIndex, setActiveIndex] = useState(0);
  const queryReady = query.trim().length >= 2;
  const pageEntries = useMemo(
    () => buildPageSearchEntries(t),
    [language, t],
  );
  const pageMatches = useMemo(
    () => queryReady
      ? searchLocalEntries(pageEntries, query, 5)
      : { items: [], total: 0 },
    [pageEntries, query, queryReady],
  );
  const configMatches = useMemo(
    () => queryReady
      ? searchLocalEntries(configEntries, query, 8)
      : { items: [], total: 0 },
    [configEntries, query, queryReady],
  );
  const inputRef = useRef<HTMLInputElement>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout>>();
  const configSchemaStatusRef = useRef<ConfigSchemaStatus>("idle");
  const schemaRequestRef = useRef<Promise<void> | null>(null);
  const schemaAttemptedThisOpenRef = useRef(false);
  const searchGenerationRef = useRef(0);
  const mountedRef = useRef(true);
  const navigationRequestIdRef = useRef(0);
  const activeOptionRef = useRef<HTMLButtonElement | null>(null);

  const updateConfigSchemaStatus = useCallback((status: ConfigSchemaStatus) => {
    if (!mountedRef.current) return;
    configSchemaStatusRef.current = status;
    setConfigSchemaStatus(status);
  }, []);

  const loadConfigSchema = useCallback((): Promise<void> => {
    if (configSchemaStatusRef.current === "ready") return Promise.resolve();
    if (schemaRequestRef.current) return schemaRequestRef.current;

    updateConfigSchemaStatus("loading");
    const request = apiGet("config/schema")
      .then((response) => {
        const data = unwrapApiData<ConfigSchemaData>(response);
        const entries = buildConfigSearchEntries(data.schema);
        if (!mountedRef.current) return;
        setConfigEntries(entries);
        updateConfigSchemaStatus("ready");
      })
      .catch(() => {
        updateConfigSchemaStatus("error");
      })
      .finally(() => {
        schemaRequestRef.current = null;
      });
    schemaRequestRef.current = request;
    return request;
  }, [updateConfigSchemaStatus]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      clearTimeout(timerRef.current);
      timerRef.current = undefined;
      searchGenerationRef.current += 1;
    };
  }, []);

  useEffect(() => {
    if (
      !open ||
      configSchemaStatus === "ready" ||
      schemaAttemptedThisOpenRef.current
    ) {
      return;
    }
    schemaAttemptedThisOpenRef.current = true;
    void loadConfigSchema();
  }, [configSchemaStatus, loadConfigSchema, open]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const doSearch = useCallback(async (q: string, generation: number) => {
    const trimmedQuery = q.trim();
    if (trimmedQuery.length < 2) return;

    const [memRes, kwRes, noteRes] = await Promise.allSettled([
      apiGet("memories", { keyword: trimmedQuery, page_size: "5" }),
      apiGet("knowledge/search", { query: trimmedQuery, limit: "5" }),
      apiGet("notes/search", { query: trimmedQuery, limit: "5" }),
    ]);

    if (!mountedRef.current || generation !== searchGenerationRef.current) return;

    setSearchData({
      memories: extractSearchGroup<ApiMemoryItem>(memRes, ["items", "memories"]),
      knowledge: extractSearchGroup<ApiKnowledgeItem>(kwRes, ["entries", "items"]),
      notes: extractSearchGroup<ApiNoteItem>(noteRes, ["notes", "items"]),
    });
    setLoading(false);
  }, []);

  const handleInput = (value: string) => {
    setQuery(value);
    clearTimeout(timerRef.current);
    timerRef.current = undefined;
    const generation = searchGenerationRef.current + 1;
    searchGenerationRef.current = generation;
    setSearchData(EMPTY_SEARCH_DATA);

    if (value.trim().length < 2) {
      setLoading(false);
      return;
    }

    setLoading(true);
    timerRef.current = setTimeout(() => {
      timerRef.current = undefined;
      void doSearch(value, generation);
    }, 300);
  };

  const closeSearch = useCallback(() => {
    clearTimeout(timerRef.current);
    timerRef.current = undefined;
    searchGenerationRef.current += 1;
    schemaAttemptedThisOpenRef.current = false;
    setLoading(false);
    setSearchData(EMPTY_SEARCH_DATA);
    setQuery("");
    setOpen(false);
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLocaleLowerCase() === "k") {
        e.preventDefault();
        if (open) closeSearch();
        else setOpen(true);
      } else if (e.key === "Escape" && open) {
        closeSearch();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [closeSearch, open]);

  const groups: SearchGroup[] = [
    {
      type: "page",
      label: t("search.groupPages"),
      icon: <PanelsTopLeft size={14} />,
      total: pageMatches.total,
      results: pageMatches.items.flatMap((entry): SearchResultItem[] =>
        entry.page ? [{
          key: entry.id,
          kind: "page",
          id: entry.id,
          title: entry.title,
          subtitle: entry.subtitle,
          page: entry.page,
        }] : [],
      ),
    },
    {
      type: "config",
      label: t("search.groupConfig"),
      icon: <Settings size={14} />,
      total: configMatches.total,
      results: configMatches.items.map((entry): SearchResultItem => ({
        key: entry.id,
        kind: "config",
        id: entry.id,
        title: entry.title,
        subtitle: entry.path,
        page: "config",
        configPath: entry.path,
      })),
    },
    {
      type: "memories",
      label: t("nav.memory"),
      icon: <ScrollText size={14} />,
      results: searchData.memories.items.map((item): SearchResultItem => {
        const id = toStr(item.id ?? "");
        return {
          key: `memory:${id}`,
          kind: "memories",
          id,
          title: item.content ?? item.summary ?? "—",
          subtitle: `${t("table.importance")}: ${formatDashboardNumber(item.importance ?? 0, locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}`,
          page: "memory",
        };
      }),
      total: searchData.memories.total,
    },
    {
      type: "knowledge",
      label: t("nav.knowledge"),
      icon: <BookOpen size={14} />,
      results: searchData.knowledge.items.map((item): SearchResultItem => {
        const id = toStr(item.entry_id ?? item.id ?? "");
        return {
          key: `knowledge:${id}`,
          kind: "knowledge",
          id,
          title: item.title ?? item.content ?? "—",
          subtitle: translateEnum(t, "category", item.category),
          page: "knowledge",
        };
      }),
      total: searchData.knowledge.total,
    },
    {
      type: "notes",
      label: t("nav.notes"),
      icon: <StickyNote size={14} />,
      results: searchData.notes.items.map((item): SearchResultItem => {
        const id = toStr(item.note_id ?? item.id ?? "");
        return {
          key: `note:${id}`,
          kind: "notes",
          id,
          title: item.title ?? item.content ?? "—",
          subtitle: translateEnum(t, "status", item.status),
          page: "notes",
        };
      }),
      total: searchData.notes.total,
    },
  ];

  const visibleGroups = queryReady
    ? groups.filter((group) =>
        group.results.length > 0
        || (group.type === "config" && configSchemaStatus === "loading"),
      )
    : [];
  const visibleResults = visibleGroups.flatMap((group) => group.results);
  const visibleResultSignature = visibleResults
    .map((result) => result.key)
    .join("\u0000");
  const totalResults = visibleResults.length;
  const searchPending = loading || configSchemaStatus === "loading";
  const activeResult = visibleResults[activeIndex] ?? null;

  useEffect(() => {
    setActiveIndex(0);
  }, [query, visibleResultSignature]);

  useEffect(() => {
    if (activeIndex >= visibleResults.length) setActiveIndex(0);
  }, [activeIndex, visibleResults.length]);

  useEffect(() => {
    activeOptionRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [activeIndex, visibleResultSignature]);

  const openResult = useCallback((result: SearchResultItem) => {
    const intent: PageNavigationIntent | undefined = result.configPath
      ? {
          configTarget: {
            requestId: ++navigationRequestIdRef.current,
            path: result.configPath,
            query: query.trim(),
          },
        }
      : result.kind === "memories"
        || result.kind === "knowledge"
        || result.kind === "notes"
        ? {
            entityTarget: {
              requestId: ++navigationRequestIdRef.current,
              id: result.id,
            },
          }
        : undefined;
    closeSearch();
    onNavigate(result.page, intent);
  }, [closeSearch, onNavigate, query]);

  const handleInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (visibleResults.length === 0) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const delta = event.key === "ArrowDown" ? 1 : -1;
      setActiveIndex((index) =>
        (index + delta + visibleResults.length) % visibleResults.length,
      );
    } else if (event.key === "Enter" && activeResult) {
      event.preventDefault();
      openResult(activeResult);
    }
  };

  return (
    <>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => setOpen(true)}
        aria-label={t("search.placeholder")}
        className="text-muted-foreground"
      >
        <Search />
        <span className="hidden sm:inline">{t("search.placeholder")}</span>
        <kbd className="ml-1 hidden rounded-md border bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground sm:inline">Ctrl+K</kbd>
      </Button>

      <Dialog
        open={open}
        onOpenChange={(nextOpen) => {
          if (nextOpen) setOpen(true);
          else closeSearch();
        }}
      >
        <DialogContent
          aria-label={t("search.title")}
          showCloseButton={false}
          className="top-[15vh] max-h-[70vh] max-w-xl -translate-y-0 grid-rows-[auto_minmax(0,1fr)_auto] gap-0 overflow-hidden p-0"
        >
          <DialogTitle className="sr-only">{t("search.title")}</DialogTitle>
          <div className="flex items-center gap-3 border-b px-4 py-3">
              <Search className="size-4 shrink-0 text-muted-foreground" />
              <input
                ref={inputRef}
                type="text"
                role="combobox"
                aria-label={t("search.title")}
                aria-controls="global-search-results"
                aria-expanded={open}
                aria-activedescendant={activeResult
                  ? `global-search-option-${activeResult.key}`
                  : undefined}
                value={query}
                onChange={(e) => handleInput(e.target.value)}
                onKeyDown={handleInputKeyDown}
                placeholder={t("search.inputPlaceholder")}
                className="h-8 min-w-0 flex-1 bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
              />
              {searchPending ? <div className="size-4 shrink-0 animate-spin rounded-full border-2 border-primary border-t-transparent" /> : null}
              <Button type="button" variant="ghost" size="icon-sm" aria-label={t("search.close")} onClick={closeSearch}>
                <X />
              </Button>
          </div>

          <div className="min-h-0 overflow-y-auto overscroll-contain">
              <div
                id="global-search-results"
                role="listbox"
                aria-label={t("search.results")}
                className={queryReady ? "py-2" : undefined}
              >
                {!queryReady ? (
                  <div className="p-8 text-center text-sm text-muted-foreground">{t("search.minChars")}</div>
                ) : (
                  <>
                  {totalResults === 0 && !searchPending ? (
                    <div className="p-8 text-center text-sm text-muted-foreground">{t("search.noResults")}</div>
                  ) : null}
                    {visibleGroups.map((group, groupIndex) => {
                    const groupOffset = visibleGroups
                      .slice(0, groupIndex)
                      .reduce((sum, entry) => sum + entry.results.length, 0);
                    return (
                      <div
                        key={group.type}
                        data-search-group={group.type}
                        className="mb-1"
                      >
                        <div className="px-4 py-1.5 text-xs font-medium text-muted-foreground">
                          <div className="flex items-center gap-2">
                            {group.icon}
                            <span>{group.label}</span>
                            <span className="opacity-60">
                              {group.total > group.results.length
                                ? t(
                                    "search.countLimited",
                                    formatDashboardNumber(group.results.length, locale),
                                    formatDashboardNumber(group.total, locale),
                                  )
                                : `(${formatDashboardNumber(group.total, locale)})`}
                            </span>
                          </div>
                          {group.type === "config" && configSchemaStatus === "loading" ? (
                            <p className="mt-0.5 pl-6 font-normal text-muted-foreground">
                              {t("search.configLoading")}
                            </p>
                          ) : null}
                        </div>
                        {group.results.map((item, itemIndex) => {
                          const flatIndex = groupOffset + itemIndex;
                          return (
                            <button
                              type="button"
                              key={item.key}
                              ref={flatIndex === activeIndex ? activeOptionRef : undefined}
                              id={`global-search-option-${item.key}`}
                              role="option"
                              aria-label={`${group.label}: ${item.title}${item.subtitle ? ` ${item.subtitle}` : ""}`}
                              aria-selected={flatIndex === activeIndex}
                              onMouseMove={() => setActiveIndex(flatIndex)}
                              onClick={() => openResult(item)}
                              className="w-full px-8 py-2 text-left transition-colors hover:bg-muted aria-selected:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                            >
                              <p className="line-clamp-1 text-sm text-foreground">
                                <HighlightedText text={item.title} query={query} />
                              </p>
                              {item.subtitle ? (
                                <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
                                  <HighlightedText text={item.subtitle} query={query} />
                                </p>
                              ) : null}
                            </button>
                          );
                        })}
                      </div>
                    );
                    })}
                  </>
                )}
              </div>
          </div>

          <div className="flex items-center gap-4 border-t bg-muted/40 px-4 py-2 text-[10px] text-muted-foreground">
            <span>{t("search.hintNav")}</span>
            <span>{t("search.hintOpen")}</span>
            <span>{t("search.hintClose")}</span>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
