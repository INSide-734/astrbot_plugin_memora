import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock3,
  DatabaseZap,
  Gauge,
  RefreshCw,
  RotateCw,
  ServerCog,
  ShieldAlert,
  Stethoscope,
} from "lucide-react";

import { Button } from "@/components/ui/Button";
import { useI18n } from "@/hooks/useI18n";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { dashboardLocale, translateEnum } from "@/lib/i18n";
import type {
  DiagnosticEvent,
  DiagnosticEventsResponse,
  DiagnosticHealthDomain,
  DiagnosticHealthResponse,
} from "@/types/intelligence";

interface DiagnosticCenterProps {
  showToast: (msg: string, isError?: boolean) => void;
}

type LoadingAction = "idle" | "refresh" | "rebuild";

const REQUIRED_DOMAINS = ["provider", "recall", "write", "scheduler", "index", "prometheus"] as const;

const DOMAIN_ICONS: Record<string, ReactNode> = {
  provider: <ServerCog size={16} />,
  recall: <Activity size={16} />,
  write: <DatabaseZap size={16} />,
  scheduler: <Clock3 size={16} />,
  index: <Gauge size={16} />,
  prometheus: <Stethoscope size={16} />,
};

const EMPTY_HEALTH: DiagnosticHealthResponse = {
  score: 0,
  level: "unknown",
  domains: [],
  recommended_actions: [],
};

function normalizeDomain(domain: DiagnosticHealthDomain): DiagnosticHealthDomain {
  return {
    name: String(domain.name || "unknown"),
    score: Number.isFinite(domain.score) ? domain.score : 0,
    status: String(domain.status || "unknown"),
    message: String(domain.message || ""),
  };
}

function normalizeEvent(event: DiagnosticEvent): DiagnosticEvent {
  const payload = event.payload && typeof event.payload === "object" && !Array.isArray(event.payload)
    ? event.payload
    : {};
  const resolvedAt = event.resolved_at === null || event.resolved_at === undefined
    ? null
    : String(event.resolved_at);
  return {
    event_id: String(event.event_id || `event-${Date.now()}`),
    created_at: String(event.created_at || ""),
    domain: String(event.domain || "unknown"),
    severity: String(event.severity || "info"),
    title: String(event.title || ""),
    message: String(event.message || ""),
    source: String(event.source || "unknown"),
    payload: payload as Record<string, unknown>,
    resolved_at: resolvedAt,
  };
}

function formatCreatedAt(value: string, locale: string): string {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(locale);
}

function statusClass(status: string): string {
  const normalized = status.toLowerCase();
  if (normalized === "healthy" || normalized === "ok" || normalized === "resolved") {
    return "text-[var(--color-success)] bg-[var(--color-success)]/10 border-[var(--color-success)]/20";
  }
  if (normalized === "critical" || normalized === "failed" || normalized === "error") {
    return "text-[var(--color-danger)] bg-[var(--color-danger)]/10 border-[var(--color-danger)]/20";
  }
  if (normalized === "watch" || normalized === "degraded" || normalized === "warning") {
    return "text-[var(--color-warning)] bg-[var(--color-warning)]/10 border-[var(--color-warning)]/20";
  }
  return "text-[var(--text-secondary)] bg-[var(--color-surface)] border-[var(--color-border)]";
}

function severityIcon(severity: string) {
  const normalized = severity.toLowerCase();
  if (normalized === "critical" || normalized === "error") return <ShieldAlert size={14} />;
  if (normalized === "warning" || normalized === "degraded") return <AlertTriangle size={14} />;
  return <CheckCircle2 size={14} />;
}

