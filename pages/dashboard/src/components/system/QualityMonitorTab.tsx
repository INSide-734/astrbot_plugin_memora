import { useState, useEffect, useCallback } from "react";
import { useI18n } from "@/hooks/useI18n";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { Button } from "@/components/ui/Button";
import { Activity, AlertTriangle, CheckCircle2, Info, XCircle, RefreshCw } from "lucide-react";
import { dashboardLocale, formatDashboardPercent, translateEnum } from "@/lib/i18n";
import type { QualityScoreEntry, QualityAlertEntry, QualityStats } from "@/types";

interface QualityMonitorTabProps {
  showToast: (msg: string, isError?: boolean) => void;
  onResetRequested?: () => void;
  refreshToken?: number;
  resetPending?: boolean;
}

/**
 * 展示当前插件进程内的记忆质量统计、告警和最近评分记录。
 *
 * @param props 用于请求刷新、触发重置与显示操作反馈的页面属性。
 * @returns 质量监控标签页内容。
 */
export function QualityMonitorTab({ showToast, onResetRequested, refreshToken = 0, resetPending = false }: QualityMonitorTabProps) {
  const { t, currentLang } = useI18n();
  const [stats, setStats] = useState<QualityStats | null>(null);
  const [scores, setScores] = useState<QualityScoreEntry[]>([]);
  const [alerts, setAlerts] = useState<QualityAlertEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const locale = dashboardLocale(currentLang());

  /** 获取三组质量监控数据，并在任一请求失败时保留已展示的数据。 */
  const fetchQuality = useCallback(async () => {
    setLoading(true);
    try {
      const [statsRes, scoresRes, alertsRes] = await Promise.all([
        apiRequest("quality/stats"),
        apiRequest("quality/recent?limit=10"),
        apiRequest("quality/alerts?limit=20"),
      ]);
      setStats(unwrapApiData(statsRes) as unknown as QualityStats);
      const scoresData = unwrapApiData(scoresRes);
      setScores(((scoresData as Record<string, unknown>)?.scores ?? []) as QualityScoreEntry[]);
      const alertsData = unwrapApiData(alertsRes);
      setAlerts(((alertsData as Record<string, unknown>)?.alerts ?? []) as QualityAlertEntry[]);
    } catch {
      // 请求失败时保留已展示的旧数据，避免操作期间闪烁为空态。
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchQuality(); }, [fetchQuality, refreshToken]);

  /** 请求父级确认重置，或在独立使用时直接重置后刷新数据。 */
  const resetQuality = async () => {
    if (onResetRequested) {
      onResetRequested();
      return;
    }
    try {
      await apiRequest("quality/reset", { method: "POST" });
      showToast(t("toast.qualityReset"));
      void fetchQuality();
    } catch (error) {
      showToast(String(error), true);
    }
  };

  if (loading) {
    return <p className="text-center text-sm text-[var(--text-tertiary)] py-12">{t("table.loading")}</p>;
  }
  if (!stats || stats.total_scored === 0) {
    return <p className="text-center text-sm text-[var(--text-tertiary)] py-12">{t("quality.noData")}</p>;
  }

  return (
    <>
      {/* 暂停状态提示。 */}
      {stats.paused && (
        <div className="flex items-center gap-2 rounded-lg bg-[var(--color-warning)]/10 border border-[var(--color-warning)]/30 px-4 py-2.5 text-sm text-[var(--text-primary)]">
          <AlertTriangle size={16} className="text-[var(--color-warning)]" />
          {t("quality.paused")}: {stats.pause_reason || "—"}
        </div>
      )}

      {/* 质量概览卡片。 */}
      <div className="grid grid-cols-3 gap-4">
        {[
          { label: t("quality.dim.consistency"), value: stats.avg_consistency },
          { label: t("quality.dim.coherence"), value: stats.avg_coherence },
          { label: t("quality.dim.relevance"), value: stats.avg_relevance },
          { label: t("quality.dim.freshness"), value: stats.avg_freshness },
          { label: t("quality.dim.accuracy"), value: stats.avg_accuracy },
          { label: t("quality.overall"), value: stats.avg_overall, primary: true },
        ].map((dim) => (
          <div key={dim.label} className={`rounded-lg border p-4 ${dim.primary ? "border-[var(--color-accent)]/30 bg-[var(--color-accent)]/5" : "border-[var(--color-border)] bg-[var(--color-surface)]"}`}>
            <div className={`text-2xl font-bold tabular-nums ${(dim.value ?? 0) >= 0.7 ? "text-[var(--color-success)]" : (dim.value ?? 0) >= 0.5 ? "text-[var(--color-accent)]" : "text-[var(--color-danger)]"}`}>
              {dim.value != null ? formatDashboardPercent(dim.value, locale, { maximumFractionDigits: 0 }) : "—"}
            </div>
            <div className="text-xs text-[var(--text-tertiary)] mt-1">{dim.label}</div>
          </div>
        ))}
      </div>

      {/* 告警列表。 */}
      {alerts.length > 0 && (
      <div className="overflow-hidden rounded-lg border border-[var(--color-border)]">
          <div className="flex items-center gap-2 border-b border-[var(--color-border)] bg-[var(--color-surface-secondary)] px-5 py-3">
            <Activity size={16} className="text-[var(--color-accent)]" />
            <span className="text-xs font-semibold text-[var(--text-primary)]">{t("quality.alerts")}</span>
            <span className="text-xs text-[var(--text-tertiary)]">({alerts.length})</span>
          </div>
          <div className="divide-y divide-[var(--color-border-light)]">
            {alerts.map((a) => {
              const levelIcon = a.level === "critical" ? <XCircle size={14} className="text-[var(--color-danger)]" /> :
                a.level === "high" ? <AlertTriangle size={14} className="text-[var(--color-warning)]" /> :
                a.level === "medium" ? <Info size={14} className="text-[var(--color-accent)]" /> :
                <CheckCircle2 size={14} className="text-[var(--text-tertiary)]" />;
              const levelBg = a.level === "critical" ? "bg-[var(--color-danger)]/10 text-[var(--color-danger)]" :
                a.level === "high" ? "bg-[var(--color-warning)]/10 text-[var(--color-warning)]" :
                a.level === "medium" ? "bg-[var(--color-accent)]/10 text-[var(--color-accent)]" :
                "bg-[var(--color-border-light)] text-[var(--text-tertiary)]";
              return (
                <div key={a.id} className="px-5 py-2.5 hover:bg-[var(--color-surface-secondary)] transition-colors">
                  <div className="flex items-center gap-2">
                    {levelIcon}
                    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-2xs font-medium ${levelBg}`}>
                      {translateEnum(t, "severity", a.level, a.level)}
                    </span>
                    <span className="text-xs font-medium text-[var(--text-primary)]">
                      {a.dimension === "overall"
                        ? t("quality.overall")
                        : translateEnum(t, "quality.dim", a.dimension, a.dimension)}
                    </span>
                    <span className="text-2xs text-[var(--text-tertiary)]">{new Date(a.timestamp * 1000).toLocaleString(locale)}</span>
                  </div>
                  <p className="text-xs text-[var(--text-secondary)] mt-1 ml-6">{a.message}</p>
                  <p className="text-2xs text-[var(--text-tertiary)] mt-0.5 ml-6">{a.suggestion}</p>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 最近评分记录。 */}
      {scores.length > 0 && (
      <div className="overflow-hidden rounded-lg border border-[var(--color-border)]">
          <div className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-surface-secondary)] px-5 py-3">
            <span className="text-xs font-semibold text-[var(--text-primary)]">{t("quality.recent")}</span>
            <Button variant="secondary" size="sm" onClick={resetQuality} disabled={resetPending}>
              <RefreshCw size={12} className="mr-1" />{t("quality.reset")}
            </Button>
          </div>
          <table className="w-full">
            <thead>
              <tr className="text-2xs text-[var(--text-tertiary)]">
                <th className="py-2 px-4 text-left font-medium">{t("quality.atomId")}</th>
                <th className="py-2 px-4 text-left font-medium">{t("quality.dim.consistency")}</th>
                <th className="py-2 px-4 text-left font-medium">{t("quality.dim.coherence")}</th>
                <th className="py-2 px-4 text-left font-medium">{t("quality.dim.relevance")}</th>
                <th className="py-2 px-4 text-left font-medium">{t("quality.dim.freshness")}</th>
                <th className="py-2 px-4 text-left font-medium">{t("quality.dim.accuracy")}</th>
                <th className="py-2 px-4 text-right font-medium">{t("quality.overall")}</th>
              </tr>
            </thead>
            <tbody>
              {scores.map((s) => (
                <tr key={s.atom_id} className="border-t border-[var(--color-border-light)] hover:bg-[var(--color-surface-secondary)] transition-colors">
                  <td className="py-2 px-4 text-xs font-mono text-[var(--text-primary)]">{s.atom_id}</td>
                  {[s.consistency, s.coherence, s.relevance, s.freshness, s.accuracy].map((v, j) => (
                    <td key={j} className="py-2 px-4 text-xs tabular-nums" style={{ color: v >= 0.7 ? "var(--color-success)" : v >= 0.5 ? "var(--color-accent)" : "var(--color-danger)" }}>{formatDashboardPercent(v, locale, { maximumFractionDigits: 0 })}</td>
                  ))}
                  <td className="py-2 px-4 text-xs tabular-nums text-right font-semibold" style={{ color: s.overall >= 0.7 ? "var(--color-success)" : s.overall >= 0.5 ? "var(--color-accent)" : "var(--color-danger)" }}>{formatDashboardPercent(s.overall, locale, { maximumFractionDigits: 0 })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
