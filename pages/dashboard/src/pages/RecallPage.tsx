import { useState } from "react";
import { Search, Zap } from "lucide-react";
import { useI18n } from "@/hooks/useI18n";
import { apiRequest, unwrapApiData, normalizeImportance } from "@/lib/bridge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/utils";
import type { RecallResult } from "@/types";
import { PageContent, PageFrame, PageHeader } from "@/components/layout/PageLayout";
import { Textarea } from "@/components/ui/textarea";

interface RecallPageProps {
  showToast: (msg: string, isError?: boolean) => void;
}

export function RecallPage({ showToast }: RecallPageProps) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [k, setK] = useState(5);
  const [sessionId, setSessionId] = useState("");
  const [results, setResults] = useState<RecallResult[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const resultsLabel = results === null
    ? ""
    : t("recall.results", String(results.length)).replace("{count}", String(results.length));

  const runRecall = async () => {
    if (!query.trim()) return;
    setLoading(true);
    const start = performance.now();
    try {
      const body: Record<string, unknown> = { query: query.trim(), k };
      if (sessionId.trim()) body.session_id = sessionId.trim();
      const res = unwrapApiData(await apiRequest("recall/test", { method: "POST", body }));
      setResults((res.results ?? res.memories ?? []) as RecallResult[]);
    } catch (e) {
      showToast(String(e), true);
    } finally {
      setElapsed(Math.round(performance.now() - start));
      setLoading(false);
    }
  };

  return (
    <PageFrame variant="standard">
      <PageHeader title={t("nav.recall")} icon={<Search size={18} />} />
      <PageContent width="full" className="flex flex-col overflow-hidden p-0 sm:p-0 lg:p-0">

      <div className="p-6 space-y-4">
        <Textarea
          className="resize-none"
          rows={3}
          placeholder={t("recall.queryPh")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="flex flex-wrap items-end gap-4">
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground">{t("recall.kLabel")}</label>
            <div className="flex items-center gap-3">
              <input
                type="range" min="1" max="50" value={k}
                onChange={(e) => setK(Number(e.target.value))}
                className="h-1.5 w-28 accent-[var(--color-accent)]"
              />
              <span className="text-sm font-medium tabular-nums text-foreground">{k}</span>
            </div>
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground">{t("recall.sessionLabel")}</label>
            <Input placeholder={t("placeholder.filterBySession")} value={sessionId}
              onChange={(e) => setSessionId(e.target.value)} className="w-48" />
          </div>
          <Button onClick={runRecall} disabled={loading}>
            <Zap size={14} /> {loading ? t("recall.searching") : t("recall.searchBtn")}
          </Button>
        </div>
      </div>

      {/* Results */}
      {results !== null && (
        <div className="flex-1 overflow-auto border-t">
          <div className="flex items-center gap-4 border-b px-6 py-2.5 text-sm">
            <span className="font-medium">{resultsLabel}</span>
            <span className="text-xs text-muted-foreground">{elapsed}ms</span>
          </div>
          {results.length === 0 ? (
            <p className="px-6 py-12 text-center text-sm text-muted-foreground">{t("recall.noMatch")}</p>
          ) : (
            <div className="space-y-2 p-4">
              {results.map((r, i) => (
                <div key={r.id ?? i} className="rounded-lg border bg-card p-4 text-card-foreground animate-slide-up"
                  style={{ animationDelay: `${i * 50}ms`, animationFillMode: "both" }}>
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm whitespace-pre-wrap">{String(r.content ?? r.summary ?? r.text ?? "")}</p>
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <Badge variant="secondary">{String(r.type ?? "").toUpperCase()}</Badge>
                        <span className="text-xs text-muted-foreground">
                          {t("recall.importanceLabel")} {normalizeImportance(Number(r.importance ?? 0)).toFixed(1)}
                        </span>
                        {r.created_at && <span className="text-xs text-muted-foreground">{String(r.created_at).slice(0, 10)}</span>}
                      </div>
                    </div>
                    <div className="shrink-0 text-right">
                      <div className={cn(
                        "text-lg font-bold tabular-nums",
                        Number(r.score ?? 0) > 0.7 ? "text-[var(--color-success)]" :
                        Number(r.score ?? 0) > 0.4 ? "text-[var(--color-warning)]" : "text-[var(--text-tertiary)]"
                      )}>
                        {Number(r.score ?? 0).toFixed(3)}
                      </div>
                    </div>
                  </div>
                  {/* Score breakdown */}
                  {(r.doc_kw_score !== undefined || r.doc_vec_score !== undefined || r.graph_kw_score !== undefined || r.graph_vec_score !== undefined) && (
                    <div className="mt-3 flex gap-2 text-2xs text-muted-foreground">
                      {r.doc_kw_score !== undefined && <span>Doc-KW: {Number(r.doc_kw_score).toFixed(3)}</span>}
                      {r.doc_vec_score !== undefined && <span>Doc-Vec: {Number(r.doc_vec_score).toFixed(3)}</span>}
                      {r.graph_kw_score !== undefined && <span>Graph-KW: {Number(r.graph_kw_score).toFixed(3)}</span>}
                      {r.graph_vec_score !== undefined && <span>Graph-Vec: {Number(r.graph_vec_score).toFixed(3)}</span>}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      </PageContent>
    </PageFrame>
  );
}