export function DiagnosticCenter({ showToast }: DiagnosticCenterProps) {
  const { t, currentLang } = useI18n();
  const [health, setHealth] = useState<DiagnosticHealthResponse | null>(null);
  const [events, setEvents] = useState<DiagnosticEvent[]>([]);
  const [totalEvents, setTotalEvents] = useState(0);
  const [loading, setLoading] = useState(true);
  const [action, setAction] = useState<LoadingAction>("idle");
  const [confirmRebuild, setConfirmRebuild] = useState(false);

  const loadHealth = useCallback(async () => {
    const data = unwrapApiData<DiagnosticHealthResponse>(await apiRequest("diagnostics/health"));
    setHealth({
      ...EMPTY_HEALTH,
      ...data,
      domains: Array.isArray(data.domains)
        ? data.domains.map(normalizeDomain)
        : [],
      recommended_actions: Array.isArray(data.recommended_actions) ? data.recommended_actions.map(String) : [],
    });
  }, []);

  const loadEvents = useCallback(async () => {
    const data = unwrapApiData<DiagnosticEventsResponse>(await apiRequest("diagnostics/events?limit=50"));
    setEvents(Array.isArray(data.events)
      ? data.events.map(normalizeEvent)
      : []);
    setTotalEvents(Number.isFinite(data.total) ? data.total : 0);
  }, []);

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      await Promise.all([loadHealth(), loadEvents()]);
    } catch (e) {
      showToast(String(e), true);
    } finally {
      setLoading(false);
    }
  }, [loadEvents, loadHealth, showToast]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const domains = useMemo(() => {
    const byName = new Map((health?.domains ?? []).map((domain) => [domain.name.toLowerCase(), domain]));
    return REQUIRED_DOMAINS.map((name) => byName.get(name) ?? {
      name,
      score: 100,
      status: "healthy",
      message: t("intelligence.diagnostics.noActiveSignal"),
    });
  }, [health, t]);

  const runRefresh = async () => {
    setAction("refresh");
    try {
      unwrapApiData(await apiRequest("diagnostics/actions/run", {
        method: "POST",
        body: { action: "refresh_metrics" },
      }));
      await loadHealth();
      showToast(t("intelligence.diagnostics.metricsRefreshed"));
    } catch (e) {
      showToast(String(e), true);
    } finally {
      setAction("idle");
    }
  };

  const runRebuild = async () => {
    setAction("rebuild");
    try {
      unwrapApiData(await apiRequest("diagnostics/actions/run", {
        method: "POST",
        body: { action: "rebuild_index", confirmed: true },
      }));
      setConfirmRebuild(false);
      await Promise.all([loadHealth(), loadEvents()]);
      showToast(t("intelligence.diagnostics.rebuildRequested"));
    } catch (e) {
      showToast(String(e), true);
    } finally {
      setAction("idle");
    }
  };

  const currentHealth = health ?? EMPTY_HEALTH;
  const locale = dashboardLocale(currentLang());

  return (
    <section className="space-y-4">
      <div className="grid gap-4 xl:grid-cols-[360px_1fr]">
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)] p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-2xs uppercase tracking-normal text-[var(--text-tertiary)]">{t("intelligence.diagnostics.health")}</p>
              <div className="mt-2 flex items-end gap-3">
                <span className="text-4xl font-semibold tabular-nums text-[var(--text-primary)]">
                  {loading ? "--" : currentHealth.score}
                </span>
                <span className={`mb-1 rounded-full border px-2 py-1 text-2xs font-semibold uppercase ${statusClass(currentHealth.level)}`}>
                  {translateEnum(t, "intelligence.diagnostics.level", currentHealth.level)}
                </span>
              </div>
            </div>
            <Gauge size={20} className="text-[var(--color-accent)]" />
          </div>

          <div className="mt-5 border-t border-[var(--color-border)] pt-4">
            <p className="text-xs font-semibold text-[var(--text-secondary)]">{t("intelligence.diagnostics.recommendedActions")}</p>
            {currentHealth.recommended_actions.length > 0 ? (
              <ul className="mt-2 space-y-1.5">
                {currentHealth.recommended_actions.map((item) => (
                  <li key={item} className="rounded-md bg-[var(--color-surface)] px-3 py-2 text-xs text-[var(--text-secondary)]">
                    {item}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 rounded-md bg-[var(--color-surface)] px-3 py-2 text-xs text-[var(--text-tertiary)]">
                {t("intelligence.diagnostics.noRecommendedAction")}
              </p>
            )}
          </div>
        </div>

        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)] p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold text-[var(--text-primary)]">{t("intelligence.diagnostics.actions")}</h3>
              <p className="mt-1 text-xs text-[var(--text-tertiary)]">{t("intelligence.diagnostics.actionsDescription")}</p>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="secondary" size="sm" onClick={runRefresh} disabled={action !== "idle"}>
                {action === "refresh" ? <RotateCw size={13} className="animate-spin" /> : <RefreshCw size={13} />}
                {t("intelligence.diagnostics.refreshMetrics")}
              </Button>
              <Button variant="destructive" size="sm" onClick={() => setConfirmRebuild(true)} disabled={action !== "idle"}>
                <DatabaseZap size={13} />
                {t("intelligence.diagnostics.rebuildIndex")}
              </Button>
            </div>
          </div>

          {confirmRebuild && (
            <div className="mt-4 flex items-center justify-between gap-3 rounded-lg border border-[var(--color-warning)]/30 bg-[var(--color-warning)]/10 px-4 py-2.5">
              <span className="text-sm text-[var(--text-primary)]">
                {t("intelligence.diagnostics.confirmRebuildMessage")}
              </span>
              <div className="flex items-center gap-2">
                <Button variant="destructive" size="sm" onClick={runRebuild} disabled={action !== "idle"}>
                  {action === "rebuild" ? <RotateCw size={13} className="animate-spin" /> : <DatabaseZap size={13} />}
                  {t("intelligence.diagnostics.confirmRebuild")}
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setConfirmRebuild(false)} disabled={action !== "idle"}>
                  {t("common.cancel")}
                </Button>
              </div>
            </div>
          )}

          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {domains.map((domain) => (
              <div key={domain.name} className="rounded-lg border border-[var(--color-border-light)] bg-[var(--color-surface)] p-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]">
                    <span className="text-[var(--text-tertiary)]">{DOMAIN_ICONS[domain.name] ?? <Stethoscope size={16} />}</span>
                    {translateEnum(t, "intelligence.diagnostics.domain", domain.name, domain.name)}
                  </div>
                  <span className={`rounded-full border px-2 py-0.5 text-2xs font-semibold uppercase ${statusClass(domain.status)}`}>
                    {translateEnum(t, "intelligence.diagnostics.status", domain.status)}
                  </span>
                </div>
                <div className="mt-3 flex items-end justify-between gap-3">
                  <p className="text-xs leading-5 text-[var(--text-secondary)]">
                    {domain.message || t("intelligence.diagnostics.noMessage")}
                  </p>
                  <span className="text-lg font-semibold tabular-nums text-[var(--text-primary)]">{domain.score}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)]">
        <div className="flex items-center justify-between border-b border-[var(--color-border)] px-4 py-3">
          <div>
            <h3 className="text-sm font-semibold text-[var(--text-primary)]">{t("intelligence.diagnostics.eventTimeline")}</h3>
            <p className="mt-0.5 text-xs text-[var(--text-tertiary)]">{t("intelligence.diagnostics.eventsCount", String(totalEvents))}</p>
          </div>
          <Activity size={16} className="text-[var(--text-tertiary)]" />
        </div>
        <div className="divide-y divide-[var(--color-border-light)]">
          {events.length === 0 ? (
            <p className="px-4 py-6 text-center text-xs text-[var(--text-tertiary)]">{t("intelligence.diagnostics.noEvents")}</p>
          ) : (
            events.map((event) => {
              const state = event.resolved_at ? "resolved" : "open";
              return (
                <article key={event.event_id} className="grid gap-3 px-4 py-3 text-xs lg:grid-cols-[170px_120px_120px_1fr_90px]">
                  <div className="font-mono text-[var(--text-tertiary)]">{formatCreatedAt(event.created_at, locale)}</div>
                  <div className="flex items-center gap-1.5 text-[var(--text-secondary)]">
                    {severityIcon(event.severity)}
                    {translateEnum(t, "intelligence.diagnostics.severity", event.severity)}
                  </div>
                  <div className="font-medium text-[var(--text-secondary)]">
                    {translateEnum(t, "intelligence.diagnostics.domain", event.domain, event.domain)}
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-[var(--text-primary)]">
                      {event.title || t("intelligence.diagnostics.untitledEvent")}
                    </p>
                    <p className="mt-1 text-[var(--text-secondary)]">{event.message}</p>
                    <p className="mt-1 text-2xs uppercase text-[var(--text-tertiary)]">{event.source}</p>
                  </div>
                  <span className={`h-fit justify-self-start rounded-full border px-2 py-1 text-2xs font-semibold uppercase ${statusClass(state)}`}>
                    {translateEnum(t, "intelligence.diagnostics.state", state)}
                  </span>
                </article>
              );
            })
          )}
        </div>
      </div>
    </section>
  );
}
