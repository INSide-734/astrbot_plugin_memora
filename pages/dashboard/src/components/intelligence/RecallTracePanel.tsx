import { useEffect, useMemo, useRef, useState } from "react";
import { GitBranch, Loader2, Search } from "lucide-react";

import { Button } from "@/components/ui/Button";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select";
import { StatePanel } from "@/components/ui/StatePanel";
import { useI18n } from "@/hooks/useI18n";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { dashboardLocale, formatDashboardNumber, translateEnum } from "@/lib/i18n";
import { ApiRequestError } from "@/types/editing";
import type {
  RecallTraceFilteredCandidate,
  RecallTraceInjectionSummary,
  RecallTraceRequest,
  RecallTraceResponse,
  RecallTraceResult,
} from "@/types/intelligence";
import type { IntelligenceNavigationTarget } from "@/types/navigation";

import { TraceContributionList } from "./TraceContributionList";

type TraceDetailState = "idle" | "loading" | "ready" | "unavailable" | "error";

/** 稳定 unavailable 语义的错误码；其余失败按可重试错误展示。 */
const UNAVAILABLE_TRACE_CODES = ["trace_unavailable", "recall_trace_not_found"];

/** 生产快照注入摘要的固定 allowlist；枚举值复用既有 injection.* 翻译。 */
const INJECTION_SUMMARY_FIELDS: ReadonlyArray<{
  field: keyof RecallTraceInjectionSummary;
  labelKey: string;
  enumPrefix?: string;
}> = [
  { field: "candidate_count", labelKey: "intelligence.trace.injection.candidate_count" },
  { field: "injected_count", labelKey: "intelligence.trace.injection.injected_count" },
  { field: "selected_count", labelKey: "intelligence.trace.injection.selected_count" },
  {
    field: "configured_budget_chars",
    labelKey: "intelligence.trace.injection.configured_budget_chars",
  },
  {
    field: "effective_budget_chars",
    labelKey: "intelligence.trace.injection.effective_budget_chars",
  },
  {
    field: "routing_mode",
    labelKey: "intelligence.trace.injection.routing_mode",
    enumPrefix: "injection.mode",
  },
  {
    field: "resolved_preset",
    labelKey: "intelligence.trace.injection.resolved_preset",
    enumPrefix: "injection.preset",
  },
  {
    field: "resolved_delivery",
    labelKey: "intelligence.trace.injection.resolved_delivery",
    enumPrefix: "injection.delivery",
  },
  {
    field: "outcome",
    labelKey: "intelligence.trace.injection.outcome",
    enumPrefix: "injection.outcome",
  },
];

interface RecallTracePanelProps {
  showToast: (msg: string, isError?: boolean) => void;
  navigationTarget?: IntelligenceNavigationTarget | null;
}

const chatTypeOptions = ["private", "group"];

/** 将用户输入钳制到后端允许的整数范围。 */
function clampNumber(value: number, min: number, max: number, fallback: number): number {
  if (!Number.isFinite(value)) return fallback;
  return Math.min(max, Math.max(min, Math.round(value)));
}

/** 按当前语言格式化毫秒值。 */
function formatMs(value: number, locale: string): string {
  return `${formatDashboardNumber(value, locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}ms`;
}

/** 格式化可选分数，缺失时显示安全占位符。 */
function formatScore(value: number | undefined, locale: string): string {
  return value === undefined
    ? "--"
    : formatDashboardNumber(value, locale, { minimumFractionDigits: 3, maximumFractionDigits: 3 });
}

/** 读取后端安全 DTO 中有界数量的标量 metadata；trace_kind 由来源徽标单独展示。 */
function metadataEntries(metadata: Record<string, unknown>, limit = 5) {
  return Object.entries(metadata)
    .filter(([key]) => key !== "trace_kind")
    .slice(0, limit);
}

/** 展示已经过后端固定 allowlist 过滤的标量。 */
function MetadataChips({ metadata, limit = 5 }: { metadata: Record<string, unknown>; limit?: number }) {
  const { t } = useI18n();
  const entries = metadataEntries(metadata, limit);
  if (entries.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-1">
      {entries.map(([key, value]) => (
        <span
          key={key}
          className="rounded bg-[var(--color-border-light)] px-1.5 py-0.5 text-2xs text-[var(--text-tertiary)]"
        >
          {translateEnum(t, "intelligence.trace.metadata", key, key)}: {String(value)}
        </span>
      ))}
    </div>
  );
}

