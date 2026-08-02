import { useState, useEffect } from "react";
import { apiGet, unwrapApiData } from "@/lib/bridge";
import { useI18n } from "@/hooks/useI18n";
import type { MemoryItem } from "@/types";
import { Calendar, Clock, RefreshCw } from "lucide-react";
import { PageContent, PageFrame, PageHeader } from "@/components/layout/PageLayout";
import { Button } from "@/components/ui/Button";
import { selectionStateVariants } from "@/components/ui/selection-state";
import { dashboardLocale, formatDashboardNumber, translateEnum } from "@/lib/i18n";
import { cn } from "@/lib/utils";

interface TimelinePageProps {
  showToast: (message: string, isError?: boolean) => void;
}

type ZoomLevel = "day" | "week" | "month";
const ZOOM_HOURS: Record<ZoomLevel, number> = { day: 24, week: 168, month: 720 };

function parseTimestamp(item: MemoryItem): number {
  const raw = item.created_at;
  if (typeof raw === "number") return raw;
  if (typeof raw === "string") {
    const d = new Date(raw);
    if (!isNaN(d.getTime())) return d.getTime() / 1000;
  }
  return 0;
}

function timelineDetailId(memoryId: string): string {
  return `timeline-detail-${encodeURIComponent(memoryId)}`;
}

export function TimelinePage({ showToast }: TimelinePageProps) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());
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
    <PageFrame variant="standard">
      <PageHeader title={t("nav.timeline")} icon={<Clock size={20} />} actions={
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={loading}
            onClick={() => void loadMemories()}
          >
            <RefreshCw data-icon="inline-start" className={loading ? "animate-spin" : undefined} />
            {t("common.refresh")}
          </Button>
          <div className="flex items-center gap-1 rounded-lg bg-muted p-1">
            {zoomLabels.map(([z, label]) => (
              <Button
                key={z}
                variant={zoom === z ? "default" : "ghost"}
                size="sm"
                onClick={() => setZoom(z)}
              >
                {label}
              </Button>
            ))}
          </div>
        </div>}
      />
      <PageContent className="flex flex-col overflow-hidden p-0 sm:p-0 lg:p-0">

      <div className="flex-1 overflow-auto">
        {loading && memories.length === 0 ? (
          <div className="flex h-full items-center justify-center text-muted-foreground animate-pulse-soft">
            {t("timeline.loading")}
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-muted-foreground">
            <Calendar size={48} />
            <p className="text-sm">{t("timeline.empty")}</p>
          </div>
        ) : (
          <div className="relative pl-8 pr-6 py-6">
            <div className="absolute bottom-0 left-[23px] top-0 w-px bg-border" />
            <div className="space-y-6">
              {filtered.map((mem) => (
                <div key={mem.id} className="relative">
                  <div
                    className="absolute left-[-21px] top-2 w-3 h-3 rounded-full border-2 border-[var(--color-surface)] z-10"
                    style={{ backgroundColor: getImportanceColor(mem.importance ?? 0.5) }}
                  />
                  <button
                    type="button"
                    aria-expanded={selectedId === mem.id}
                    aria-controls={timelineDetailId(mem.id)}
                    onClick={() => setSelectedId(selectedId === mem.id ? null : mem.id)}
                    className={cn(
                      "block w-full cursor-pointer rounded-lg border border-border bg-card p-4 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      selectionStateVariants({
                        kind: "current-item",
                        selected: selectedId === mem.id,
                      }),
                      selectedId !== mem.id && "hover:border-foreground/20 card-hover",
                    )}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <p className="line-clamp-2 text-sm font-medium text-foreground">
                          {getMemoryText(mem)}
                        </p>
                        {selectedId === mem.id && (
                          <div id={timelineDetailId(mem.id)} className="mt-3 space-y-2 border-t pt-3">
                            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                              <span className="text-muted-foreground">{t("table.importance")}</span>
                              <span className="text-foreground">{formatDashboardNumber(mem.importance ?? 0, locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                              <span className="text-muted-foreground">{t("table.type")}</span>
                              <span className="text-foreground">{mem.type ? translateEnum(t, "memory.type", mem.type) : "—"}</span>
                            </div>
                          </div>
                        )}
                      </div>
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {formatDate(parseTimestamp(mem))}
                      </span>
                    </div>
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="flex shrink-0 items-center justify-between border-t px-6 py-2 text-xs text-muted-foreground">
        <span>{t("timeline.count", String(filtered.length))}</span>
        <span>{t(`timeline.zoom${zoom.charAt(0).toUpperCase() + zoom.slice(1)}`)}</span>
      </div>
      </PageContent>
    </PageFrame>
  );
}
