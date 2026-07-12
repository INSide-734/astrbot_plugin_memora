import { useMemo, useState } from "react";
import { GitBranch, Loader2, Search } from "lucide-react";

import { Button } from "@/components/ui/Button";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select";
import { useI18n } from "@/hooks/useI18n";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { dashboardLocale, formatDashboardNumber, translateEnum } from "@/lib/i18n";
import type {
  RecallTraceFilteredCandidate,
  RecallTraceRequest,
  RecallTraceResponse,
  RecallTraceResult,
} from "@/types/intelligence";

import { TraceContributionList } from "./TraceContributionList";

interface RecallTracePanelProps {
  showToast: (msg: string, isError?: boolean) => void;
}

const chatTypeOptions = ["private", "group"];

function clampNumber(value: number, min: number, max: number, fallback: number): number {
  if (!Number.isFinite(value)) return fallback;
  return Math.min(max, Math.max(min, Math.round(value)));
}

function formatMs(value: number, locale: string): string {
  return `${formatDashboardNumber(value, locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}ms`;
}

function formatScore(value: number | undefined, locale: string): string {
  return value === undefined
    ? "--"
    : formatDashboardNumber(value, locale, { minimumFractionDigits: 3, maximumFractionDigits: 3 });
}

function metadataEntries(metadata: Record<string, unknown>, limit = 5) {
  return Object.entries(metadata).slice(0, limit);
}

function MetadataChips({ metadata, limit = 5 }: { metadata: Record<string, unknown>; limit?: number }) {
  const entries = metadataEntries(metadata, limit);
  if (entries.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-1">
      {entries.map(([key, value]) => (
        <span
          key={key}
          className="rounded bg-[var(--color-border-light)] px-1.5 py-0.5 text-2xs text-[var(--text-tertiary)]"
        >
          {key}: {String(value)}
        </span>
      ))}
    </div>
  );
}

