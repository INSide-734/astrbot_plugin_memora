import { useEffect, useState } from "react";
import {
  Archive,
  Check,
  FilePenLine,
  GitMerge,
  ShieldCheck,
  Trash2,
} from "lucide-react";

import { Button } from "@/components/ui/Button";
import { useI18n } from "@/hooks/useI18n";
import { dashboardLocale, translateEnum } from "@/lib/i18n";
import type { ReviewAction, ReviewActionValue, ReviewItem } from "@/types/intelligence";

interface ReviewItemDetailProps {
  item: ReviewItem | null;
  actions: ReviewAction[];
  loading?: boolean;
  submitting?: boolean;
  onAction: (
    action: ReviewActionValue,
    payload?: Record<string, unknown>,
    confirmed?: boolean,
  ) => Promise<void>;
}

type ConfirmAction = "merge" | "archive" | "delete" | null;

function formatTime(value: number, locale: string): string {
  if (!value) return "--";
  const ms = value < 10_000_000_000 ? value * 1000 : value;
  const date = new Date(ms);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString(locale);
}

function chipClass(value: string): string {
  const normalized = value.toLowerCase();
  if (normalized === "high" || normalized === "critical" || normalized === "delete") {
    return "border-[var(--color-danger)]/25 bg-[var(--color-danger)]/10 text-[var(--color-danger)]";
  }
  if (normalized === "medium" || normalized === "archive" || normalized === "merge" || normalized === "merged") {
    return "border-[var(--color-warning)]/25 bg-[var(--color-warning)]/10 text-[var(--color-warning)]";
  }
  if (normalized === "approved" || normalized === "safe" || normalized === "mark_safe" || normalized === "edited") {
    return "border-[var(--color-success)]/25 bg-[var(--color-success)]/10 text-[var(--color-success)]";
  }
  return "border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--text-secondary)]";
}

function metadataEntries(metadata: Record<string, unknown>) {
  return Object.entries(metadata).filter(([, value]) => {
    return value !== null && value !== undefined && typeof value !== "object";
  });
}