/** 展示不含 canonical memory ID 的单项排名和分数贡献。 */
function ResultCard({ result }: { result: RecallTraceResult }) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());

  return (
    <article className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)]">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--color-border-light)] px-4 py-3">
        <div>
          <p className="text-sm font-semibold text-[var(--text-primary)]">
            #{result.rank}
          </p>
          <p className="mt-1 text-2xs text-[var(--text-tertiary)]">
            {t("intelligence.trace.initialFinal", formatScore(result.initial_score, locale), formatScore(result.final_score, locale))}
          </p>
        </div>
        <MetadataChips metadata={result.metadata} limit={4} />
      </div>
      <div className="p-4">
        <div>
          <p className="mb-2 text-2xs uppercase text-[var(--text-tertiary)]">{t("intelligence.trace.contributions")}</p>
          <TraceContributionList contributions={result.score_contributions} />
        </div>
      </div>
    </article>
  );
}

/** 展示不含候选 ID 的过滤原因、阶段和分数。 */
function FilteredCandidateRow({ item }: { item: RecallTraceFilteredCandidate }) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());
  return (
    <tr className="border-t border-[var(--color-border-light)]">
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

/** 提供只读召回追踪输入与隐私安全结果视图。 */
export function RecallTracePanel({
  navigationTarget,
  showToast,
}: RecallTracePanelProps) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());
  const [query, setQuery] = useState("");
  const [k, setK] = useState(5);
  const [sessionId, setSessionId] = useState("");
  const [userId, setUserId] = useState("");
  const [chatType, setChatType] = useState("private");
  const [chainDepth, setChainDepth] = useState(2);
  const [trace, setTrace] = useState<RecallTraceResponse | null>(null);
  const [detailState, setDetailState] = useState<TraceDetailState>("idle");
  const [loading, setLoading] = useState(false);
  const feedbackRef = useRef({ showToast, t });

  const clampedK = useMemo(() => clampNumber(k, 1, 20, 5), [k]);
  const clampedChainDepth = useMemo(() => clampNumber(chainDepth, 0, 5, 2), [chainDepth]);
  const canSubmit = query.trim().length > 0 && !loading;

  useEffect(() => {
    feedbackRef.current = { showToast, t };
  }, [showToast, t]);

  useEffect(() => {
    const traceId = navigationTarget?.traceId;
    if (!traceId || navigationTarget.tab !== "recallTrace") return;
    let active = true;
    setLoading(true);
    setDetailState("loading");
    void apiRequest(
      `recall/trace/detail?trace_id=${encodeURIComponent(traceId)}`,
      { retries: 0 },
    )
      .then((response) => {
        if (!active) return;
        const nextTrace = unwrapApiData<RecallTraceResponse>(response);
        setTrace(nextTrace);
        setDetailState("ready");
      })
      .catch((error) => {
        if (!active) return;
        setTrace(null);
        if (error instanceof ApiRequestError && UNAVAILABLE_TRACE_CODES.includes(error.code)) {
          setDetailState("unavailable");
          return;
        }
        setDetailState("error");
        const feedback = feedbackRef.current;
        feedback.showToast(feedback.t("common.errorPrefix", String(error)), true);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [
    navigationTarget?.requestId,
    navigationTarget?.tab,
    navigationTarget?.traceId,
  ]);

  const submitTrace = async () => {
    const trimmedQuery = query.trim();
    if (!trimmedQuery) return;

    const body: RecallTraceRequest = {
      query: trimmedQuery,
      k: clampedK,
      chat_type: chatType,
      chain_depth: clampedChainDepth,
    };
    const trimmedSessionId = sessionId.trim();
    const trimmedUserId = userId.trim();
    if (trimmedSessionId) body.session_id = trimmedSessionId;
    if (trimmedUserId) body.user_id = trimmedUserId;

    setK(clampedK);
    setChainDepth(clampedChainDepth);
    setLoading(true);
    try {
      const response = await apiRequest("recall/trace", { method: "POST", body });
      setTrace(unwrapApiData<RecallTraceResponse>(response));
      setDetailState("ready");
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
            <span>{t("intelligence.trace.chatTypeLabel")}</span>
            <Select
              value={chatType}
              onValueChange={(value) => {
                if (value) setChatType(value);
              }}
            >
              <SelectTrigger aria-label={t("intelligence.trace.chatTypeLabel")} className="mt-1 h-8 w-full text-xs">
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
        {detailState === "loading" && !trace ? (
          <StatePanel state="loading" title={t("intelligence.trace.detailLoading")} />
        ) : detailState === "unavailable" ? (
          <StatePanel
            state="empty"
            title={t("intelligence.trace.detailUnavailable")}
            description={t("intelligence.trace.detailUnavailableHint")}
          />
        ) : detailState === "error" && !trace ? (
          <StatePanel
            state="error"
            title={t("intelligence.trace.detailFailed")}
            description={t("intelligence.trace.detailFailedHint")}
          />
        ) : trace ? (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded bg-[var(--color-border-light)] px-1.5 py-0.5 text-2xs text-[var(--text-secondary)]">
                {trace.metadata?.trace_kind === "production"
                  ? t("intelligence.trace.origin.production")
                  : t("intelligence.trace.origin.preview")}
              </span>
              <MetadataChips metadata={trace.metadata} limit={4} />
            </div>

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

            {trace.injection ? (
              <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)]">
                <div className="border-b border-[var(--color-border)] px-4 py-3">
                  <h4 className="text-sm font-semibold text-[var(--text-primary)]">{t("intelligence.trace.injection.title")}</h4>
                </div>
                <dl className="grid gap-3 p-4 md:grid-cols-3">
                  {INJECTION_SUMMARY_FIELDS.map(({ field, labelKey, enumPrefix }) => {
                    const value = trace.injection?.[field];
                    if (value === undefined || value === null) return null;
                    return (
                      <div key={field} className="min-w-0">
                        <dt className="text-2xs uppercase text-[var(--text-tertiary)]">{t(labelKey)}</dt>
                        <dd className="mt-1 break-words text-sm tabular-nums text-[var(--text-primary)]">
                          {enumPrefix
                            ? translateEnum(t, enumPrefix, value, String(value))
                            : formatDashboardNumber(value, locale)}
                        </dd>
                      </div>
                    );
                  })}
                </dl>
              </div>
            ) : null}

            <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)]">
              <div className="border-b border-[var(--color-border)] px-4 py-3">
                <h4 className="text-sm font-semibold text-[var(--text-primary)]">{t("intelligence.trace.stages")}</h4>
              </div>
              <div className="grid gap-2 p-4 md:grid-cols-2 xl:grid-cols-3">
                {trace.stages.map((stage) => (
                  <div key={`${stage.name}-${stage.status ?? "default"}`} className="rounded-lg border border-[var(--color-border-light)] bg-[var(--color-surface)] p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="text-xs font-medium text-[var(--text-primary)]">
                        {translateEnum(t, "intelligence.trace.stage", stage.name, stage.name)}
                      </p>
                      <div className="flex flex-wrap items-center justify-end gap-1.5">
                        {stage.status ? (
                          <span className="rounded-full border border-[var(--color-border)] bg-[var(--color-surface-secondary)] px-1.5 py-0.5 text-2xs font-semibold uppercase text-[var(--text-secondary)]">
                            {translateEnum(t, "intelligence.trace.stageStatus", stage.status, stage.status)}
                          </span>
                        ) : null}
                        <span className="text-2xs tabular-nums text-[var(--text-secondary)]">{formatMs(stage.duration_ms, locale)}</span>
                      </div>
                    </div>
                    <p className="mt-2 text-2xs text-[var(--text-tertiary)]">{t("intelligence.trace.candidates", String(stage.candidate_count))}</p>
                    <div className="mt-2">
                      <MetadataChips metadata={stage.metadata} limit={3} />
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {trace.fact_alignment || trace.source_status ? (
              <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)]">
                <div className="border-b border-[var(--color-border)] px-4 py-3">
                  <h4 className="text-sm font-semibold text-[var(--text-primary)]">{t("intelligence.trace.evidence.title")}</h4>
                </div>
                <dl className="grid gap-3 p-4 sm:grid-cols-2 xl:grid-cols-4">
                  {trace.fact_alignment ? (
                    <>
                      <div className="min-w-0">
                        <dt className="text-2xs uppercase text-[var(--text-tertiary)]">{t("intelligence.trace.factAlignment.aligned")}</dt>
                        <dd className="mt-1 text-sm font-semibold tabular-nums text-[var(--text-primary)]">{formatDashboardNumber(trace.fact_alignment.aligned, locale)}</dd>
                      </div>
                      <div className="min-w-0">
                        <dt className="text-2xs uppercase text-[var(--text-tertiary)]">{t("intelligence.trace.factAlignment.misaligned")}</dt>
                        <dd className="mt-1 text-sm font-semibold tabular-nums text-[var(--text-primary)]">{formatDashboardNumber(trace.fact_alignment.misaligned, locale)}</dd>
                      </div>
                      <div className="min-w-0">
                        <dt className="text-2xs uppercase text-[var(--text-tertiary)]">{t("intelligence.trace.factAlignment.undeterminable")}</dt>
                        <dd className="mt-1 text-sm font-semibold tabular-nums text-[var(--text-primary)]">{formatDashboardNumber(trace.fact_alignment.undeterminable, locale)}</dd>
                      </div>
                    </>
                  ) : null}
                  {trace.source_status ? (
                    <div className="min-w-0">
                      <dt className="text-2xs uppercase text-[var(--text-tertiary)]">{t("intelligence.trace.sourceStatus.title")}</dt>
                      <dd className="mt-1 break-words text-sm font-semibold text-[var(--text-primary)]">
                        {translateEnum(t, "intelligence.trace.sourceStatus", trace.source_status, trace.source_status)}
                      </dd>
                    </div>
                  ) : null}
                </dl>
              </div>
            ) : null}

            <div className="space-y-3">
              {trace.results.map((result) => <ResultCard key={result.rank} result={result} />)}
            </div>

            <div className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)]">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--color-border)] px-4 py-3">
                <h4 className="text-sm font-semibold text-[var(--text-primary)]">{t("intelligence.trace.filteredCandidates")}</h4>
              </div>
              {trace.filtered.length === 0 && (trace.filter_summary?.length ?? 0) === 0 ? (
                <p className="px-4 py-5 text-xs text-[var(--text-tertiary)]">{t("intelligence.trace.noFilteredCandidates")}</p>
              ) : (
                <div className="overflow-x-auto">
                  {(trace.filter_summary?.length ?? 0) > 0 ? (
                    <table className="min-w-[520px] w-full text-left text-xs">
                      <thead className="text-[var(--text-tertiary)]">
                        <tr>
                          <th className="px-4 py-2 font-medium">{t("intelligence.trace.table.reason")}</th>
                          <th className="px-4 py-2 font-medium">{t("intelligence.trace.table.stage")}</th>
                          <th className="px-4 py-2 text-right font-medium">{t("intelligence.trace.table.count")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(trace.filter_summary ?? []).map((item) => (
                          <tr
                            key={`${item.stage}-${item.reason}`}
                            className="border-t border-[var(--color-border-light)]"
                          >
                            <td className="px-4 py-2 text-[var(--text-secondary)]">
                              {translateEnum(t, "intelligence.trace.filterReason", item.reason, item.reason)}
                            </td>
                            <td className="px-4 py-2 text-[var(--text-tertiary)]">
                              {item.stage
                                ? translateEnum(t, "intelligence.trace.stage", item.stage, item.stage)
                                : "--"}
                            </td>
                            <td className="px-4 py-2 text-right tabular-nums text-[var(--text-secondary)]">
                              {formatDashboardNumber(item.count, locale)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : null}
                  {trace.filtered.length > 0 ? (
                    <table className="min-w-[520px] w-full text-left text-xs">
                      <thead className="text-[var(--text-tertiary)]">
                        <tr>
                          <th className="px-4 py-2 font-medium">{t("intelligence.trace.table.reason")}</th>
                          <th className="px-4 py-2 font-medium">{t("intelligence.trace.table.stage")}</th>
                          <th className="px-4 py-2 text-right font-medium">{t("intelligence.trace.table.score")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {trace.filtered.map((item, index) => (
                          <FilteredCandidateRow
                            key={`${item.reason}-${item.stage ?? "none"}-${index}`}
                            item={item}
                          />
                        ))}
                      </tbody>
                    </table>
                  ) : null}
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
