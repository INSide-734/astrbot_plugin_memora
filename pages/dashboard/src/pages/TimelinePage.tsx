import { useState, useEffect } from "react";
import { apiGet, unwrapApiData } from "@/lib/bridge";
import { useI18n } from "@/hooks/useI18n";
import type { MemoryItem } from "@/types";
import { Clock, Calendar } from "lucide-react";

interface TimelinePageProps {
  showToast: (message: string, isError?: boolean) => void;
}

type ZoomLevel = "day" | "week" | "month";
const ZOOM_HOURS: Record<ZoomLevel, number> = { day: 24, week: 168, month: 720 };

function localeForLang(lang: string): string {
  const map: Record<string, string> = { zh: "zh-CN", en: "en-US", ru: "ru-RU" };
  return map[lang] ?? "zh-CN";
}

function parseTimestamp(item: MemoryItem): number {
  const raw = item.created_at;
  if (typeof raw === "number") return raw;
  if (typeof raw === "string") {
    const d = new Date(raw);
    if (!isNaN(d.getTime())) return d.getTime() / 1000;
  }
  return 0;
}

export function TimelinePage({ showToast }: TimelinePageProps) {
  const { t, currentLang } = useI18n();
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [zoom, setZoom] = useState<ZoomLevel>("week");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => { loadMemories(); }, []);

  async function loadMemories() {
    setLoading(true);
    try {
      const data = await apiGet("memories", { page_size: "200" });
      const res = unwrapApiData(data) as Record<string, unknown>;
      const list = (res.items ?? res.memories ?? []) as MemoryItem[];
      setMemories(list?.filter((m: MemoryItem) => m.created_at) ?? []);
    } catch {
      showToast(t("error.memories"), true);
    } finally {
      setLoading(false);
    }
  }

  const now = Date.now() / 1000;
  const cutoff = now - ZOOM_HOURS[zoom] * 3600;
  const filtered = memories
    .filter((m) => parseTimestamp(m) >= cutoff)
    .sort((a, b) => parseTimestamp(b) - parseTimestamp(a));

  function formatDate(ts: number): string {
    const locale = localeForLang(currentLang());
    const d = new Date(ts * 1000);
    if (zoom === "day") return d.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
    if (zoom === "week") return d.toLocaleDateString(locale, { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    return d.toLocaleDateString(locale, { month: "long", day: "numeric" });
  }

  function getImportanceColor(imp: number): string {
    if (imp >= 0.7) return "var(--color-accent)";
    if (imp >= 0.4) return "var(--color-warning)";
    return "var(--text-tertiary)";
  }

  function getMemoryText(mem: MemoryItem): string {
    return mem.content ?? mem.summary ?? "—";
  }

  const zoomLabels: [ZoomLevel, string][] = [
    ["day", t("timeline.zoomDay")],
    ["week", t("timeline.zoomWeek")],
    ["month", t("timeline.zoomMonth")],
  ];

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--color-border)] shrink-0">
        <div className="flex items-center gap-3">
          <Clock size={20} className="text-[var(--color-accent)]" />
          <h1 className="text-lg font-semibold text-[var(--text-primary)]">{t("nav.timeline")}</h1>
        </div>
        <div className="flex items-center gap-1 bg-[var(--color-surface-secondary)] rounded-lg p-1">
          {zoomLabels.map(([z, label]) => (
            <button
              key={z}
              onClick={() => setZoom(z)}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
                zoom === z ? "bg-[var(--color-accent)] text-white" : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-auto">
        {loading ? (
          <div className="flex items-center justify-center h-full text-[var(--text-tertiary)] animate-pulse-soft">
            {t("timeline.loading")}
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-[var(--text-tertiary)]">
            <Calendar size={48} />
            <p className="text-sm">{t("timeline.empty")}</p>
          </div>
        ) : (
          <div className="relative pl-8 pr-6 py-6">
            <div className="absolute left-[23px] top-0 bottom-0 w-px bg-[var(--color-border)]" />
            <div className="space-y-6">
              {filtered.map((mem) => (
                <div key={mem.id} className="relative">
                  <div
                    className="absolute left-[-21px] top-2 w-3 h-3 rounded-full border-2 border-[var(--color-surface)] z-10"
                    style={{ backgroundColor: getImportanceColor(mem.importance ?? 0.5) }}
                  />
                  <div
                    onClick={() => setSelectedId(selectedId === mem.id ? null : mem.id)}
                    className={`p-4 rounded-xl border transition-all cursor-pointer ${
                      selectedId === mem.id
                        ? "border-[var(--color-accent)] bg-[var(--color-accent)]/5"
                        : "border-[var(--color-border)] bg-[var(--color-surface-secondary)] hover:border-[var(--color-border-light)] card-hover"
                    }`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-[var(--text-primary)] line-clamp-2">
                          {getMemoryText(mem)}
                        </p>
                        {selectedId === mem.id && (
                          <div className="mt-3 pt-3 border-t border-[var(--color-border)] space-y-2">
                            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                              <span className="text-[var(--text-tertiary)]">{t("table.importance")}</span>
                              <span className="text-[var(--text-primary)]">{(mem.importance ?? 0).toFixed(2)}</span>
                              <span className="text-[var(--text-tertiary)]">{t("table.type")}</span>
                              <span className="text-[var(--text-primary)]">{mem.type ?? "—"}</span>
                            </div>
                          </div>
                        )}
                      </div>
                      <span className="text-xs text-[var(--text-tertiary)] shrink-0">
                        {formatDate(parseTimestamp(mem))}
                      </span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="flex items-center justify-between px-6 py-2 border-t border-[var(--color-border)] text-xs text-[var(--text-tertiary)] shrink-0">
        <span>{t("timeline.count", String(filtered.length))}</span>
        <span>{t(`timeline.zoom${zoom.charAt(0).toUpperCase() + zoom.slice(1)}`)}</span>
      </div>
    </div>
  );
}