export function ReviewItemDetail({ item, actions, loading, submitting, onAction }: ReviewItemDetailProps) {
  const { t, currentLang } = useI18n();
  const [editContent, setEditContent] = useState("");
  const [mergeTarget, setMergeTarget] = useState("");
  const [confirmAction, setConfirmAction] = useState<ConfirmAction>(null);
  const [confirming, setConfirming] = useState(false);
  const locale = dashboardLocale(currentLang());
  const statusLabel = (value: string) => translateEnum(t, "intelligence.review.status", value, value);
  const reasonLabel = (value: string) => translateEnum(t, "intelligence.review.reason", value, value);
  const severityLabel = (value: string) => translateEnum(t, "severity", value, value);
  const actionLabel = (value: string) => translateEnum(t, "intelligence.review.action", value, value);

  useEffect(() => {
    setEditContent(item?.content_preview ?? "");
    setMergeTarget("");
    setConfirmAction(null);
    setConfirming(false);
  }, [item?.item_id, item?.content_preview]);

  if (loading) {
    return (
      <section className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)] p-4">
        <p className="text-sm text-[var(--text-tertiary)]">{t("intelligence.review.loadingDetail")}</p>
      </section>
    );
  }

  if (!item) {
    return (
      <section className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)] p-4">
        <p className="text-sm text-[var(--text-tertiary)]">{t("intelligence.review.noSelection")}</p>
      </section>
    );
  }

  const metadata = metadataEntries(item.metadata ?? {});

  const submitAction = (action: ReviewActionValue, payload: Record<string, unknown> = {}, confirmed = false) => {
    void onAction(action, payload, confirmed).catch(() => undefined);
  };

  const confirm = async () => {
    if (!confirmAction || confirming) return;
    const action = confirmAction;
    setConfirming(true);
    try {
      if (action === "merge") {
        await onAction("merge", { target_memory_id: mergeTarget.trim() }, true);
      } else {
        await onAction(action, {}, true);
      }
      setConfirmAction(null);
    } catch {
      // Keep the confirmation visible so the operator can retry or cancel.
    } finally {
      setConfirming(false);
    }
  };

  return (
    <section className="space-y-4">
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)]">
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[var(--color-border)] px-4 py-3">
          <div>
            <p className="text-2xs uppercase tracking-normal text-[var(--text-tertiary)]">{t("intelligence.review.memoryReview")}</p>
            <h3 className="mt-1 font-mono text-sm font-semibold text-[var(--text-primary)]">{item.memory_id}</h3>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-full border px-2 py-1 text-2xs font-semibold uppercase ${chipClass(item.severity)}`}>
              {severityLabel(item.severity)}
            </span>
            <span className={`rounded-full border px-2 py-1 text-2xs font-semibold uppercase ${chipClass(item.status)}`}>
              {statusLabel(item.status)}
            </span>
          </div>
        </div>

        <div className="space-y-4 p-4">
          <div>
            <p className="text-xs font-semibold text-[var(--text-secondary)]">{t("intelligence.review.memoryContent")}</p>
            <p className="mt-2 rounded-lg border border-[var(--color-border-light)] bg-[var(--color-surface)] px-3 py-2 text-sm leading-6 text-[var(--text-primary)]">
              {item.content_preview}
            </p>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <div>
              <p className="text-xs font-semibold text-[var(--text-secondary)]">{t("intelligence.review.reviewReasons")}</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {item.reasons.map((reason) => (
                  <span key={reason} className={`rounded-full border px-2 py-1 text-2xs font-semibold ${chipClass(reason)}`}>
                    {reasonLabel(reason)}
                  </span>
                ))}
              </div>
            </div>
            <div>
              <p className="text-xs font-semibold text-[var(--text-secondary)]">{t("intelligence.review.provenance")}</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {metadata.length === 0 ? (
                  <span className="text-xs text-[var(--text-tertiary)]">{t("intelligence.review.noProvenance")}</span>
                ) : (
                  metadata.map(([key, value]) => (
                    <span key={key} className="rounded-full border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-2xs text-[var(--text-secondary)]">
                      {key}: {String(value)}
                    </span>
                  ))
                )}
              </div>
            </div>
          </div>

          <div className="grid gap-4 xl:grid-cols-[1fr_240px]">
            <label className="block">
              <span className="text-xs font-semibold text-[var(--text-secondary)]">{t("intelligence.review.editContent")}</span>
              <textarea
                value={editContent}
                onChange={(event) => setEditContent(event.target.value)}
                className="mt-2 min-h-24 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--color-accent)]"
              />
            </label>
            <div>
              <label className="block">
                <span className="text-xs font-semibold text-[var(--text-secondary)]">{t("intelligence.review.mergeTarget")}</span>
                <input
                  value={mergeTarget}
                  onChange={(event) => setMergeTarget(event.target.value)}
                  placeholder="target_memory_id"
                  className="mt-2 h-9 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--color-accent)]"
                />
              </label>
            </div>
          </div>

          <div>
            <p className="text-xs font-semibold text-[var(--text-secondary)]">{t("intelligence.review.candidateActions")}</p>
            <div className="mt-2 flex flex-wrap gap-2">
              <Button size="sm" variant="secondary" disabled={submitting} onClick={() => submitAction("approve", {}, false)}>
                <Check size={13} />
                {t("intelligence.review.approve")}
              </Button>
              <Button size="sm" variant="secondary" disabled={submitting} onClick={() => submitAction("mark_safe", {}, false)}>
                <ShieldCheck size={13} />
                {t("intelligence.review.markSafe")}
              </Button>
              <Button size="sm" variant="outline" disabled={submitting} onClick={() => submitAction("edit", { content: editContent }, false)}>
                <FilePenLine size={13} />
                {t("intelligence.review.edit")}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={submitting || !mergeTarget.trim()}
                onClick={() => setConfirmAction("merge")}
              >
                <GitMerge size={13} />
                {t("intelligence.review.merge")}
              </Button>
              <Button size="sm" variant="outline" disabled={submitting} onClick={() => setConfirmAction("archive")}>
                <Archive size={13} />
                {t("common.archive")}
              </Button>
              <Button size="sm" variant="destructive" disabled={submitting} onClick={() => setConfirmAction("delete")}>
                <Trash2 size={13} />
                {t("common.delete")}
              </Button>
            </div>
          </div>

          {confirmAction && (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-[var(--color-warning)]/30 bg-[var(--color-warning)]/10 px-4 py-2.5">
              <span className="text-sm text-[var(--text-primary)]">
                <span className="font-semibold">{t("intelligence.review.confirmAction", t(`intelligence.review.action.${confirmAction}`))}</span> {item.memory_id}
              </span>
              <div className="flex items-center gap-2">
                <Button size="sm" variant={confirmAction === "delete" ? "destructive" : "secondary"} disabled={submitting || confirming} onClick={confirm}>
                  {t("intelligence.review.confirm")}
                </Button>
                <Button size="sm" variant="ghost" disabled={submitting || confirming} onClick={() => setConfirmAction(null)}>
                  {t("common.cancel")}
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)]">
        <div className="border-b border-[var(--color-border)] px-4 py-3">
          <h3 className="text-sm font-semibold text-[var(--text-primary)]">{t("intelligence.review.actionHistory")}</h3>
        </div>
        <div className="divide-y divide-[var(--color-border-light)]">
          {actions.length === 0 ? (
            <p className="px-4 py-5 text-sm text-[var(--text-tertiary)]">{t("intelligence.review.noActions")}</p>
          ) : (
            actions.map((action) => (
              <article key={action.action_id} className="grid gap-3 px-4 py-3 text-xs md:grid-cols-[150px_120px_120px_1fr]">
                <span className="font-mono text-[var(--text-tertiary)]">{formatTime(action.created_at, locale)}</span>
                <span className={`w-fit rounded-full border px-2 py-1 font-semibold uppercase ${chipClass(action.action)}`}>
                  {actionLabel(action.action)}
                </span>
                <span className="text-[var(--text-secondary)]">{action.actor_id || "--"}</span>
                <span className="font-mono text-[var(--text-tertiary)]">{JSON.stringify(action.payload ?? {})}</span>
              </article>
            ))
          )}
        </div>
      </div>
    </section>
  );
}
