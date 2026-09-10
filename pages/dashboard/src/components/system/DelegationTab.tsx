import { useState, useEffect, useCallback } from "react";
import { useI18n } from "@/hooks/useI18n";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { ShieldCheck, Activity } from "lucide-react";
import type { DelegationStatus } from "@/types";

interface DelegationTabProps {
  showToast: (msg: string, isError?: boolean) => void;
}

export function DelegationTab({ showToast: _showToast }: DelegationTabProps) {
  const { t } = useI18n();
  const [delegation, setDelegation] = useState<DelegationStatus | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchDelegation = useCallback(async () => {
    setLoading(true);
    try {
      const res = unwrapApiData(await apiRequest("delegation/status"));
      setDelegation(res as unknown as DelegationStatus);
    } catch {
      // silently keep stale data
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchDelegation(); }, [fetchDelegation]);

  if (loading) {
    return <p className="text-center text-sm text-[var(--text-tertiary)] py-12">{t("table.loading")}</p>;
  }
  if (!delegation) {
    return <p className="text-center text-sm text-[var(--text-tertiary)] py-12">{t("table.noData")}</p>;
  }

  return (
    <>
      {/* Plugin status cards */}
      <div className="grid grid-cols-2 gap-4">
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
          <div className="flex items-center gap-2 mb-3">
            <ShieldCheck size={16} className="text-[var(--color-accent)]" />
            <span className="text-sm font-semibold text-[var(--text-primary)]">Self Learning</span>
          </div>
          <div className="flex items-center gap-3">
            <div className={`h-3 w-3 rounded-full ${delegation.self_learning_active ? "bg-[var(--color-success)]" : "bg-[var(--text-tertiary)]"}`} />
            <span className={`text-sm font-medium ${delegation.self_learning_active ? "text-[var(--color-success)]" : "text-[var(--text-tertiary)]"}`}>
              {delegation.self_learning_active ? t("delegation.active") : t("delegation.inactive")}
            </span>
          </div>
          {delegation.self_learning_active && (
            <div className="text-xs text-[var(--text-tertiary)] mt-2">{delegation.self_learning_label}</div>
          )}
        </div>
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
          <div className="flex items-center gap-2 mb-3">
            <ShieldCheck size={16} className="text-[var(--color-accent)]" />
            <span className="text-sm font-semibold text-[var(--text-primary)]">Group Chat Plus</span>
          </div>
          <div className="flex items-center gap-3">
            <div className={`h-3 w-3 rounded-full ${delegation.chatplus_active ? "bg-[var(--color-success)]" : "bg-[var(--text-tertiary)]"}`} />
            <span className={`text-sm font-medium ${delegation.chatplus_active ? "text-[var(--color-success)]" : "text-[var(--text-tertiary)]"}`}>
              {delegation.chatplus_active ? t("delegation.active") : t("delegation.inactive")}
            </span>
          </div>
          {delegation.chatplus_active && (
            <div className="text-xs text-[var(--text-tertiary)] mt-2">{delegation.chatplus_label}</div>
          )}
        </div>
      </div>

      {/* Delegation matrix */}
      <div className="overflow-hidden rounded-lg border border-[var(--color-border)]">
        <div className="flex items-center gap-2 border-b border-[var(--color-border)] bg-[var(--color-surface-secondary)] px-5 py-3">
          <Activity size={16} className="text-[var(--color-accent)]" />
          <span className="text-xs font-semibold text-[var(--text-primary)]">{t("delegation.matrix")}</span>
        </div>
        <table className="w-full">
          <thead>
            <tr className="text-xs text-[var(--text-tertiary)] border-b border-[var(--color-border-light)]">
              <th className="py-2.5 px-5 text-left font-medium">{t("table.name")}</th>
              <th className="py-2.5 px-5 text-left font-medium">{t("table.status")}</th>
            </tr>
          </thead>
          <tbody>
            {[
              { label: t("delegation.jargon"), delegated: delegation.delegated_jargon },
              { label: t("delegation.expression"), delegated: delegation.delegated_expression },
              { label: t("delegation.affection"), delegated: delegation.delegated_affection },
              { label: t("delegation.reply"), delegated: delegation.delegated_reply },
            ].map((row) => (
              <tr key={row.label} className="border-t border-[var(--color-border-light)] hover:bg-[var(--color-surface-secondary)] transition-colors">
                <td className="py-2.5 px-5 text-xs text-[var(--text-primary)]">{row.label}</td>
                <td className="py-2.5 px-5">
                  <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-2xs font-medium ${
                    row.delegated ? "bg-[var(--color-accent)]/10 text-[var(--color-accent)]" : "bg-[var(--color-border-light)] text-[var(--text-tertiary)]"
                  }`}>
                    {row.delegated ? t("delegation.delegated") : t("delegation.local")}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
