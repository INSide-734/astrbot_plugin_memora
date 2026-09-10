import { useState } from "react";
import { Search, Zap } from "lucide-react";
import { useI18n } from "@/hooks/useI18n";
import { apiRequest, unwrapApiData, normalizeImportance } from "@/lib/bridge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/utils";
import type { RecallResult } from "@/types";
import { PageContent, PageFrame, PageHeader } from "@/components/layout/PageLayout";
import { Textarea } from "@/components/ui/textarea";
import { dashboardLocale, formatDashboardDate, formatDashboardNumber, translateEnum } from "@/lib/i18n";

interface RecallPageProps {
  showToast: (msg: string, isError?: boolean) => void;
}

/** 渲染检索调试页，提供查询参数、异步状态和召回结果的可访问展示。 */
export function RecallPage({ showToast }: RecallPageProps) {
  const { t, currentLang } = useI18n();
  const [query, setQuery] = useState("");
  const [k, setK] = useState(5);
  const [sessionId, setSessionId] = useState("");
  const [results, setResults] = useState<RecallResult[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const resultsLabel = results === null
    ? ""
    : t("recall.results", String(results.length)).replace("{count}", String(results.length));
  const locale = dashboardLocale(currentLang());
  const liveStatus = loading
    ? t("recall.searching")
    : results !== null
      ? t("recall.completed", resultsLabel, String(elapsed))
      : "";

  /** 提交一次检索请求，并在失败时清除过期结果。 */
  const runRecall = async () => {
    if (!query.trim()) return;
    setLoading(true);
    setResults(null);
    setErrorMessage(null);
    const start = performance.now();
    try {
      const body: Record<string, unknown> = { query: query.trim(), k };
      if (sessionId.trim()) body.session_id = sessionId.trim();
      const res = unwrapApiData(await apiRequest("recall/test", { method: "POST", body }));
      setResults((res.results ?? res.memories ?? []) as RecallResult[]);
    } catch (e) {
      const message = String(e);
      const localizedMessage = t("recall.requestFailed", message);
      setErrorMessage(localizedMessage);
      showToast(localizedMessage, true);
    } finally {
      setElapsed(Math.round(performance.now() - start));
      setLoading(false);
    }
  };

  return (
    <PageFrame variant="standard" aria-labelledby="recall-page-title">
      <PageHeader id="recall-page-title" title={t("nav.recall")} icon={<Search size={18} />} />
      <PageContent
        width="full"
        className="flex flex-col overflow-hidden p-0 sm:p-0 lg:p-0"
        aria-busy={loading}
      >
        <form
          className="space-y-4 p-4 sm:p-6"
          onSubmit={(event) => {
            event.preventDefault();
            void runRecall();
          }}
        >
          <div className="space-y-1.5">
            <label htmlFor="recall-query" className="text-xs font-medium text-muted-foreground">
              {t("recall.queryLabel")}
            </label>
            <Textarea
              id="recall-query"
              className="resize-none"
              rows={3}
              placeholder={t("recall.queryPh")}
              value={query}
              disabled={loading}
              aria-describedby="recall-query-hint"
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(event) => {
                if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
                  event.preventDefault();
                  event.currentTarget.form?.requestSubmit();
                }
              }}
            />
            <p id="recall-query-hint" className="text-xs text-muted-foreground">
              {t("recall.submitHint")}
            </p>
          </div>
          <div className="flex flex-wrap items-end gap-4">
            <div className="space-y-1">
              <label htmlFor="recall-k" className="text-xs font-medium text-muted-foreground">
                {t("recall.kLabel")}
              </label>
              <div className="flex min-h-11 items-center gap-3 md:min-h-8">
                <input
                  id="recall-k"
                  type="range"
                  min="1"
                  max="50"
                  value={k}
                  disabled={loading}
                  onChange={(e) => setK(Number(e.target.value))}
                  className="h-6 w-full max-w-48 accent-primary md:h-1.5 md:w-28"
                />
                <output htmlFor="recall-k" className="text-sm font-medium tabular-nums text-foreground">
                  {k}
                </output>
              </div>
            </div>
            <div className="space-y-1">
              <label htmlFor="recall-session" className="text-xs font-medium text-muted-foreground">
                {t("recall.sessionLabel")}
              </label>
              <Input
                id="recall-session"
                placeholder={t("placeholder.filterBySession")}
                value={sessionId}
                disabled={loading}
                onChange={(e) => setSessionId(e.target.value)}
                className="w-full min-w-0 min-h-11 sm:w-48 md:min-h-8"
              />
            </div>
            <Button type="submit" disabled={loading} className="min-h-11 md:min-h-8">
              <Zap data-icon="inline-start" size={14} /> {loading ? t("recall.searching") : t("recall.searchBtn")}
            </Button>
          </div>
        </form>
        <div data-status="recall" role="status" aria-live="polite" aria-atomic="true" className="sr-only">
          {liveStatus}
        </div>

        {errorMessage && (
          <p role="alert" className="border-y border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive sm:px-6">
            {errorMessage}
          </p>
        )}

        {results !== null && (
          <div className="flex-1 overflow-auto border-t" aria-labelledby="recall-results-summary">
            <div id="recall-results-summary" className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b px-4 py-2.5 text-sm sm:px-6">
              <span className="font-medium">{resultsLabel}</span>
              <span className="text-xs text-muted-foreground">{elapsed}ms</span>
            </div>
            {results.length === 0 ? (
              <p className="px-4 py-12 text-center text-sm text-muted-foreground sm:px-6">{t("recall.noMatch")}</p>
            ) : (
              <ol className="m-0 flex list-none flex-col gap-2 p-4" aria-label={t("recall.resultsRegion")}>
                {results.map((r, i) => {
                  const score = formatDashboardNumber(r.score ?? 0, locale, { minimumFractionDigits: 3, maximumFractionDigits: 3 });
                  return (
                    <li
                      key={r.id ?? i}
                      className="rounded-lg border bg-card p-3 text-card-foreground motion-safe:animate-slide-up sm:p-4"
                      style={{ animationDelay: `${Math.min(i, 5) * 50}ms`, animationFillMode: "both" }}
                    >
                      <div className="flex items-start justify-between gap-4">
                        <div className="min-w-0 flex-1">
                          <p className="break-words whitespace-pre-wrap text-sm">{String(r.content ?? r.summary ?? r.text ?? "")}</p>
                          <div className="mt-2 flex flex-wrap items-center gap-2">
                            <Badge variant="secondary">{r.type ? translateEnum(t, "memory.type", r.type) : "--"}</Badge>
                            <span className="text-xs text-muted-foreground">
                              {t("recall.importanceLabel")} {formatDashboardNumber(normalizeImportance(Number(r.importance ?? 0)), locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}
                            </span>
                            {r.created_at && (
                              <time dateTime={r.created_at} className="text-xs text-muted-foreground">
                                {formatDashboardDate(r.created_at, locale)}
                              </time>
                            )}
                          </div>
                        </div>
                        <div className="shrink-0 text-right">
                          <div className="text-2xs text-muted-foreground">{t("recall.scoreLabel")}</div>
                          <div className={cn(
                            "text-lg font-bold tabular-nums",
                            Number(r.score ?? 0) > 0.7 ? "text-accent-success" :
                            Number(r.score ?? 0) > 0.4 ? "text-accent-warning" : "text-text-tertiary"
                          )}>
                            {score}
                          </div>
                        </div>
                      </div>
                      {(r.doc_kw_score !== undefined || r.doc_vec_score !== undefined || r.graph_kw_score !== undefined || r.graph_vec_score !== undefined) && (
                        <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-2xs text-muted-foreground">
                          {r.doc_kw_score !== undefined && <span>{t("recall.docKwScore")}: {formatDashboardNumber(r.doc_kw_score, locale, { minimumFractionDigits: 3, maximumFractionDigits: 3 })}</span>}
                          {r.doc_vec_score !== undefined && <span>{t("recall.docVecScore")}: {formatDashboardNumber(r.doc_vec_score, locale, { minimumFractionDigits: 3, maximumFractionDigits: 3 })}</span>}
                          {r.graph_kw_score !== undefined && <span>{t("recall.graphKwScore")}: {formatDashboardNumber(r.graph_kw_score, locale, { minimumFractionDigits: 3, maximumFractionDigits: 3 })}</span>}
                          {r.graph_vec_score !== undefined && <span>{t("recall.graphVecScore")}: {formatDashboardNumber(r.graph_vec_score, locale, { minimumFractionDigits: 3, maximumFractionDigits: 3 })}</span>}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ol>
            )}
          </div>
        )}
      </PageContent>
    </PageFrame>
  );
}