function ResultCard({ result }: { result: RecallTraceResult }) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());

  return (
    <article className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)]">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--color-border-light)] px-4 py-3">
        <div>
          <p className="text-sm font-semibold text-[var(--text-primary)]">
            #{result.rank} {result.doc_id}
          </p>
          <p className="mt-1 text-2xs text-[var(--text-tertiary)]">
            {t("intelligence.trace.initialFinal", formatScore(result.initial_score, locale), formatScore(result.final_score, locale))}
          </p>
        </div>
        <MetadataChips metadata={result.metadata} limit={4} />
      </div>
      <div className="grid gap-4 p-4 lg:grid-cols-[1.1fr_0.9fr]">
        <div>
          <p className="mb-2 text-2xs uppercase text-[var(--text-tertiary)]">{t("intelligence.trace.contributions")}</p>
          <TraceContributionList contributions={result.score_contributions} />
        </div>
        <div>
          <p className="mb-2 text-2xs uppercase text-[var(--text-tertiary)]">{t("intelligence.trace.graphPaths")}</p>
          {result.graph_paths.length === 0 ? (
            <p className="text-xs text-[var(--text-tertiary)]">{t("intelligence.trace.noGraphProvenance")}</p>
          ) : (
            <div className="space-y-2">
              {result.graph_paths.map((path, index) => (
                <div
                  key={`${result.doc_id}-path-${index}`}
                  className="rounded-lg border border-[var(--color-border-light)] bg-[var(--color-surface)] p-2.5"
                >
                  <p className="text-xs text-[var(--text-primary)]">{path.nodes.join(" -> ")}</p>
                  <p className="mt-1 text-2xs text-[var(--text-tertiary)]">
                    {path.edges.join(" / ")} · {t("intelligence.trace.scoreValue", formatScore(path.score, locale))}
                  </p>
                  <div className="mt-2">
                    <MetadataChips metadata={path.metadata} limit={3} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </article>
  );
}

function FilteredCandidateRow({ item }: { item: RecallTraceFilteredCandidate }) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());
  return (
    <tr className="border-t border-[var(--color-border-light)]">
      <td className="px-4 py-2 font-medium text-[var(--text-primary)]">{item.doc_id}</td>
      <td className="px-4 py-2 text-[var(--text-secondary)]">
        {translateEnum(t, "intelligence.trace.filterReason", item.reason, item.reason)}
      </td>
      <td className="px-4 py-2 text-[var(--text-tertiary)]">
        {item.stage ? translateEnum(t, "intelligence.trace.stage", item.stage, item.stage) : "--"}
      </td>
      <td className="px-4 py-2 text-right tabular-nums text-[var(--text-secondary)]">{formatScore(item.score, locale)}</td>
    </tr>
  );
}

export function RecallTracePanel({ showToast }: RecallTracePanelProps) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());
  const [query, setQuery] = useState("");
  const [k, setK] = useState(5);
  const [sessionId, setSessionId] = useState("");
  const [userId, setUserId] = useState("");
  const [chatType, setChatType] = useState("private");
  const [chainDepth, setChainDepth] = useState(2);
  const [trace, setTrace] = useState<RecallTraceResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const clampedK = useMemo(() => clampNumber(k, 1, 20, 5), [k]);
  const clampedChainDepth = useMemo(() => clampNumber(chainDepth, 0, 5, 2), [chainDepth]);
  const canSubmit = query.trim().length > 0 && !loading;

  const submitTrace = async () => {
    const trimmedQuery = query.trim();
    if (!trimmedQuery) return;

    const body: RecallTraceRequest = {
      query: trimmedQuery,
      k: clampedK,
      session_id: sessionId.trim(),
      user_id: userId.trim(),
      chat_type: chatType,
      chain_depth: clampedChainDepth,
    };

    setK(clampedK);
    setChainDepth(clampedChainDepth);
    setLoading(true);
    try {
      const response = await apiRequest("recall/trace", { method: "POST", body });
      const nextTrace = unwrapApiData<RecallTraceResponse>(response);
      setTrace(nextTrace);
    } catch (error) {
      showToast(t("common.errorPrefix", error instanceof Error ? error.message : String(error)), true);
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="grid gap-4 xl:grid-cols-[0.72fr_1.28fr]">
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)]">
        <div className="flex items-center gap-2 border-b border-[var(--color-border)] px-4 py-3">
          <GitBranch size={14} className="text-[var(--color-accent)]" />
          <h3 className="text-sm font-semibold text-[var(--text-primary)]">{t("intelligence.trace.title")}</h3>
        </div>
        <div className="space-y-4 p-4">
          <label className="block text-xs font-medium text-[var(--text-secondary)]">
            {t("intelligence.trace.query")}
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t("intelligence.trace.queryPlaceholder")}
              className="mt-1 h-8 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2 text-xs text-[var(--text-primary)]"
            />
          </label>

          <div className="grid grid-cols-2 gap-3">
            <label className="text-xs font-medium text-[var(--text-secondary)]">
              k
              <input
                aria-label="k"
                type="number"
                min={1}
                max={20}
                value={k}
                onBlur={() => setK((value) => clampNumber(value, 1, 20, 5))}
                onChange={(event) => setK(Number(event.target.value))}
                className="mt-1 h-8 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2 text-xs text-[var(--text-primary)]"
              />
            </label>
            <label className="text-xs font-medium text-[var(--text-secondary)]">
              {t("intelligence.trace.chainDepth")}
              <input
                aria-label={t("intelligence.trace.chainDepth")}
                type="number"
                min={0}
                max={5}
                value={chainDepth}
                onBlur={() => setChainDepth((value) => clampNumber(value, 0, 5, 2))}
                onChange={(event) => setChainDepth(Number(event.target.value))}
                className="mt-1 h-8 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2 text-xs text-[var(--text-primary)]"
              />
            </label>
          </div>

          <label className="block text-xs font-medium text-[var(--text-secondary)]">
            {t("intelligence.trace.sessionId")}
            <input
              value={sessionId}
              onChange={(event) => setSessionId(event.target.value)}
              placeholder={t("intelligence.trace.sessionPlaceholder")}
              className="mt-1 h-8 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2 text-xs text-[var(--text-primary)]"
            />
          </label>

          <label className="block text-xs font-medium text-[var(--text-secondary)]">
            {t("intelligence.trace.userId")}
            <input
              value={userId}
              onChange={(event) => setUserId(event.target.value)}
              placeholder={t("intelligence.trace.userPlaceholder")}
              className="mt-1 h-8 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2 text-xs text-[var(--text-primary)]"
            />
          </label>

          <div className="block text-xs font-medium text-[var(--text-secondary)]">
            <span>{t("intelligence.trace.chatType")}</span>
            <Select
              value={chatType}
              onValueChange={(value) => {
                if (value) setChatType(value);
              }}
            >
              <SelectTrigger aria-label={t("intelligence.trace.chatType")} className="mt-1 h-8 w-full text-xs">
                <span>{t(`intelligence.trace.chatType.${chatType}`)}</span>
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {chatTypeOptions.map((option) => (
                    <SelectItem key={option} value={option}>{t(`intelligence.trace.chatType.${option}`)}</SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
          </div>

          <Button onClick={() => { void submitTrace(); }} disabled={!canSubmit} className="w-full">
            {loading ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
            {loading ? t("intelligence.trace.tracing") : t("intelligence.trace.trace")}
          </Button>
        </div>
      </div>

      <div className="space-y-4">
        {trace ? (
          <>
            <div className="grid gap-3 md:grid-cols-4">
              {[
                [t("intelligence.trace.stat.trace"), trace.trace_id],
                [t("intelligence.trace.stat.total"), formatMs(trace.total_ms, locale)],
                [t("intelligence.trace.stat.stages"), String(trace.stages.length)],
                [t("intelligence.trace.stat.results"), String(trace.results.length)],
              ].map(([label, value]) => (
                <div key={label} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)] p-3">
                  <p className="text-2xs uppercase text-[var(--text-tertiary)]">{label}</p>
                  <p className="mt-2 truncate text-sm font-semibold tabular-nums text-[var(--text-primary)]">{value}</p>
                </div>
              ))}
            </div>

            <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)]">
              <div className="border-b border-[var(--color-border)] px-4 py-3">
                <h4 className="text-sm font-semibold text-[var(--text-primary)]">{t("intelligence.trace.stages")}</h4>
              </div>
              <div className="grid gap-2 p-4 md:grid-cols-2 xl:grid-cols-3">
                {trace.stages.map((stage) => (
                  <div key={stage.name} className="rounded-lg border border-[var(--color-border-light)] bg-[var(--color-surface)] p-3">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-xs font-medium text-[var(--text-primary)]">
                        {translateEnum(t, "intelligence.trace.stage", stage.name, stage.name)}
                      </p>
                      <span className="text-2xs tabular-nums text-[var(--text-secondary)]">{formatMs(stage.duration_ms, locale)}</span>
                    </div>
                    <p className="mt-2 text-2xs text-[var(--text-tertiary)]">{t("intelligence.trace.candidates", String(stage.candidate_count))}</p>
                    <div className="mt-2">
                      <MetadataChips metadata={stage.metadata} limit={3} />
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="space-y-3">
              {trace.results.map((result) => <ResultCard key={result.doc_id} result={result} />)}
            </div>

            <div className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)]">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--color-border)] px-4 py-3">
                <h4 className="text-sm font-semibold text-[var(--text-primary)]">{t("intelligence.trace.filteredCandidates")}</h4>
                <MetadataChips metadata={trace.metadata} limit={4} />
              </div>
              {trace.filtered.length === 0 ? (
                <p className="px-4 py-5 text-xs text-[var(--text-tertiary)]">{t("intelligence.trace.noFilteredCandidates")}</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="min-w-[520px] w-full text-left text-xs">
                    <thead className="text-[var(--text-tertiary)]">
                      <tr>
                        <th className="px-4 py-2 font-medium">{t("intelligence.trace.table.doc")}</th>
                        <th className="px-4 py-2 font-medium">{t("intelligence.trace.table.reason")}</th>
                        <th className="px-4 py-2 font-medium">{t("intelligence.trace.table.stage")}</th>
                        <th className="px-4 py-2 text-right font-medium">{t("intelligence.trace.table.score")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {trace.filtered.map((item) => <FilteredCandidateRow key={`${item.doc_id}-${item.reason}`} item={item} />)}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="rounded-lg border border-dashed border-[var(--color-border)] bg-[var(--color-surface-secondary)] px-4 py-10 text-center text-sm text-[var(--text-tertiary)]">
            {t("intelligence.trace.emptyPrompt")}
          </div>
        )}
      </div>
    </section>
  );
}
