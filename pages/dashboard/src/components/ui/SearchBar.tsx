import { useState, useEffect, useCallback, useRef } from "react";
import { Search, X, ScrollText, BookOpen, StickyNote } from "lucide-react";
import { apiGet, unwrapApiData } from "@/lib/bridge";
import { useI18n } from "@/hooks/useI18n";
import type { PageId } from "@/types";

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
      <button
        onClick={() => setOpen(true)}
        className="flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)] px-3 py-1.5 text-xs text-[var(--text-tertiary)] hover:border-[var(--color-border-light)] hover:text-[var(--text-secondary)] transition-colors"
      >
        <Search size={14} />
        <span className="hidden sm:inline">{t("search.placeholder")}</span>
        <kbd className="hidden sm:inline ml-1 rounded bg-[var(--color-surface)] px-1.5 py-0.5 text-[10px] text-[var(--text-tertiary)] border border-[var(--color-border)]">Ctrl+K</kbd>
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh]">
          <div className="fixed inset-0 bg-black/40 animate-fade-in" onClick={() => setOpen(false)} />
          <div className="relative w-full max-w-xl max-h-[60vh] flex flex-col rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-elevated)] shadow-modal animate-scale-in mx-4">
            <div className="flex items-center gap-3 px-4 py-3 border-b border-[var(--color-border)]">
              <Search size={18} className="text-[var(--text-tertiary)] shrink-0" />
              <input
                ref={inputRef} type="text" value={query}
                onChange={(e) => handleInput(e.target.value)}
                placeholder={t("search.inputPlaceholder")}
                className="flex-1 bg-transparent text-sm text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] outline-none"
              />
              {loading && <div className="h-4 w-4 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent shrink-0" />}
              <button onClick={() => setOpen(false)} className="p-1 rounded hover:bg-[var(--color-surface-secondary)] shrink-0">
                <X size={16} className="text-[var(--text-tertiary)]" />
              </button>
            </div>

            <div className="flex-1 overflow-auto">
              {query.length < 2 ? (
                <div className="p-8 text-center text-sm text-[var(--text-tertiary)]">{t("search.minChars")}</div>
              ) : totalResults === 0 ? (
                <div className="p-8 text-center text-sm text-[var(--text-tertiary)]">{t("search.noResults")}</div>
              ) : (
                <div className="py-2">
                  {groups.filter((g) => g.results.length > 0).map((group) => (
                    <div key={group.type} className="mb-1">
                      <div className="flex items-center gap-2 px-4 py-1.5 text-xs font-medium text-[var(--text-tertiary)]">
                        {group.icon} {group.label} <span className="text-[var(--text-tertiary)]/60">({group.results.length})</span>
                      </div>
                      {group.results.map((item) => (
                        <button
                          key={`${group.type}-${item.id}`}
                          onClick={() => handleClick(group.type, item.id)}
                          className="w-full text-left px-8 py-2 hover:bg-[var(--color-surface-secondary)] transition-colors"
                        >
                          <p className="text-sm text-[var(--text-primary)] line-clamp-1">{item.title}</p>
                          {item.subtitle && <p className="text-xs text-[var(--text-tertiary)] mt-0.5">{item.subtitle}</p>}
                        </button>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="flex items-center gap-4 px-4 py-2 border-t border-[var(--color-border)] text-[10px] text-[var(--text-tertiary)]">
              <span>{t("search.hintNav")}</span> <span>{t("search.hintOpen")}</span> <span>{t("search.hintClose")}</span>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
