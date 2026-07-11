import { useState, useEffect, useCallback, useRef } from "react";
import { Search, X, ScrollText, BookOpen, StickyNote } from "lucide-react";
import { apiGet, unwrapApiData } from "@/lib/bridge";
import { useI18n } from "@/hooks/useI18n";
import type { PageId } from "@/types";
import { Button } from "@/components/ui/Button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";

interface SearchBarProps {
  onNavigate: (page: PageId, state?: unknown) => void;
}

interface SearchResultItem {
  id: string;
  title: string;
  subtitle?: string;
}

interface SearchGroup {
  type: string;
  label: string;
  icon: React.ReactNode;
  results: SearchResultItem[];
}

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

function toStr(v: unknown): string {
  return String(v ?? "");
}

export function SearchBar({ onNavigate }: SearchBarProps) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [groups, setGroups] = useState<SearchGroup[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "k") {
        e.preventDefault();
        setOpen((prev) => !prev);
      }
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  useEffect(() => { if (open) inputRef.current?.focus(); }, [open]);

  const doSearch = useCallback(async (q: string) => {
    if (q.trim().length < 2) { setGroups([]); return; }
    setLoading(true);
    try {
      const [memRes, kwRes, noteRes] = await Promise.allSettled([
        apiGet("memories", { keyword: q, page_size: "5" }),
        apiGet("knowledge/search", { query: q, limit: "5" }),
        apiGet("notes/search", { query: q, limit: "5" }),
      ]);
      const memList: ApiMemoryItem[]   = memRes.status   === "fulfilled" ? (unwrapApiData(memRes.value)  ?? []) : [];
      const kwList: ApiKnowledgeItem[] = kwRes.status    === "fulfilled" ? (unwrapApiData(kwRes.value)   ?? []) : [];
      const noteList: ApiNoteItem[]    = noteRes.status  === "fulfilled" ? (unwrapApiData(noteRes.value)  ?? []) : [];

      setGroups([
        { type: "memories", label: t("nav.memory"), icon: <ScrollText size={14} />,
          results: memList.map((m) => ({ id: toStr(m.id ?? ""), title: m.content ?? m.summary ?? "—", subtitle: `importance: ${(m.importance ?? 0).toFixed(1)}` })) },
        { type: "knowledge", label: t("nav.knowledge"), icon: <BookOpen size={14} />,
          results: kwList.map((k) => ({ id: toStr(k.entry_id ?? k.id ?? ""), title: k.title ?? k.content ?? "—", subtitle: k.category ?? "" })) },
        { type: "notes", label: t("nav.notes"), icon: <StickyNote size={14} />,
          results: noteList.map((n) => ({ id: toStr(n.note_id ?? n.id ?? ""), title: n.title ?? n.content ?? "—", subtitle: n.status ?? "" })) },
      ]);
    } catch { setGroups([]); }
    finally { setLoading(false); }
  }, [t]);

  const handleInput = (value: string) => {
    setQuery(value);
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => doSearch(value), 300);
  };

  const handleClick = (type: string, id: string) => {
    setOpen(false); setQuery("");
    const page = type === "memories" ? "memory" : type === "knowledge" ? "knowledge" : "notes";
    onNavigate(page as PageId, { highlightId: id });
  };

  const totalResults = groups.reduce((s, g) => s + g.results.length, 0);

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

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          aria-label="Global search"
          showCloseButton={false}
          className="top-[15vh] max-h-[70vh] max-w-xl -translate-y-0 gap-0 overflow-hidden p-0"
        >
          <DialogTitle className="sr-only">Global search</DialogTitle>
          <div className="flex items-center gap-3 border-b px-4 py-3">
              <Search className="size-4 shrink-0 text-muted-foreground" />
              <input
                ref={inputRef} type="text" value={query}
                onChange={(e) => handleInput(e.target.value)}
                placeholder={t("search.inputPlaceholder")}
                className="h-8 min-w-0 flex-1 bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
              />
              {loading ? <div className="size-4 shrink-0 animate-spin rounded-full border-2 border-primary border-t-transparent" /> : null}
              <Button type="button" variant="ghost" size="icon-sm" aria-label="Close search" onClick={() => setOpen(false)}>
                <X />
              </Button>
          </div>

          <div className="min-h-0 flex-1 overflow-auto">
              {query.length < 2 ? (
                <div className="p-8 text-center text-sm text-muted-foreground">{t("search.minChars")}</div>
              ) : totalResults === 0 ? (
                <div className="p-8 text-center text-sm text-muted-foreground">{t("search.noResults")}</div>
              ) : (
                <div className="py-2">
                  {groups.filter((g) => g.results.length > 0).map((group) => (
                    <div key={group.type} className="mb-1">
                      <div className="flex items-center gap-2 px-4 py-1.5 text-xs font-medium text-muted-foreground">
                        {group.icon} {group.label} <span className="opacity-60">({group.results.length})</span>
                      </div>
                      {group.results.map((item) => (
                        <button
                          type="button"
                          key={`${group.type}-${item.id}`}
                          onClick={() => handleClick(group.type, item.id)}
                          className="w-full px-8 py-2 text-left transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                        >
                          <p className="line-clamp-1 text-sm text-foreground">{item.title}</p>
                          {item.subtitle ? <p className="mt-0.5 text-xs text-muted-foreground">{item.subtitle}</p> : null}
                        </button>
                      ))}
                    </div>
                  ))}
                </div>
              )}
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
